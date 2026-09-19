"""Background worker for asynchronous outbox job execution.

The worker polls `OutboxStore` for pending jobs and executes them outside the
user request path. Each job type maps to a specific handler that replicates
the original synchronous logic from `complete_turn_lifecycle()`.

Design principles:
- Worker runs in a separate process/thread from the main request handler
- Each job execution is wrapped in try/except; failures are logged and retried
- Exponential backoff for failed jobs (60s, 120s, 240s, ...)
- Max retry limit prevents infinite loops on persistent failures

Job type handlers:
- feedback: Write to FeedbackStore
- episode: Write to EpisodeStore
- reflection: Run ReflectionScheduler.run_if_due()
- consolidation: Run ConsolidationScheduler.run_if_due()
- tencentdb_memory: Capture to TencentDB Agent Memory
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, Optional

from artpm_agent.memory.outbox_store import JobType, OutboxJob, OutboxStore, _utc_now

logger = logging.getLogger(__name__)

# Retry policy
MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 60


class OutboxWorker:
    """Background worker that processes outbox jobs."""

    def __init__(
        self,
        outbox_store: OutboxStore,
        *,
        backoff_base_seconds: float = BACKOFF_BASE_SECONDS,
        feedback_store: Optional[Any] = None,
        episode_store: Optional[Any] = None,
        reflection_scheduler: Optional[Any] = None,
        consolidation_scheduler: Optional[Any] = None,
        strategy_store: Optional[Any] = None,
        knowledge_store: Optional[Any] = None,
        tencentdb_memory: Optional[Any] = None,
    ):
        """Initialize worker with required stores and schedulers."""
        if isinstance(backoff_base_seconds, bool):
            raise ValueError("backoff_base_seconds must be a non-negative number")
        try:
            parsed_backoff = float(backoff_base_seconds)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "backoff_base_seconds must be a non-negative number"
            ) from error
        if not math.isfinite(parsed_backoff) or parsed_backoff < 0:
            raise ValueError("backoff_base_seconds must be a non-negative number")
        self.outbox_store = outbox_store
        # Keep production retries conservative while allowing deterministic,
        # immediate retries in tests and local maintenance workers.
        self.backoff_base_seconds = parsed_backoff
        self.feedback_store = feedback_store
        self.episode_store = episode_store
        self.reflection_scheduler = reflection_scheduler
        self.consolidation_scheduler = consolidation_scheduler
        self.strategy_store = strategy_store
        self.knowledge_store = knowledge_store
        self.tencentdb_memory = tencentdb_memory
        self._running = False

    def _execute_feedback_job(self, job: OutboxJob) -> None:
        """Execute a feedback recording job."""
        if self.feedback_store is None:
            raise RuntimeError("FeedbackStore not available")

        payload = job.payload
        self.feedback_store.add(
            kind=payload["kind"],
            content=payload["content"],
            scope=payload.get("scope", "global"),
            tenant_id=job.tenant_id,
            workspace_id=job.workspace_id,
            principal_id=payload.get("principal_id", ""),
        )
        logger.info(
            "Feedback recorded asynchronously",
            extra={"turn_id": job.turn_id, "job_id": job.id},
        )

    def _execute_episode_job(self, job: OutboxJob) -> None:
        """Execute an episode recording job."""
        if self.episode_store is None:
            raise RuntimeError("EpisodeStore not available")

        from artpm_agent.memory.episode_store import Episode

        payload = job.payload
        episode = Episode(
            turn_id=job.turn_id,
            conversation_id=payload["conversation_id"],
            handler=payload["handler"],
            success=payload["success"],
            error_kind=payload.get("error_kind"),
            user_input_excerpt=payload.get("user_input_excerpt", ""),
            feedback=payload.get("feedback"),
            run_id=payload.get("run_id", ""),
            metadata=payload.get("metadata", {}),
            tenant_id=job.tenant_id,
            workspace_id=job.workspace_id,
            principal_id=payload.get("principal_id", ""),
        )

        episode_id = self.episode_store.add(episode)
        logger.info(
            "Episode recorded asynchronously",
            extra={"turn_id": job.turn_id, "episode_id": episode_id},
        )

    def _execute_reflection_job(self, job: OutboxJob) -> None:
        """Execute a reflection scheduler job."""
        if self.reflection_scheduler is None:
            raise RuntimeError("ReflectionScheduler not available")
        if self.episode_store is None or self.feedback_store is None:
            raise RuntimeError("Episode/Feedback stores not available")
        if self.strategy_store is None:
            raise RuntimeError("StrategyStore not available")

        payload = job.payload
        report = self.reflection_scheduler.run_if_due(
            self.episode_store,
            self.feedback_store,
            self.strategy_store,
            tenant_id=job.tenant_id,
            workspace_id=job.workspace_id,
            all_principals=payload.get("all_principals", True),
        )
        logger.info(
            "Reflection ran asynchronously",
            extra={"turn_id": job.turn_id, "ran": report is not None},
        )

    def _execute_consolidation_job(self, job: OutboxJob) -> None:
        """Execute a knowledge consolidation job."""
        if self.consolidation_scheduler is None:
            raise RuntimeError("ConsolidationScheduler not available")
        if self.knowledge_store is None:
            raise RuntimeError("WorkspaceKnowledgeStore not available")

        from artpm_agent.memory.consolidation import ConsolidationService

        service = ConsolidationService(self.knowledge_store)
        report = self.consolidation_scheduler.run_if_due(
            service,
            tenant_id=job.tenant_id,
            workspace_id=job.workspace_id,
        )
        logger.info(
            "Consolidation ran asynchronously",
            extra={"turn_id": job.turn_id, "ran": report is not None},
        )

    def _execute_tencentdb_memory_job(self, job: OutboxJob) -> None:
        """Execute a TencentDB memory capture job."""
        if self.tencentdb_memory is None:
            raise RuntimeError("TencentDB memory not available")

        payload = job.payload
        scope_data = payload.get("scope")
        if scope_data is None:
            logger.warning(
                "TencentDB memory job missing scope", extra={"job_id": job.id}
            )
            return

        # Reconstruct scope object (this is opaque to the worker)
        from artpm_agent.memory.tencentdb_agent_memory import TencentDBAgentMemoryScope

        scope = TencentDBAgentMemoryScope(
            tenant_id=scope_data.get("tenant_id"),
            workspace_id=scope_data.get("workspace_id"),
            principal_id=scope_data.get("user_id"),
            conversation_id=scope_data.get("session_id"),
            agent_id=scope_data.get("agent_id"),
        )

        self.tencentdb_memory.capture(
            payload["user_input"],
            payload["response"],
            scope,
        )
        logger.info(
            "TencentDB memory captured asynchronously",
            extra={"turn_id": job.turn_id},
        )

    def _execute_job(self, job: OutboxJob) -> None:
        """Execute a single job based on its type."""
        handlers: Dict[str, Callable[[OutboxJob], None]] = {
            JobType.FEEDBACK.value: self._execute_feedback_job,
            JobType.EPISODE.value: self._execute_episode_job,
            JobType.REFLECTION.value: self._execute_reflection_job,
            JobType.CONSOLIDATION.value: self._execute_consolidation_job,
            JobType.TENCENTDB_MEMORY.value: self._execute_tencentdb_memory_job,
        }

        handler = handlers.get(job.job_type)
        if handler is None:
            raise ValueError(f"Unknown job type: {job.job_type}")

        handler(job)

    def process_batch(
        self,
        batch_size: int = 10,
        *,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> int:
        """Process a batch of pending jobs. Returns number of jobs processed."""
        jobs = self.outbox_store.claim(
            limit=batch_size,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        processed = 0

        for job in jobs:
            try:
                # Check max attempts
                if job.attempts >= MAX_ATTEMPTS:
                    logger.error(
                        "Job exceeded max attempts; marking as failed",
                        extra={
                            "job_id": job.id,
                            "turn_id": job.turn_id,
                            "job_type": job.job_type,
                            "attempts": job.attempts,
                        },
                    )
                    self.outbox_store.mark_completed(
                        job.id,
                        tenant_id=job.tenant_id,
                        workspace_id=job.workspace_id,
                    )  # Archive as "completed" to avoid infinite retry
                    continue

                self._execute_job(job)
                self.outbox_store.mark_completed(
                    job.id, tenant_id=job.tenant_id, workspace_id=job.workspace_id
                )
                processed += 1

            except Exception as error:  # noqa: BLE001 - worker must not crash on individual job failure
                error_code = error.__class__.__name__
                retry_delay = self.backoff_base_seconds * (2**job.attempts)
                logger.warning(
                    "Outbox job failed; scheduling retry",
                    exc_info=True,
                    extra={
                        "job_id": job.id,
                        "turn_id": job.turn_id,
                        "job_type": job.job_type,
                        "attempts": job.attempts + 1,
                        "retry_after_seconds": retry_delay,
                        "error_code": error_code,
                    },
                )
                if job.attempts + 1 >= MAX_ATTEMPTS:
                    # Do not leave a dead job pending behind a long backoff.
                    # The durable row remains available for audit, while the
                    # worker stops retrying it deterministically.
                    self.outbox_store.mark_completed(
                        job.id,
                        tenant_id=job.tenant_id,
                        workspace_id=job.workspace_id,
                    )
                else:
                    self.outbox_store.mark_failed(
                        job.id,
                        error_code,
                        retry_after_seconds=retry_delay,
                        tenant_id=job.tenant_id,
                        workspace_id=job.workspace_id,
                    )

        return processed

    def run_forever(self, poll_interval_seconds: float = 5.0) -> None:
        """Run the worker loop indefinitely (blocking)."""
        self._running = True
        logger.info(
            "Outbox worker started", extra={"poll_interval": poll_interval_seconds}
        )

        while self._running:
            try:
                processed = self.process_all_scopes()
                if processed > 0:
                    logger.info("Outbox batch processed", extra={"count": processed})
            except Exception:  # noqa: BLE001 - worker must stay alive
                logger.error("Outbox worker batch failed", exc_info=True)

            time.sleep(poll_interval_seconds)

        logger.info("Outbox worker stopped")

    def process_all_scopes(self, batch_size: int = 10) -> int:
        """Claim jobs across known scopes without relying on ambient identity.

        Scope discovery happens inside the same SQLite database and only
        returns identifiers, never payloads. Each actual claim and state update
        remains scope-qualified through ``process_batch``.
        """
        with self.outbox_store.db.get_connection() as connection:
            scopes = connection.execute(
                """
                SELECT DISTINCT tenant_id, workspace_id
                FROM outbox_jobs
                WHERE status = ? AND available_at <= ?
                ORDER BY tenant_id, workspace_id
                """,
                ("pending", _utc_now()),
            ).fetchall()
        return sum(
            self.process_batch(
                batch_size=batch_size,
                tenant_id=str(row["tenant_id"]),
                workspace_id=str(row["workspace_id"]),
            )
            for row in scopes
        )

    def stop(self) -> None:
        """Signal the worker loop to stop."""
        self._running = False
