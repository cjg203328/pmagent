"""Reflection scheduler — throttle reflection so it runs periodically, not per turn.

Reflection mines the whole episode window and writes to the strategy/feedback
stores; running it on every turn would be wasteful and could over-fit. The
scheduler only allows a run when both:

    * at least ``interval_minutes`` have passed since the last run, AND
    * at least ``min_new_episodes`` new episodes have been recorded since then.

Fully additive: owns its own ``reflection_runs`` table in a dedicated db file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from artpm_agent.memory.sqlite_manager import SQLiteManager
from artpm_agent.memory.episode_store import EpisodeStore
from artpm_agent.memory.feedback_store import FeedbackStore
from artpm_agent.evolution.strategy_store import StrategyStore
from artpm_agent.evolution.reflection import ReflectionReport, run_reflection
from artpm_agent.tenancy import TenantContextManager, WorkspaceAccessDenied


class ReflectionScheduler:
    def __init__(self, db_path: Optional[str] = None):
        self.db = SQLiteManager(
            db_path or default_reflection_db_path(), initialize_schema=False
        )
        self.db.remove_empty_primary_schema_scaffold()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reflection_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL DEFAULT 'local',
                    workspace_id TEXT NOT NULL DEFAULT 'local-default',
                    ran_at TEXT,
                    episode_count INTEGER
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(reflection_runs)"
                ).fetchall()
            }
            for name, definition in (
                ("tenant_id", "TEXT NOT NULL DEFAULT 'local'"),
                ("workspace_id", "TEXT NOT NULL DEFAULT 'local-default'"),
            ):
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE reflection_runs ADD COLUMN {name} {definition}"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reflection_runs_scope "
                "ON reflection_runs(tenant_id, workspace_id, id)"
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
                raise WorkspaceAccessDenied(
                    "tenant does not match the authenticated context"
                )
            if requested_workspace and requested_workspace != current.workspace_id:
                raise WorkspaceAccessDenied(
                    "workspace does not match the authenticated context"
                )
            return current.tenant_id, current.workspace_id
        return (
            str(tenant_id or "local").strip() or "local",
            str(workspace_id or "local-default").strip() or "local-default",
        )

    def last_run(
        self,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> tuple[Optional[str], int]:
        """Return (last_ran_at_iso, episode_count_at_that_time)."""
        resolved_tenant, resolved_workspace = self._resolve_scope(
            tenant_id, workspace_id
        )
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT ran_at, episode_count FROM reflection_runs "
                "WHERE tenant_id = ? AND workspace_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (resolved_tenant, resolved_workspace),
            ).fetchone()
        if not row:
            return None, 0
        return row["ran_at"], int(row["episode_count"] or 0)

    def should_run(
        self,
        *,
        interval_minutes: int = 30,
        min_new_episodes: int = 5,
        current_episode_count: int = 0,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> bool:
        ran_at, last_count = self.last_run(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if ran_at is None:
            return True
        try:
            last = datetime.fromisoformat(ran_at)
        except ValueError:
            return True
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        if elapsed < interval_minutes * 60:
            return False
        return (current_episode_count - last_count) >= min_new_episodes

    def mark_run(
        self,
        episode_count: int,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> None:
        resolved_tenant, resolved_workspace = self._resolve_scope(
            tenant_id, workspace_id
        )
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO reflection_runs "
                "(tenant_id, workspace_id, ran_at, episode_count) "
                "VALUES (?, ?, ?, ?)",
                (
                    resolved_tenant,
                    resolved_workspace,
                    datetime.now(timezone.utc).isoformat(),
                    episode_count,
                ),
            )

    def run_if_due(
        self,
        episode_store: EpisodeStore,
        feedback_store: FeedbackStore,
        strategy_store: StrategyStore,
        *,
        interval_minutes: int = 30,
        min_new_episodes: int = 5,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        all_principals: bool = True,
        **reflection_kw,
    ) -> Optional[ReflectionReport]:
        """Run reflection only when due; otherwise return None."""
        resolved_tenant, resolved_workspace = self._resolve_scope(
            tenant_id, workspace_id
        )
        ran_at, _ = self.last_run(
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
        )
        current = episode_store.count(
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
            all_principals=all_principals,
        )
        if not self.should_run(
            interval_minutes=interval_minutes,
            min_new_episodes=min_new_episodes,
            current_episode_count=current,
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
        ):
            return None
        # A scheduler run is an incremental checkpoint.  Passing the previous
        # run timestamp into the miner prevents old episodes (and their
        # feedback) from being proposed again on every later run.  The first
        # run intentionally keeps ``since=None`` and analyzes the backlog.
        if ran_at:
            reflection_kw["since"] = ran_at
        reflection_kw.setdefault("tenant_id", resolved_tenant)
        reflection_kw.setdefault("workspace_id", resolved_workspace)
        reflection_kw.setdefault("all_principals", all_principals)
        report = run_reflection(
            episode_store,
            feedback_store,
            strategy_store,
            **reflection_kw,
        )
        self.mark_run(
            current,
            tenant_id=resolved_tenant,
            workspace_id=resolved_workspace,
        )
        return report


_DEFAULT_SCHEDULER: Optional["ReflectionScheduler"] = None


def default_reflection_db_path() -> str:
    from artpm_agent.config import resolve_state_path

    return str(resolve_state_path("reflection.db", "ARTPM_REFLECTION_DB"))


def get_default_scheduler() -> Optional["ReflectionScheduler"]:
    global _DEFAULT_SCHEDULER
    if _DEFAULT_SCHEDULER is not None:
        return _DEFAULT_SCHEDULER
    try:
        _DEFAULT_SCHEDULER = ReflectionScheduler(default_reflection_db_path())
    except Exception:  # noqa: BLE001
        _DEFAULT_SCHEDULER = None
    return _DEFAULT_SCHEDULER
