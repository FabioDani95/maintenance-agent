from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from kg_agents.config import OPENAI_CHAT_MODEL
from kg_agents.engine.clarification import (
    build_clarification_state,
    invalid_clarification_reply,
    reorder_ranked_groups,
    resolve_clarification_answer,
)
from kg_agents.engine.domain_check import check_domain_relevance
from kg_agents.engine.embeddings import get_query_embedding
from kg_agents.engine.graph_traversal import (
    find_error_code_by_value,
    get_troubleshooting_paths,
    get_troubleshooting_paths_from_error_code,
)
from kg_agents.engine.ontology_loader import OntologyIndex, build_product_metadata
from kg_agents.engine.query_alignment import align_ranked_groups_to_query, rerank_groups_for_query
from kg_agents.engine.telemetry_loader import evict_telemetry_cache
from kg_agents.engine.response_builder import (
    format_answer_single_group,
    low_confidence_response,
    out_of_domain_response,
    unclear_domain_response,
)
from kg_agents.engine.similarity import find_top_k_symptoms, is_high_confidence
from kg_agents.engine.workflow import (
    build_telemetry_payload,
    build_trace,
    group_paths_by_failure_mode,
    group_paths_by_symptom_score,
)
from kg_agents.models import (
    CurrentIssue,
    ChatRequest,
    ChatResponse,
    OutcomeLogRequest,
    OutcomeLogResponse,
    NextIssueRequest,
    PathStatsResponse,
    ProductInfoResponse,
    ResetRequest,
    StatusResponse,
)
from kg_agents.services import instance_store, intervention_store

router = APIRouter(prefix="/v1/kg-agents", tags=["chat"])

_ERROR_CODE_PATTERN = re.compile(
    r"\b([0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4})\b"
)

# Instance-scoped caches.
_ontology_indexes: dict[str, OntologyIndex] = {}
_symptom_embeddings: dict[str, dict[str, list[float]]] = {}
_product_metadata: dict[str, dict] = {}

# Session store (instance_id + session_id).
_sessions: dict[str, dict] = {}


def evict_instance_cache(instance_id: str) -> None:
    """Remove all in-memory caches for a given instance (ontology, embeddings, product metadata)."""
    _ontology_indexes.pop(instance_id, None)
    _symptom_embeddings.pop(instance_id, None)
    _product_metadata.pop(instance_id, None)


def _session_key(instance_id: str, session_id: str) -> str:
    return f"{instance_id}:{session_id}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_session_state() -> dict[str, object]:
    return {
        "trace": {},
        "ranked_issues": [],
        "current_issue_idx": 0,
        "current_group_paths": [],
        "clarification": None,
        "diagnosis_started_at": None,
        "user_queries": [],
    }


def _get_session(instance_id: str, session_id: str) -> dict:
    key = _session_key(instance_id, session_id)
    if key not in _sessions:
        _sessions[key] = _new_session_state()
    return _sessions[key]


def _begin_diagnosis(session: dict, message: str) -> None:
    session["trace"] = {}
    session["ranked_issues"] = []
    session["current_issue_idx"] = 0
    session["current_group_paths"] = []
    session["clarification"] = None
    session["diagnosis_started_at"] = _utc_now()
    session["user_queries"] = [message]


def _set_active_issue(session: dict, group_paths: list[dict], trace: dict[str, object]) -> None:
    session["current_group_paths"] = group_paths
    session["trace"] = trace


def _symptom_ids_from_group(group_paths: list[dict], trace: dict[str, object]) -> list[str]:
    trace_ids = trace.get("symptom_ids") if isinstance(trace, dict) else None
    if isinstance(trace_ids, list) and trace_ids:
        return [sid for sid in trace_ids if isinstance(sid, str)]
    seen: list[str] = []
    for path in group_paths:
        symptom_id = path.get("symptom_id")
        if symptom_id and symptom_id not in seen:
            seen.append(symptom_id)
    return seen


