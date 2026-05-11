"""LLM-based intent classifier for the chat workflow.

Decides whether a user message is a current-troubleshooting request, a log
history question, a log analytics request, a hybrid diagnosis-plus-history
question, or a work-order lookup. Also extracts filters (component, date
range, severity, etc.) and a cleaned search query.

No deterministic fallback — the LLM is the source of truth. If the LLM call
fails, the exception propagates to the caller.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from kg_agents.config import OPENAI_API_KEY, OPENAI_CHAT_MODEL

from .log_loader import LogStore, load_log_store
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


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _ontology_vocabulary(index: OntologyIndex) -> dict[str, list[dict[str, str]]]:
    components = [
        {"component_id": c.get("component_id", ""), "name": c.get("name", "")}
        for c in index.nodes_by_id.values()
        if isinstance(c, dict) and c.get("component_id")
    ]
    failure_modes = [
        {"failure_mode_id": fm.get("failure_mode_id", ""), "name": fm.get("name", "")}
        for fm in index.failure_modes
    ]
    return {"components": components, "failure_modes": failure_modes}


def _signature_vocabulary(store: LogStore | None) -> list[dict[str, Any]]:
    if store is None:
        return []
    return [
        {
            "event_signature_id": sig_id,
            "linked_failure_mode_id": meta.get("linked_failure_mode_id", ""),
            "occurrence_count": meta.get("occurrence_count", 0),
        }
        for sig_id, meta in store.signature_meta.items()
    ]


def classify_intent(
    message: str,
    instance_id: str,
    ontology_index: OntologyIndex,
) -> dict[str, Any]:
    """Return the routed intent + extracted filters + cleaned search query.

    Shape:
        {
          "intent": str,
          "search_query": str,
          "filters": dict,
          "rationale": str,
        }
    """
    log_store = load_log_store(instance_id)
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
        "FATAL=22). Return only valid JSON."
    )

    user_payload = {
        "today": today,
        "message": message,
        "ontology_vocabulary": _ontology_vocabulary(ontology_index),
        "log_signatures": _signature_vocabulary(log_store),
        "schema": {
            "intent": "one of the 5 intent strings above",
            "search_query": "string optimized for log retrieval",
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
        model=OPENAI_CHAT_MODEL,
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

    raw_filters = parsed.get("filters") or {}
    filters: dict[str, Any] = {}
    for k in _FILTER_KEYS:
        v = raw_filters.get(k)
        if v in (None, "", "null"):
            continue
        filters[k] = v

    return {
        "intent": intent,
        "search_query": str(parsed.get("search_query") or message).strip(),
        "filters": filters,
        "rationale": str(parsed.get("rationale") or ""),
    }
