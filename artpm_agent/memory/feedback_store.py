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
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from artpm_agent.memory.sqlite_manager import SQLiteManager
from artpm_agent.tenancy import TenantContextManager, WorkspaceAccessDenied
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
    tenant_id: str = "local"
    workspace_id: str = "local-default"
    principal_id: str = ""
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
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "principal_id": self.principal_id,
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
        tenant_id=row["tenant_id"] or "local",
        workspace_id=row["workspace_id"] or "local-default",
        principal_id=row["principal_id"] or "",
        weight=float(row["weight"]),
        active=bool(row["active"]),
        created_at=row["created_at"] or "",
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )


class FeedbackStore:
    """Persist and query user feedback / preferences."""

    def __init__(self, db_path: str):
        # Feedback has an isolated schema and does not need project tables.
        self.db = SQLiteManager(db_path, initialize_schema=False)
        self.db.remove_empty_primary_schema_scaffold()
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
                    tenant_id TEXT NOT NULL DEFAULT 'local',
                    workspace_id TEXT NOT NULL DEFAULT 'local-default',
                    principal_id TEXT NOT NULL DEFAULT '',
                    weight REAL,
                    active INTEGER,
                    created_at TEXT,
                    metadata TEXT
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(feedback)").fetchall()
            }
            for name, definition in (
                ("tenant_id", "TEXT NOT NULL DEFAULT 'local'"),
                ("workspace_id", "TEXT NOT NULL DEFAULT 'local-default'"),
                ("principal_id", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE feedback ADD COLUMN {name} {definition}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_scope ON feedback(scope)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_active ON feedback(active)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_workspace "
                "ON feedback(tenant_id, workspace_id, principal_id, active)"
            )

    @staticmethod
    def _resolve_scope(
        tenant_id: Optional[str],
        workspace_id: Optional[str],
        principal_id: Optional[str],
    ) -> tuple[str, str, str]:
        current = TenantContextManager.get_current()
        if current is not None:
            requested_tenant = str(tenant_id or "").strip()
            requested_workspace = str(workspace_id or "").strip()
            requested_principal = str(principal_id or "").strip()
            if requested_tenant and requested_tenant != current.tenant_id:
                raise WorkspaceAccessDenied("tenant does not match the authenticated context")
            if requested_workspace and requested_workspace != current.workspace_id:
                raise WorkspaceAccessDenied(
                    "workspace does not match the authenticated context"
                )
            if requested_principal and requested_principal != current.principal_id:
                raise WorkspaceAccessDenied(
                    "principal does not match the authenticated context"
                )
            return current.tenant_id, current.workspace_id, current.principal_id
        return (
            str(tenant_id or "local").strip() or "local",
            str(workspace_id or "local-default").strip() or "local-default",
            str(principal_id or "").strip(),
        )

    # ── writes ──
    def add(
        self,
        kind: str,
        content: str,
        *,
        scope: str = "global",
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        principal_id: Optional[str] = None,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Persist a feedback/preference entry; returns its id."""
        resolved_tenant, resolved_workspace, resolved_principal = self._resolve_scope(
            tenant_id, workspace_id, principal_id
        )
        entry = FeedbackEntry(
            kind=kind,
            content=content,
            scope=scope,
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
            principal_id=resolved_principal,
            weight=weight,
            metadata=metadata or {},
        )
        row = entry.to_row()
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO feedback (
                    id, kind, content, scope,
                    tenant_id, workspace_id, principal_id,
                    weight, active, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["kind"],
                    row["content"],
                    row["scope"],
                    row["tenant_id"],
                    row["workspace_id"],
                    row["principal_id"],
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
        self,
        *,
        kind: Optional[str] = None,
        scope: Optional[str] = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        principal_id: Optional[str] = None,
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
        current = TenantContextManager.get_current()
        scoped = current is not None or any(
            value is not None for value in (tenant_id, workspace_id, principal_id)
        )
        if scoped:
            resolved_tenant, resolved_workspace, resolved_principal = self._resolve_scope(
                tenant_id, workspace_id, principal_id
            )
            clauses.extend(
                [
                    "tenant_id = ?",
                    "workspace_id = ?",
                    "(principal_id = ? OR principal_id = '')",
                ]
            )
            params.extend(
                [resolved_tenant, resolved_workspace, resolved_principal]
            )
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
    from artpm_agent.config import resolve_state_path

    return str(resolve_state_path("feedback.db", "ARTPM_FEEDBACK_DB"))


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
