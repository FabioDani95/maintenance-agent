from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from kg_agents.config import DATA_DIR


DB_PATH = DATA_DIR / "interventions.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def init_chat_log_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              instance_id TEXT NOT NULL,
              session_id TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
              content TEXT NOT NULL,
              payload_json TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_logs_instance_session
            ON chat_logs(instance_id, session_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_logs_instance_time
            ON chat_logs(instance_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_memory (
              instance_id TEXT NOT NULL,
              session_id TEXT NOT NULL,
              state_json TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (instance_id, session_id)
            )
            """
        )


def log_user_message(
    *,
    instance_id: str,
    session_id: str,
    content: str,
) -> None:
    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_logs (instance_id, session_id, role, content, payload_json, created_at)
            VALUES (?, ?, 'user', ?, NULL, ?)
            """,
            (instance_id, session_id, content, now),
        )


def log_assistant_message(
    *,
    instance_id: str,
    session_id: str,
    content: str,
    payload: dict[str, Any] | None = None,
) -> None:
    now = _utc_now()
    payload_json = _json_dumps(payload) if payload else None
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_logs (instance_id, session_id, role, content, payload_json, created_at)
            VALUES (?, ?, 'assistant', ?, ?, ?)
            """,
            (instance_id, session_id, content, payload_json, now),
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": row["id"],
        "instance_id": row["instance_id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"],
    }
    if row["payload_json"]:
        d["payload"] = _json_loads(row["payload_json"], None)
    else:
        d["payload"] = None
    return d


def get_sessions(instance_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
              session_id,
              MIN(created_at) AS started_at,
              MAX(created_at) AS last_message_at,
              COUNT(*) AS message_count,
              MIN(CASE WHEN role = 'user' THEN content END) AS first_user_message
            FROM chat_logs
            WHERE instance_id = ?
            GROUP BY session_id
            ORDER BY MAX(created_at) DESC
            LIMIT ?
            """,
            (instance_id, max(1, limit)),
        ).fetchall()
    return [
        {
            "session_id": row["session_id"],
            "started_at": row["started_at"],
            "last_message_at": row["last_message_at"],
            "message_count": int(row["message_count"]),
            "first_user_message": row["first_user_message"] or "",
        }
        for row in rows
    ]


def get_session_messages(
    instance_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM chat_logs
            WHERE instance_id = ? AND session_id = ?
            ORDER BY created_at ASC
            """,
            (instance_id, session_id),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_conversation_memory(instance_id: str, session_id: str) -> dict[str, Any] | None:
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT state_json
                FROM chat_memory
                WHERE instance_id = ? AND session_id = ?
                """,
                (instance_id, session_id),
            ).fetchone()
    except sqlite3.OperationalError:
        init_chat_log_db()
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT state_json
                FROM chat_memory
                WHERE instance_id = ? AND session_id = ?
                """,
                (instance_id, session_id),
            ).fetchone()
    if not row:
        return None
    state = _json_loads(row["state_json"], None)
    return state if isinstance(state, dict) else None


def upsert_conversation_memory(
    *,
    instance_id: str,
    session_id: str,
    state: dict[str, Any],
) -> None:
    now = _utc_now()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_memory (instance_id, session_id, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(instance_id, session_id)
                DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at
                """,
                (instance_id, session_id, _json_dumps(state), now),
            )
    except sqlite3.OperationalError:
        init_chat_log_db()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_memory (instance_id, session_id, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(instance_id, session_id)
                DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at
                """,
                (instance_id, session_id, _json_dumps(state), now),
            )


def delete_conversation_memory(instance_id: str, session_id: str) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """
                DELETE FROM chat_memory
                WHERE instance_id = ? AND session_id = ?
                """,
                (instance_id, session_id),
            )
    except sqlite3.OperationalError:
        init_chat_log_db()
