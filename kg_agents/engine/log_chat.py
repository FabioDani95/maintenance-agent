"""Log-flavoured chat handlers.

Each handler takes a user query (already routed by intent_router) and produces
the natural-language reply plus a list of evidence dicts to attach to the
ChatResponse. Replies are LLM-generated so phrasing varies and grounds in the
retrieved log records.
"""
from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any

from openai import OpenAI

from kg_agents.config import OPENAI_API_KEY, OPENAI_CHAT_MODEL

from .log_loader import load_log_store
from .log_search import search_logs, summarize_logs

_client: OpenAI | None = None
_WORK_ORDER_RE = re.compile(r"\bWO-[A-Za-z0-9][A-Za-z0-9-]*\b", re.IGNORECASE)
_SOLUTION_QUESTION_RE = re.compile(
    r"\b(solution|fix|fixed|resolve|resolved|repair|repaired|"
    r"what\s+did\s+we\s+do|what\s+was\s+done|last\s+time|previous\s+fix)\b",
    re.IGNORECASE,
)


class _HandlerTimer:
    def __init__(self) -> None:
        now = perf_counter()
        self._start = now
        self._last = now
        self.timings: dict[str, float] = {}

    def mark(self, stage: str) -> None:
        now = perf_counter()
        self.timings[f"{stage}_s"] = round(now - self._last, 3)
        self._last = now

    def add_nested(self, prefix: str, nested: dict[str, float]) -> None:
        for key, value in nested.items():
            self.timings[f"{prefix}_{key}"] = value

    def snapshot(self) -> dict[str, float]:
        out = dict(self.timings)
        out["total_s"] = round(perf_counter() - self._start, 3)
        return out


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _use_fast_template(chat_model: str) -> bool:
    return chat_model.strip().lower() in {"gpt-5-nano", "fast"}