def _build_current_issue(instance_id: str, group_paths: list[dict], trace: dict[str, object]) -> CurrentIssue | None:
    if not group_paths:
        return None

    first_path = group_paths[0]
    action_payloads: list[dict[str, object]] = []
    path_keys: list[str] = []
    seen_action_ids: set[str] = set()

    for path in group_paths:
        action_id = path.get("action_id", "")
        if not action_id or action_id in seen_action_ids:
            continue
        seen_action_ids.add(action_id)
        final_path = intervention_store.build_path_node_ids(path)
        path_key = intervention_store.build_path_key(final_path)
        path_keys.append(path_key)
        action_payloads.append({
            "action_id": action_id,
            "action_name": path.get("action_name", action_id),
            "instruction_text": path.get("instruction_text", ""),
            "source_title": path.get("source_title", ""),
            "source_reference": path.get("source_reference", ""),
            "path_key": path_key,
            "final_path": final_path,
        })

    stats_by_key = intervention_store.get_path_stats_map(instance_id, path_keys)
    for action in action_payloads:
        path_key = action["path_key"]
        action["stats"] = stats_by_key.get(path_key)

    return CurrentIssue(
        failure_mode_id=first_path.get("failure_mode_id", ""),
        failure_mode_name=first_path.get("failure_mode_name", ""),
        component_id=first_path.get("component_id", ""),
        component_name=first_path.get("component_name", ""),
        symptom_ids=_symptom_ids_from_group(group_paths, trace),
        action_options=action_payloads,
    )


def _public_clarification_options(session: dict) -> list[dict[str, str]]:
    clarification = session.get("clarification")
    if not isinstance(clarification, dict):
        return []
    options = clarification.get("options")
    if not isinstance(options, list):
        return []
    return [
        {
            "id": str(option.get("id", "")),
            "label": str(option.get("label", "")),
            "description": str(option.get("description", "")),
        }
        for option in options
        if isinstance(option, dict)
    ]


def _build_chat_response(
    *,
    instance_id: str,
    session_id: str,
    reply: str,
    trace: dict[str, object] | None = None,
    group_paths: list[dict] | None = None,
    has_more_issues: bool = False,
    issue_number: int | None = None,
    total_issues: int | None = None,
    telemetry: dict[str, object] | None = None,
    awaiting_clarification: bool = False,
    clarification_question: str | None = None,
    clarification_options: list[dict[str, str]] | None = None,
) -> ChatResponse:
    active_trace = trace or {}
    active_group_paths = group_paths or []
    current_issue = _build_current_issue(instance_id, active_group_paths, active_trace)
    return ChatResponse(
        reply=reply,
        session_id=session_id,
        highlight=active_trace,
        has_more_issues=has_more_issues,
        issue_number=issue_number,
        total_issues=total_issues,
        telemetry=telemetry,
        current_issue=current_issue,
        awaiting_clarification=awaiting_clarification,
        clarification_question=clarification_question,
        clarification_options=clarification_options or [],
    )


def _load_instance_ontology(instance_id: str) -> OntologyIndex:
    if instance_id in _ontology_indexes:
        return _ontology_indexes[instance_id]

    ont_path = instance_store.get_ontology_path(instance_id)
    if not ont_path.exists():
        raise HTTPException(status_code=404, detail="Ontology not found for this instance")

    with ont_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    index = OntologyIndex(data)
    _ontology_indexes[instance_id] = index
    _product_metadata[instance_id] = build_product_metadata(data)
    return index


def _load_instance_embeddings(instance_id: str) -> dict[str, list[float]]:
    if instance_id in _symptom_embeddings:
        return _symptom_embeddings[instance_id]

    emb_path = instance_store.get_embeddings_path(instance_id)
    if not emb_path.exists():
        raise HTTPException(status_code=404, detail="Symptom embeddings not found. Please generate them first.")

    with emb_path.open("r", encoding="utf-8") as f:
        embeddings = json.load(f)

    _symptom_embeddings[instance_id] = embeddings
    return embeddings


def _telemetry_dir(instance_id: str):
    return instance_store.get_telemetry_dir(instance_id)


def _active_clarification(session: dict) -> dict[str, object] | None:
    clarification = session.get("clarification")
    return clarification if isinstance(clarification, dict) and clarification.get("question") else None


def _build_clarification_response(
    *,
    instance_id: str,
    session_id: str,
    session: dict,
    reply: str,
) -> ChatResponse:
    _set_active_issue(session, [], {})
    return _build_chat_response(
        instance_id=instance_id,
        session_id=session_id,
        reply=reply,
        awaiting_clarification=True,
        clarification_question=str(session["clarification"].get("question", "")),
        clarification_options=_public_clarification_options(session),
    )


