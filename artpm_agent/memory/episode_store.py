"""Episodic outcome memory — the data foundation for the evolution loop.

An Episode records the observable outcome of a single conversational turn:
which handler produced it, whether it succeeded, the error kind (if any), and
any explicit user feedback. Phase 3's ReflectionJob mines these signals to
improve routing, prompts, and preferences.

This module is fully additive. It reuses SQLiteManager for connection handling
but owns its own ``episodes`` table in a dedicated database file, and does not
touch any existing schema or business logic.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from artpm_agent.memory.sqlite_manager import SQLiteManager
from artpm_agent.utils import generate_uuid
import dataclasses

from artpm_agent.core.redis_cache import (
    get_redis,
    cache_get_json,
    cache_set_json,
    cache_delete_prefix,
    key,
)


@dataclass
class Episode:
    """One observable outcome of a single agent turn."""

    turn_id: str
    conversation_id: str
    handler: str
    success: bool
    error_kind: Optional[str] = None
    user_input_excerpt: str = ""
    feedback: Optional[str] = None
    run_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": generate_uuid(),
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "handler": self.handler,
            "success": int(bool(self.success)),
            "error_kind": self.error_kind,
            "user_input_excerpt": self.user_input_excerpt,
            "feedback": self.feedback,
            "run_id": self.run_id,
            "metadata": json.dumps(self.metadata, ensure_ascii=False),
            "created_at": self.created_at or datetime.now(timezone.utc).isoformat(),
        }


def _row_to_episode(row: sqlite3.Row) -> Episode:
    return Episode(
        turn_id=row["turn_id"],
        conversation_id=row["conversation_id"],
        handler=row["handler"],
        success=bool(row["success"]),
        error_kind=row["error_kind"],
        user_input_excerpt=row["user_input_excerpt"] or "",
        feedback=row["feedback"],
        run_id=row["run_id"] or "",
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        created_at=row["created_at"] or "",
    )


class EpisodeStore:
    """Persist and query per-turn outcome Episodes."""

    def __init__(self, db_path: str):
        # This database is dedicated to episodes; do not install the primary
        # project schema (including legacy tables) into it.
        self.db = SQLiteManager(db_path, initialize_schema=False)
        self.db.remove_empty_primary_schema_scaffold()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    id TEXT PRIMARY KEY,
                    turn_id TEXT,
                    run_id TEXT,
                    conversation_id TEXT,
                    handler TEXT,
                    success INTEGER,
                    error_kind TEXT,
                    user_input_excerpt TEXT,
                    feedback TEXT,
                    metadata TEXT,
                    created_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_handler ON episodes(handler)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_created ON episodes(created_at)"
            )

    def record(self, episode: Episode) -> str:
        """Persist an episode; returns its generated id."""
        row = episode.to_row()
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO episodes (
                    id, turn_id, run_id, conversation_id, handler, success,
                    error_kind, user_input_excerpt, feedback, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["turn_id"],
                    row["run_id"],
                    row["conversation_id"],
                    row["handler"],
                    row["success"],
                    row["error_kind"],
                    row["user_input_excerpt"],
                    row["feedback"],
                    row["metadata"],
                    row["created_at"],
                ),
            )
        self._invalidate_redis_caches()
        return row["id"]

    def _invalidate_redis_caches(self) -> None:
        """Drop Redis-cached episode queries after a write (best-effort)."""
        try:
            cache_delete_prefix(key("ep"))
        except Exception:  # noqa: BLE001
            pass

    def recent(self, limit: int = 50, handler: Optional[str] = None) -> List[Episode]:
        """Return the most recent episodes, optionally filtered by handler."""
        r = get_redis()
        ck = key("ep", "recent", str(limit), handler or "")
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return [Episode(**e) for e in cached]

        with self.db.get_connection() as conn:
            if handler:
                rows = conn.execute(
                    "SELECT * FROM episodes WHERE handler = ? ORDER BY created_at DESC LIMIT ?",
                    (handler, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM episodes ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        result = [_row_to_episode(r) for r in rows]
        if r is not None:
            cache_set_json(ck, [dataclasses.asdict(e) for e in result], ttl=30)
        return result

    def recent_feedback(self, limit: int = 20) -> List[Episode]:
        """Return episodes that carry explicit user feedback."""
        r = get_redis()
        ck = key("ep", "recent_fb", str(limit))
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return [Episode(**e) for e in cached]

        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM episodes WHERE feedback IS NOT NULL AND feedback != '' "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = [_row_to_episode(r) for r in rows]
        if r is not None:
            cache_set_json(ck, [dataclasses.asdict(e) for e in result], ttl=30)
        return result

    def count(self) -> int:
        """Total number of persisted episodes."""
        r = get_redis()
        ck = key("ep", "count")
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return int(cached)

        with self.db.get_connection() as conn:
            n = int(conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0])
        if r is not None:
            cache_set_json(ck, n, ttl=30)
        return n

    def set_feedback(self, turn_id: str, feedback_text: Optional[str]) -> int:
        """Tag every episode of a turn with explicit user feedback.

        Returns the number of rows updated. Used by the chat UI feedback
        buttons (👍 / 👎) so the signal reaches ``ReflectionJob`` (which mines
        ``Episode.feedback``) and closes the evolution loop.
        """
        if not turn_id:
            return 0
        with self.db.get_connection() as conn:
            cur = conn.execute(
                "UPDATE episodes SET feedback = ? WHERE turn_id = ?",
                (feedback_text, turn_id),
            )
            rowcount = cur.rowcount
        self._invalidate_redis_caches()
        return rowcount

    def failure_rate(
        self, handler: Optional[str] = None, since: Optional[str] = None
    ) -> float:
        """Failure rate in [0, 1]; 0.0 when there is no data."""
        r = get_redis()
        ck = key("ep", "failure", handler or "", since or "")
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return float(cached)

        with self.db.get_connection() as conn:
            if handler and since:
                total = conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE handler = ? AND created_at >= ?",
                    (handler, since),
                ).fetchone()[0]
                failed = conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE handler = ? AND success = 0 AND created_at >= ?",
                    (handler, since),
                ).fetchone()[0]
            elif handler:
                total = conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE handler = ?", (handler,)
                ).fetchone()[0]
                failed = conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE handler = ? AND success = 0",
                    (handler,),
                ).fetchone()[0]
            else:
                total = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
                failed = conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE success = 0"
                ).fetchone()[0]
        rate = (failed / total) if total else 0.0
        if r is not None:
            cache_set_json(ck, rate, ttl=30)
        return rate
