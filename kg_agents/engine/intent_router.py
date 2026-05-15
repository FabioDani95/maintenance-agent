"""Intent routing for the chat workflow.

Decides whether a user message is a current-troubleshooting request, a log
history question, a log analytics request, a hybrid diagnosis-plus-history
question, or a work-order lookup. Also extracts filters (component, date
range, severity, etc.) and a cleaned search query.

Common cases use a deterministic fast path to avoid per-turn LLM latency.
Ambiguous routing can still fall back to the LLM classifier.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from openai import OpenAI

from kg_agents.config import OPENAI_API_KEY, OPENAI_ROUTER_MODEL

from .ontology_loader import OntologyIndex

VALID_INTENTS = {
    "troubleshooting_current",
    "log_history_search",
    "log_analytics",
    "hybrid_diagnosis_with_history",
    "work_order_lookup",
}

# Filters returned to the chat layer. Component / failure-mode / signature
# filters are deliberately excluded: the dense + sparse retrieval already
# matches them semantically, and a hard pre-filter on those tends to exclude
# valid neighbours (e.g. drive overtemperature rows that were re-linked to a
# different ontology FM by the embedding verification step).
_FILTER_KEYS = (
    "maintenance_type",
    "event_category",
    "status",
    "severity_min",
    "date_from",
    "date_to",
)

_client: OpenAI | None = None

_WORK_ORDER_RE = re.compile(r"\bWO-[A-Za-z0-9][A-Za-z0-9-]*\b", re.IGNORECASE)
_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_UNSUPPORTED_DATE_WORDS = (
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
    "mese", "anno", "giorni", "dal", "al",
)

_HISTORY_PATTERNS = (
    "happened before",
    "seen before",
    "have we seen",
    "we have seen",
    "we've seen",
    "have seen",
    "ever seen",
    "seen this before",
    "has this happened",
    "past",
    "history",
    "historical",
    "previous",
    "previously",
    "last time",
    "ever happened",
    "ever report",
    "ever reported",
    "operators ever",
    "we've had",
    "we have had",
    "we had",
    "log",
    "logs",
    "events",
    "in passato",
    "storico",
    "precedent",
)
_ANALYTICS_PATTERNS = (
    "how often",
    "how many",
    "frequency",
    "frequent",
    "most repeated",
    "most affected",
    "recurring",
    "recurrence",
    "trend",
    "trends",
    "count",
    "counts",
    "total",
    "totals",
    "which component",
    "top component",
    "per month",
    "monthly",
    "quante",
    "quanto spesso",
    "piu frequ",
)
_CURRENT_DIAGNOSIS_PATTERNS = (
    "what should i check",
    "what do i check",
    "what's wrong",
    "what is wrong",
    "how do i fix",
    "how to fix",
    "how was it fixed",
    "how was this fixed",
    "how did we fix",
    "fix it",
    "diagnose",
    "troubleshoot",
    "should i check",
    "cosa controllo",
    "come risolvo",
)
_AMBIGUOUS_HISTORY_SOLUTION_PATTERNS = (
    "recover the solution",
    "retrieve the solution",
    "recover solution",
    "retrieve solution",
    "find the solution",
    "find previous fix",
    "previous fix",
    "previous solution",
    "solution used",
    "fix used",
    "what was done",
    "what did we do",
    "how did we solve",
    "how was it solved",
    "how was this solved",
    "how did we resolve",
    "how was it resolved",
    "recupera soluzione",
    "recuperare soluzione",
    "soluzione usata",
    "cosa abbiamo fatto",
)
_ANCHOR_TERMS = {
    "irc5", "robot", "controller", "drive", "motor", "motors", "brake",
    "voltage", "power", "flexpendant", "pendant", "ethernet", "network",
    "axis", "joystick", "dsqc", "module", "fan", "thermal", "temperature",
    "encoder", "resolver", "gearbox", "fieldbus", "io", "i/o",
}
_ANCHOR_STOPWORDS = {
    "happened", "before", "history", "historical", "previous", "previously",
    "often", "many", "frequency", "frequent", "recurring", "trend", "trends",
    "count", "counts", "total", "totals", "which", "component", "show",
    "past", "events", "logs", "have", "seen", "this", "that", "quello",
    "questa", "questo", "storico", "precedente", "quante", "quanto",
    "spesso", "frequente", "ricorrente",
}


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _shift_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    month_lengths = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(d.day, month_lengths[month - 1]))


def _parse_month_day(month_text: str, day_text: str, today: date) -> date | None:
    month = _MONTHS.get(month_text.lower().rstrip("."))
    if not month:
        return None
    day = int(day_text)
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    return candidate


def _explicit_date_range(text: str, today: date) -> dict[str, str]:
    month_names = "|".join(sorted(_MONTHS, key=len, reverse=True))
    patterns = (
        # from January 10 to March 4
        rf"\b(?:(?:from)\s+)?({month_names})\s+(\d{{1,2}})\s+(?:to|until|through)\s+({month_names})\s+(\d{{1,2}})\b",
        # from 10 January to 4 March
        rf"\b(?:(?:from)\s+)?(\d{{1,2}})\s+({month_names})\s+(?:to|until|through)\s+(\d{{1,2}})\s+({month_names})\b",
    )
    for i, pattern in enumerate(patterns):
        match = re.search(pattern, text)
        if not match:
            continue
        if i == 0:
            start = _parse_month_day(match.group(1), match.group(2), today)
            end = _parse_month_day(match.group(3), match.group(4), today)
        else:
            start = _parse_month_day(match.group(2), match.group(1), today)
            end = _parse_month_day(match.group(4), match.group(3), today)
        if not start or not end:
            continue
        if end < start:
            end = date(today.year + 1, end.month, end.day)
        return {
            "date_from": start.isoformat(),
            # date_to is exclusive in log filters, so include the named end day.
            "date_to": (end + timedelta(days=1)).isoformat(),
        }
    return {}


def _clean_temporal_phrases(query: str, filters: dict[str, Any]) -> str:
    if not filters.get("date_from") and not filters.get("date_to"):
        return query.strip()

    month_names = "|".join(sorted(_MONTHS, key=len, reverse=True))
    cleaned = query
    explicit_patterns = (
        rf"\b(?:(?:from)\s+)?(?:{month_names})\s+\d{{1,2}}\s+(?:to|until|through)\s+(?:{month_names})\s+\d{{1,2}}\b",
        rf"\b(?:(?:from)\s+)?\d{{1,2}}\s+(?:{month_names})\s+(?:to|until|through)\s+\d{{1,2}}\s+(?:{month_names})\b",
    )
    for pattern in explicit_patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    relative_patterns = (
        r"\b(?:in|during|over|for)\s+(?:the\s+)?(?:past|last)\s+\d+\s+(?:days?|weeks?|months?|years?)\b",
        r"\b(?:past|last)\s+\d+\s+(?:days?|weeks?|months?|years?)\b",
        r"\b(?:past|last)\s+year\b",
        r"\b(?:last|previous|this|current)\s+(?:week|month|year)\b",
    )
    for pattern in relative_patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+([?.!,;:])", r"\1", cleaned)
    return " ".join(cleaned.split()).strip()


def _has_temporal_language(text: str) -> bool:
    month_names = "|".join(sorted(_MONTHS, key=len, reverse=True))
    unsupported_date_words = "|".join(sorted(_UNSUPPORTED_DATE_WORDS, key=len, reverse=True))
    patterns = (
        rf"\b(?:{month_names})\b",
        rf"\b(?:{unsupported_date_words})\b",
        r"\b(?:last|past|previous|this|current)\s+(?:\w+\s+)?(?:days?|weeks?|months?|quarters?|years?)\b",
        r"\b(?:since|before|after|between|from|until|through)\b",
        r"\bq[1-4]\b|\bquarter\b",
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _has_date_filter(filters: dict[str, Any]) -> bool:
    return bool(filters.get("date_from") or filters.get("date_to"))


def _fast_filters(text: str) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    today = datetime.now(timezone.utc).date()
    first_this_month = today.replace(day=1)
    first_last_month = (first_this_month - timedelta(days=1)).replace(day=1)
    explicit_range = _explicit_date_range(text, today)

    if explicit_range:
        filters.update(explicit_range)
    elif re.search(r"\b(last month|previous month)\b", text):
        filters["date_from"] = first_last_month.isoformat()
        filters["date_to"] = first_this_month.isoformat()
    elif re.search(r"\b(this month|current month)\b", text):
        filters["date_from"] = first_this_month.isoformat()
    elif re.search(r"\b(last year|previous year)\b", text):
        filters["date_from"] = date(today.year - 1, 1, 1).isoformat()
        filters["date_to"] = date(today.year, 1, 1).isoformat()
    elif re.search(r"\b(past year|past 1 year|last 1 year)\b", text):
        filters["date_from"] = date(today.year - 1, today.month, today.day).isoformat()
    elif m := re.search(r"\b(?:last|past)\s+(\d+)\s+months?\b", text):
        filters["date_from"] = _shift_months(today, -int(m.group(1))).isoformat()
    elif m := re.search(r"\b(?:last|past)\s+(\d+)\s+years?\b", text):
        filters["date_from"] = date(today.year - int(m.group(1)), today.month, today.day).isoformat()
    elif re.search(r"\b(last|past)\s+30\s+days?\b", text):
        filters["date_from"] = (today - timedelta(days=30)).isoformat()
    elif re.search(r"\b(last|past)\s+7\s+days?\b", text) or re.search(r"\blast week\b", text):
        filters["date_from"] = (today - timedelta(days=7)).isoformat()

    if "fatal" in text:
        filters["severity_min"] = 22
    elif "critical" in text or re.search(r"\bonly\s+errors?\b|\berror\s+severity\b|\bseverity\s+error\b", text):
        filters["severity_min"] = 18
    elif "warn" in text or "warning" in text:
        filters["severity_min"] = 14

    if "operator" in text and ("report" in text or "note" in text):
        filters["event_category"] = "operator_note"
    elif re.search(r"\bfaults?\b", text):
        filters["event_category"] = "fault"

    for status in ("open", "closed", "completed"):
        if status in text:
            filters["status"] = status
            break
    return filters


def _base_result(intent: str, message: str, rationale: str) -> dict[str, Any]:
    text = " ".join((message or "").lower().split())
    filters = _fast_filters(text)
    return {
        "intent": intent,
        "search_query": _clean_temporal_phrases(message.strip(), filters),
        "filters": filters,
        "rationale": rationale,
        "source": "deterministic_fast_path",
    }


def _has_solution_history_signal(text: str) -> bool:
    return any(pattern in text for pattern in _AMBIGUOUS_HISTORY_SOLUTION_PATTERNS)


def _has_explicit_current_repair_signal(text: str) -> bool:
    return any(
        pattern in text
        for pattern in (
            "how do i fix",
            "how to fix",
            "what should i check",
            "what do i check",
            "diagnose",
            "troubleshoot",
            "fix it now",
            "solve this now",
            "current issue",
            "right now",
        )
    )


def _has_clear_anchor(text: str, message: str) -> bool:
    if _WORK_ORDER_RE.search(message):
        return True
    if any(term in text for term in _ANCHOR_TERMS):
        return True
    words = [
        word for word in re.findall(r"[a-z0-9_/-]{4,}", text)
        if word not in _ANCHOR_STOPWORDS
    ]
    return bool(words)


def _contextual_search_query(message: str, memory_context: dict[str, Any] | None) -> str:
    if not memory_context or not memory_context.get("has_context_reference"):
        return message
    bits = [message.strip()]
    component = str(memory_context.get("component") or "").strip()
    symptom = str(memory_context.get("symptom") or "").strip()
    failure_mode = str(memory_context.get("failure_mode") or "").strip()
    if component:
        bits.append(f"component: {component}")
    if failure_mode:
        bits.append(f"failure mode: {failure_mode}")
    elif symptom:
        bits.append(f"previous symptom: {symptom}")
    return " | ".join(bit for bit in bits if bit)


def classify_intent_fast(
    message: str,
    memory_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Fast deterministic intent routing for common cases."""
    text = " ".join((message or "").lower().split())
    if not text:
        return None

    if _WORK_ORDER_RE.search(message):
        return _base_result("work_order_lookup", message, "work order id detected")

    filters = _fast_filters(text)
    if _has_temporal_language(text) and not _has_date_filter(filters):
        return None

    has_history = any(pattern in text for pattern in _HISTORY_PATTERNS)
    has_analytics = any(pattern in text for pattern in _ANALYTICS_PATTERNS)
    has_current_diagnosis = any(pattern in text for pattern in _CURRENT_DIAGNOSIS_PATTERNS)
    has_solution_history = _has_solution_history_signal(text)
    has_anchor = _has_clear_anchor(text, message)

    # Mixed history + analytics wording with no concrete machine/component
    # anchor is exactly where the string router is most likely to guess wrong.
    # In fast mode this is the narrow path that can fall through to the LLM
    # classifier without putting every turn on the slower path.
    if has_history and has_analytics and not has_anchor:
        return None

    if memory_context and memory_context.get("has_context_reference") and not has_anchor:
        contextual_intent = ""
        if has_analytics:
            contextual_intent = "log_analytics"
        elif has_history and has_current_diagnosis:
            contextual_intent = "hybrid_diagnosis_with_history"
        elif has_history:
            contextual_intent = "log_history_search"
        elif memory_context.get("last_intent") in VALID_INTENTS:
            contextual_intent = str(memory_context["last_intent"])
        if contextual_intent:
            result = _base_result(
                contextual_intent,
                _contextual_search_query(message, memory_context),
                "context reference resolved from session memory",
            )
            result["source"] = "deterministic_fast_path_with_memory"
            return result

    if has_analytics:
        return _base_result("log_analytics", message, "analytics wording detected")
    if re.search(r"\b(show|list|find)\b", text) and re.search(r"\bfaults?\b", text):
        return _base_result("log_history_search", message, "fault listing wording detected")
    if has_history and has_current_diagnosis:
        return _base_result(
            "hybrid_diagnosis_with_history",
            message,
            "current diagnosis plus history wording detected",
        )
    if has_history:
        return _base_result("log_history_search", message, "history wording detected")

    # Phrases like "recover the solution" are intentionally routed through the
    # LLM classifier. They can mean "find how it was fixed before" or "solve my
    # current issue"; the string router does not have enough context to decide.
    if has_solution_history:
        return None

    # Ambiguous anchored maintenance questions are cheap enough to classify
    # with the router LLM in fast mode. Keep deterministic defaulting only for
    # messages without a clear technical anchor.
    if has_anchor:
        return None

    return _base_result("troubleshooting_current", message, "default troubleshooting route")


