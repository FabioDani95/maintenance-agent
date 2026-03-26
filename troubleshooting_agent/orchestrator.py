from __future__ import annotations

"""
Orchestrator: manages the multi-turn troubleshooting dialogue.

Session state per session_id:
  {
    "trace": dict,                 # last highlight trace for the frontend graph
    "ranked_issues": list[dict],   # failure-mode groups ordered by symptom score
    "current_issue_idx": int,      # which issue is currently shown
  }
"""

import re
from collections import OrderedDict
from typing import Any

from domain_check import check_domain_relevance
from embeddings import get_query_embedding, load_symptom_embeddings
from graph_traversal import (
    find_error_code_by_value,
    get_troubleshooting_paths,
    get_troubleshooting_paths_from_error_code,
)
from response_builder import (
    format_answer_single_group,
    low_confidence_response,
    out_of_domain_response,
    unclear_domain_response,
)
from similarity import find_top_k_symptoms, is_high_confidence
from telemetry_loader import get_signals, get_stats

# In-memory session store (per-process, sufficient for MVP)
_sessions: dict[str, dict[str, Any]] = {}

# Loaded once at first call
_symptom_embeddings: dict[str, list[float]] | None = None


def _get_embeddings() -> dict[str, list[float]]:
    global _symptom_embeddings
    if _symptom_embeddings is None:
        _symptom_embeddings = load_symptom_embeddings()
    return _symptom_embeddings


def _get_session(session_id: str) -> dict[str, Any]:
    if session_id not in _sessions:
        _sessions[session_id] = {
            "trace": {},
            "ranked_issues": [],
            "current_issue_idx": 0,
        }
    return _sessions[session_id]


