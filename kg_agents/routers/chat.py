from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from time import perf_counter

from fastapi import APIRouter, HTTPException, Query

from kg_agents.config import OPENAI_CHAT_MODEL, OPENAI_NON_FAST_CHAT_MODEL
from kg_agents.engine.conversation_memory import (
    normalize_memory,
    routing_context,
    update_memory_after_turn,
)
from kg_agents.engine.behavior_mode import (
    SOLVE_CURRENT_PROBLEM,
    SEARCH_PAST_EVENTS,
    behavior_mode_for_intent,
)
from kg_agents.engine.clarification import (
    build_clarification_state,
    invalid_clarification_reply,
    reorder_ranked_groups,
    resolve_clarification_answer,
)
from kg_agents.engine.domain_check import check_domain_relevance
from kg_agents.engine.embeddings import get_query_embedding, load_embeddings
from kg_agents.engine.graph_traversal import (
    find_error_code_by_value,
    get_troubleshooting_paths,
    get_troubleshooting_paths_from_error_code,
    get_troubleshooting_paths_from_failure_modes,
)
from kg_agents.engine.intent_router import classify_intent, classify_intent_fast
from kg_agents.engine.log_chat import (
    _logs_only_reply,
    _past_resolution_template,
    basis_rows_from_log_ids,
    compose_past_cases_expanded_analysis,
    fetch_past_cases_for_diagnosis,
    handle_log_analytics,
    handle_log_history_search,
    handle_work_order_lookup,
)
from kg_agents.engine.ontology_loader import OntologyIndex, build_product_metadata
from kg_agents.engine.query_alignment import align_ranked_groups_to_query, rerank_groups_for_query
from kg_agents.engine.log_loader import evict_log_cache
from kg_agents.engine.log_search import evict_search_cache
from kg_agents.engine.telemetry_loader import evict_telemetry_cache
from kg_agents.engine.response_builder import (
    compose_prioritized_solve_answer,
    format_answer_single_group,
    low_confidence_response,
    out_of_domain_response,
    unclear_domain_response,
)
from kg_agents.engine.similarity import (
    find_top_k_failure_modes,
    find_top_k_symptoms,
    is_high_confidence,
)
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
    ChatSessionsResponse,
    ChatSessionMessagesResponse,
    OutcomeLogRequest,
    OutcomeLogResponse,
    NextIssueRequest,
    PastCasesAnalysisRequest,
    PastCasesAnalysisResponse,
    PastCasesSummary,
    PathStatsResponse,
    ProductInfoResponse,
    ResetRequest,
    StatusResponse,
)
from kg_agents.services import instance_store, intervention_store
from kg_agents.services import chat_log_store

router = APIRouter(prefix="/v1/kg-agents", tags=["chat"])
logger = logging.getLogger(__name__)

_ERROR_CODE_PATTERN = re.compile(
    r"\b([0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4}[_\-][0-9A-Fa-f]{4})\b"
)

# Instance-scoped caches.
_ontology_indexes: dict[str, OntologyIndex] = {}
# Sectioned embeddings per instance: {"symptoms": {...}, "failure_modes": {...}}
_embeddings_cache: dict[str, dict[str, dict[str, list[float]]]] = {}
_product_metadata: dict[str, dict] = {}

# Session store (instance_id + session_id).
_sessions: dict[str, dict] = {}


class _StageTimer:
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


def _timed_call(fn, *args, **kwargs):
    started = perf_counter()
    result = fn(*args, **kwargs)
    return result, round(perf_counter() - started, 3)


def evict_instance_cache(instance_id: str) -> None:
    """Remove all in-memory caches for a given instance (ontology, embeddings, product metadata)."""
    _ontology_indexes.pop(instance_id, None)
    _embeddings_cache.pop(instance_id, None)
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


def _resolve_chat_mode(req: ChatRequest | NextIssueRequest) -> str:
    if req.mode in {"fast", "non-fast"}:
        return req.mode
    legacy_model = (req.model or "").strip().lower()
    if legacy_model in {"non-fast", "normal", "deep", "thorough", "gpt-5-mini", "gpt-5.4", "gpt-5"}:
        return "non-fast"
    return "fast"


def _chat_model_for_mode(mode: str) -> str:
    return OPENAI_CHAT_MODEL if mode == "fast" else OPENAI_NON_FAST_CHAT_MODEL


def _load_conversation_memory(
    instance_id: str,
    session_id: str,
    product_meta: dict[str, object],
) -> dict[str, object]:
    cached = _get_session(instance_id, session_id).get("conversation_memory")
    if isinstance(cached, dict):
        return normalize_memory(cached, product_meta)
    persisted = chat_log_store.get_conversation_memory(instance_id, session_id)
    memory = normalize_memory(persisted, product_meta)
    _get_session(instance_id, session_id)["conversation_memory"] = memory
    return memory


def _save_conversation_memory(
    instance_id: str,
    session_id: str,
    memory: dict[str, object],
) -> None:
    _get_session(instance_id, session_id)["conversation_memory"] = memory
    chat_log_store.upsert_conversation_memory(
        instance_id=instance_id,
        session_id=session_id,
        state=memory,
    )


