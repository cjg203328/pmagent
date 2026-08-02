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
                    ran_at TEXT,
                    episode_count INTEGER
                )
                """
            )

    def last_run(self) -> tuple[Optional[str], int]:
        """Return (last_ran_at_iso, episode_count_at_that_time)."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT ran_at, episode_count FROM reflection_runs "
                "ORDER BY id DESC LIMIT 1"
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
    ) -> bool:
        ran_at, last_count = self.last_run()
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

    def mark_run(self, episode_count: int) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO reflection_runs (ran_at, episode_count) VALUES (?, ?)",
                (datetime.now(timezone.utc).isoformat(), episode_count),
            )

    def run_if_due(
        self,
        episode_store: EpisodeStore,
        feedback_store: FeedbackStore,
        strategy_store: StrategyStore,
        *,
        interval_minutes: int = 30,
        min_new_episodes: int = 5,
        **reflection_kw,
    ) -> Optional[ReflectionReport]:
        """Run reflection only when due; otherwise return None."""
        ran_at, _ = self.last_run()
        current = episode_store.count()
        if not self.should_run(
            interval_minutes=interval_minutes,
            min_new_episodes=min_new_episodes,
            current_episode_count=current,
        ):
            return None
        # A scheduler run is an incremental checkpoint.  Passing the previous
        # run timestamp into the miner prevents old episodes (and their
        # feedback) from being proposed again on every later run.  The first
        # run intentionally keeps ``since=None`` and analyzes the backlog.
        if ran_at:
            reflection_kw["since"] = ran_at
        report = run_reflection(
            episode_store,
            feedback_store,
            strategy_store,
            **reflection_kw,
        )
        self.mark_run(current)
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
