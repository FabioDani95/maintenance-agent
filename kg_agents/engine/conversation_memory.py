from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from kg_agents.models import ChatResponse

RECENT_TURN_LIMIT = 8
ATTEMPT_LIMIT = 5
OUTCOME_LIMIT = 5

_CONTEXT_REF_RE = re.compile(
    r"\b("
    r"it|that|this|same|previous|first option|second option|last one|"
    r"quello|quella|questo|questa|stesso|stessa|prima opzione|seconda opzione|"
    r"precedente|di prima|come prima"
    r")\b",
    re.IGNORECASE,
)
_ATTEMPT_RE = re.compile(
    r"\b("
    r"tried|checked|replaced|reset|restarted|cleaned|measured|tested|"
    r"provato|controllato|sostituito|resettato|riavviato|pulito|misurato|testato"
    r")\b",
    re.IGNORECASE,
)
_OUTCOME_RE = re.compile(
    r"\b("
    r"resolved|fixed|works|still|unchanged|worse|better|failed|"
    r"risolto|funziona|ancora|uguale|peggio|meglio|fallito|non cambia"
    r")\b",
    re.IGNORECASE,
)
_ITALIAN_HINT_RE = re.compile(
    r"\b(il|lo|la|gli|le|un|una|questo|quello|come|cosa|risolvo|controllo|"
    r"guasto|errore|macchina|componente|sintomo|prima|opzione)\b",
    re.IGNORECASE,
)


def empty_memory(product_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    product_meta = product_meta or {}
    return {
        "recent_turns": [],
        "preferences": {
            "language": "",
            "tone": "practical",
        },
        "last_intent": None,
        "last_mode": None,
        "facts": {
            "machine": product_meta.get("product_name") or "",
            "component": "",
            "symptom": "",
            "failure_mode": "",
            "attempted_actions": [],
            "outcomes": [],
        },
        "last_issue": None,
        "updated_at": "",
    }


def normalize_memory(
    state: dict[str, Any] | None,
    product_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = empty_memory(product_meta)
    if not isinstance(state, dict):
        return base

    for key in ("recent_turns", "preferences", "facts"):
        if isinstance(state.get(key), dict):
            base[key].update(state[key])
        elif isinstance(state.get(key), list):
            base[key] = state[key]

    for key in ("last_intent", "last_mode", "last_issue", "updated_at"):
        if key in state:
            base[key] = state[key]

    if not base["facts"].get("machine") and product_meta:
        base["facts"]["machine"] = product_meta.get("product_name") or ""
    base["recent_turns"] = [
        turn for turn in base.get("recent_turns", [])
        if isinstance(turn, dict) and turn.get("role") in {"user", "assistant"}
    ][-RECENT_TURN_LIMIT:]
    return base


def has_context_reference(message: str) -> bool:
    return bool(_CONTEXT_REF_RE.search(message or ""))


def infer_language(message: str, current: str = "") -> str:
    if _ITALIAN_HINT_RE.search(message or ""):
        return "it"
    if current:
        return current
    return "en"


def routing_context(memory: dict[str, Any], message: str) -> dict[str, Any]:
    facts = memory.get("facts") or {}
    context_ref = has_context_reference(message)
    return {
        "has_context_reference": context_ref,
        "last_intent": memory.get("last_intent"),
        "machine": facts.get("machine") or "",
        "component": facts.get("component") or "",
        "symptom": facts.get("symptom") or "",
        "failure_mode": facts.get("failure_mode") or "",
    }


def response_context(memory: dict[str, Any]) -> dict[str, str]:
    facts = memory.get("facts") or {}
    return {
        "machine": str(facts.get("machine") or ""),
        "component": str(facts.get("component") or ""),
        "symptom": str(facts.get("symptom") or ""),
        "failure_mode": str(facts.get("failure_mode") or ""),
        "language": str((memory.get("preferences") or {}).get("language") or ""),
    }


def _append_unique(values: list[Any], value: str, limit: int) -> list[str]:
    value = " ".join((value or "").split())
    out = [str(v) for v in values if v]
    if not value:
        return out[-limit:]
    lowered = {v.lower() for v in out}
    if value.lower() not in lowered:
        out.append(value)
    return out[-limit:]


def _short_user_fact(message: str) -> str:
    compact = " ".join((message or "").split())
    if len(compact) <= 120:
        return compact
    return compact[:117].rstrip() + "..."


def update_memory_after_turn(
    memory: dict[str, Any],
    *,
    user_message: str,
    response: ChatResponse,
    intent: str,
    mode: str,
    product_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updated = normalize_memory(memory, product_meta)
    preferences = updated.setdefault("preferences", {})
    preferences["language"] = infer_language(user_message, str(preferences.get("language") or ""))
    preferences.setdefault("tone", "practical")

    facts = updated.setdefault("facts", {})
    if product_meta and not facts.get("machine"):
        facts["machine"] = product_meta.get("product_name") or ""

    if response.current_issue:
        issue = response.current_issue
        facts["component"] = issue.component_name or issue.component_id or facts.get("component", "")
        facts["failure_mode"] = issue.failure_mode_name or issue.failure_mode_id or facts.get("failure_mode", "")
        updated["last_issue"] = {
            "failure_mode": issue.failure_mode_name,
            "component": issue.component_name,
            "action_count": len(issue.action_options or []),
        }

    synthetic_turn = user_message.startswith("[")
    if user_message and not synthetic_turn and not response.awaiting_clarification:
        facts["symptom"] = _short_user_fact(user_message)

    if not synthetic_turn and _ATTEMPT_RE.search(user_message):
        facts["attempted_actions"] = _append_unique(
            list(facts.get("attempted_actions") or []),
            _short_user_fact(user_message),
            ATTEMPT_LIMIT,
        )
    if not synthetic_turn and _OUTCOME_RE.search(user_message):
        facts["outcomes"] = _append_unique(
            list(facts.get("outcomes") or []),
            _short_user_fact(user_message),
            OUTCOME_LIMIT,
        )

    if not synthetic_turn:
        recent = list(updated.get("recent_turns") or [])
        recent.append({"role": "user", "content": _short_user_fact(user_message)})
        recent.append({"role": "assistant", "content": _short_user_fact(response.reply)})
        updated["recent_turns"] = recent[-RECENT_TURN_LIMIT:]

    updated["last_intent"] = intent
    updated["last_mode"] = mode
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    return updated