def _begin_diagnosis(session: dict, message: str) -> None:
    session["trace"] = {}
    session["ranked_issues"] = []
    session["current_issue_idx"] = 0
    session["current_group_paths"] = []
    session["clarification"] = None
    session["diagnosis_started_at"] = _utc_now()
    session["user_queries"] = [message]
    # Pending hybrid-history context only spans a single diagnosis turn;
    # clear it whenever a new query starts so we never leak prior history
    # into an unrelated answer.
    session.pop("_hybrid_appendix_pending", None)
    session.pop("_hybrid_evidence_pending", None)
    session.pop("_past_cases_summary_pending", None)
    session.pop("_past_matches_pending", None)


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
        # Skip pseudo-symptoms (fm-sourced paths re-use the fm id as symptom_id
        # when no real Symptom is linked).
        if not symptom_id or symptom_id == path.get("failure_mode_id"):
            continue
        if symptom_id not in seen:
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


def _load_instance_embeddings(instance_id: str) -> dict[str, dict[str, list[float]]]:
    """Load sectioned embeddings for an instance.

    Returns {"symptoms": {...}, "failure_modes": {...}}. Accepts both the new
    sectioned format and the legacy flat-dict format (treated as symptoms only).
    """
    if instance_id in _embeddings_cache:
        return _embeddings_cache[instance_id]

    emb_path = instance_store.get_embeddings_path(instance_id)
    if not emb_path.exists():
        raise HTTPException(status_code=404, detail="Symptom embeddings not found. Please generate them first.")

    embeddings = load_embeddings(emb_path)
    _embeddings_cache[instance_id] = embeddings
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

    selected_option_id = str(resolution["option_id"])
    # "None of these" opt-out → render the logs-only fallback using the
    # stashed past-cases context from the originating turn. No further KG
    # attempt, no second clarification.
    if selected_option_id == "none_of_these":
        session["clarification"] = None
        session["current_issue_idx"] = 0
        session.setdefault("user_queries", []).append(message)
        pending_summary_dict = session.pop("_past_cases_summary_pending", None)
        pending_matches = session.pop("_past_matches_pending", []) or []
        pending_summary = (
            PastCasesSummary(**pending_summary_dict) if isinstance(pending_summary_dict, dict) else None
        )
        if pending_summary is None or pending_summary.occurrence_count == 0:
            return _build_chat_response(
                instance_id=instance_id,
                session_id=session_id,
                reply=(
                    "I don't have a confident match in the manuals for this, and I could not find "
                    "similar past events on this machine either. Try describing the symptom in more detail."
                ),
            )
        reply = _logs_only_reply(pending_summary, pending_matches)
        response = _build_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            reply=reply,
        )
        # Mark the response so the caller can also apply the summary metadata.
        session["_opt_out_just_used"] = True
        session["_opt_out_summary"] = pending_summary
        return response

    ranked = reorder_ranked_groups(session.get("ranked_issues") or [], clarification, selected_option_id)
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


def _build_logs_only_response(
    *,
    instance_id: str,
    session_id: str,
    past_cases_summary: PastCasesSummary | None,
    hybrid_evidence: list[dict],
    past_matches: list[dict],
    fallback_reply: str,
) -> ChatResponse | None:
    """Render a logs-only diagnose response.

    Returns ``None`` if there is nothing meaningful to show (no past cases on
    this machine); the caller should keep its existing fallback reply in that
    case.
    """
    if not past_cases_summary or past_cases_summary.occurrence_count == 0:
        return None
    reply = _logs_only_reply(past_cases_summary, past_matches)
    return _build_chat_response(
        instance_id=instance_id,
        session_id=session_id,
        reply=reply,
    )


def _apply_intent(
    response: ChatResponse,
    intent: str,
    hybrid_appendix: str = "",
    hybrid_evidence: list[dict] | None = None,
    past_cases_summary: PastCasesSummary | None = None,
) -> ChatResponse:
    """Stamp the routed intent (and any hybrid-history appendix) onto a
    KG-flow response. Pure mutation + return for use right before logging."""
    response.intent = intent
    response.behavior_mode = behavior_mode_for_intent(intent)
    if hybrid_appendix:
        response.reply = response.reply + hybrid_appendix
    if hybrid_evidence:
        response.log_evidence = hybrid_evidence
    if past_cases_summary is not None:
        response.past_cases_summary = past_cases_summary
        # Surface the top signature so the front-end can open the existing
        # log navigator filtered to it (reuse of the log-search Inspector view).
        log_filters = dict(response.metrics.get("log_filters") or {})
        log_filters.setdefault("event_signature_id", past_cases_summary.top_event_signature_id)
        response.metrics = {**response.metrics, "log_filters": log_filters}
    return response


def _log_chat_exchange(
    instance_id: str,
    session_id: str,
    user_message: str,
    response: ChatResponse,
) -> None:
    try:
        chat_log_store.log_user_message(
            instance_id=instance_id,
            session_id=session_id,
            content=user_message,
        )
        chat_log_store.log_assistant_message(
            instance_id=instance_id,
            session_id=session_id,
            content=response.reply,
            payload=response.model_dump(),
        )
    except Exception:
        pass