def _fmt(value: Any, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def _date(value: Any) -> str:
    return _fmt(value)[:10]


def _format_duration(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return f"{value} min"


def _is_solution_question(query: str) -> bool:
    return bool(_SOLUTION_QUESTION_RE.search(query or ""))


def _history_template(query: str, matches: list[dict[str, Any]]) -> str:
    total = sum(int(m.get("occurrence_count") or 0) for m in matches)
    top_match = matches[0].get("top_match_log") or {}
    answer_label = "Most Relevant Fix" if _is_solution_question(query) else "Best Match"
    lines = [
        "**Snapshot**",
        f"- Matching occurrences: **{total}**",
        f"- Best matching pattern: `{_fmt(matches[0].get('event_signature_id'))}`",
        f"- Most relevant work order: `{_fmt(top_match.get('work_order_id'))}`",
        "",
        f"**{answer_label}**",
        f"- Date: {_date(top_match.get('occurred_at'))}",
        f"- Severity: {_fmt(top_match.get('severity_text'))}",
        f"- Status: {_fmt(top_match.get('status'))}",
        f"- Title: {_fmt(top_match.get('title'))}",
    ]
    if top_match.get("outcome"):
        lines.append(f"- Outcome: {_fmt(top_match.get('outcome'))}")
    if top_match.get("action_taken"):
        lines.extend(["", "**Action Taken**", str(top_match["action_taken"])])
    if top_match.get("body"):
        lines.extend(["", "**Observed Evidence**", str(top_match["body"])])

    linked = matches[0].get("linked_failure_mode_id")
    if linked:
        lines.extend(["", "**Knowledge Graph Context**", f"- Linked failure mode: `{linked}`"])

    others = matches[1:5]
    if others:
        lines.extend(["", "**Other Relevant Patterns**"])
        for match in others:
            top = match.get("top_match_log") or {}
            count = int(match.get("occurrence_count") or 0)
            lines.append(
                f"- `{_fmt(match.get('event_signature_id'))}` - {count} occurrence"
                f"{'s' if count != 1 else ''} - {_date(top.get('occurred_at'))} - {_fmt(top.get('title'))}"
            )
    return "\n".join(lines)


def _analytics_template(query: str, summary: dict[str, Any]) -> str:
    rows = int(summary.get("row_count") or 0)
    top_components = summary.get("top_components") or []
    top_signatures = summary.get("top_event_signatures") or []
    severity = summary.get("severity_distribution") or {}
    open_events = int(summary.get("open_events") or 0)

    lines = [
        "**Snapshot**",
        f"- Total log rows: **{rows}**",
        f"- Open or monitoring events: **{open_events}**",
    ]
    if top_components:
        first = top_components[0]
        lines.append(
            f"- Most affected component: `{_fmt(first.get('component_id'))}` "
            f"({int(first.get('count') or 0)} events)"
        )

    if top_components:
        lines.extend(["", "**Top Components**"])
        for item in top_components[:5]:
            lines.append(f"- `{_fmt(item.get('component_id'))}` - {int(item.get('count') or 0)} events")

    if top_signatures:
        lines.extend(["", "**Top Recurring Patterns**"])
        for item in top_signatures[:5]:
            lines.append(
                f"- `{_fmt(item.get('event_signature_id'))}` - "
                f"{int(item.get('occurrence_count') or 0)} occurrences"
            )

    if severity:
        lines.extend(["", "**Severity Mix**"])
        for key, value in sorted(severity.items()):
            lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _extract_work_order_id(query: str) -> str | None:
    match = _WORK_ORDER_RE.search(query or "")
    return match.group(0).upper() if match else None


def _work_order_rows(instance_id: str, work_order_id: str) -> list[dict[str, Any]]:
    store = load_log_store(instance_id)
    if store is None or store.is_empty:
        return []
    needle = work_order_id.upper()
    rows = [
        row for row in store.rows
        if str(row.get("work_order_id") or row.get("source_record_id") or "").upper() == needle
    ]
    rows.sort(key=lambda row: row.get("occurred_at") or "", reverse=True)
    return rows


def _matches_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for row in rows:
        matches.append({
            "event_signature_id": row.get("event_signature_id") or "_unsignatured",
            "score": 1.0,
            "occurrence_count": 1,
            "first_seen_at": row.get("occurred_at"),
            "last_seen_at": row.get("occurred_at"),
            "linked_failure_mode_id": row.get("linked_failure_mode_id") or "",
            "linked_symptom_id": row.get("linked_symptom_id") or "",
            "top_match_log": row,
            "most_recent_log": row,
            "all_log_ids": [row.get("log_id")] if row.get("log_id") else [],
            "rerank_rationale": "Exact work order id match",
        })
    return matches


def _work_order_template(rows: list[dict[str, Any]]) -> str:
    first = rows[0]
    lines = [
        "**Work Order**",
        f"- ID: `{_fmt(first.get('work_order_id') or first.get('source_record_id'))}`",
        f"- Date: {_date(first.get('occurred_at'))}",
        f"- Title: {_fmt(first.get('title'))}",
        "",
        "**Execution Summary**",
        f"- Status: {_fmt(first.get('status'))}",
        f"- Outcome: {_fmt(first.get('outcome'))}",
        f"- Planned duration: {_format_duration(first.get('planned_duration_min'))}",
        f"- Actual duration: {_format_duration(first.get('actual_duration_min'))}",
        f"- Downtime: {_format_duration(first.get('downtime_min'))}",
        f"- Component: {_fmt(first.get('component_name_raw') or first.get('component_id'))}",
    ]
    if first.get("action_taken"):
        lines.extend(["", "**Action taken**", str(first["action_taken"])])
    if first.get("body"):
        lines.extend(["", "**Details**", str(first["body"])])

    if len(rows) > 1:
        lines.extend(["", "**Other entries for this work order**"])
        for row in rows[1:]:
            lines.append(f"- {_date(row.get('occurred_at'))} - {_fmt(row.get('title'))}")
    return "\n".join(lines)


def _slim_match_for_llm(m: dict[str, Any]) -> dict[str, Any]:
    """Project a match into the minimum fields the compose LLM needs.

    The full match dict includes 30+ fields per occurrence; passing all of them
    into the prompt for every match balloons the token count and slows the
    response substantially without improving reply quality.
    """
    top = m.get("top_match_log") or {}
    return {
        "signature": m.get("event_signature_id"),
        "count": m.get("occurrence_count"),
        "first_seen_at": m.get("first_seen_at"),
        "last_seen_at": m.get("last_seen_at"),
        "linked_failure_mode_id": m.get("linked_failure_mode_id"),
        "top": {
            "date": (top.get("occurred_at") or "")[:10],
            "severity": top.get("severity_text"),
            "status": top.get("status"),
            "wo": top.get("work_order_id"),
            "title": top.get("title"),
            "body": top.get("body"),
            "action_taken": top.get("action_taken"),
            "outcome": top.get("outcome"),
            "actual_duration_min": top.get("actual_duration_min"),
            "planned_duration_min": top.get("planned_duration_min"),
            "downtime_min": top.get("downtime_min"),
            "signal_name": top.get("signal_name"),
            "observed_value": top.get("observed_value"),
            "observed_unit": top.get("observed_unit"),
            "threshold_value": top.get("threshold_value"),
        },
    }


def _evidence_items(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact projection of search matches for the ChatResponse payload."""
    out: list[dict[str, Any]] = []
    for m in matches:
        top = m.get("top_match_log") or {}
        recent = m.get("most_recent_log") or {}
        out.append({
            "event_signature_id": m.get("event_signature_id"),
            "score": m.get("score"),
            "matched_occurrence_count": m.get("matched_occurrence_count"),
            "occurrence_count": m.get("occurrence_count"),
            "linked_failure_mode_id": m.get("linked_failure_mode_id"),
            "linked_symptom_id": m.get("linked_symptom_id"),
            "first_seen_at": m.get("first_seen_at"),
            "last_seen_at": m.get("last_seen_at"),
            "top_match": {
                "log_id": top.get("log_id"),
                "occurred_at": top.get("occurred_at"),
                "severity_text": top.get("severity_text"),
                "status": top.get("status"),
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
                "status": recent.get("status"),
                "title": recent.get("title"),
            },
            "matched_log_ids": m.get("matched_log_ids", []),
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
) -> tuple[str, list[dict[str, Any]], dict[str, float]]:
    timer = _HandlerTimer()
    # Skip the dedicated LLM rerank: the compose call below already selects
    # and orders the most relevant candidates while writing the reply, so the
    # rerank step would just be redundant latency. RRF alone hits the right
    # signature at top-1 in ~8/9 canonical queries on the IRC5 seed.
    result = search_logs(query, instance_id, filters=filters, limit=5, use_llm_rerank=False)
    timer.add_nested("search", result.get("diagnostics", {}).get("timings", {}))
    timer.mark("search")
    if result["match_count"] == 0:
        return (
            "I couldn't find any past events on this machine that match that description.",
            [],
            timer.snapshot(),
        )

    if _use_fast_template(chat_model):
        reply = _history_template(query, result["matches"])
        timer.mark("compose")
        return reply, _evidence_items(result["matches"]), timer.snapshot()

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
        "matches": [_slim_match_for_llm(m) for m in result["matches"]],
    }
    reply = _llm_compose(system, user_payload, chat_model)
    timer.mark("compose")
    return reply, _evidence_items(result["matches"]), timer.snapshot()


def handle_log_analytics(
    query: str,
    instance_id: str,
    chat_model: str = OPENAI_CHAT_MODEL,
) -> tuple[str, list[dict[str, Any]], dict[str, float]]:
    timer = _HandlerTimer()
    summary = summarize_logs(instance_id)
    timer.mark("summary")
    if not summary or summary.get("row_count", 0) == 0:
        return ("There are no logs available for this machine yet.", [], timer.snapshot())

    if _use_fast_template(chat_model):
        reply = _analytics_template(query, summary)
        timer.mark("compose")
        return reply, [], timer.snapshot()

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
    timer.mark("compose")
    return reply, [], timer.snapshot()


def handle_work_order_lookup(
    query: str,
    instance_id: str,
    filters: dict[str, Any],
    chat_model: str = OPENAI_CHAT_MODEL,
) -> tuple[str, list[dict[str, Any]], dict[str, float]]:
    timer = _HandlerTimer()
    result: dict[str, Any] | None = None
    work_order_id = _extract_work_order_id(query)
    if work_order_id:
        rows = _work_order_rows(instance_id, work_order_id)
        timer.mark("exact_lookup")
        if rows:
            matches = _matches_from_rows(rows)
            if _use_fast_template(chat_model):
                reply = _work_order_template(rows)
                timer.mark("compose")
                return reply, _evidence_items(matches), timer.snapshot()
            result = {"match_count": len(matches), "matches": matches}
        else:
            return (
                f"I couldn't locate work order `{work_order_id}` on this machine.",
                [],
                timer.snapshot(),
            )

    # Work-order queries hinge on an exact identifier match. Pure RRF can let
    # a frequently-occurring signature outrank the row that literally carries
    # the WO id (max-per-signature can lose to a signature with many
    # mid-ranked rows). The LLM rerank reliably elevates the exact match,
    # which matters more here than the small extra latency.
    if result is None:
        result = search_logs(query, instance_id, filters=filters, limit=3, use_llm_rerank=True)
        timer.add_nested("search", result.get("diagnostics", {}).get("timings", {}))
        timer.mark("search_with_rerank")
    if result["match_count"] == 0:
        return (
            "I couldn't locate a work order matching that on this machine.",
            [],
            timer.snapshot(),
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
        "matches": [_slim_match_for_llm(m) for m in result["matches"]],
    }
    reply = _llm_compose(system, user_payload, chat_model)
    timer.mark("compose")
    return reply, _evidence_items(result["matches"]), timer.snapshot()


def fetch_hybrid_history_evidence(
    query: str,
    instance_id: str,
    filters: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, float]]:
    """For hybrid intent: run log search, return a markdown appendix string and
    the evidence list. The KG flow handles the diagnostic part of the reply
    and this is appended after."""
    timer = _HandlerTimer()
    result = search_logs(query, instance_id, filters=filters, limit=3, use_llm_rerank=False)
    timer.add_nested("search", result.get("diagnostics", {}).get("timings", {}))
    timer.mark("search")
    if result["match_count"] == 0:
        return ("", [], timer.snapshot())

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

    return ("\n".join(lines), _evidence_items(result["matches"]), timer.snapshot())
