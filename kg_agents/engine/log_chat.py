"""Log-flavoured chat handlers.

Each handler takes a user query (already routed by intent_router) and produces
the natural-language reply plus a list of evidence dicts to attach to the
ChatResponse. Replies are LLM-generated so phrasing varies and grounds in the
retrieved log records.
"""
from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from kg_agents.config import OPENAI_API_KEY, OPENAI_CHAT_MODEL

from .log_search import search_logs, summarize_logs

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _evidence_items(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact projection of search matches for the ChatResponse payload."""
    out: list[dict[str, Any]] = []
    for m in matches:
        top = m.get("top_match_log") or {}
        recent = m.get("most_recent_log") or {}
        out.append({
            "event_signature_id": m.get("event_signature_id"),
            "score": m.get("score"),
            "occurrence_count": m.get("occurrence_count"),
            "linked_failure_mode_id": m.get("linked_failure_mode_id"),
            "linked_symptom_id": m.get("linked_symptom_id"),
            "first_seen_at": m.get("first_seen_at"),
            "last_seen_at": m.get("last_seen_at"),
            "top_match": {
                "log_id": top.get("log_id"),
                "occurred_at": top.get("occurred_at"),
                "severity_text": top.get("severity_text"),
                "title": top.get("title"),
                "body": top.get("body"),
                "action_taken": top.get("action_taken"),
                "outcome": top.get("outcome"),
                "work_order_id": top.get("work_order_id"),
                "component_name_raw": top.get("component_name_raw"),
            },
            "most_recent": {
                "log_id": recent.get("log_id"),
                "occurred_at": recent.get("occurred_at"),
                "severity_text": recent.get("severity_text"),
                "title": recent.get("title"),
            },
            "all_log_ids": m.get("all_log_ids", []),
            "rerank_rationale": m.get("rerank_rationale"),
        })
    return out


def _llm_compose(
    system: str,
    user_payload: dict[str, Any],
    chat_model: str,
) -> str:
    resp = _get_client().chat.completions.create(
        model=chat_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def handle_log_history_search(
    query: str,
    instance_id: str,
    filters: dict[str, Any],
    chat_model: str = OPENAI_CHAT_MODEL,
) -> tuple[str, list[dict[str, Any]]]:
    result = search_logs(query, instance_id, filters=filters, limit=5)
    if result["match_count"] == 0:
        return (
            "I couldn't find any past events on this machine that match that description.",
            [],
        )

    system = (
        "You are an industrial maintenance assistant answering a HISTORICAL "
        "question about past events on a specific machine. You are given the "
        "user query and the top matching event signatures from the machine's "
        "log database. Answer concisely in markdown:\n"
        "- Lead with a one-line summary (yes/no + count).\n"
        "- Show the most relevant occurrence with date, severity, work order, "
        "and what the technician did and the outcome.\n"
        "- If there are other relevant occurrences, list them as bullets with "
        "date and short title.\n"
        "- If a knowledge-graph failure mode is linked, mention it briefly at "
        "the end as supporting context (one sentence). Do not force a "
        "diagnosis if the user only asked for history.\n"
        "Cite work order ids when present. Do not invent dates or fields."
    )
    user_payload = {
        "user_query": query,
        "matches": result["matches"],
    }
    reply = _llm_compose(system, user_payload, chat_model)
    return reply, _evidence_items(result["matches"])


def handle_log_analytics(
    query: str,
    instance_id: str,
    chat_model: str = OPENAI_CHAT_MODEL,
) -> tuple[str, list[dict[str, Any]]]:
    summary = summarize_logs(instance_id)
    if not summary or summary.get("row_count", 0) == 0:
        return ("There are no logs available for this machine yet.", [])

    system = (
        "You are an industrial maintenance assistant answering an ANALYTICS "
        "question about a specific machine. You are given the user query and "
        "an aggregate summary of the log database (top recurring signatures, "
        "top affected components, severity distribution, monthly counts, open "
        "events, downtime by component). Answer concisely in markdown:\n"
        "- Lead with the headline number relevant to the question.\n"
        "- Surface the 3-5 most informative items as a short table or bullets.\n"
        "- Mention open vs closed where useful.\n"
        "Do not invent numbers; only use values from the summary payload."
    )
    user_payload = {
        "user_query": query,
        "summary": summary,
    }
    reply = _llm_compose(system, user_payload, chat_model)
    return reply, []


def handle_work_order_lookup(
    query: str,
    instance_id: str,
    filters: dict[str, Any],
    chat_model: str = OPENAI_CHAT_MODEL,
) -> tuple[str, list[dict[str, Any]]]:
    result = search_logs(query, instance_id, filters=filters, limit=3)
    if result["match_count"] == 0:
        return (
            "I couldn't locate a work order matching that on this machine.",
            [],
        )

    system = (
        "You are an industrial maintenance assistant answering a question about "
        "a specific past work order or intervention. You are given the user "
        "query and the matching log entries. Answer concisely in markdown:\n"
        "- Lead with the work order id, date, and short title.\n"
        "- Then: status, outcome, planned vs actual duration, downtime.\n"
        "- Then: action_taken (what the technician did), with key technical "
        "details from the body.\n"
        "If multiple work orders match, surface the best match first and list "
        "the others as a short bullet list. Do not invent fields."
    )
    user_payload = {
        "user_query": query,
        "matches": result["matches"],
    }
    reply = _llm_compose(system, user_payload, chat_model)
    return reply, _evidence_items(result["matches"])


def fetch_hybrid_history_evidence(
    query: str,
    instance_id: str,
    filters: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """For hybrid intent: run log search, return a markdown appendix string and
    the evidence list. The KG flow handles the diagnostic part of the reply
    and this is appended after."""
    result = search_logs(query, instance_id, filters=filters, limit=3)
    if result["match_count"] == 0:
        return ("", [])

    lines: list[str] = ["", "---", "", "**Past similar events on this machine:**", ""]
    for m in result["matches"]:
        top = m.get("top_match_log") or {}
        date = (top.get("occurred_at") or "")[:10]
        title = top.get("title") or ""
        wo = top.get("work_order_id") or ""
        outcome = top.get("outcome") or ""
        bullet = f"- {date}"
        if wo:
            bullet += f" · {wo}"
        if title:
            bullet += f" — {title}"
        if outcome:
            bullet += f" *(outcome: {outcome})*"
        lines.append(bullet)

    return ("\n".join(lines), _evidence_items(result["matches"]))