def _handle_clarification_turn(
    *,
    instance_id: str,
    session_id: str,
    session: dict,
    message: str,
    product_meta: dict[str, object],
    chat_model: str,
) -> ChatResponse:
    clarification = _active_clarification(session)
    if clarification is None:
        raise HTTPException(status_code=409, detail="Clarification state is missing")

    resolution = resolve_clarification_answer(message, clarification)
    if resolution.get("status") != "selected":
        clarification["attempts"] = int(clarification.get("attempts") or 0) + 1
        session["clarification"] = clarification
        return _build_clarification_response(
            instance_id=instance_id,
            session_id=session_id,
            session=session,
            reply=invalid_clarification_reply(clarification),
        )

    ranked = reorder_ranked_groups(session.get("ranked_issues") or [], clarification, str(resolution["option_id"]))
    session["clarification"] = None
    session["ranked_issues"] = ranked
    session["current_issue_idx"] = 0
    session.setdefault("user_queries", []).append(message)

    if not ranked:
        _set_active_issue(session, [], {})
        return _build_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            reply=low_confidence_response(product_meta=product_meta),
        )

    first_group = ranked[0]["paths"]
    trace = build_trace(first_group)
    _set_active_issue(session, first_group, trace)
    total = len(ranked)
    reply = format_answer_single_group(
        first_group,
        " ".join(query for query in session.get("user_queries", []) if isinstance(query, str)),
        model=chat_model,
        product_meta=product_meta,
    )
    if total > 1:
        reply += f'\n\n---\n*Possible cause 1 of {total}. Use "Next" to see the next most likely cause.*'

    return _build_chat_response(
        instance_id=instance_id,
        session_id=session_id,
        reply=reply,
        trace=trace,
        group_paths=first_group,
        has_more_issues=total > 1,
        issue_number=1,
        total_issues=total,
        telemetry=build_telemetry_payload(first_group, telemetry_dir=_telemetry_dir(instance_id)),
    )


