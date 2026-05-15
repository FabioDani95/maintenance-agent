"""HTTP endpoints for instance-level machine logs.

Routes:
    GET  /v1/kg-agents/instances/{instance_id}/logs/summary
    GET  /v1/kg-agents/instances/{instance_id}/logs/{log_id}
    GET  /v1/kg-agents/instances/{instance_id}/logs
    POST /v1/kg-agents/instances/{instance_id}/log-search

Order matters: the explicit `/logs/summary` and `/logs/{log_id}` paths are
declared before the listing endpoint so FastAPI's path matching does not
swallow them as the dynamic listing route.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from kg_agents.engine.log_loader import get_row, load_log_store
from kg_agents.engine.log_search import search_logs, summarize_logs
from kg_agents.models import (
    LogListResponse,
    LogRecord,
    LogSearchRequest,
    LogSearchResponse,
    LogSummaryResponse,
)
from kg_agents.services import instance_store

router = APIRouter(prefix="/v1/kg-agents", tags=["logs"])


def _ensure_instance(instance_id: str) -> None:
    if not instance_store.get_instance(instance_id):
        raise HTTPException(status_code=404, detail="Instance not found")


def _row_to_record(row: dict) -> LogRecord:
    """Coerce a loader row into the LogRecord pydantic model.

    The loader stores `quality_flags` as list[str] and `attributes_json` as
    dict, while observed/threshold values are already floats or None — so
    pydantic can validate directly. `instance_id` is required, so callers
    must pass rows from `load_log_store(instance_id)`.
    """
    return LogRecord(**row)


@router.get(
    "/instances/{instance_id}/logs/summary",
    response_model=LogSummaryResponse,
)
async def logs_summary(
    instance_id: str,
    event_category: str | None = Query(None),
    maintenance_type: str | None = Query(None),
    status: str | None = Query(None),
    severity_min: int | None = Query(None, ge=0),
    component_id: str | None = Query(None),
    linked_failure_mode_id: str | None = Query(None),
    event_signature_id: str | None = Query(None),
    date_from: str | None = Query(None, description="ISO 8601 lower bound on occurred_at (inclusive)"),
    date_to: str | None = Query(None, description="ISO 8601 upper bound on occurred_at (exclusive when date-only)"),
) -> LogSummaryResponse:
    _ensure_instance(instance_id)
    filters = {
        "event_category": event_category,
        "maintenance_type": maintenance_type,
        "status": status,
        "severity_min": severity_min,
        "component_id": component_id,
        "linked_failure_mode_id": linked_failure_mode_id,
        "event_signature_id": event_signature_id,
        "date_from": date_from,
        "date_to": date_to,
    }
    filters = {k: v for k, v in filters.items() if v is not None}
    summary = summarize_logs(instance_id, filters=filters)
    return LogSummaryResponse(**summary)


@router.get(
    "/instances/{instance_id}/logs/{log_id}",
    response_model=LogRecord,
)
async def log_detail(instance_id: str, log_id: str) -> LogRecord:
    _ensure_instance(instance_id)
    row = get_row(instance_id, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Log not found")
    return _row_to_record(row)


@router.get(
    "/instances/{instance_id}/logs",
    response_model=LogListResponse,
)
async def list_logs(
    instance_id: str,
    q: str | None = Query(None, description="Free-text substring filter on title+body+action_taken"),
    event_category: str | None = Query(None),
    maintenance_type: str | None = Query(None),
    status: str | None = Query(None),
    severity_min: int | None = Query(None, ge=0),
    component_id: str | None = Query(None),
    linked_failure_mode_id: str | None = Query(None),
    event_signature_id: str | None = Query(None),
    date_from: str | None = Query(None, description="ISO 8601 lower bound on occurred_at (inclusive)"),
    date_to: str | None = Query(None, description="ISO 8601 upper bound on occurred_at (inclusive)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> LogListResponse:
    """Structured listing with filters.

    Pure structured filtering (no embeddings, no rerank). Use POST /log-search
    when the user intent is semantic similarity rather than literal filter
    matching.
    """
    _ensure_instance(instance_id)
    store = load_log_store(instance_id)
    if store is None or store.is_empty:
        return LogListResponse(
            instance_id=instance_id, total=0, limit=limit, offset=offset, items=[],
        )

    rows = store.rows

    def passes(row: dict) -> bool:
        if event_category and row.get("event_category") != event_category:
            return False
        if maintenance_type and row.get("maintenance_type") != maintenance_type:
            return False
        if status and row.get("status") != status:
            return False
        if component_id and row.get("component_id") != component_id:
            return False
        if linked_failure_mode_id and row.get("linked_failure_mode_id") != linked_failure_mode_id:
            return False
        if event_signature_id and row.get("event_signature_id") != event_signature_id:
            return False
        if severity_min is not None and (row.get("severity_number") or 0) < severity_min:
            return False
        if date_from and (row.get("occurred_at") or "") < date_from:
            return False
        if date_to and (row.get("occurred_at") or "") > date_to:
            return False
        if q:
            haystack = " ".join(
                str(row.get(k) or "") for k in (
                    "title", "body", "action_taken", "semantic_text",
                    "work_order_id", "error_code", "alarm_code",
                    "component_name_raw", "event_name",
                )
            ).lower()
            if q.lower() not in haystack:
                return False
        return True

    filtered = [r for r in rows if passes(r)]
    filtered.sort(key=lambda r: r.get("occurred_at") or "", reverse=True)
    total = len(filtered)
    page = filtered[offset : offset + limit]

    return LogListResponse(
        instance_id=instance_id,
        total=total,
        limit=limit,
        offset=offset,
        items=[_row_to_record(r) for r in page],
    )


@router.post(
    "/instances/{instance_id}/log-search",
    response_model=LogSearchResponse,
)
async def post_log_search(
    instance_id: str, body: LogSearchRequest,
) -> LogSearchResponse:
    """Hybrid retrieval: dense + sparse + RRF + optional LLM rerank.

    Returns signature-level matches with the top-scoring occurrence as
    `top_match_log` and the most recent occurrence as `most_recent_log`.
    """
    _ensure_instance(instance_id)

    filters = {
        k: getattr(body, k) for k in (
            "date_from", "date_to", "component_id", "linked_failure_mode_id",
            "maintenance_type", "event_category", "event_signature_id",
            "status", "severity_min",
        )
    }
    filters = {k: v for k, v in filters.items() if v is not None}

    result = search_logs(
        query=body.query,
        instance_id=instance_id,
        filters=filters,
        limit=body.limit,
        use_llm_rerank=body.use_llm_rerank,
    )

    return LogSearchResponse(**result)
