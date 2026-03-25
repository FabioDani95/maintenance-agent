from __future__ import annotations

import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from kg_agents.models import ChatRequest, ChatResponse, NextIssueRequest, ResetRequest, ProductInfoResponse, StatusResponse
from kg_agents.services import instance_store

# Add troubleshooting_agent to path so its modules can be imported
_TS_DIR = Path(__file__).resolve().parent.parent.parent / "troubleshooting_agent"
if str(_TS_DIR) not in sys.path:
    sys.path.insert(0, str(_TS_DIR))

router = APIRouter(prefix="/v1/kg-agents", tags=["chat"])

# ── Instance-scoped caches ──
_ontology_indexes: dict[str, object] = {}
_symptom_embeddings: dict[str, dict] = {}
_product_metadata: dict[str, dict] = {}


def _load_instance_ontology(instance_id: str):
    """Load ontology for a specific instance, with caching."""
    if instance_id in _ontology_indexes:
        return _ontology_indexes[instance_id]

    ont_path = instance_store.get_ontology_path(instance_id)
    if not ont_path.exists():
        raise HTTPException(status_code=404, detail="Ontology not found for this instance")

    import json
    with ont_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    from ontology_loader import OntologyIndex
    index = OntologyIndex(data)
    _ontology_indexes[instance_id] = index

    # Cache metadata
    meta = data.get("metadata", {})
    _product_metadata[instance_id] = {
        "product_name": meta.get("product_name", "Unknown Product"),
        "product_short_name": meta.get("product_short_name", meta.get("product_name", "Product")),
        "product_type": meta.get("product_type", "product"),
        "domain_topics": meta.get("domain_topics", []),
    }

    return index


def _load_instance_embeddings(instance_id: str) -> dict[str, list[float]]:
    """Load symptom embeddings for a specific instance, with caching."""
    if instance_id in _symptom_embeddings:
        return _symptom_embeddings[instance_id]

    emb_path = instance_store.get_embeddings_path(instance_id)
    if not emb_path.exists():
        raise HTTPException(status_code=404, detail="Symptom embeddings not found. Please generate them first.")

    import json
    with emb_path.open("r", encoding="utf-8") as f:
        embeddings = json.load(f)

    _symptom_embeddings[instance_id] = embeddings
    return embeddings


# ── Session store (instance_id + session_id) ──
_sessions: dict[str, dict] = {}


def _session_key(instance_id: str, session_id: str) -> str:
    return f"{instance_id}:{session_id}"


@router.post("/instances/{instance_id}/chat", response_model=ChatResponse)
async def chat(instance_id: str, req: ChatRequest):
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    message = (req.message or "").strip()
    session_id = req.session_id or str(uuid.uuid4())
    model = req.model

    if not message:
        raise HTTPException(status_code=400, detail="Empty message")

    index = _load_instance_ontology(instance_id)
    embeddings = _load_instance_embeddings(instance_id)
    product_meta = _product_metadata.get(instance_id, {})

    from kg_agents.config import OPENAI_CHAT_MODEL, SIMILARITY_THRESHOLD, HIGH_CONFIDENCE_THRESHOLD, TOP_K_SYMPTOMS
    chat_model = model or OPENAI_CHAT_MODEL

    sk = _session_key(instance_id, session_id)
    if sk not in _sessions:
        _sessions[sk] = {"trace": {}, "ranked_issues": [], "current_issue_idx": 0}
    session = _sessions[sk]

    import re
    from graph_traversal import get_troubleshooting_paths, get_troubleshooting_paths_from_error_code, find_error_code_by_value
    from embeddings import get_query_embedding
    from similarity import find_top_k_symptoms, is_high_confidence
    from domain_check import check_domain_relevance as _domain_check_fn
    from response_builder import format_answer_single_group, low_confidence_response, out_of_domain_response, unclear_domain_response

    # Temporarily patch ontology_loader to use our instance
    import ontology_loader as _ol
    _old_get_index = _ol.get_index
    _old_get_meta = _ol.get_product_metadata
    _ol.get_index = lambda: index
    _ol.get_product_metadata = lambda: product_meta

    try:
        # Error code detection
        error_pattern = re.compile(r"\b([0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4})\b")
        raw_code_match = error_pattern.search(message)
        if raw_code_match:
            raw_code = raw_code_match.group(1)
            ec_id = find_error_code_by_value(raw_code, index)
            if ec_id:
                paths = get_troubleshooting_paths_from_error_code(ec_id, index)
                if paths:
                    from orchestrator import _group_paths_by_failure_mode, _build_trace, _build_telemetry_payload
                    ranked = _group_paths_by_failure_mode(paths)
                    session["ranked_issues"] = ranked
                    session["current_issue_idx"] = 0
                    first_group = ranked[0]["paths"]
                    session["trace"] = _build_trace(first_group)
                    reply = format_answer_single_group(first_group, message, model=chat_model)
                    total = len(ranked)
                    return ChatResponse(
                        reply=reply, session_id=session_id,
                        highlight=session["trace"],
                        has_more_issues=total > 1,
                        issue_number=1, total_issues=total,
                        telemetry=_build_telemetry_payload(first_group),
                    )
            session["trace"] = {}
            return ChatResponse(
                reply=f"I recognised the error code **{raw_code}** but I don't have specific troubleshooting data for it yet. Could you also describe the symptom you're seeing?",
                session_id=session_id,
            )

        # Embedding + similarity
        query_emb = get_query_embedding(message)
        top_symptoms = find_top_k_symptoms(query_emb, embeddings)

        # Domain check
        if not is_high_confidence(top_symptoms):
            best_score = top_symptoms[0][1] if top_symptoms else 0.0
            relevance = _domain_check_fn(message, top_score=best_score)
            if relevance == "not_relevant":
                session["trace"] = {}
                return ChatResponse(reply=out_of_domain_response(), session_id=session_id)
            if relevance == "unclear":
                session["trace"] = {}
                return ChatResponse(reply=unclear_domain_response(), session_id=session_id)

        if not top_symptoms:
            session["trace"] = {}
            return ChatResponse(reply=low_confidence_response(), session_id=session_id)

        # Graph traversal
        symptom_ids = [sid for sid, _ in top_symptoms]
        paths = get_troubleshooting_paths(symptom_ids, index)

        if not paths:
            session["trace"] = {}
            return ChatResponse(reply=low_confidence_response(), session_id=session_id)

        from orchestrator import _group_paths_by_symptom_score, _build_trace, _build_telemetry_payload
        ranked = _group_paths_by_symptom_score(paths, top_symptoms)
        session["ranked_issues"] = ranked
        session["current_issue_idx"] = 0
        first_group = ranked[0]["paths"]
        session["trace"] = _build_trace(first_group, top_symptoms)
        total = len(ranked)
        reply = format_answer_single_group(first_group, message, model=chat_model)
        if total > 1:
            reply += f"\n\n---\n*Possible cause 1 of {total}. Use \"Next\" to see the next most likely cause.*"

        return ChatResponse(
            reply=reply, session_id=session_id,
            highlight=session["trace"],
            has_more_issues=total > 1,
            issue_number=1, total_issues=total,
            telemetry=_build_telemetry_payload(first_group),
        )
    finally:
        _ol.get_index = _old_get_index
        _ol.get_product_metadata = _old_get_meta