@router.post("/instances/{instance_id}/chat", response_model=ChatResponse)
async def chat(instance_id: str, req: ChatRequest):
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    message = (req.message or "").strip()
    session_id = req.session_id or str(uuid.uuid4())
    if not message:
        raise HTTPException(status_code=400, detail="Empty message")

    chat_model = req.model or OPENAI_CHAT_MODEL
    index = _load_instance_ontology(instance_id)
    embeddings = _load_instance_embeddings(instance_id)
    product_meta = _product_metadata.get(instance_id, {})
    session = _get_session(instance_id, session_id)

    if _active_clarification(session):
        return _handle_clarification_turn(
            instance_id=instance_id,
            session_id=session_id,
            session=session,
            message=message,
            product_meta=product_meta,
            chat_model=chat_model,
        )

    _begin_diagnosis(session, message)

    raw_code_match = _ERROR_CODE_PATTERN.search(message)
    if raw_code_match:
        raw_code = raw_code_match.group(1)
        ec_id = find_error_code_by_value(raw_code, index)
        if ec_id:
            paths = get_troubleshooting_paths_from_error_code(ec_id, index)
            if paths:
                ranked = group_paths_by_failure_mode(paths)
                session["ranked_issues"] = ranked
                session["current_issue_idx"] = 0
                first_group = ranked[0]["paths"]
                trace = build_trace(first_group)
                _set_active_issue(session, first_group, trace)
                reply = format_answer_single_group(
                    first_group,
                    message,
                    model=chat_model,
                    product_meta=product_meta,
                )
                total = len(ranked)
                return _build_chat_response(
                    instance_id=instance_id,
                    session_id=session_id,
                    reply=reply,
                    trace=trace,
                    group_paths=first_group,
                    has_more_issues=total > 1,
                    issue_number=1,
                    total_issues=total,
                    telemetry=build_telemetry_payload(first_group, telemetry_dir=_telemetry_dir(instance_id)),
                )
        _set_active_issue(session, [], {})
        return _build_chat_response(
            instance_id=instance_id,
            reply=(
                f"I recognised the error code **{raw_code}** but I don't have specific troubleshooting "
                "data for it yet. Could you also describe the symptom you're seeing?"
            ),
            session_id=session_id,
        )

    query_emb = get_query_embedding(message)
    top_symptoms = find_top_k_symptoms(query_emb, embeddings)

    if not is_high_confidence(top_symptoms):
        best_score = top_symptoms[0][1] if top_symptoms else 0.0
        relevance = check_domain_relevance(
            message,
            top_score=best_score,
            product_meta=product_meta,
        )
        if relevance == "not_relevant":
            _set_active_issue(session, [], {})
            return _build_chat_response(
                instance_id=instance_id,
                reply=out_of_domain_response(product_meta=product_meta),
                session_id=session_id,
            )
        if relevance == "unclear":
            _set_active_issue(session, [], {})
            return _build_chat_response(
                instance_id=instance_id,
                reply=unclear_domain_response(product_meta=product_meta),
                session_id=session_id,
            )

    if not top_symptoms:
        _set_active_issue(session, [], {})
        return _build_chat_response(
            instance_id=instance_id,
            reply=low_confidence_response(product_meta=product_meta),
            session_id=session_id,
        )

    symptom_ids = [sid for sid, _ in top_symptoms]
    paths = get_troubleshooting_paths(symptom_ids, index)
    if not paths:
        _set_active_issue(session, [], {})
        return _build_chat_response(
            instance_id=instance_id,
            reply=low_confidence_response(product_meta=product_meta),
            session_id=session_id,
        )

    ranked = group_paths_by_symptom_score(paths, top_symptoms)
    ranked = rerank_groups_for_query(ranked, message, query_emb, index, top_symptoms)
    ranked, unmatched_terms = align_ranked_groups_to_query(ranked, message, index)
    if not ranked:
        _set_active_issue(session, [], {})
        return _build_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            reply=low_confidence_response(
                product_meta=product_meta,
                unmatched_terms=unmatched_terms,
            ),
        )

    session["ranked_issues"] = ranked
    clarification = build_clarification_state(ranked, message, index)
    if clarification:
        session["clarification"] = clarification
        session["current_issue_idx"] = 0
        return _build_clarification_response(
            instance_id=instance_id,
            session_id=session_id,
            session=session,
            reply=str(clarification["question"]),
        )

    session["current_issue_idx"] = 0
    first_group = ranked[0]["paths"]
    trace = build_trace(first_group, top_symptoms)
    _set_active_issue(session, first_group, trace)
    total = len(ranked)
    reply = format_answer_single_group(
        first_group,
        message,
        model=chat_model,
        product_meta=product_meta,
    )
    if total > 1:
        reply += f"\n\n---\n*Possible cause 1 of {total}. Use \"Next\" to see the next most likely cause.*"

    return _build_chat_response(
        instance_id=instance_id,
        session_id=session_id,
        reply=reply,
        trace=trace,
        group_paths=first_group,
        has_more_issues=total > 1,
        issue_number=1,
        total_issues=total,
        telemetry=build_telemetry_payload(first_group, telemetry_dir=_telemetry_dir(instance_id)),
    )


@router.post("/instances/{instance_id}/next-issue", response_model=ChatResponse)
async def next_issue(instance_id: str, req: NextIssueRequest):
    if not req.session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")

    chat_model = req.model or OPENAI_CHAT_MODEL
    session = _sessions.get(_session_key(instance_id, req.session_id))
    if not session or not session.get("ranked_issues"):
        return _build_chat_response(
            instance_id=instance_id,
            reply="No more issues to show. Please describe a new problem.",
            session_id=req.session_id,
        )
    if _active_clarification(session):
        return _build_clarification_response(
            instance_id=instance_id,
            session_id=req.session_id,
            session=session,
            reply=str(session["clarification"].get("question", "Please answer the clarification question first.")),
        )

    _load_instance_ontology(instance_id)
    product_meta = _product_metadata.get(instance_id, {})
    ranked = session["ranked_issues"]
    next_idx = session["current_issue_idx"] + 1

    if next_idx >= len(ranked):
        return _build_chat_response(
            instance_id=instance_id,
            reply="Those were all the possible causes I found. If the problem persists, please describe it in more detail.",
            session_id=req.session_id,
        )

    session["current_issue_idx"] = next_idx
    group_paths = ranked[next_idx]["paths"]
    trace = build_trace(group_paths)
    _set_active_issue(session, group_paths, trace)
    reply = format_answer_single_group(
        group_paths,
        "",
        model=chat_model,
        product_meta=product_meta,
    )
    total = len(ranked)

    return _build_chat_response(
        instance_id=instance_id,
        session_id=req.session_id,
        reply=reply,
        trace=trace,
        group_paths=group_paths,
        has_more_issues=next_idx + 1 < total,
        issue_number=next_idx + 1,
        total_issues=total,
        telemetry=build_telemetry_payload(group_paths, telemetry_dir=_telemetry_dir(instance_id)),
    )


