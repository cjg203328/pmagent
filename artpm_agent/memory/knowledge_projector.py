"""Lease-based projector for the derived workspace vector index."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4


class KnowledgeVectorProjector:
    """Claim durable index work and project it exactly once per active lease."""

    def __init__(
        self,
        store: Any,
        *,
        worker_id: str | None = None,
        lease_seconds: float = 30.0,
        max_attempts: int = 5,
        base_backoff_seconds: float = 0.1,
        max_backoff_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if base_backoff_seconds < 0 or max_backoff_seconds < base_backoff_seconds:
            raise ValueError("invalid retry backoff")
        self.store = store
        self.worker_id = worker_id or f"projector-{uuid4().hex}"
        self.lease_seconds = float(lease_seconds)
        self.max_attempts = int(max_attempts)
        self.base_backoff_seconds = float(base_backoff_seconds)
        self.max_backoff_seconds = float(max_backoff_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    def _now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    def metrics(self) -> dict[str, Any]:
        """Return queue depth, dead-letter count, and oldest unresolved lag."""
        now = self._now()
        with self.store._connection() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count, MIN(created_at) AS oldest,
                       MIN(CASE WHEN status = 'pending' THEN available_at END) AS next_retry
                FROM knowledge_index_outbox
                WHERE status != 'done'
                GROUP BY status
                """
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        oldest_values = [str(row["oldest"]) for row in rows if row["oldest"]]
        next_retries = [str(row["next_retry"]) for row in rows if row["next_retry"]]
        lag_seconds = 0.0
        if oldest_values:
            try:
                oldest = datetime.fromisoformat(min(oldest_values))
                if oldest.tzinfo is None:
                    oldest = oldest.replace(tzinfo=timezone.utc)
                lag_seconds = max(0.0, (now - oldest).total_seconds())
            except ValueError:
                lag_seconds = 0.0
        pending = counts.get("pending", 0)
        processing = counts.get("processing", 0)
        dead = counts.get("dead", 0)
        return {
            "pending": pending + processing,
            "queued": pending,
            "processing": processing,
            "dead": dead,
            "unresolved": pending + processing + dead,
            "oldest_lag_seconds": round(lag_seconds, 6),
            "next_retry_at": min(next_retries) if next_retries else None,
        }

    def _claim(self, *, limit: int) -> list[Any]:
        now = self._now()
        now_text = self._timestamp(now)
        lease_expires = self._timestamp(now + timedelta(seconds=self.lease_seconds))
        with self.store._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE knowledge_index_outbox
                SET status = 'pending', lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE status = 'processing' AND lease_expires_at <= ?
                """,
                (now_text, now_text),
            )
            rows = connection.execute(
                """
                SELECT id, tenant_id, workspace_id, resource_id, operation, attempts
                FROM knowledge_index_outbox
                WHERE status = 'pending' AND available_at <= ?
                ORDER BY id
                LIMIT ?
                """,
                (now_text, limit),
            ).fetchall()
            if not rows:
                return []
            row_ids = [int(row["id"]) for row in rows]
            placeholders = ", ".join("?" for _ in row_ids)
            connection.execute(
                f"""
                UPDATE knowledge_index_outbox
                SET status = 'processing', lease_owner = ?, lease_expires_at = ?,
                    attempts = attempts + 1, last_attempt_at = ?, updated_at = ?
                WHERE status = 'pending' AND id IN ({placeholders})
                """,
                (self.worker_id, lease_expires, now_text, now_text, *row_ids),
            )
        return rows

    def _complete(self, rows: list[Any]) -> None:
        row_ids = [int(row["id"]) for row in rows]
        placeholders = ", ".join("?" for _ in row_ids)
        now = self._timestamp(self._now())
        with self.store._connection(write=True) as connection:
            connection.execute(
                f"""
                UPDATE knowledge_index_outbox
                SET status = 'done', completed_at = ?, last_error = NULL,
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE lease_owner = ? AND id IN ({placeholders})
                """,
                (now, now, self.worker_id, *row_ids),
            )

    def _fail(self, rows: list[Any], error: Exception) -> None:
        now = self._now()
        with self.store._connection(write=True) as connection:
            for row in rows:
                attempts = int(row["attempts"]) + 1
                if attempts >= self.max_attempts:
                    status = "dead"
                    available_at = self._timestamp(now)
                    dead_lettered_at = self._timestamp(now)
                else:
                    status = "pending"
                    delay = min(
                        self.max_backoff_seconds,
                        self.base_backoff_seconds * (2 ** max(0, attempts - 1)),
                    )
                    available_at = self._timestamp(now + timedelta(seconds=delay))
                    dead_lettered_at = None
                connection.execute(
                    """
                    UPDATE knowledge_index_outbox
                    SET status = ?, available_at = ?, last_error = ?,
                        dead_lettered_at = ?, lease_owner = NULL,
                        lease_expires_at = NULL, updated_at = ?
                    WHERE id = ? AND lease_owner = ?
                    """,
                    (
                        status,
                        available_at,
                        str(error)[:1000],
                        dead_lettered_at,
                        self._timestamp(now),
                        int(row["id"]),
                        self.worker_id,
                    ),
                )

    def run_once(self, *, limit: int = 100) -> dict[str, Any]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
        ):
            raise ValueError("limit must be between 1 and 1000")
        vector_store = self.store.vector_store
        if vector_store is None or not vector_store.available:
            return {**self.metrics(), "processed": 0, "status": "unavailable"}

        rows = self._claim(limit=limit)
        if not rows:
            metrics = self.metrics()
            self.store.sync_pending = bool(
                metrics["unresolved"] or vector_store.needs_rebuild
            )
            return {**metrics, "processed": 0, "status": "idle"}

        self.store.sync_pending = True
        try:
            if vector_store.needs_rebuild or any(
                row["operation"] == "rebuild" for row in rows
            ):
                self.store._sync_vector_index(force=True)
            else:
                resource_ids = {
                    str(row["resource_id"])
                    for row in rows
                    if row["resource_id"] is not None
                }
                self.store._sync_vector_resources(resource_ids)
        except Exception as error:  # noqa: BLE001 - durable retry boundary
            self._fail(rows, error)
            vector_store.needs_rebuild = True
            vector_store.last_error = f"knowledge vector sync pending: {error}"
            metrics = self.metrics()
            return {
                **metrics,
                "processed": 0,
                "status": "error",
                "error": str(error),
            }

        self._complete(rows)
        metrics = self.metrics()
        self.store.sync_pending = bool(
            metrics["unresolved"] or vector_store.needs_rebuild
        )
        return {**metrics, "processed": len(rows), "status": "ok"}

    def run_until_idle(
        self,
        *,
        batch_size: int = 100,
        max_batches: int = 100,
        max_retry_wait_seconds: float = 1.0,
    ) -> dict[str, Any]:
        processed = 0
        report: dict[str, Any] = {**self.metrics(), "processed": 0, "status": "idle"}
        for _ in range(max_batches):
            report = self.run_once(limit=batch_size)
            processed += int(report.get("processed", 0))
            if report.get("processed"):
                continue
            next_retry = report.get("next_retry_at")
            if report.get("queued") and next_retry and max_retry_wait_seconds > 0:
                retry_at = datetime.fromisoformat(str(next_retry))
                wait = max(0.0, (retry_at - self._now()).total_seconds())
                if 0 < wait <= max_retry_wait_seconds:
                    time.sleep(wait)
                    continue
            break
        return {**report, "processed": processed}

    def retry_dead_letters(self, *, limit: int = 100) -> int:
        """Explicitly requeue dead letters after an operator-visible decision."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
        ):
            raise ValueError("limit must be between 1 and 1000")
        now = self._timestamp(self._now())
        with self.store._connection(write=True) as connection:
            rows = connection.execute(
                "SELECT id FROM knowledge_index_outbox "
                "WHERE status = 'dead' ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
            if not rows:
                return 0
            row_ids = [int(row["id"]) for row in rows]
            placeholders = ", ".join("?" for _ in row_ids)
            connection.execute(
                f"""
                UPDATE knowledge_index_outbox
                SET status = 'pending', attempts = 0, available_at = ?,
                    last_error = NULL, dead_lettered_at = NULL, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (now, now, *row_ids),
            )
        return len(row_ids)


__all__ = ["KnowledgeVectorProjector"]