def classify_intent(
    message: str,
    instance_id: str,
    ontology_index: OntologyIndex,
    memory_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the routed intent + extracted filters + cleaned search query.

    Shape:
        {
          "intent": str,
          "search_query": str,
          "filters": dict,
          "rationale": str,
        }

    The prompt is kept small on purpose: filters are limited to
    date/severity/status/maintenance_type/event_category (everything else is
    handled by the semantic retrieval downstream), so neither the ontology
    vocabulary nor the log signature list is needed as context here. Smaller
    prompt = much faster routing on `gpt-5-nano`.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    system = (
        "You are an intent classifier and entity extractor for an industrial "
        "maintenance assistant. The assistant answers questions about an ABB "
        "IRC5 robot controller using both a knowledge graph (for diagnostic "
        "guidance) and a per-machine log database (for history, work orders, "
        "and analytics).\n\n"
        "Classify the user message into exactly one of these intents:\n"
        "- troubleshooting_current: user describes a symptom they are facing "
        "now and wants diagnostic or corrective guidance.\n"
        "- log_history_search: user asks whether/when something happened, what "
        "was done before, or for past similar cases.\n"
        "- log_analytics: user asks about frequency, totals, recurrence, "
        "trends, or which components are most affected.\n"
        "- hybrid_diagnosis_with_history: user describes a current symptom AND "
        "explicitly asks if it has been seen before or how it was resolved "
        "previously.\n"
        "- work_order_lookup: user references a specific work order id or asks "
        "for details about a particular past intervention.\n\n"
        "Also extract a cleaned search_query suitable for log retrieval and a "
        "small set of structural filters (date range, severity floor, status, "
        "maintenance type, event category) ONLY when the user message names "
        "them explicitly. Do NOT pre-filter on components, failure modes, or "
        "event signatures — the retrieval layer matches those semantically; a "
        "hard filter here would exclude valid neighbouring events. Resolve "
        "relative dates against TODAY into ISO 8601 (YYYY-MM-DD). severity_min "
        "uses the OpenTelemetry-style scale (INFO=10, WARN=14, ERROR=18, "
        "FATAL=22). date_to is an exclusive upper bound. When the user names "
        "an inclusive natural-language end date such as 'to March 4', "
        "'through March 4', or a complete calendar period, return the following "
        "day as date_to.\n\n"
        "Examples:\n"
        "- 'Recover the solution used for Ethernet packet loss' => "
        "log_history_search.\n"
        "- 'What did we do last time the drive overheated?' => "
        "log_history_search.\n"
        "- 'Has this FlexPendant disconnect happened before?' => "
        "log_history_search.\n"
        "- 'The drive is overheating now; also check if it happened before' => "
        "hybrid_diagnosis_with_history.\n"
        "- 'How do I fix drive overheating?' => troubleshooting_current.\n"
        "Return only valid JSON."
    )

    user_payload = {
        "today": today,
        "message": message,
        "session_context": memory_context or {},
        "schema": {
            "intent": "one of the 5 intent strings above",
            "search_query": "string optimized for log retrieval; use session_context only to resolve pronouns, never to change intent labels or invent filters",
            "filters": {
                "maintenance_type": "CM | PM | PDM | null",
                "event_category": "alarm | fault | maintenance | operator_note | condition_monitoring | null",
                "status": "open | closed | completed | null",
                "severity_min": "integer or null",
                "date_from": "YYYY-MM-DD or null",
                "date_to": "YYYY-MM-DD or null",
            },
            "rationale": "one short sentence",
        },
    }

    resp = _get_client().chat.completions.create(
        model=OPENAI_ROUTER_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    parsed = json.loads(content)

    intent = parsed.get("intent", "")
    if intent not in VALID_INTENTS:
        intent = "troubleshooting_current"

    text = " ".join((message or "").lower().split())
    if (
        _has_solution_history_signal(text)
        and not _has_explicit_current_repair_signal(text)
        and intent in {"troubleshooting_current", "hybrid_diagnosis_with_history"}
    ):
        intent = "log_history_search"
        parsed["rationale"] = (
            "solution-history wording detected; prefer past log retrieval over current diagnosis"
        )

    raw_filters = parsed.get("filters") or {}
    filters: dict[str, Any] = {}
    for k in _FILTER_KEYS:
        v = raw_filters.get(k)
        if v in (None, "", "null"):
            continue
        filters[k] = v
    filters.update(_fast_filters(text))

    return {
        "intent": intent,
        "search_query": str(parsed.get("search_query") or message).strip(),
        "filters": filters,
        "rationale": str(parsed.get("rationale") or ""),
        "source": "llm_classifier",
    }
