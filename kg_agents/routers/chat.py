from __future__ import annotations

import json
import re
import uuid

from fastapi import APIRouter, HTTPException

from kg_agents.config import OPENAI_CHAT_MODEL
from kg_agents.engine.domain_check import check_domain_relevance
from kg_agents.engine.embeddings import get_query_embedding
from kg_agents.engine.graph_traversal import (
    find_error_code_by_value,
    get_troubleshooting_paths,
    get_troubleshooting_paths_from_error_code,
)
from kg_agents.engine.ontology_loader import OntologyIndex, build_product_metadata
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
    ChatRequest,
    ChatResponse,
    NextIssueRequest,
    ProductInfoResponse,
    ResetRequest,
    StatusResponse,
)
from kg_agents.services import instance_store

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


def _get_session(instance_id: str, session_id: str) -> dict:
    key = _session_key(instance_id, session_id)
    if key not in _sessions:
        _sessions[key] = {"trace": {}, "ranked_issues": [], "current_issue_idx": 0}
    return _sessions[key]


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
                session["trace"] = build_trace(first_group)
                reply = format_answer_single_group(
                    first_group,
                    message,
                    model=chat_model,
                    product_meta=product_meta,
                )
                total = len(ranked)
                return ChatResponse(
                    reply=reply,
                    session_id=session_id,
                    highlight=session["trace"],
                    has_more_issues=total > 1,
                    issue_number=1,
                    total_issues=total,
                    telemetry=build_telemetry_payload(first_group, telemetry_dir=_telemetry_dir(instance_id)),
                )
        session["trace"] = {}
        return ChatResponse(
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
            session["trace"] = {}
            return ChatResponse(
                reply=out_of_domain_response(product_meta=product_meta),
                session_id=session_id,
            )
        if relevance == "unclear":
            session["trace"] = {}
            return ChatResponse(
                reply=unclear_domain_response(product_meta=product_meta),
                session_id=session_id,
            )

    if not top_symptoms:
        session["trace"] = {}
        return ChatResponse(
            reply=low_confidence_response(product_meta=product_meta),
            session_id=session_id,
        )

    symptom_ids = [sid for sid, _ in top_symptoms]
    paths = get_troubleshooting_paths(symptom_ids, index)
    if not paths:
        session["trace"] = {}
        return ChatResponse(
            reply=low_confidence_response(product_meta=product_meta),
            session_id=session_id,
        )

    ranked = group_paths_by_symptom_score(paths, top_symptoms)
    session["ranked_issues"] = ranked
    session["current_issue_idx"] = 0
    first_group = ranked[0]["paths"]
    session["trace"] = build_trace(first_group, top_symptoms)
    total = len(ranked)
    reply = format_answer_single_group(
        first_group,
        message,
        model=chat_model,
        product_meta=product_meta,
    )
    if total > 1:
        reply += f"\n\n---\n*Possible cause 1 of {total}. Use \"Next\" to see the next most likely cause.*"

    return ChatResponse(
        reply=reply,
        session_id=session_id,
        highlight=session["trace"],
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
        return ChatResponse(
            reply="No more issues to show. Please describe a new problem.",
            session_id=req.session_id,
        )

    _load_instance_ontology(instance_id)
    product_meta = _product_metadata.get(instance_id, {})
    ranked = session["ranked_issues"]
    next_idx = session["current_issue_idx"] + 1

    if next_idx >= len(ranked):
        return ChatResponse(
            reply="Those were all the possible causes I found. If the problem persists, please describe it in more detail.",
            session_id=req.session_id,
        )

    session["current_issue_idx"] = next_idx
    group_paths = ranked[next_idx]["paths"]
    session["trace"] = build_trace(group_paths)
    reply = format_answer_single_group(
        group_paths,
        "",
        model=chat_model,
        product_meta=product_meta,
    )
    total = len(ranked)

    return ChatResponse(
        reply=reply,
        session_id=req.session_id,
        highlight=session["trace"],
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