def _finish_chat_response(
    *,
    instance_id: str,
    session_id: str,
    user_message: str,
    response: ChatResponse,
    timer: _StageTimer,
    branch: str,
    intent: str,
    mode: str,
    memory: dict[str, object],
    product_meta: dict[str, object],
    intent_source: str = "",
    intent_fallback_used: bool = False,
) -> ChatResponse:
    resolved_intent_source = intent_source or branch
    resolved_intent = response.intent or intent
    response.behavior_mode = response.behavior_mode or behavior_mode_for_intent(resolved_intent)
    response.metrics = {
        **response.metrics,
        "mode": mode,
        "intent": resolved_intent,
        "behavior_mode": response.behavior_mode,
        "intent_source": resolved_intent_source,
        "intent_fallback_used": intent_fallback_used,
    }
    session_state = _get_session(instance_id, session_id)
    session_state["last_intent"] = resolved_intent
    session_state["last_behavior_mode"] = response.behavior_mode
    session_state["last_intent_source"] = resolved_intent_source
    session_state["last_intent_fallback_used"] = intent_fallback_used
    try:
        updated_memory = update_memory_after_turn(
            memory,
            user_message=user_message,
            response=response,
            intent=resolved_intent,
            mode=mode,
            product_meta=product_meta,
        )
        _save_conversation_memory(instance_id, session_id, updated_memory)
    except Exception:
        logger.exception("Failed to update conversation memory")
    _log_chat_exchange(instance_id, session_id, user_message, response)
    timer.mark("chat_log")
    response.timings = timer.snapshot()
    logger.info(
        "kg_chat_timing instance_id=%s session_id=%s branch=%s intent=%s total_s=%.3f timings=%s",
        instance_id,
        session_id,
        branch,
        resolved_intent,
        response.timings.get("total_s", 0.0),
        response.timings,
    )
    return response


def _finish_next_issue_response(
    *,
    instance_id: str,
    session_id: str,
    response: ChatResponse,
    mode: str,
    memory: dict[str, object],
    product_meta: dict[str, object],
) -> ChatResponse:
    response.intent = response.intent or "troubleshooting_current"
    response.behavior_mode = response.behavior_mode or behavior_mode_for_intent(response.intent)
    response.metrics = {
        **response.metrics,
        "mode": mode,
        "intent": response.intent,
        "behavior_mode": response.behavior_mode,
        "intent_source": "next_issue_session_state",
        "intent_fallback_used": False,
    }
    try:
        updated_memory = update_memory_after_turn(
            memory,
            user_message="[next issue]",
            response=response,
            intent=response.intent,
            mode=mode,
            product_meta=product_meta,
        )
        _save_conversation_memory(instance_id, session_id, updated_memory)
    except Exception:
        logger.exception("Failed to update conversation memory")
    _log_chat_exchange(instance_id, session_id, "[next issue]", response)
    return response