@router.post("/instances/{instance_id}/next-issue", response_model=ChatResponse)
async def next_issue(instance_id: str, req: NextIssueRequest):
    if not req.session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")

    from kg_agents.config import OPENAI_CHAT_MODEL
    chat_model = req.model or OPENAI_CHAT_MODEL

    sk = _session_key(instance_id, req.session_id)
    session = _sessions.get(sk)
    if not session or not session.get("ranked_issues"):
        return ChatResponse(
            reply="No more issues to show. Please describe a new problem.",
            session_id=req.session_id,
        )

    index = _load_instance_ontology(instance_id)
    product_meta = _product_metadata.get(instance_id, {})

    import ontology_loader as _ol
    _old_get_index = _ol.get_index
    _old_get_meta = _ol.get_product_metadata
    _ol.get_index = lambda: index
    _ol.get_product_metadata = lambda: product_meta

    try:
        ranked = session["ranked_issues"]
        next_idx = session["current_issue_idx"] + 1

        if next_idx >= len(ranked):
            return ChatResponse(
                reply="Those were all the possible causes I found. If the problem persists, please describe it in more detail.",
                session_id=req.session_id,
            )

        session["current_issue_idx"] = next_idx
        group_paths = ranked[next_idx]["paths"]

        from orchestrator import _build_trace, _build_telemetry_payload
        from response_builder import format_answer_single_group

        session["trace"] = _build_trace(group_paths)
        reply = format_answer_single_group(group_paths, "", model=chat_model)
        total = len(ranked)

        return ChatResponse(
            reply=reply, session_id=req.session_id,
            highlight=session["trace"],
            has_more_issues=next_idx + 1 < total,
            issue_number=next_idx + 1, total_issues=total,
            telemetry=_build_telemetry_payload(group_paths),
        )
    finally:
        _ol.get_index = _old_get_index
        _ol.get_product_metadata = _old_get_meta


@router.post("/instances/{instance_id}/reset")
async def reset_session(instance_id: str, req: ResetRequest):
    if req.session_id:
        sk = _session_key(instance_id, req.session_id)
        _sessions.pop(sk, None)
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


@router.get("/instances/{instance_id}/status", response_model=StatusResponse)
async def status(instance_id: str):
    ont_path = instance_store.get_ontology_path(instance_id)
    if not ont_path.exists():
        return StatusResponse(ok=False)
    import json
    with ont_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    meta = data.get("metadata", {})
    return StatusResponse(
        ok=True,
        ontology_version=meta.get("version"),
        total_nodes=meta.get("total_nodes"),
        total_relationships=meta.get("total_relationships"),
    )
