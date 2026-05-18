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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kg_agents.config import DATA_DIR, OPENAI_EMBEDDING_MODEL

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
_SIGNATURE_RE = re.compile(r"^irc5_[a-z0-9_]{2,75}$")


def _is_canonical_signature_id(value: Any) -> bool:
    return bool(_SIGNATURE_RE.match(str(value or "").strip()))


def _canonical_signature_id(row: dict[str, Any]) -> str:
    """Return a stable event signature id for grouping and public payloads.

    Some seeded CSV rows have shifted columns from unescaped commas, so the
    `event_signature_id` cell can contain semantic prose while the canonical
    signature landed in a neighbouring link field. Normalize at load time so
    API clients only see stable ids.
    """
    for key in ("event_signature_id", "linked_failure_mode_id", "linked_symptom_id"):
        candidate = str(row.get(key) or "").strip()
        if _is_canonical_signature_id(candidate):
            return candidate

    event_name = str(row.get("event_name") or "").strip().lower()
    fallback = f"irc5_{event_name}" if event_name else ""
    if _is_canonical_signature_id(fallback):
        return fallback

    return "_unsignatured"


def _normalize_log_row(row: dict[str, Any]) -> dict[str, Any]:
    original_signature = row.get("event_signature_id")
    normalized_signature = _canonical_signature_id(row)
    if normalized_signature != "_unsignatured":
        row["event_signature_id"] = normalized_signature
    elif not _is_canonical_signature_id(original_signature):
        row["event_signature_id"] = None
    if original_signature and original_signature != row.get("event_signature_id"):
        row["_raw_event_signature_id"] = original_signature
    linked_fm = str(row.get("linked_failure_mode_id") or "").strip()
    if linked_fm and not linked_fm.startswith("fm_"):
        row["linked_failure_mode_id"] = None
    linked_symptom = str(row.get("linked_symptom_id") or "").strip()
    if linked_symptom and not linked_symptom.startswith("sym_"):
        row["linked_symptom_id"] = None
    return row


def _coerce(row: dict[str, str]) -> dict[str, Any]:
    """Convert empty strings to None and coerce numeric / structured fields."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k == "quality_flags":
            out[k] = [f for f in (v or "").split(",") if f]
            continue
        if k == "attributes_json":
            if not v:
                out[k] = {}
            else:
                try:
                    out[k] = json.loads(v)
                except json.JSONDecodeError:
                    out[k] = {}
            continue
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
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in csv.DictReader(f):
            rows.append(_normalize_log_row(_coerce({k: v for k, v in raw.items() if k is not None})))

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
        model_mismatch = bool(embedding_model) and embedding_model != OPENAI_EMBEDDING_MODEL
        if model_mismatch:
            # Dense search would produce garbage (or crash on a dimension
            # mismatch) if we mixed a query embedding from one model with
            # stored vectors from another. Drop the dense side; sparse + LLM
            # still work. Signature metadata (counts, dates) is kept.
            logger.warning(
                "Log embeddings at %s were built with model %r but the current "
                "OPENAI_EMBEDDING_MODEL is %r — disabling dense log search for "
                "instance %s. Re-run scripts/embed_logs.py to refresh.",
                emb_path, embedding_model, OPENAI_EMBEDDING_MODEL, instance_id,
            )
        else:
            occurrence_embeddings = payload.get("occurrences", {}) or {}
        for sig_id, sig_obj in (payload.get("signatures", {}) or {}).items():
            normalized_sig_id = _canonical_signature_id({
                "event_signature_id": sig_id,
                "linked_failure_mode_id": sig_obj.get("linked_failure_mode_id"),
                "linked_symptom_id": sig_obj.get("linked_symptom_id"),
                "event_name": "",
            })
            if normalized_sig_id == "_unsignatured":
                continue
            if not model_mismatch:
                signature_embeddings.setdefault(normalized_sig_id, sig_obj.get("embedding", []))
            signature_meta[normalized_sig_id] = {
                k: v for k, v in sig_obj.items() if k != "embedding"
            }
            signature_meta[normalized_sig_id]["event_signature_id"] = normalized_sig_id
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
