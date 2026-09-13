"""Strategy memory — the learned, approval-gated rules that shape behaviour.

A ``Strategy`` is an approved (or pending) rule derived by the evolution loop:
e.g. "when the skill handler fails on vision inputs, fall back to the model
earlier". Strategies are injected into every turn by the
``MemoryRetrievalHook`` and are the durable output of the reflection job.

Fully additive: owns its own ``strategies`` table in a dedicated database file.
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


@dataclass
class Strategy:
    """One learned rule that influences future turns."""

    capability: str                       # handler / skill / "global"
    rule_text: str                       # human-readable instruction
    rationale: str = ""                  # why this strategy exists
    risk: str = "low"                    # low | medium | high | critical
    tenant_id: str = "local"
    workspace_id: str = "local-default"
    active: bool = True
    source: str = "reflection"           # reflection | feedback | manual
    hit_count: int = 0
    id: str = ""
    created_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": self.id or generate_uuid(),
            "capability": self.capability,
            "rule_text": self.rule_text,
            "rationale": self.rationale,
            "risk": self.risk,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "active": int(bool(self.active)),
            "source": self.source,
            "hit_count": int(self.hit_count),
            "created_at": self.created_at or datetime.now(timezone.utc).isoformat(),
            "metadata": json.dumps(self.metadata, ensure_ascii=False),
        }


def _row_to_strategy(row: sqlite3.Row) -> Strategy:
    return Strategy(
        id=row["id"],
        capability=row["capability"],
        rule_text=row["rule_text"],
        rationale=row["rationale"] or "",
        risk=row["risk"] or "low",
        tenant_id=row["tenant_id"] or "local",
        workspace_id=row["workspace_id"] or "local-default",
        active=bool(row["active"]),
        source=row["source"] or "reflection",
        hit_count=int(row["hit_count"]),
        created_at=row["created_at"] or "",
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )


class StrategyStore:
    """Persist and query learned strategies."""

    def __init__(self, db_path: str):
        # Strategies live in a dedicated database; keep unrelated business
        # and legacy tables out of the file.
        self.db = SQLiteManager(db_path, initialize_schema=False)
        self.db.remove_empty_primary_schema_scaffold()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategies (
                    id TEXT PRIMARY KEY,
                    capability TEXT,
                    rule_text TEXT,
                    rationale TEXT,
                    risk TEXT,
                    tenant_id TEXT NOT NULL DEFAULT 'local',
                    workspace_id TEXT NOT NULL DEFAULT 'local-default',
                    active INTEGER,
                    source TEXT,
                    hit_count INTEGER,
                    created_at TEXT,
                    metadata TEXT
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(strategies)").fetchall()
            }
            for name, definition in (
                ("tenant_id", "TEXT NOT NULL DEFAULT 'local'"),
                ("workspace_id", "TEXT NOT NULL DEFAULT 'local-default'"),
            ):
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE strategies ADD COLUMN {name} {definition}"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_strategies_cap ON strategies(capability)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_strategies_active ON strategies(active)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_strategies_workspace "
                "ON strategies(tenant_id, workspace_id, active)"
            )

    @staticmethod
    def _resolve_scope(
        tenant_id: Optional[str], workspace_id: Optional[str]
    ) -> tuple[str, str]:
        current = TenantContextManager.get_current()
        if current is not None:
            requested_tenant = str(tenant_id or "").strip()
            requested_workspace = str(workspace_id or "").strip()
            if requested_tenant and requested_tenant != current.tenant_id:
                raise WorkspaceAccessDenied("tenant does not match the authenticated context")
            if requested_workspace and requested_workspace != current.workspace_id:
                raise WorkspaceAccessDenied(
                    "workspace does not match the authenticated context"
                )
            return current.tenant_id, current.workspace_id
        return (
            str(tenant_id or "local").strip() or "local",
            str(workspace_id or "local-default").strip() or "local-default",
        )

    def add(
        self,
        strategy: Strategy,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> str:
        resolved_tenant, resolved_workspace = self._resolve_scope(
            tenant_id if tenant_id is not None else strategy.tenant_id,
            workspace_id if workspace_id is not None else strategy.workspace_id,
        )
        strategy = Strategy(
            **{
                **strategy.__dict__,
                "tenant_id": resolved_tenant,
                "workspace_id": resolved_workspace,
            }
        )
        row = strategy.to_row()
        with self.db.get_connection() as conn:
            # Reflection can be retried after a timeout or process restart.
            # Treat the same active capability/rule pair as idempotent so one
            # episode cannot inflate the strategy list on every retry.
            existing = conn.execute(
                """
                SELECT id FROM strategies
                WHERE active = 1 AND capability = ? AND rule_text = ?
                  AND tenant_id = ? AND workspace_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    row["capability"],
                    row["rule_text"],
                    row["tenant_id"],
                    row["workspace_id"],
                ),
            ).fetchone()
            if existing is not None:
                return str(existing["id"])
            conn.execute(
                """
                INSERT INTO strategies (
                    id, capability, rule_text, rationale, risk,
                    tenant_id, workspace_id, active,
                    source, hit_count, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["capability"],
                    row["rule_text"],
                    row["rationale"],
                    row["risk"],
                    row["tenant_id"],
                    row["workspace_id"],
                    row["active"],
                    row["source"],
                    row["hit_count"],
                    row["created_at"],
                    row["metadata"],
                ),
            )
        return row["id"]

    def active(
        self,
        *,
        capability: Optional[str] = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> List[Strategy]:
        clauses = ["active = 1"]
        params: List[Any] = []
        if capability:
            clauses.append("(capability = ? OR capability = 'global')")
            params.append(capability)
        current = TenantContextManager.get_current()
        scoped = current is not None or any(
            value is not None for value in (tenant_id, workspace_id)
        )
        if scoped:
            resolved_tenant, resolved_workspace = self._resolve_scope(
                tenant_id, workspace_id
            )
            clauses.extend(["tenant_id = ?", "workspace_id = ?"])
            params.extend([resolved_tenant, resolved_workspace])
        sql = (
            "SELECT * FROM strategies WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC"
        )
        with self.db.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_strategy(r) for r in rows]

    def by_capability(
        self,
        capability: str,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> List[Strategy]:
        return self.active(
            capability=capability,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

    def get(self, strategy_id: str) -> Optional[Strategy]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM strategies WHERE id = ?", (strategy_id,)
            ).fetchone()
        return _row_to_strategy(row) if row else None

    def record_hit(self, strategy_id: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE strategies SET hit_count = hit_count + 1 WHERE id = ?",
                (strategy_id,),
            )

    def deactivate(self, strategy_id: str) -> bool:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                "UPDATE strategies SET active = 0 WHERE id = ?", (strategy_id,)
            )
            return cur.rowcount > 0


_DEFAULT_STORE: Optional["StrategyStore"] = None


def default_strategies_db_path() -> str:
    from artpm_agent.config import resolve_state_path

    return str(resolve_state_path("strategies.db", "ARTPM_STRATEGY_DB"))


def get_default_strategy_store() -> Optional["StrategyStore"]:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is not None:
        return _DEFAULT_STORE
    try:
        _DEFAULT_STORE = StrategyStore(default_strategies_db_path())
    except Exception:  # noqa: BLE001 - must never break startup
        _DEFAULT_STORE = None
    return _DEFAULT_STORE
