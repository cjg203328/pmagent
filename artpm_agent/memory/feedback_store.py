"""Feedback & preference memory — the persistent voice of the user.

Stores explicit user signals (👍 / 👎 / corrections / blacklist items) and
learned preferences that should shape future behaviour. Both the
``MemoryRetrievalHook`` (injected into every turn) and the evolution
``ReflectionJob`` consume this store, so it is the bridge between one user's
correction today and the agent's behaviour tomorrow.

This module is fully additive: it owns its own ``feedback`` table in a
dedicated database file and does not touch any existing schema or logic.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from artpm_agent.memory.sqlite_manager import SQLiteManager
from artpm_agent.utils import generate_uuid


# Kinds of feedback/preference we persist.
KIND_PREFERENCE = "preference"   # a general preference ("prefer concise replies")
KIND_AVOID = "avoid"             # "don't do X"
KIND_CORRECTION = "correction"   # an explicit correction of a past answer
KIND_BLACKLIST = "blacklist"     # hard block a capability/skill/handler


@dataclass
class FeedbackEntry:
    """One persisted user signal or learned preference."""

    kind: str
    content: str
    scope: str = "global"          # global | handler name | skill name | client
    weight: float = 1.0
    active: bool = True
    id: str = ""
    created_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": self.id or generate_uuid(),
            "kind": self.kind,
            "content": self.content,
            "scope": self.scope,
            "weight": float(self.weight),
            "active": int(bool(self.active)),
            "created_at": self.created_at or datetime.now(timezone.utc).isoformat(),
            "metadata": json.dumps(self.metadata, ensure_ascii=False),
        }


def _row_to_entry(row: sqlite3.Row) -> FeedbackEntry:
    return FeedbackEntry(
        id=row["id"],
        kind=row["kind"],
        content=row["content"],
        scope=row["scope"] or "global",
        weight=float(row["weight"]),
        active=bool(row["active"]),
        created_at=row["created_at"] or "",
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )


class FeedbackStore:
    """Persist and query user feedback / preferences."""

    def __init__(self, db_path: str):
        self.db = SQLiteManager(db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    kind TEXT,
                    content TEXT,
                    scope TEXT,
                    weight REAL,
                    active INTEGER,
                    created_at TEXT,
                    metadata TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_scope ON feedback(scope)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_active ON feedback(active)"
            )

    # ── writes ──
    def add(
        self,
        kind: str,
        content: str,
        *,
        scope: str = "global",
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Persist a feedback/preference entry; returns its id."""
        entry = FeedbackEntry(
            kind=kind,
            content=content,
            scope=scope,
            weight=weight,
            metadata=metadata or {},
        )
        row = entry.to_row()
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO feedback (
                    id, kind, content, scope, weight, active, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["kind"],
                    row["content"],
                    row["scope"],
                    row["weight"],
                    row["active"],
                    row["created_at"],
                    row["metadata"],
                ),
            )
        return row["id"]

    def deactivate(self, entry_id: str) -> bool:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                "UPDATE feedback SET active = 0 WHERE id = ?", (entry_id,)
            )
            return cur.rowcount > 0

    def record_hit(self, entry_id: str) -> None:
        """Bump the weight of an entry when it is applied/injected."""
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE feedback SET weight = weight + 0.01 WHERE id = ?", (entry_id,)
            )

    # ── reads ──
    def active(
        self, *, kind: Optional[str] = None, scope: Optional[str] = None
    ) -> List[FeedbackEntry]:
        """Return active entries, optionally filtered by kind / scope."""
        clauses = ["active = 1"]
        params: List[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if scope:
            clauses.append("(scope = ? OR scope = 'global')")
            params.append(scope)
        sql = (
            "SELECT * FROM feedback WHERE "
            + " AND ".join(clauses)
            + " ORDER BY weight DESC, created_at DESC"
        )
        with self.db.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]

    def all_active(self) -> List[FeedbackEntry]:
        return self.active()

    def get(self, entry_id: str) -> Optional[FeedbackEntry]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM feedback WHERE id = ?", (entry_id,)
            ).fetchone()
        return _row_to_entry(row) if row else None


_DEFAULT_STORE: Optional["FeedbackStore"] = None


def default_feedback_db_path() -> str:
    env = os.environ.get("ARTPM_FEEDBACK_DB")
    if env:
        return env
    repo_root = Path(__file__).resolve().parent.parent.parent
    return str(repo_root / "data" / "feedback.db")


def get_default_feedback_store() -> Optional["FeedbackStore"]:
    """Lazily build and cache the default store; None if it cannot be created."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is not None:
        return _DEFAULT_STORE
    try:
        _DEFAULT_STORE = FeedbackStore(default_feedback_db_path())
    except Exception:  # noqa: BLE001 - must never break startup
        _DEFAULT_STORE = None
    return _DEFAULT_STORE