@router.post("/instances/{instance_id}/reset")
async def reset_session(instance_id: str, req: ResetRequest):
    if req.session_id:
        _sessions.pop(_session_key(instance_id, req.session_id), None)
    return {"ok": True}


@router.post("/instances/{instance_id}/log-outcome", response_model=OutcomeLogResponse)
async def log_outcome(instance_id: str, req: OutcomeLogRequest):
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    session = _sessions.get(_session_key(instance_id, req.session_id))
    if not session:
        raise HTTPException(status_code=404, detail="No active diagnosis found for this session")

    group_paths = session.get("current_group_paths") or []
    trace = session.get("trace") or {}
    current_issue = _build_current_issue(instance_id, group_paths, trace)
    if current_issue is None or not current_issue.action_options:
        raise HTTPException(status_code=409, detail="No active corrective actions found for this session")

    selected_action_id = req.selected_action_id
    if not selected_action_id:
        if len(current_issue.action_options) == 1:
            selected_action_id = current_issue.action_options[0].action_id
        else:
            raise HTTPException(
                status_code=422,
                detail="selected_action_id is required when multiple corrective actions are available",
            )

    selected_action = next(
        (action for action in current_issue.action_options if action.action_id == selected_action_id),
        None,
    )
    if selected_action is None:
        raise HTTPException(status_code=422, detail="selected_action_id is not valid for the active diagnosis")

    try:
        intervention, stats = intervention_store.record_outcome(
            instance_id=instance_id,
            session_id=req.session_id,
            started_at=session.get("diagnosis_started_at"),
            symptom_ids=current_issue.symptom_ids,
            final_failure_mode_id=current_issue.failure_mode_id,
            final_path=selected_action.final_path,
            selected_action_id=selected_action.action_id,
            outcome=req.outcome,
            user_queries=session.get("user_queries", []),
            user_feedback=req.user_feedback.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return OutcomeLogResponse(intervention=intervention, stats=stats)


@router.get("/instances/{instance_id}/path-stats", response_model=PathStatsResponse)
async def path_stats(
    instance_id: str,
    path_key: str | None = None,
    final_failure_mode_id: str | None = None,
    selected_action_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
):
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    stats = intervention_store.get_path_stats(
        instance_id,
        path_key=path_key,
        final_failure_mode_id=final_failure_mode_id,
        selected_action_id=selected_action_id,
        limit=limit,
    )
    return PathStatsResponse(stats=stats)


@router.get("/instances/{instance_id}/product-info", response_model=ProductInfoResponse)
async def product_info(instance_id: str):
    index = _load_instance_ontology(instance_id)
    meta = _product_metadata.get(instance_id, {})
    chips = []
    for symptom in index.symptoms[:4]:
        chips.append({
            "label": symptom.get("name", ""),
            "query": symptom.get("name", "") + (
                ". " + symptom.get("description", "") if symptom.get("description") else ""
            ),
        })
    for ec in index.error_codes[:4]:
        code = ec.get("code", "")
        chips.append({"label": code, "query": code})
    return {**meta, "suggested_symptoms": chips}


@router.post("/instances/{instance_id}/reload", include_in_schema=True)
async def reload_instance(instance_id: str):
    """Evict the in-memory ontology and embeddings cache for this instance so it reloads from disk."""
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    evict_instance_cache(instance_id)
    evict_telemetry_cache(telemetry_dir=_telemetry_dir(instance_id))
    return {"ok": True, "instance_id": instance_id}


@router.get("/instances/{instance_id}/status", response_model=StatusResponse)
async def status(instance_id: str):
    ont_path = instance_store.get_ontology_path(instance_id)
    if not ont_path.exists():
        return StatusResponse(ok=False)
    with ont_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    meta = data.get("metadata", {})
    return StatusResponse(
        ok=True,
        ontology_version=meta.get("version"),
        total_nodes=meta.get("total_nodes"),
        total_relationships=meta.get("total_relationships"),
    )