@router.post("/instances/{instance_id}/chat", response_model=ChatResponse)
async def chat(instance_id: str, req: ChatRequest):
    timer = _StageTimer()
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")

    message = (req.message or "").strip()
    session_id = req.session_id or str(uuid.uuid4())
    if not message:
        raise HTTPException(status_code=400, detail="Empty message")

    mode = _resolve_chat_mode(req)
    chat_model = _chat_model_for_mode(mode)
    index = _load_instance_ontology(instance_id)
    embeddings = _load_instance_embeddings(instance_id)
    product_meta = _product_metadata.get(instance_id, {})
    session = _get_session(instance_id, session_id)
    memory = _load_conversation_memory(instance_id, session_id, product_meta)
    timer.mark("setup")

    response: ChatResponse
    intent: str = "troubleshooting_current"
    intent_source: str = ""
    intent_fallback_used = False
    hybrid_appendix: str = ""
    hybrid_evidence: list[dict] = []

    if _active_clarification(session):
        intent = str(session.get("last_intent") or intent)
        intent_source = str(session.get("last_intent_source") or "clarification_session_state")
        intent_fallback_used = bool(session.get("last_intent_fallback_used") or False)
        response = _handle_clarification_turn(
            instance_id=instance_id,
            session_id=session_id,
            session=session,
            message=message,
            product_meta=product_meta,
            chat_model=chat_model,
        )
        timer.mark("clarification_turn")
        # Opt-out short-circuit: the clarification handler already rendered a
        # logs-only reply. Attach the summary + evidence and we are done.
        if session.pop("_opt_out_just_used", False):
            opt_out_summary = session.pop("_opt_out_summary", None)
            opt_out_evidence: list[dict] = []
            pending_matches = session.pop("_past_matches_pending", []) or []
            # Re-derive a slim evidence projection from the matches stashed at
            # the originating turn so the front-end can still open the log
            # navigator filtered to the top signature.
            if pending_matches:
                from kg_agents.engine.log_chat import _evidence_items
                opt_out_evidence = _evidence_items(pending_matches)
            _apply_intent(
                response,
                "troubleshooting_current",
                "",
                opt_out_evidence,
                opt_out_summary,
            )
            return _finish_chat_response(
                instance_id=instance_id,
                session_id=session_id,
                user_message=message,
                response=response,
                timer=timer,
                branch="clarification_opt_out",
                intent="troubleshooting_current",
                mode=mode,
                memory=memory,
                product_meta=product_meta,
                intent_source=intent_source,
                intent_fallback_used=intent_fallback_used,
            )
        # If a hybrid query stashed history during the previous turn and the
        # clarification is now resolved (i.e. we just produced a final KG
        # answer), append the history and elevate the intent. If the user is
        # still being asked for clarification, keep the stash for next turn.
        pending_appendix = session.get("_hybrid_appendix_pending", "")
        pending_evidence = session.get("_hybrid_evidence_pending") or []
        pending_intent = str(session.get("_hybrid_intent_pending") or "hybrid_diagnosis_with_history")
        pending_summary_dict = session.get("_past_cases_summary_pending")
        pending_summary = (
            PastCasesSummary(**pending_summary_dict) if isinstance(pending_summary_dict, dict) else None
        )
        if not response.awaiting_clarification:
            # The clarification has resolved into a KG answer. Append our
            # quantified past-resolution block to the reply (same as the
            # primary troubleshooting branch does), and forward the summary.
            if pending_summary and pending_summary.occurrence_count > 0:
                block = _past_resolution_template(pending_summary)
                if block:
                    response.reply = response.reply + "\n" + block
        if (pending_appendix or pending_evidence) and not response.awaiting_clarification:
            _apply_intent(
                response,
                pending_intent,
                pending_appendix,
                pending_evidence,
                pending_summary,
            )
            session.pop("_hybrid_appendix_pending", None)
            session.pop("_hybrid_evidence_pending", None)
            session.pop("_hybrid_intent_pending", None)
            session.pop("_past_cases_summary_pending", None)
            session.pop("_past_matches_pending", None)
        else:
            # Either still awaiting clarification, or resolved with no hybrid
            # stash — in both cases forward the past-cases summary so the
            # front-end can render the box on the post-clarification turn.
            if not response.awaiting_clarification:
                # Reuse the same evidence list captured at the originating turn
                # so the log-navigator deep link works after clarification.
                pending_matches = session.get("_past_matches_pending") or []
                pending_evidence_only: list[dict] = []
                if pending_matches:
                    from kg_agents.engine.log_chat import _evidence_items
                    pending_evidence_only = _evidence_items(pending_matches)
                _apply_intent(
                    response,
                    intent,
                    "",
                    pending_evidence_only,
                    pending_summary,
                )
                session.pop("_past_cases_summary_pending", None)
                session.pop("_past_matches_pending", None)
            else:
                _apply_intent(response, intent)
        return _finish_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            user_message=message,
            response=response,
            timer=timer,
            branch="clarification",
            intent=response.intent or intent,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
            intent_source=intent_source,
            intent_fallback_used=intent_fallback_used,
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
                timer.mark("error_code_lookup")
                reply = format_answer_single_group(
                    first_group,
                    message,
                    model=chat_model,
                    product_meta=product_meta,
                )
                total = len(ranked)
                telemetry = build_telemetry_payload(first_group, telemetry_dir=_telemetry_dir(instance_id))
                timer.mark("telemetry")
                response = _build_chat_response(
                    instance_id=instance_id,
                    session_id=session_id,
                    reply=reply,
                    trace=trace,
                    group_paths=first_group,
                    has_more_issues=total > 1,
                    issue_number=1,
                    total_issues=total,
                    telemetry=telemetry,
                )
                timer.mark("response_build")
                _apply_intent(response, intent)
                return _finish_chat_response(
                    instance_id=instance_id,
                    session_id=session_id,
                    user_message=message,
                    response=response,
                    timer=timer,
                    branch="error_code",
                    intent=intent,
                    mode=mode,
                    memory=memory,
                    product_meta=product_meta,
                    intent_source=intent_source,
                    intent_fallback_used=intent_fallback_used,
                )
        timer.mark("error_code_lookup")
        _set_active_issue(session, [], {})
        response = _build_chat_response(
            instance_id=instance_id,
            reply=(
                f"I recognised the error code **{raw_code}** but I don't have specific troubleshooting "
                "data for it yet. Could you also describe the symptom you're seeing?"
            ),
            session_id=session_id,
        )
        timer.mark("response_build")
        _apply_intent(response, intent)
        return _finish_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            user_message=message,
            response=response,
            timer=timer,
            branch="error_code_unknown",
            intent=intent,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
            intent_source=intent_source,
            intent_fallback_used=intent_fallback_used,
        )

    query_emb: list[float] | None = None
    memory_context = routing_context(memory, message)
    if mode == "fast":
        fast_intent_started = perf_counter()
        intent_result = classify_intent_fast(message, memory_context=memory_context)
        timer.timings["intent_fast_path_s"] = round(perf_counter() - fast_intent_started, 3)
        if intent_result is not None:
            intent_source = str(intent_result.get("source") or "deterministic_fast_path")
            timer.timings["intent_classifier_s"] = 0.0
            timer.timings["query_embedding_s"] = 0.0
            timer.mark("intent_fast_path")
        else:
            intent_fallback_used = True
            (intent_result, intent_classifier_s), (query_emb, query_embedding_s) = await asyncio.gather(
                asyncio.to_thread(_timed_call, classify_intent, message, instance_id, index, memory_context),
                asyncio.to_thread(_timed_call, get_query_embedding, message),
            )
            intent_source = str(intent_result.get("source") or "llm_classifier")
            timer.timings["intent_classifier_s"] = intent_classifier_s
            timer.timings["query_embedding_s"] = query_embedding_s
            timer.mark("intent_and_query_embedding")
    else:
        timer.timings["intent_fast_path_s"] = 0.0
        (intent_result, intent_classifier_s), (query_emb, query_embedding_s) = await asyncio.gather(
            asyncio.to_thread(_timed_call, classify_intent, message, instance_id, index, memory_context),
            asyncio.to_thread(_timed_call, get_query_embedding, message),
        )
        intent_source = str(intent_result.get("source") or "llm_classifier")
        intent_fallback_used = True
        timer.timings["intent_classifier_s"] = intent_classifier_s
        timer.timings["query_embedding_s"] = query_embedding_s
        timer.mark("intent_and_query_embedding")
    intent = intent_result["intent"]
    intent_filters = intent_result["filters"]
    intent_query = intent_result["search_query"]
    behavior_mode = behavior_mode_for_intent(intent)
    requested_behavior_mode = req.behavior_mode
    if requested_behavior_mode == SOLVE_CURRENT_PROBLEM:
        behavior_mode = SOLVE_CURRENT_PROBLEM
        if behavior_mode_for_intent(intent) == SEARCH_PAST_EVENTS:
            intent = "troubleshooting_current"
            intent_query = message
            intent_source = f"{intent_source}+ui_behavior_override" if intent_source else "ui_behavior_override"
    elif requested_behavior_mode == SEARCH_PAST_EVENTS:
        behavior_mode = SEARCH_PAST_EVENTS
        if behavior_mode_for_intent(intent) != SEARCH_PAST_EVENTS:
            intent = "log_history_search"
            intent_source = f"{intent_source}+ui_behavior_override" if intent_source else "ui_behavior_override"

    if behavior_mode == SEARCH_PAST_EVENTS and intent == "log_history_search":
        reply, evidence, handler_timings = handle_log_history_search(
            intent_query, instance_id, intent_filters, chat_model,
        )
        timer.add_nested("log_history", handler_timings)
        timer.mark("log_history_handler")
        _set_active_issue(session, [], {})
        response = _build_chat_response(
            instance_id=instance_id, session_id=session_id, reply=reply,
        )
        timer.mark("response_build")
        response.intent = intent
        response.behavior_mode = behavior_mode
        response.log_evidence = evidence
        response.metrics = {
            **response.metrics,
            "log_query": intent_query,
            "log_filters": intent_filters,
        }
        return _finish_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            user_message=message,
            response=response,
            timer=timer,
            branch="log_history_search",
            intent=intent,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
            intent_source=intent_source,
            intent_fallback_used=intent_fallback_used,
        )

    if behavior_mode == SEARCH_PAST_EVENTS and intent == "log_analytics":
        reply, evidence, handler_timings, log_summary = handle_log_analytics(
            intent_query,
            instance_id,
            intent_filters,
            chat_model,
        )
        timer.add_nested("log_analytics", handler_timings)
        timer.mark("log_analytics_handler")
        _set_active_issue(session, [], {})
        response = _build_chat_response(
            instance_id=instance_id, session_id=session_id, reply=reply,
        )
        timer.mark("response_build")
        response.intent = intent
        response.behavior_mode = behavior_mode
        response.log_evidence = evidence
        response.metrics = {
            **response.metrics,
            "log_query": intent_query,
            "log_filters": intent_filters,
            "log_summary": log_summary,
        }
        return _finish_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            user_message=message,
            response=response,
            timer=timer,
            branch="log_analytics",
            intent=intent,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
            intent_source=intent_source,
            intent_fallback_used=intent_fallback_used,
        )

    if behavior_mode == SEARCH_PAST_EVENTS and intent == "work_order_lookup":
        reply, evidence, handler_timings = handle_work_order_lookup(
            intent_query, instance_id, intent_filters, chat_model,
        )
        timer.add_nested("work_order", handler_timings)
        timer.mark("work_order_handler")
        _set_active_issue(session, [], {})
        response = _build_chat_response(
            instance_id=instance_id, session_id=session_id, reply=reply,
        )
        timer.mark("response_build")
        response.intent = intent
        response.behavior_mode = behavior_mode
        response.log_evidence = evidence
        response.metrics = {
            **response.metrics,
            "log_query": intent_query,
            "log_filters": intent_filters,
        }
        return _finish_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            user_message=message,
            response=response,
            timer=timer,
            branch="work_order_lookup",
            intent=intent,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
            intent_source=intent_source,
            intent_fallback_used=intent_fallback_used,
        )

    # Past-cases retrieval runs on every troubleshooting turn (including Fast
    # mode) so the reply can always carry quantified history. When no logs
    # exist for the instance the helper returns ``(None, [], _, [])`` and the
    # downstream rendering simply skips the past-cases block.
    past_cases_summary, hybrid_evidence, past_timings, past_matches = fetch_past_cases_for_diagnosis(
        intent_query, instance_id, intent_filters,
    )
    timer.add_nested("past_cases", past_timings)
    timer.mark("past_cases_search")
    # No separate hybrid bullet appendix: the structured past-resolution block
    # already carries the same information in a cleaner shape, and the
    # past-case box links to the full log navigator for details.

    if query_emb is None:
        query_emb, query_embedding_s = _timed_call(get_query_embedding, message)
        timer.timings["query_embedding_s"] = query_embedding_s
        timer.mark("query_embedding")

    symptom_embs = embeddings.get("symptoms", {})
    fm_embs = embeddings.get("failure_modes", {})
    top_symptoms = find_top_k_symptoms(query_emb, symptom_embs)
    top_failure_modes = find_top_k_failure_modes(query_emb, fm_embs)
    timer.mark("kg_similarity")

    best_symptom_score = top_symptoms[0][1] if top_symptoms else 0.0
    best_fm_score = top_failure_modes[0][1] if top_failure_modes else 0.0
    best_overall_score = max(best_symptom_score, best_fm_score)
    combined_top = top_symptoms + top_failure_modes

    if not is_high_confidence(combined_top):
        relevance = check_domain_relevance(
            message,
            top_score=best_overall_score,
            product_meta=product_meta,
        )
        timer.mark("domain_check")
        if relevance == "not_relevant":
            _set_active_issue(session, [], {})
            logs_only = _build_logs_only_response(
                instance_id=instance_id,
                session_id=session_id,
                past_cases_summary=past_cases_summary,
                hybrid_evidence=hybrid_evidence,
                past_matches=past_matches,
                fallback_reply="",
            )
            response = logs_only or _build_chat_response(
                instance_id=instance_id,
                reply=out_of_domain_response(product_meta=product_meta),
                session_id=session_id,
            )
            timer.mark("response_build")
            _apply_intent(
                response, intent,
                "" if logs_only else hybrid_appendix,
                hybrid_evidence,
                past_cases_summary,
            )
            return _finish_chat_response(
                instance_id=instance_id,
                session_id=session_id,
                user_message=message,
                response=response,
                timer=timer,
                branch="logs_only_out_of_domain" if logs_only else "out_of_domain",
                intent=intent,
                mode=mode,
                memory=memory,
                product_meta=product_meta,
                intent_source=intent_source,
                intent_fallback_used=intent_fallback_used,
            )
        if relevance == "unclear":
            _set_active_issue(session, [], {})
            logs_only = _build_logs_only_response(
                instance_id=instance_id,
                session_id=session_id,
                past_cases_summary=past_cases_summary,
                hybrid_evidence=hybrid_evidence,
                past_matches=past_matches,
                fallback_reply="",
            )
            response = logs_only or _build_chat_response(
                instance_id=instance_id,
                reply=unclear_domain_response(product_meta=product_meta),
                session_id=session_id,
            )
            timer.mark("response_build")
            _apply_intent(
                response, intent,
                "" if logs_only else hybrid_appendix,
                hybrid_evidence,
                past_cases_summary,
            )
            return _finish_chat_response(
                instance_id=instance_id,
                session_id=session_id,
                user_message=message,
                response=response,
                timer=timer,
                branch="logs_only_unclear_domain" if logs_only else "unclear_domain",
                intent=intent,
                mode=mode,
                memory=memory,
                product_meta=product_meta,
                intent_source=intent_source,
                intent_fallback_used=intent_fallback_used,
            )

    if not top_symptoms and not top_failure_modes:
        _set_active_issue(session, [], {})
        logs_only = _build_logs_only_response(
            instance_id=instance_id,
            session_id=session_id,
            past_cases_summary=past_cases_summary,
            hybrid_evidence=hybrid_evidence,
            past_matches=past_matches,
            fallback_reply="",
        )
        response = logs_only or _build_chat_response(
            instance_id=instance_id,
            reply=low_confidence_response(product_meta=product_meta),
            session_id=session_id,
        )
        timer.mark("response_build")
        _apply_intent(
            response, intent,
            "" if logs_only else hybrid_appendix,
            hybrid_evidence,
            past_cases_summary,
        )
        return _finish_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            user_message=message,
            response=response,
            timer=timer,
            branch="logs_only_low_confidence" if logs_only else "low_confidence",
            intent=intent,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
            intent_source=intent_source,
            intent_fallback_used=intent_fallback_used,
        )

    symptom_ids = [sid for sid, _ in top_symptoms]
    fm_ids = [fm_id for fm_id, _ in top_failure_modes]

    paths = get_troubleshooting_paths(symptom_ids, index)
    fm_paths = get_troubleshooting_paths_from_failure_modes(fm_ids, index)
    # Merge, deduplicating on (failure_mode_id, action_id).
    seen_keys: set[tuple[str, str]] = {
        (p.get("failure_mode_id", ""), p.get("action_id", "")) for p in paths
    }
    for p in fm_paths:
        key = (p.get("failure_mode_id", ""), p.get("action_id", ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        paths.append(p)
    timer.mark("graph_traversal")

    if not paths:
        _set_active_issue(session, [], {})
        logs_only = _build_logs_only_response(
            instance_id=instance_id,
            session_id=session_id,
            past_cases_summary=past_cases_summary,
            hybrid_evidence=hybrid_evidence,
            past_matches=past_matches,
            fallback_reply="",
        )
        response = logs_only or _build_chat_response(
            instance_id=instance_id,
            reply=low_confidence_response(product_meta=product_meta),
            session_id=session_id,
        )
        timer.mark("response_build")
        _apply_intent(
            response, intent,
            "" if logs_only else hybrid_appendix,
            hybrid_evidence,
            past_cases_summary,
        )
        return _finish_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            user_message=message,
            response=response,
            timer=timer,
            branch="logs_only_no_paths" if logs_only else "no_paths",
            intent=intent,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
            intent_source=intent_source,
            intent_fallback_used=intent_fallback_used,
        )

    ranked = group_paths_by_symptom_score(paths, combined_top)
    ranked = rerank_groups_for_query(ranked, message, query_emb, index, combined_top)
    ranked, unmatched_terms = align_ranked_groups_to_query(ranked, message, index)
    timer.mark("rerank_alignment")
    if not ranked:
        _set_active_issue(session, [], {})
        logs_only = _build_logs_only_response(
            instance_id=instance_id,
            session_id=session_id,
            past_cases_summary=past_cases_summary,
            hybrid_evidence=hybrid_evidence,
            past_matches=past_matches,
            fallback_reply="",
        )
        response = logs_only or _build_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            reply=low_confidence_response(
                product_meta=product_meta,
                unmatched_terms=unmatched_terms,
            ),
        )
        timer.mark("response_build")
        _apply_intent(
            response, intent,
            "" if logs_only else hybrid_appendix,
            hybrid_evidence,
            past_cases_summary,
        )
        return _finish_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            user_message=message,
            response=response,
            timer=timer,
            branch="logs_only_alignment_no_fit" if logs_only else "alignment_no_fit",
            intent=intent,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
            intent_source=intent_source,
            intent_fallback_used=intent_fallback_used,
        )

    session["ranked_issues"] = ranked
    clarification = build_clarification_state(ranked, message, index)
    timer.mark("clarification_build")
    if clarification:
        # Always append the "None of these" opt-out so the operator can exit
        # the manual-based flow and fall back to logs-only — but only if past
        # cases are actually available to fall back to.
        if past_cases_summary is not None and past_cases_summary.occurrence_count > 0:
            clarification.setdefault("options", []).append({
                "id": "none_of_these",
                "label": "None of these — show me past events instead",
                "description": "Skip manual guidance and base the answer on past similar incidents on this machine.",
                "failure_mode_id": "",
                "specific_label": "",
                "observable_symptom": "",
                "observable_failure_mode": "",
                "keywords": ["none", "neither", "past", "history", "logs"],
                "semantic_text": "None of these — show me past events instead",
                "signal_concepts": [],
                "component_names": [],
                "failure_mode_name": "",
                "is_opt_out": True,
            })
        session["clarification"] = clarification
        session["current_issue_idx"] = 0
        # Always stash past-cases context for the post-clarification turn so
        # the final answer carries the same quantified history regardless of
        # which option the user picks.
        session["_past_cases_summary_pending"] = (
            past_cases_summary.model_dump() if past_cases_summary else None
        )
        session["_past_matches_pending"] = past_matches
        # For hybrid intent: stash the history appendix in session so it can
        # be appended to the final answer once the user resolves the
        # clarification, instead of cluttering the clarification prompt.
        if intent == "hybrid_diagnosis_with_history" and (hybrid_appendix or hybrid_evidence):
            session["_hybrid_intent_pending"] = intent
            session["_hybrid_appendix_pending"] = hybrid_appendix
            session["_hybrid_evidence_pending"] = hybrid_evidence
        elif requested_behavior_mode == SOLVE_CURRENT_PROBLEM and hybrid_evidence:
            session["_hybrid_intent_pending"] = intent
            session["_hybrid_appendix_pending"] = ""
            session["_hybrid_evidence_pending"] = hybrid_evidence
        response = _build_clarification_response(
            instance_id=instance_id,
            session_id=session_id,
            session=session,
            reply=str(clarification["question"]),
        )
        timer.mark("response_build")
        _apply_intent(response, intent)
        return _finish_chat_response(
            instance_id=instance_id,
            session_id=session_id,
            user_message=message,
            response=response,
            timer=timer,
            branch="clarification_prompt",
            intent=intent,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
            intent_source=intent_source,
            intent_fallback_used=intent_fallback_used,
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
    if mode == "non-fast":
        try:
            composed_reply = compose_prioritized_solve_answer(
                paths=first_group,
                user_message=message,
                log_evidence=hybrid_evidence,
                model=chat_model,
                product_meta=product_meta,
            )
            if composed_reply:
                reply = composed_reply
                # The guided answer already folds past-event evidence into the
                # prioritisation. Keep evidence in the payload, but avoid a
                # duplicated appendix in the visible reply.
                hybrid_appendix = ""
            timer.mark("solve_compose")
        except Exception:
            logger.exception("Failed to compose non-fast solve-current answer")
            timer.mark("solve_compose_failed")
    # Always quantify the past history in the reply when we have it. The
    # deterministic block carries occurrence + outcome counts that the LLM
    # narrative does not produce reliably.
    past_block = _past_resolution_template(past_cases_summary) if past_cases_summary else ""
    if past_block:
        reply += "\n" + past_block
    if total > 1:
        reply += f"\n\n---\n*Possible cause 1 of {total}. Use \"Next\" to see the next most likely cause.*"
    timer.mark("response_render")
    telemetry = build_telemetry_payload(first_group, telemetry_dir=_telemetry_dir(instance_id))
    timer.mark("telemetry")

    response = _build_chat_response(
        instance_id=instance_id,
        session_id=session_id,
        reply=reply,
        trace=trace,
        group_paths=first_group,
        has_more_issues=total > 1,
        issue_number=1,
        total_issues=total,
        telemetry=telemetry,
    )
    timer.mark("response_build")
    _apply_intent(response, intent, hybrid_appendix, hybrid_evidence, past_cases_summary)
    return _finish_chat_response(
        instance_id=instance_id,
        session_id=session_id,
        user_message=message,
        response=response,
        timer=timer,
        branch="troubleshooting_current",
        intent=intent,
        mode=mode,
        memory=memory,
        product_meta=product_meta,
        intent_source=intent_source,
        intent_fallback_used=intent_fallback_used,
    )


@router.post("/instances/{instance_id}/next-issue", response_model=ChatResponse)
async def next_issue(instance_id: str, req: NextIssueRequest):
    if not req.session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")

    mode = _resolve_chat_mode(req)
    chat_model = _chat_model_for_mode(mode)
    _load_instance_ontology(instance_id)
    product_meta = _product_metadata.get(instance_id, {})
    memory = _load_conversation_memory(instance_id, req.session_id, product_meta)
    session = _sessions.get(_session_key(instance_id, req.session_id))
    if not session or not session.get("ranked_issues"):
        response = _build_chat_response(
            instance_id=instance_id,
            reply="No more issues to show. Please describe a new problem.",
            session_id=req.session_id,
        )
        return _finish_next_issue_response(
            instance_id=instance_id,
            session_id=req.session_id,
            response=response,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
        )
    if _active_clarification(session):
        response = _build_clarification_response(
            instance_id=instance_id,
            session_id=req.session_id,
            session=session,
            reply=str(session["clarification"].get("question", "Please answer the clarification question first.")),
        )
        return _finish_next_issue_response(
            instance_id=instance_id,
            session_id=req.session_id,
            response=response,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
        )

    ranked = session["ranked_issues"]
    next_idx = session["current_issue_idx"] + 1

    if next_idx >= len(ranked):
        response = _build_chat_response(
            instance_id=instance_id,
            reply="Those were all the possible causes I found. If the problem persists, please describe it in more detail.",
            session_id=req.session_id,
        )
        return _finish_next_issue_response(
            instance_id=instance_id,
            session_id=req.session_id,
            response=response,
            mode=mode,
            memory=memory,
            product_meta=product_meta,
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

    response = _build_chat_response(
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
    return _finish_next_issue_response(
        instance_id=instance_id,
        session_id=req.session_id,
        response=response,
        mode=mode,
        memory=memory,
        product_meta=product_meta,
    )


@router.post("/instances/{instance_id}/reset")
async def reset_session(instance_id: str, req: ResetRequest):
    if req.session_id:
        _sessions.pop(_session_key(instance_id, req.session_id), None)
        chat_log_store.delete_conversation_memory(instance_id, req.session_id)
    return {"ok": True}


@router.post(
    "/instances/{instance_id}/past-cases-analysis",
    response_model=PastCasesAnalysisResponse,
)
async def past_cases_analysis(instance_id: str, req: PastCasesAnalysisRequest):
    """Lazy-fetched markdown analysis derived from the top-K basis logs of a
    past-cases search. Called when the operator opens the log navigator from
    the past-cases card. The body carries the same ``query`` + ``log_ids``
    that were sent back to the client on the originating chat response, so
    the endpoint is stateless and the LLM cost is only paid on demand."""
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    log_ids = [lid for lid in (req.log_ids or []) if isinstance(lid, str) and lid]
    rows = basis_rows_from_log_ids(log_ids, instance_id)
    if not rows:
        return PastCasesAnalysisResponse(analysis_markdown="", log_ids_used=[])
    analysis = await asyncio.to_thread(
        compose_past_cases_expanded_analysis, query, rows, OPENAI_CHAT_MODEL,
    )
    used = [str(row.get("log_id") or "") for row in rows if row.get("log_id")]
    return PastCasesAnalysisResponse(analysis_markdown=analysis, log_ids_used=used)


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
    _load_instance_ontology(instance_id)
    meta = _product_metadata.get(instance_id, {})
    chips = [
        {
            "label": "Fix now",
            "query": "Robot brake voltage too low, how do I fix it?",
        },
        {
            "label": "Past drive cases",
            "query": "Show me past drive motor overtemperature cases",
        },
        {
            "label": "Past network cases",
            "query": "Show me past Ethernet packet loss cases",
        },
        {
            "label": "Repeated events",
            "query": "Which IRC5 component has the most repeated events?",
        },
        {
            "label": "Work order",
            "query": "Show me details for work order WO-IRC5-1042",
        },
        {
            "label": "Symptom + history",
            "query": "FlexPendant just disconnected - has this happened before and how was it fixed?",
        },
    ]
    return {**meta, "suggested_symptoms": chips}


@router.post("/instances/{instance_id}/reload", include_in_schema=True)
async def reload_instance(instance_id: str):
    """Evict all in-memory caches for this instance so the next request
    reloads from disk: ontology + embeddings, telemetry CSV, machine logs
    CSV, and the log sparse index."""
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    evict_instance_cache(instance_id)
    evict_telemetry_cache(telemetry_dir=_telemetry_dir(instance_id))
    evict_log_cache(instance_id)
    evict_search_cache(instance_id)
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


@router.get("/instances/{instance_id}/chat-sessions", response_model=ChatSessionsResponse)
async def get_chat_sessions(instance_id: str, limit: int = Query(default=50, ge=1, le=200)):
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    sessions = chat_log_store.get_sessions(instance_id, limit=limit)
    return ChatSessionsResponse(sessions=sessions)


@router.get("/instances/{instance_id}/chat-sessions/{session_id}", response_model=ChatSessionMessagesResponse)
async def get_chat_session_messages(instance_id: str, session_id: str):
    inst = instance_store.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    messages = chat_log_store.get_session_messages(instance_id, session_id)
    return ChatSessionMessagesResponse(messages=messages)
