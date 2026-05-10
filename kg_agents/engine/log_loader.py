"""Loader for instance-level machine logs.

Reads `instances/<id>/logs/machine_logs.csv` and the matching
`log_embeddings.json`, normalizes types, and caches per-instance.

This module is intentionally read-only and does not import OpenAI — the
search/embedding side lives in `log_search.py`.
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kg_agents.config import DATA_DIR

logger = logging.getLogger(__name__)


_INT_FIELDS = {
    "severity_number",
    "planned_duration_min",
    "actual_duration_min",
    "downtime_min",
}
_FLOAT_FIELDS = {
    "observed_value",
    "threshold_value",
}


def _coerce(row: dict[str, str]) -> dict[str, Any]:
    """Convert empty strings to None and coerce numeric fields."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if v == "":
            out[k] = None
            continue
        if k in _INT_FIELDS:
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                out[k] = None
            continue
        if k in _FLOAT_FIELDS:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                out[k] = None
            continue
        if k == "attributes_json":
            try:
                out[k] = json.loads(v) if v else {}
            except json.JSONDecodeError:
                out[k] = {}
            continue
        if k == "quality_flags":
            out[k] = [f for f in v.split(",") if f]
            continue
        out[k] = v
    return out


@dataclass
class LogStore:
    """In-memory representation of an instance's logs and embeddings."""

    instance_id: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    rows_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    rows_by_signature: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    occurrence_embeddings: dict[str, list[float]] = field(default_factory=dict)
    signature_embeddings: dict[str, list[float]] = field(default_factory=dict)
    signature_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    embedding_model: str | None = None

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def is_empty(self) -> bool:
        return not self.rows


_CACHE: dict[str, LogStore] = {}
_UNAVAILABLE: set[str] = set()


def _logs_dir(instance_id: str) -> Path:
    return DATA_DIR / "instances" / instance_id / "logs"


def evict_log_cache(instance_id: str | None = None) -> None:
    """Drop the cached LogStore so next call reloads from disk."""
    if instance_id is None:
        _CACHE.clear()
        _UNAVAILABLE.clear()
        return
    _CACHE.pop(instance_id, None)
    _UNAVAILABLE.discard(instance_id)


def load_log_store(instance_id: str) -> LogStore | None:
    """Return the LogStore for an instance, or None if no logs are configured.

    A missing CSV is treated as "logs disabled for this instance" (logged once,
    cached), not as an error — so the rest of the chat flow keeps working.
    """
    if instance_id in _UNAVAILABLE:
        return None
    if instance_id in _CACHE:
        return _CACHE[instance_id]

    logs_dir = _logs_dir(instance_id)
    csv_path = logs_dir / "machine_logs.csv"
    emb_path = logs_dir / "log_embeddings.json"

    if not csv_path.exists():
        logger.info(
            "Logs CSV not found at %s — log search disabled for instance %s",
            csv_path, instance_id,
        )
        _UNAVAILABLE.add(instance_id)
        return None

    rows: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            rows.append(_coerce(raw))

    rows_by_id = {r["log_id"]: r for r in rows if r.get("log_id")}
    rows_by_signature: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        sig = r.get("event_signature_id") or "_unsignatured"
        rows_by_signature.setdefault(sig, []).append(r)
    for sig in rows_by_signature:
        rows_by_signature[sig].sort(
            key=lambda r: r.get("occurred_at") or "", reverse=True
        )

    occurrence_embeddings: dict[str, list[float]] = {}
    signature_embeddings: dict[str, list[float]] = {}
    signature_meta: dict[str, dict[str, Any]] = {}
    embedding_model: str | None = None
    if emb_path.exists():
        with emb_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        embedding_model = payload.get("model")
        occurrence_embeddings = payload.get("occurrences", {}) or {}
        for sig_id, sig_obj in (payload.get("signatures", {}) or {}).items():
            signature_embeddings[sig_id] = sig_obj.get("embedding", [])
            signature_meta[sig_id] = {
                k: v for k, v in sig_obj.items() if k != "embedding"
            }
    else:
        logger.warning(
            "Log embeddings missing at %s — dense log search will return no matches",
            emb_path,
        )

    store = LogStore(
        instance_id=instance_id,
        rows=rows,
        rows_by_id=rows_by_id,
        rows_by_signature=rows_by_signature,
        occurrence_embeddings=occurrence_embeddings,
        signature_embeddings=signature_embeddings,
        signature_meta=signature_meta,
        embedding_model=embedding_model,
    )
    _CACHE[instance_id] = store
    logger.info(
        "Loaded %d log rows (%d signatures) for instance %s",
        len(rows), len(rows_by_signature), instance_id,
    )
    return store


def get_row(instance_id: str, log_id: str) -> dict[str, Any] | None:
    store = load_log_store(instance_id)
    if store is None:
        return None
    return store.rows_by_id.get(log_id)


def get_signature_occurrences(instance_id: str, signature_id: str) -> list[dict[str, Any]]:
    store = load_log_store(instance_id)
    if store is None:
        return []
    return list(store.rows_by_signature.get(signature_id, []))
