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
from artpm_agent.tenancy import TenantContextManager, WorkspaceAccessDenied
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
    tenant_id: str = ""
    workspace_id: str = ""
    principal_id: str = ""

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": generate_uuid(),
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "handler": self.handler,
            "success": int(bool(self.success)),
            "tenant_id": self.tenant_id or "local",
            "workspace_id": self.workspace_id or "local-default",
            "principal_id": self.principal_id or "",
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
        tenant_id=row["tenant_id"] or "local",
        workspace_id=row["workspace_id"] or "local-default",
        principal_id=row["principal_id"] or "",
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
                    tenant_id TEXT NOT NULL DEFAULT 'local',
                    workspace_id TEXT NOT NULL DEFAULT 'local-default',
                    principal_id TEXT NOT NULL DEFAULT '',
                    error_kind TEXT,
                    user_input_excerpt TEXT,
                    feedback TEXT,
                    metadata TEXT,
                    created_at TEXT
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(episodes)").fetchall()
            }
            for name, definition in (
                ("tenant_id", "TEXT NOT NULL DEFAULT 'local'"),
                ("workspace_id", "TEXT NOT NULL DEFAULT 'local-default'"),
                ("principal_id", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE episodes ADD COLUMN {name} {definition}"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_handler ON episodes(handler)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_created ON episodes(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_scope "
                "ON episodes(tenant_id, workspace_id, principal_id, created_at)"
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
                raise WorkspaceAccessDenied(
                    "tenant does not match the authenticated context"
                )
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

    @classmethod
    def _query_scope(
        cls,
        tenant_id: Optional[str],
        workspace_id: Optional[str],
        principal_id: Optional[str],
        *,
        all_principals: bool = False,
    ) -> Optional[tuple[str, str, str]]:
        if all_principals:
            resolved_tenant, resolved_workspace, _ = cls._resolve_scope(
                tenant_id, workspace_id, None
            )
            return resolved_tenant, resolved_workspace, ""
        current = TenantContextManager.get_current()
        if current is None and all(
            value is None for value in (tenant_id, workspace_id, principal_id)
        ):
            return None
        return cls._resolve_scope(tenant_id, workspace_id, principal_id)

    @staticmethod
    def _scope_clauses(
        scope: Optional[tuple[str, str, str]],
    ) -> tuple[list[str], list[Any]]:
        if scope is None:
            return [], []
        tenant_id, workspace_id, principal_id = scope
        clauses = ["tenant_id = ?", "workspace_id = ?"]
        params: list[Any] = [tenant_id, workspace_id]
        if principal_id:
            clauses.append("principal_id = ?")
            params.append(principal_id)
        return clauses, params

    def record(self, episode: Episode) -> str:
        """Persist an episode; returns its generated id."""
        row = episode.to_row()
        tenant_id, workspace_id, principal_id = self._resolve_scope(
            episode.tenant_id,
            episode.workspace_id,
            episode.principal_id,
        )
        row.update(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=principal_id,
        )
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO episodes (
                    id, turn_id, run_id, conversation_id, handler, success,
                    tenant_id, workspace_id, principal_id, error_kind,
                    user_input_excerpt, feedback, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["turn_id"],
                    row["run_id"],
                    row["conversation_id"],
                    row["handler"],
                    row["success"],
                    row["tenant_id"],
                    row["workspace_id"],
                    row["principal_id"],
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

    def recent(
        self,
        limit: int = 50,
        handler: Optional[str] = None,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        principal_id: Optional[str] = None,
        all_principals: bool = False,
    ) -> List[Episode]:
        """Return the most recent episodes, optionally filtered by handler."""
        scope = self._query_scope(
            tenant_id,
            workspace_id,
            principal_id,
            all_principals=all_principals,
        )
        scope_clauses, scope_params = self._scope_clauses(scope)
        r = get_redis()
        ck = key(
            "ep",
            "recent",
            str(limit),
            handler or "",
            *(scope or ("*", "*", "*")),
        )
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return [Episode(**e) for e in cached]

        with self.db.get_connection() as conn:
            clauses = list(scope_clauses)
            params = list(scope_params)
            if handler:
                clauses.append("handler = ?")
                params.append(handler)
            params.append(limit)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM episodes{where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        result = [_row_to_episode(r) for r in rows]
        if r is not None:
            cache_set_json(ck, [dataclasses.asdict(e) for e in result], ttl=30)
        return result

    def recent_feedback(
        self,
        limit: int = 20,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        principal_id: Optional[str] = None,
        all_principals: bool = False,
    ) -> List[Episode]:
        """Return episodes that carry explicit user feedback."""
        scope = self._query_scope(
            tenant_id,
            workspace_id,
            principal_id,
            all_principals=all_principals,
        )
        scope_clauses, scope_params = self._scope_clauses(scope)
        r = get_redis()
        ck = key(
            "ep",
            "recent_fb",
            str(limit),
            *(scope or ("*", "*", "*")),
        )
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return [Episode(**e) for e in cached]

        with self.db.get_connection() as conn:
            clauses = ["feedback IS NOT NULL", "feedback != ''"]
            clauses.extend(scope_clauses)
            params = list(scope_params)
            params.append(limit)
            rows = conn.execute(
                "SELECT * FROM episodes WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        result = [_row_to_episode(r) for r in rows]
        if r is not None:
            cache_set_json(ck, [dataclasses.asdict(e) for e in result], ttl=30)
        return result

    def count(
        self,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        principal_id: Optional[str] = None,
        all_principals: bool = False,
    ) -> int:
        """Total number of persisted episodes."""
        scope = self._query_scope(
            tenant_id,
            workspace_id,
            principal_id,
            all_principals=all_principals,
        )
        scope_clauses, scope_params = self._scope_clauses(scope)
        r = get_redis()
        ck = key("ep", "count", *(scope or ("*", "*", "*")))
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return int(cached)

        with self.db.get_connection() as conn:
            where = " WHERE " + " AND ".join(scope_clauses) if scope_clauses else ""
            n = int(
                conn.execute(
                    "SELECT COUNT(*) FROM episodes" + where,
                    scope_params,
                ).fetchone()[0]
            )
        if r is not None:
            cache_set_json(ck, n, ttl=30)
        return n

    def set_feedback(
        self,
        turn_id: str,
        feedback_text: Optional[str],
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        principal_id: Optional[str] = None,
    ) -> int:
        """Tag every episode of a turn with explicit user feedback.

        Returns the number of rows updated. Used by the chat UI feedback
        buttons (👍 / 👎) so the signal reaches ``ReflectionJob`` (which mines
        ``Episode.feedback``) and closes the evolution loop.
        """
        if not turn_id:
            return 0
        scope = self._query_scope(tenant_id, workspace_id, principal_id)
        scope_clauses, scope_params = self._scope_clauses(scope)
        clauses = ["turn_id = ?"] + scope_clauses
        with self.db.get_connection() as conn:
            cur = conn.execute(
                "UPDATE episodes SET feedback = ? WHERE "
                + " AND ".join(clauses),
                [feedback_text, turn_id] + scope_params,
            )
            rowcount = cur.rowcount
        self._invalidate_redis_caches()
        return rowcount

    def failure_rate(
        self,
        handler: Optional[str] = None,
        since: Optional[str] = None,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        principal_id: Optional[str] = None,
        all_principals: bool = False,
    ) -> float:
        """Failure rate in [0, 1]; 0.0 when there is no data."""
        scope = self._query_scope(
            tenant_id,
            workspace_id,
            principal_id,
            all_principals=all_principals,
        )
        scope_clauses, scope_params = self._scope_clauses(scope)
        r = get_redis()
        ck = key(
            "ep",
            "failure",
            handler or "",
            since or "",
            *(scope or ("*", "*", "*")),
        )
        if r is not None:
            cached = cache_get_json(ck)
            if cached is not None:
                return float(cached)

        with self.db.get_connection() as conn:
            clauses = list(scope_clauses)
            params = list(scope_params)
            if handler:
                clauses.append("handler = ?")
                params.append(handler)
            if since:
                clauses.append("created_at >= ?")
                params.append(since)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            failed_where = (
                where + (" AND " if where else " WHERE ") + "success = 0"
            )
            total = conn.execute(
                "SELECT COUNT(*) FROM episodes" + where,
                params,
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM episodes" + failed_where,
                params,
            ).fetchone()[0]
        rate = (failed / total) if total else 0.0
        if r is not None:
            cache_set_json(ck, rate, ttl=30)
        return rate