def _group_paths_by_failure_mode(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group paths so each failure mode appears once with all its paths."""
    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for p in paths:
        fm_id = p["failure_mode_id"]
        groups.setdefault(fm_id, []).append(p)
    return [{"failure_mode_id": fm_id, "paths": group_paths} for fm_id, group_paths in groups.items()]


def _group_paths_by_symptom_score(
    paths: list[dict[str, Any]],
    top_symptoms: list[tuple[str, float]],
) -> list[dict[str, Any]]:
    """Group by failure mode, then sort groups by the best symptom score that triggered them."""
    score_map = {sid: score for sid, score in top_symptoms}
    grouped = _group_paths_by_failure_mode(paths)
    for g in grouped:
        g["_best_score"] = max(
            score_map.get(p["symptom_id"], 0.0) for p in g["paths"]
        )
    grouped.sort(key=lambda g: g["_best_score"], reverse=True)
    for g in grouped:
        del g["_best_score"]
    return grouped


def _build_trace(paths: list[dict[str, Any]], top_symptoms: list[tuple[str, float]] | None = None) -> dict:
    """Build a highlight trace from matched paths for the frontend graph."""
    # Separate error-code paths from symptom paths
    error_code_ids = list({p["error_code_id"] for p in paths if p.get("error_code_id")})
    symptom_ids = list({p["symptom_id"] for p in paths if not p.get("error_code_id")})
    failure_mode_ids = list({p["failure_mode_id"] for p in paths})
    action_ids = list({p["action_id"] for p in paths})
    component_ids = list({p["component_id"] for p in paths if p.get("component_id")})

    # Collect edges that should be highlighted (from→to pairs)
    edges: list[dict[str, str]] = []
    for p in paths:
        if p.get("error_code_id"):
            edges.append({"from": p["error_code_id"], "to": p["failure_mode_id"]})
        else:
            edges.append({"from": p["symptom_id"], "to": p["failure_mode_id"]})
        edges.append({"from": p["failure_mode_id"], "to": p["action_id"]})
        if p.get("component_id"):
            edges.append({"from": p["failure_mode_id"], "to": p["component_id"]})

    scores = {}
    if top_symptoms:
        scores = {sid: round(score, 3) for sid, score in top_symptoms}

    # Build human-readable reasoning trace
    reasoning: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, str]] = set()
    for p in paths:
        key = (p["failure_mode_id"], p["action_id"])
        if key in seen_paths:
            continue
        seen_paths.add(key)
        entry: dict[str, Any] = {
            "symptom": p["symptom_name"],
            "failure_mode": p["failure_mode_name"],
            "action": p["action_name"],
        }
        if p.get("component_name"):
            entry["component"] = p["component_name"]
        if p.get("source_title"):
            entry["source"] = p["source_title"]
            if p.get("source_reference"):
                entry["source_reference"] = p["source_reference"]
        if scores.get(p["symptom_id"]):
            entry["score"] = scores[p["symptom_id"]]
        reasoning.append(entry)

    return {
        "symptom_ids": symptom_ids,
        "error_code_ids": error_code_ids,
        "failure_mode_ids": failure_mode_ids,
        "action_ids": action_ids,
        "component_ids": component_ids,
        "edges": edges,
        "scores": scores,
        "reasoning": reasoning,
    }


def _build_telemetry_payload(paths: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Collect related_measurements from paths and fetch telemetry data."""
    all_measurements: list[str] = []
    for p in paths:
        for m in p.get("related_measurements", []):
            if m not in all_measurements:
                all_measurements.append(m)
    if not all_measurements:
        return None
    signals = get_signals(all_measurements)
    stats = get_stats(all_measurements)
    if not signals:
        return None
    return {
        "signals": signals,
        "stats": stats,
        "columns": [c for c in all_measurements if c in signals],
    }


_ERROR_CODE_PATTERN = re.compile(
    r"\b([0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4})\b"
)


def _extract_error_code(message: str) -> str | None:
    """Return the first HMS-format error code found in the message, or None."""
    match = _ERROR_CODE_PATTERN.search(message)
    return match.group(1) if match else None


def reset_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


def get_last_trace(session_id: str) -> dict:
    """Return the last highlight trace for the given session."""
    session = _sessions.get(session_id)
    if session:
        return session.get("trace", {})
    return {}


def handle_message(user_message: str, session_id: str, model: str | None = None) -> dict[str, Any]:
    """Return a dict with keys: reply, has_more_issues, issue_number, total_issues."""
    from config import OPENAI_CHAT_MODEL
    chat_model = model or OPENAI_CHAT_MODEL

    session = _get_session(session_id)

    # --- Step 1a: error code detection (bypasses embedding pipeline) ---
    raw_code = _extract_error_code(user_message)
    if raw_code:
        ec_id = find_error_code_by_value(raw_code)
        if ec_id:
            paths = get_troubleshooting_paths_from_error_code(ec_id)
            if paths:
                ranked = _group_paths_by_failure_mode(paths)
                session["ranked_issues"] = ranked
                session["current_issue_idx"] = 0
                first_group = ranked[0]["paths"]
                session["trace"] = _build_trace(first_group)
                reply = format_answer_single_group(first_group, user_message, model=chat_model)
                total = len(ranked)
                return {
                    "reply": reply,
                    "has_more_issues": total > 1,
                    "issue_number": 1,
                    "total_issues": total,
                    "telemetry": _build_telemetry_payload(first_group),
                }
        # Code not found in ontology — inform the user and continue with symptom matching
        session["trace"] = {}
        return {
            "reply": (
                f"I recognised the error code **{raw_code}** but I don't have specific "
                "troubleshooting data for it yet. "
                "Could you also describe the symptom you're seeing? I'll try to help based on that."
            ),
            "has_more_issues": False,
        }

    # --- Step 1b: embedding + symptom matching (done first to skip domain check when confident) ---
    query_emb = get_query_embedding(user_message)
    top_symptoms = find_top_k_symptoms(query_emb, _get_embeddings())

    # --- Step 1c: domain relevance check (deterministic, no LLM) ---
    if not is_high_confidence(top_symptoms):
        best_score = top_symptoms[0][1] if top_symptoms else 0.0
        relevance = check_domain_relevance(user_message, top_score=best_score)
        if relevance == "not_relevant":
            session["trace"] = {}
            return {"reply": out_of_domain_response(), "has_more_issues": False}
        if relevance == "unclear":
            session["trace"] = {}
            return {"reply": unclear_domain_response(), "has_more_issues": False}

    if not top_symptoms:
        session["trace"] = {}
        return {"reply": low_confidence_response(), "has_more_issues": False}

    # --- Step 3: graph traversal ---
    symptom_ids = [sid for sid, _ in top_symptoms]
    paths = get_troubleshooting_paths(symptom_ids)

    if not paths:
        session["trace"] = {}
        return {"reply": low_confidence_response(), "has_more_issues": False}

    # Save trace for the frontend (all paths for now)
    session["trace"] = _build_trace(paths, top_symptoms)

    # --- Step 4: rank by similarity score and show first cause immediately ---
    ranked = _group_paths_by_symptom_score(paths, top_symptoms)
    session["ranked_issues"] = ranked
    session["current_issue_idx"] = 0
    first_group = ranked[0]["paths"]
    session["trace"] = _build_trace(first_group, top_symptoms)
    total = len(ranked)
    reply = format_answer_single_group(first_group, user_message, model=chat_model)
    if total > 1:
        reply += f"\n\n---\n*Possible cause 1 of {total}. Use \"Next\" to see the next most likely cause.*"
    return {
        "reply": reply,
        "has_more_issues": total > 1,
        "issue_number": 1,
        "total_issues": total,
        "telemetry": _build_telemetry_payload(first_group),
    }



def handle_next_issue(session_id: str, model: str | None = None) -> dict[str, Any]:
    """Advance to the next failure mode in the ranked list.
    Return dict with: reply, has_more_issues, issue_number, total_issues, highlight."""
    from config import OPENAI_CHAT_MODEL
    chat_model = model or OPENAI_CHAT_MODEL

    session = _sessions.get(session_id)
    if not session or not session.get("ranked_issues"):
        return {"reply": "No more issues to show. Please describe a new problem.", "has_more_issues": False}

    ranked = session["ranked_issues"]
    next_idx = session["current_issue_idx"] + 1

    if next_idx >= len(ranked):
        return {"reply": "Those were all the possible causes I found. If the problem persists, please describe it in more detail or try a different description.", "has_more_issues": False}

    session["current_issue_idx"] = next_idx
    group_paths = ranked[next_idx]["paths"]
    session["trace"] = _build_trace(group_paths)

    reply = format_answer_single_group(group_paths, "", model=chat_model)
    total = len(ranked)
    return {
        "reply": reply,
        "has_more_issues": next_idx + 1 < total,
        "issue_number": next_idx + 1,
        "total_issues": total,
        "highlight": session["trace"],
        "telemetry": _build_telemetry_payload(group_paths),
    }
