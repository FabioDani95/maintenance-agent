from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kg_agents.config import DATA_DIR
from kg_agents.services import instance_store


DB_PATH = DATA_DIR / "interventions.db"
_OUTCOME_VALUES = ("resolved", "partially_resolved", "not_resolved", "escalated")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS interventions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              instance_id TEXT NOT NULL,
              session_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              started_at TEXT,
              duration_sec INTEGER NOT NULL DEFAULT 0,
              ontology_version TEXT,
              ontology_hash TEXT,
              symptom_ids_json TEXT NOT NULL,
              final_failure_mode_id TEXT NOT NULL,
              final_path_json TEXT NOT NULL,
              path_key TEXT NOT NULL,
              selected_action_id TEXT NOT NULL,
              outcome TEXT NOT NULL CHECK(outcome IN ('resolved', 'partially_resolved', 'not_resolved', 'escalated')),
              user_queries_json TEXT NOT NULL DEFAULT '[]',
              user_feedback TEXT NOT NULL DEFAULT '',
              UNIQUE(instance_id, session_id, path_key, selected_action_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS path_stats (
              instance_id TEXT NOT NULL,
              path_key TEXT NOT NULL,
              symptom_ids_json TEXT NOT NULL,
              final_failure_mode_id TEXT NOT NULL,
              final_path_json TEXT NOT NULL,
              selected_action_id TEXT NOT NULL,
              total_uses INTEGER NOT NULL DEFAULT 0,
              resolved_count INTEGER NOT NULL DEFAULT 0,
              partially_resolved_count INTEGER NOT NULL DEFAULT 0,
              not_resolved_count INTEGER NOT NULL DEFAULT 0,
              escalated_count INTEGER NOT NULL DEFAULT 0,
              total_duration_sec INTEGER NOT NULL DEFAULT 0,
              avg_duration_min REAL NOT NULL DEFAULT 0,
              success_rate_pct REAL NOT NULL DEFAULT 0,
              last_outcome_at TEXT NOT NULL,
              PRIMARY KEY (instance_id, path_key)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_interventions_instance_time
            ON interventions(instance_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_interventions_path
            ON interventions(instance_id, path_key)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_interventions_fm
            ON interventions(instance_id, final_failure_mode_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_interventions_action
            ON interventions(instance_id, selected_action_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_interventions_outcome
            ON interventions(instance_id, outcome)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_path_stats_fm
            ON path_stats(instance_id, final_failure_mode_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_path_stats_action
            ON path_stats(instance_id, selected_action_id)
            """
        )


def build_path_key(path_node_ids: list[str]) -> str:
    return ">".join(node_id for node_id in path_node_ids if node_id)


def build_path_node_ids(path: dict[str, Any]) -> list[str]:
    node_ids = [
        path.get("symptom_id", ""),
        path.get("failure_mode_id", ""),
        path.get("action_id", ""),
    ]
    return [node_id for node_id in node_ids if node_id]


def get_ontology_fingerprint(instance_id: str) -> tuple[str | None, str | None]:
    ont_path = instance_store.get_ontology_path(instance_id)
    if not ont_path.exists():
        return None, None
    raw = ont_path.read_bytes()
    ontology_hash = hashlib.sha1(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, ontology_hash
    version = data.get("metadata", {}).get("version")
    return version, ontology_hash


def _duration_sec(started_at: str | None, ended_at: str) -> int:
    start_dt = _parse_iso_datetime(started_at)
    end_dt = _parse_iso_datetime(ended_at)
    if not start_dt or not end_dt:
        return 0
    return max(0, int((end_dt - start_dt).total_seconds()))


def _stats_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    total_uses = int(row["total_uses"] or 0)
    resolved_count = int(row["resolved_count"] or 0)
    partially_resolved_count = int(row["partially_resolved_count"] or 0)
    not_resolved_count = int(row["not_resolved_count"] or 0)
    escalated_count = int(row["escalated_count"] or 0)
    total_duration_sec = int(row["total_duration_sec"] or 0)
    success_rate_pct = float(row["success_rate_pct"] or 0.0)
    avg_duration_min = float(row["avg_duration_min"] or 0.0)
    return {
        "instance_id": row["instance_id"],
        "path_key": row["path_key"],
        "symptom_ids": _json_loads(row["symptom_ids_json"], []),
        "final_failure_mode_id": row["final_failure_mode_id"],
        "final_path": _json_loads(row["final_path_json"], []),
        "selected_action_id": row["selected_action_id"],
        "total_uses": total_uses,
        "resolved_count": resolved_count,
        "partially_resolved_count": partially_resolved_count,
        "not_resolved_count": not_resolved_count,
        "escalated_count": escalated_count,
        "total_duration_sec": total_duration_sec,
        "avg_duration_min": avg_duration_min,
        "success_rate_pct": success_rate_pct,
        "last_outcome_at": row["last_outcome_at"],
    }


def _intervention_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "instance_id": row["instance_id"],
        "session_id": row["session_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "duration_sec": int(row["duration_sec"] or 0),
        "ontology_version": row["ontology_version"],
        "ontology_hash": row["ontology_hash"],
        "symptom_ids": _json_loads(row["symptom_ids_json"], []),
        "final_failure_mode_id": row["final_failure_mode_id"],
        "final_path": _json_loads(row["final_path_json"], []),
        "path_key": row["path_key"],
        "selected_action_id": row["selected_action_id"],
        "outcome": row["outcome"],
        "user_queries": _json_loads(row["user_queries_json"], []),
        "user_feedback": row["user_feedback"],
    }


def _refresh_path_stats(
    conn: sqlite3.Connection,
    *,
    instance_id: str,
    path_key: str,
    symptom_ids_json: str,
    final_failure_mode_id: str,
    final_path_json: str,
    selected_action_id: str,
    last_outcome_at: str,
) -> dict[str, Any]:
    aggregate = conn.execute(
        """
        SELECT
          COUNT(*) AS total_uses,
          SUM(CASE WHEN outcome = 'resolved' THEN 1 ELSE 0 END) AS resolved_count,
          SUM(CASE WHEN outcome = 'partially_resolved' THEN 1 ELSE 0 END) AS partially_resolved_count,
          SUM(CASE WHEN outcome = 'not_resolved' THEN 1 ELSE 0 END) AS not_resolved_count,
          SUM(CASE WHEN outcome = 'escalated' THEN 1 ELSE 0 END) AS escalated_count,
          SUM(duration_sec) AS total_duration_sec
        FROM interventions
        WHERE instance_id = ? AND path_key = ?
        """,
        (instance_id, path_key),
    ).fetchone()

    total_uses = int(aggregate["total_uses"] or 0)
    resolved_count = int(aggregate["resolved_count"] or 0)
    partially_resolved_count = int(aggregate["partially_resolved_count"] or 0)
    not_resolved_count = int(aggregate["not_resolved_count"] or 0)
    escalated_count = int(aggregate["escalated_count"] or 0)
    total_duration_sec = int(aggregate["total_duration_sec"] or 0)
    avg_duration_min = round((total_duration_sec / total_uses / 60), 2) if total_uses else 0.0
    success_rate_pct = round((resolved_count / total_uses) * 100, 1) if total_uses else 0.0

    conn.execute(
        """
        INSERT INTO path_stats (
          instance_id,
          path_key,
          symptom_ids_json,
          final_failure_mode_id,
          final_path_json,
          selected_action_id,
          total_uses,
          resolved_count,
          partially_resolved_count,
          not_resolved_count,
          escalated_count,
          total_duration_sec,
          avg_duration_min,
          success_rate_pct,
          last_outcome_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instance_id, path_key) DO UPDATE SET
          symptom_ids_json = excluded.symptom_ids_json,
          final_failure_mode_id = excluded.final_failure_mode_id,
          final_path_json = excluded.final_path_json,
          selected_action_id = excluded.selected_action_id,
          total_uses = excluded.total_uses,
          resolved_count = excluded.resolved_count,
          partially_resolved_count = excluded.partially_resolved_count,
          not_resolved_count = excluded.not_resolved_count,
          escalated_count = excluded.escalated_count,
          total_duration_sec = excluded.total_duration_sec,
          avg_duration_min = excluded.avg_duration_min,
          success_rate_pct = excluded.success_rate_pct,
          last_outcome_at = excluded.last_outcome_at
        """,
        (
            instance_id,
            path_key,
            symptom_ids_json,
            final_failure_mode_id,
            final_path_json,
            selected_action_id,
            total_uses,
            resolved_count,
            partially_resolved_count,
            not_resolved_count,
            escalated_count,
            total_duration_sec,
            avg_duration_min,
            success_rate_pct,
            last_outcome_at,
        ),
    )
    stats_row = conn.execute(
        """
        SELECT *
        FROM path_stats
        WHERE instance_id = ? AND path_key = ?
        """,
        (instance_id, path_key),
    ).fetchone()
    stats = _stats_from_row(stats_row)
    return stats or {
        "instance_id": instance_id,
        "path_key": path_key,
        "symptom_ids": _json_loads(symptom_ids_json, []),
        "final_failure_mode_id": final_failure_mode_id,
        "final_path": _json_loads(final_path_json, []),
        "selected_action_id": selected_action_id,
        "total_uses": total_uses,
        "resolved_count": resolved_count,
        "partially_resolved_count": partially_resolved_count,
        "not_resolved_count": not_resolved_count,
        "escalated_count": escalated_count,
        "total_duration_sec": total_duration_sec,
        "avg_duration_min": avg_duration_min,
        "success_rate_pct": success_rate_pct,
        "last_outcome_at": last_outcome_at,
    }


def record_outcome(
    *,
    instance_id: str,
    session_id: str,
    started_at: str | None,
    symptom_ids: list[str],
    final_failure_mode_id: str,
    final_path: list[str],
    selected_action_id: str,
    outcome: str,
    user_queries: list[str],
    user_feedback: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if outcome not in _OUTCOME_VALUES:
        raise ValueError(f"Unsupported outcome '{outcome}'")

    now = _utc_now()
    duration_sec = _duration_sec(started_at, now)
    ontology_version, ontology_hash = get_ontology_fingerprint(instance_id)
    path_key = build_path_key(final_path)
    symptom_ids_json = _json_dumps(symptom_ids)
    final_path_json = _json_dumps(final_path)
    user_queries_json = _json_dumps(user_queries)

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO interventions (
              instance_id,
              session_id,
              created_at,
              updated_at,
              started_at,
              duration_sec,
              ontology_version,
              ontology_hash,
              symptom_ids_json,
              final_failure_mode_id,
              final_path_json,
              path_key,
              selected_action_id,
              outcome,
              user_queries_json,
              user_feedback
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance_id, session_id, path_key, selected_action_id) DO UPDATE SET
              updated_at = excluded.updated_at,
              started_at = excluded.started_at,
              duration_sec = excluded.duration_sec,
              ontology_version = excluded.ontology_version,
              ontology_hash = excluded.ontology_hash,
              symptom_ids_json = excluded.symptom_ids_json,
              final_failure_mode_id = excluded.final_failure_mode_id,
              final_path_json = excluded.final_path_json,
              outcome = excluded.outcome,
              user_queries_json = excluded.user_queries_json,
              user_feedback = excluded.user_feedback
            """,
            (
                instance_id,
                session_id,
                now,
                now,
                started_at,
                duration_sec,
                ontology_version,
                ontology_hash,
                symptom_ids_json,
                final_failure_mode_id,
                final_path_json,
                path_key,
                selected_action_id,
                outcome,
                user_queries_json,
                user_feedback,
            ),
        )
        row = conn.execute(
            """
            SELECT *
            FROM interventions
            WHERE instance_id = ? AND session_id = ? AND path_key = ? AND selected_action_id = ?
            """,
            (instance_id, session_id, path_key, selected_action_id),
        ).fetchone()
        stats = _refresh_path_stats(
            conn,
            instance_id=instance_id,
            path_key=path_key,
            symptom_ids_json=symptom_ids_json,
            final_failure_mode_id=final_failure_mode_id,
            final_path_json=final_path_json,
            selected_action_id=selected_action_id,
            last_outcome_at=now,
        )

    if row is None:
        raise RuntimeError("Failed to persist intervention outcome")
    return _intervention_from_row(row), stats


def get_path_stats(
    instance_id: str,
    *,
    path_key: str | None = None,
    final_failure_mode_id: str | None = None,
    selected_action_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    clauses = ["instance_id = ?"]
    params: list[Any] = [instance_id]
    if path_key:
        clauses.append("path_key = ?")
        params.append(path_key)
    if final_failure_mode_id:
        clauses.append("final_failure_mode_id = ?")
        params.append(final_failure_mode_id)
    if selected_action_id:
        clauses.append("selected_action_id = ?")
        params.append(selected_action_id)
    params.append(max(1, limit))

    query = f"""
        SELECT *
        FROM path_stats
        WHERE {' AND '.join(clauses)}
        ORDER BY total_uses DESC, success_rate_pct DESC, last_outcome_at DESC
        LIMIT ?
    """
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [stats for row in rows if (stats := _stats_from_row(row)) is not None]


def get_path_stats_map(instance_id: str, path_keys: list[str]) -> dict[str, dict[str, Any]]:
    unique_keys = list(dict.fromkeys(key for key in path_keys if key))
    if not unique_keys:
        return {}
    placeholders = ",".join("?" for _ in unique_keys)
    query = f"""
        SELECT *
        FROM path_stats
        WHERE instance_id = ? AND path_key IN ({placeholders})
    """
    with _connect() as conn:
        rows = conn.execute(query, [instance_id, *unique_keys]).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        stats = _stats_from_row(row)
        if stats:
            result[stats["path_key"]] = stats
    return result


def get_db_path() -> Path:
    return DB_PATH
