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
from .log_search import is_presentable_signature_id, search_logs, summarize_logs

_client: OpenAI | None = None
_WORK_ORDER_RE = re.compile(r"\bWO-[A-Za-z0-9][A-Za-z0-9-]*\b", re.IGNORECASE)
_SOLUTION_QUESTION_RE = re.compile(
    r"\b(solution|fix|fixed|resolve|resolved|repair|repaired|"
    r"what\s+did\s+we\s+do|what\s+was\s+done|last\s+time|previous\s+fix)\b",
    re.IGNORECASE,
)
_COUNT_QUESTION_RE = re.compile(
    r"\b(how\s+many|how\s+often|how\s+frequent|frequency|count|times|"
    r"quante|quanto\s+spesso)\b",
    re.IGNORECASE,
)
_GLOBAL_ANALYTICS_RE = re.compile(
    r"\b(most\s+recurring|most\s+repeated|top\s+recurring|top\s+component|"
    r"most\s+affected|which\s+component|severity\s+mix|trend|trends|"
    r"summary|total\s+logs?|total\s+events?)\b",
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


def _filter_scope(filters: dict[str, Any]) -> str:
    if not filters:
        return "all available logs"
    bits: list[str] = []
    if filters.get("date_from") or filters.get("date_to"):
        bits.append(f"{_fmt(filters.get('date_from'), 'start')} to {_fmt(filters.get('date_to'), 'now')}")
    for key, label in (
        ("status", "status"),
        ("maintenance_type", "maintenance"),
        ("event_category", "category"),
        ("severity_min", "severity >= "),
    ):
        if filters.get(key) is not None:
            bits.append(f"{label}: {filters[key]}")
    return "; ".join(bits) if bits else "filtered logs"


def _is_specific_count_question(query: str) -> bool:
    text = " ".join((query or "").lower().split())
    if not _COUNT_QUESTION_RE.search(text) or _GLOBAL_ANALYTICS_RE.search(text):
        return False
    if any(marker in text for marker in ("component:", "failure mode:", "previous symptom:")):
        return True
    stripped = re.sub(
        r"\b(how|many|often|frequent|frequency|count|times|did|has|have|"
        r"this|that|problem|issue|event|events|case|cases|occurred|happened|"
        r"last|past|month|week|days|in|the|a|an|on|for|of|there|been)\b",
        " ",
        text,
    )
    terms = [term for term in re.findall(r"[a-z0-9_/-]{3,}", stripped)]
    return len(terms) >= 2


def _count_query_terms(query: str) -> list[str]:
    text = " ".join((query or "").lower().split())
    text = re.sub(r"\b(last|past)\s+\d+\s+days?\b", " ", text)
    text = re.sub(r"\b(last|this|current|previous)\s+(month|week)\b", " ", text)
    text = re.sub(
        r"\b(how|many|often|frequent|frequency|count|times|did|has|have|"
        r"this|that|problem|issue|event|events|case|cases|occurred|happened|"
        r"in|the|a|an|on|for|of|there|been|was|were|is|are)\b",
        " ",
        text,
    )
    out: list[str] = []
    for token in re.findall(r"[a-z][a-z0-9_/-]{2,}", text):
        token = token.strip("_-/")
        if token and token not in out:
            out.append(token)
    return out


def _log_row_passes_filters(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    if not filters:
        return True
    df = filters.get("date_from")
    if df and (row.get("occurred_at") or "") < df:
        return False
    dt = filters.get("date_to")
    if dt and (row.get("occurred_at") or "") >= dt:
        return False
    for key in ("component_id", "linked_failure_mode_id", "maintenance_type", "event_category", "status", "event_signature_id"):
        if filters.get(key) is not None and row.get(key) != filters[key]:
            return False
    if filters.get("severity_min") is not None and (row.get("severity_number") or 0) < int(filters["severity_min"]):
        return False
    return True


def _row_count_haystack(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "") for key in (
            "event_signature_id", "title", "body", "action_taken",
            "event_name", "component_name_raw", "semantic_text",
            "linked_failure_mode_id", "work_order_id", "error_code", "alarm_code",
        )
    ).lower().replace("_", " ")


def _unmatched_code_like_tokens(query: str, instance_id: str) -> list[str]:
    tokens = [
        token.lower()
        for token in re.findall(r"\b(?=[a-z0-9-]*[a-z])(?=[a-z0-9-]*\d)[a-z0-9-]{6,}\b", query or "", re.I)
        if not token.upper().startswith("WO-")
    ]
    if not tokens:
        return []
    store = load_log_store(instance_id)
    if store is None or store.is_empty:
        return tokens
    corpus = "\n".join(_row_count_haystack(row) for row in store.rows)
    return [token for token in tokens if token not in corpus]


def _literal_count_matches(
    query: str,
    instance_id: str,
    filters: dict[str, Any],
) -> list[dict[str, Any]] | None:
    terms = _count_query_terms(query)
    if len(terms) < 2:
        return None
    store = load_log_store(instance_id)
    if store is None or store.is_empty:
        return []

    all_literal = [
        row for row in store.rows
        if all(term in _row_count_haystack(row) for term in terms)
    ]
    if not all_literal:
        return None
    filtered = [row for row in all_literal if _log_row_passes_filters(row, filters)]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in filtered:
        sig = row.get("event_signature_id") or "_unsignatured"
        if not is_presentable_signature_id(sig):
            continue
        grouped.setdefault(sig, []).append(row)

    matches: list[dict[str, Any]] = []
    for sig, rows in grouped.items():
        rows.sort(key=lambda row: row.get("occurred_at") or "", reverse=True)
        top = rows[0]
        matches.append({
            "event_signature_id": sig,
            "score": 1.0,
            "matched_occurrence_count": len(rows),
            "occurrence_count": len(rows),
            "first_seen_at": rows[-1].get("occurred_at"),
            "last_seen_at": rows[0].get("occurred_at"),
            "linked_failure_mode_id": top.get("linked_failure_mode_id") or "",
            "linked_symptom_id": top.get("linked_symptom_id") or "",
            "top_match_log": top,
            "most_recent_log": top,
            "matched_log_ids": [row.get("log_id") for row in rows if row.get("log_id")],
            "all_log_ids": [row.get("log_id") for row in rows if row.get("log_id")],
            "rerank_rationale": "Literal count match",
        })
    matches.sort(key=lambda m: (m.get("occurrence_count") or 0, m.get("last_seen_at") or ""), reverse=True)
    return matches


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


def _analytics_template(query: str, summary: dict[str, Any], filters: dict[str, Any] | None = None) -> str:
    rows = int(summary.get("row_count") or 0)
    top_components = summary.get("top_components") or []
    top_signatures = summary.get("top_event_signatures") or []
    severity = summary.get("severity_distribution") or {}
    open_events = int(summary.get("open_events") or 0)

    lines = [
        "**Snapshot**",
        f"- Scope: {_filter_scope(filters or {})}",
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


def _count_template(query: str, matches: list[dict[str, Any]], filters: dict[str, Any]) -> str:
    total = sum(int(m.get("occurrence_count") or 0) for m in matches)
    lines = [
        "**Snapshot**",
        f"- Scope: {_filter_scope(filters)}",
        f"- Matching occurrences: **{total}**",
        f"- Matching patterns: **{len(matches)}**",
    ]
    if matches:
        top = matches[0].get("top_match_log") or {}
        lines.extend([
            f"- Best matching pattern: `{_fmt(matches[0].get('event_signature_id'))}`",
            f"- Most recent relevant event: {_date((matches[0].get('most_recent_log') or {}).get('occurred_at'))}",
            "",
            "**Matched Patterns**",
        ])
        for match in matches[:5]:
            recent = match.get("most_recent_log") or {}
            lines.append(
                f"- `{_fmt(match.get('event_signature_id'))}` - "
                f"{int(match.get('occurrence_count') or 0)} occurrence"
                f"{'s' if int(match.get('occurrence_count') or 0) != 1 else ''} - "
                f"last seen {_date(recent.get('occurred_at'))}"
            )
        if top.get("action_taken"):
            lines.extend(["", "**Most Relevant Fix**", str(top["action_taken"])])
    return "\n".join(lines)


def _summary_evidence(summary: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for item in summary.get("top_event_signatures") or []:
        evidence.append({
            "event_signature_id": item.get("event_signature_id"),
            "score": 1.0,
            "matched_occurrence_count": item.get("occurrence_count"),
            "occurrence_count": item.get("occurrence_count"),
            "linked_failure_mode_id": item.get("linked_failure_mode_id") or "",
            "linked_symptom_id": "",
            "first_seen_at": None,
            "last_seen_at": item.get("last_seen_at"),
            "top_match": {},
            "most_recent": {"occurred_at": item.get("last_seen_at")},
            "matched_log_ids": [],
            "all_log_ids": [],
            "rerank_rationale": "Aggregate summary result",
        })
    return evidence


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
    unmatched_tokens = _unmatched_code_like_tokens(query, instance_id)
    if unmatched_tokens:
        timer.mark("unknown_identifier_guard")
        return (
            "I couldn't find any past events on this machine that match that identifier.",
            [],
            timer.snapshot(),
        )
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
    filters: dict[str, Any] | None = None,
    chat_model: str = OPENAI_CHAT_MODEL,
) -> tuple[str, list[dict[str, Any]], dict[str, float]]:
    timer = _HandlerTimer()
    filters = filters or {}
    if _is_specific_count_question(query):
        literal_matches = _literal_count_matches(query, instance_id, filters)
        if literal_matches is not None:
            timer.mark("literal_count")
            reply = _count_template(query, literal_matches, filters)
            timer.mark("compose")
            return reply, _evidence_items(literal_matches), timer.snapshot(), {}

        result = search_logs(query, instance_id, filters=filters, limit=5, use_llm_rerank=False)
        timer.add_nested("search", result.get("diagnostics", {}).get("timings", {}))
        timer.mark("specific_count_search")
        if result["match_count"] > 0:
            reply = _count_template(query, result["matches"], filters)
            timer.mark("compose")
            return reply, _evidence_items(result["matches"]), timer.snapshot(), {}

    summary = summarize_logs(instance_id, filters=filters)
    timer.mark("summary")
    if not summary or summary.get("row_count", 0) == 0:
        return ("There are no logs available for this machine yet.", [], timer.snapshot(), summary)

    if _use_fast_template(chat_model):
        reply = _analytics_template(query, summary, filters)
        timer.mark("compose")
        return reply, _summary_evidence(summary), timer.snapshot(), summary

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
        "filters": filters,
    }
    reply = _llm_compose(system, user_payload, chat_model)
    timer.mark("compose")
    return reply, _summary_evidence(summary), timer.snapshot(), summary


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
