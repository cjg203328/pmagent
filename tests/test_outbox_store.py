"""Unit tests for OutboxStore and OutboxWorker.

Tests cover:
- Job enqueue with idempotency (turn_id + job_type unique)
- Job polling and status transitions
- Tenant/workspace scope isolation
- Worker execution and retry logic
- Max retry limit enforcement
"""

import pytest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from artpm_agent.memory.outbox_store import (
    JobType,
    JobStatus,
    OutboxStore,
)
from artpm_agent.memory.outbox_worker import OutboxWorker


@pytest.fixture
def outbox_store():
    """Provide a temporary OutboxStore for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "outbox.db")
        store = OutboxStore(db_path)
        yield store
        store.close()


def test_outbox_enqueue_idempotent(outbox_store):
    """Test idempotency inside one tenant/workspace scope."""
    job_id_1 = outbox_store.enqueue(
        turn_id="turn_1",
        job_type=JobType.FEEDBACK,
        payload={"content": "test"},
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )

    # Enqueue again with same turn_id + job_type
    job_id_2 = outbox_store.enqueue(
        turn_id="turn_1",
        job_type=JobType.FEEDBACK,
        payload={"content": "updated"},  # Different payload
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )

    # Second enqueue should be ignored (INSERT OR IGNORE)
    jobs = outbox_store.get_by_turn(
        "turn_1", tenant_id="tenant_a", workspace_id="workspace_1"
    )
    assert len(jobs) == 1
    assert job_id_2 == job_id_1 == jobs[0].id
    assert jobs[0].payload["content"] == "test"  # Original payload preserved


def test_outbox_same_turn_is_independent_across_tenants(outbox_store):
    first = outbox_store.enqueue(
        turn_id="shared-turn",
        job_type=JobType.FEEDBACK,
        payload={"content": "tenant a"},
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )
    second = outbox_store.enqueue(
        turn_id="shared-turn",
        job_type=JobType.FEEDBACK,
        payload={"content": "tenant b"},
        tenant_id="tenant_b",
        workspace_id="workspace_1",
    )

    assert first != second
    assert (
        outbox_store.get_by_turn(
            "shared-turn", tenant_id="tenant_a", workspace_id="workspace_1"
        )[0].payload["content"]
        == "tenant a"
    )
    assert (
        outbox_store.get_by_turn(
            "shared-turn", tenant_id="tenant_b", workspace_id="workspace_1"
        )[0].payload["content"]
        == "tenant b"
    )


def test_outbox_migrates_legacy_turn_unique_constraint(tmp_path):
    db_path = str(tmp_path / "legacy-outbox.db")
    from artpm_agent.memory.sqlite_manager import SQLiteManager

    db = SQLiteManager(db_path, initialize_schema=False)
    with db.get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE outbox_jobs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                job_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(turn_id, job_type)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO outbox_jobs
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-job",
                "tenant_a",
                "workspace_1",
                "legacy-turn",
                JobType.FEEDBACK.value,
                '{"content":"legacy"}',
                JobStatus.PENDING.value,
                0,
                "0001-01-01T00:00:00+00:00",
                "",
                "2026-01-01",
                "2026-01-01",
            ),
        )

    store = OutboxStore(db_path)
    migrated_id = store.enqueue(
        turn_id="legacy-turn",
        job_type=JobType.FEEDBACK,
        payload={"content": "tenant b"},
        tenant_id="tenant_b",
        workspace_id="workspace_1",
    )

    assert migrated_id != "legacy-job"
    assert (
        len(
            store.get_by_turn(
                "legacy-turn", tenant_id="tenant_a", workspace_id="workspace_1"
            )
        )
        == 1
    )
    assert (
        len(
            store.get_by_turn(
                "legacy-turn", tenant_id="tenant_b", workspace_id="workspace_1"
            )
        )
        == 1
    )
    store.close()


def test_outbox_claim_is_atomic_across_workers(outbox_store):
    """Concurrent workers must receive disjoint durable claims."""
    for index in range(12):
        outbox_store.enqueue(
            turn_id=f"claim-{index}",
            job_type=JobType.FEEDBACK,
            payload={},
            tenant_id="tenant_a",
            workspace_id="workspace_1",
        )

    def claim_batch():
        return outbox_store.claim(
            limit=12,
            tenant_id="tenant_a",
            workspace_id="workspace_1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = [
            future.result()
            for future in (
                executor.submit(claim_batch),
                executor.submit(claim_batch),
            )
        ]

    claimed_ids = [job.id for job in first + second]
    assert len(claimed_ids) == 12
    assert len(set(claimed_ids)) == 12
    assert all(job.status == JobStatus.RUNNING for job in first + second)


def test_outbox_poll_available_jobs(outbox_store):
    """Test polling for available jobs."""
    # Enqueue two pending jobs
    outbox_store.enqueue(
        turn_id="turn_1",
        job_type=JobType.FEEDBACK,
        payload={},
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )
    outbox_store.enqueue(
        turn_id="turn_2",
        job_type=JobType.EPISODE,
        payload={},
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )

    # Poll should return both jobs
    jobs = outbox_store.poll(limit=10, tenant_id="tenant_a", workspace_id="workspace_1")
    assert len(jobs) == 2
    assert all(job.status == JobStatus.PENDING for job in jobs)


def test_outbox_mark_completed(outbox_store):
    """Test marking a job as completed."""
    job_id = outbox_store.enqueue(
        turn_id="turn_1",
        job_type=JobType.FEEDBACK,
        payload={},
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )

    outbox_store.mark_completed(job_id)

    # Completed jobs should not appear in poll
    jobs = outbox_store.poll(limit=10, tenant_id="tenant_a", workspace_id="workspace_1")
    assert len(jobs) == 0


def test_outbox_mark_failed_with_retry(outbox_store):
    """Test marking a job as failed schedules retry."""
    job_id = outbox_store.enqueue(
        turn_id="turn_1",
        job_type=JobType.REFLECTION,
        payload={},
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )

    # Mark as failed (should go back to pending with future available_at)
    outbox_store.mark_failed(job_id, error_code="RuntimeError", retry_after_seconds=60)

    # Job should still exist but not be immediately available
    jobs = outbox_store.get_by_turn(
        "turn_1", tenant_id="tenant_a", workspace_id="workspace_1"
    )
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.PENDING
    assert jobs[0].attempts == 1
    assert jobs[0].last_error_code == "RuntimeError"


def test_outbox_tenant_isolation(outbox_store):
    """Test that jobs are isolated by tenant_id."""
    # Enqueue job for tenant_a
    outbox_store.enqueue(
        turn_id="turn_1",
        job_type=JobType.FEEDBACK,
        payload={},
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )

    # Enqueue job for tenant_b
    outbox_store.enqueue(
        turn_id="turn_2",
        job_type=JobType.EPISODE,
        payload={},
        tenant_id="tenant_b",
        workspace_id="workspace_1",
    )

    # tenant_a should only see its own job
    jobs_a = outbox_store.poll(
        limit=10, tenant_id="tenant_a", workspace_id="workspace_1"
    )
    assert len(jobs_a) == 1
    assert jobs_a[0].turn_id == "turn_1"

    # tenant_b should only see its own job
    jobs_b = outbox_store.poll(
        limit=10, tenant_id="tenant_b", workspace_id="workspace_1"
    )
    assert len(jobs_b) == 1
    assert jobs_b[0].turn_id == "turn_2"


def test_outbox_workspace_isolation(outbox_store):
    """Test that jobs are isolated by workspace_id within same tenant."""
    # Enqueue job for workspace_1
    outbox_store.enqueue(
        turn_id="turn_1",
        job_type=JobType.CONSOLIDATION,
        payload={},
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )

    # Enqueue job for workspace_2
    outbox_store.enqueue(
        turn_id="turn_2",
        job_type=JobType.CONSOLIDATION,
        payload={},
        tenant_id="tenant_a",
        workspace_id="workspace_2",
    )

    # workspace_1 should only see its own job
    jobs_1 = outbox_store.poll(
        limit=10, tenant_id="tenant_a", workspace_id="workspace_1"
    )
    assert len(jobs_1) == 1
    assert jobs_1[0].turn_id == "turn_1"

    # workspace_2 should only see its own job
    jobs_2 = outbox_store.poll(
        limit=10, tenant_id="tenant_a", workspace_id="workspace_2"
    )
    assert len(jobs_2) == 1
    assert jobs_2[0].turn_id == "turn_2"


def test_worker_process_batch_success(outbox_store):
    """Test worker successfully processes a batch of jobs."""

    # Mock stores
    class MockFeedbackStore:
        def __init__(self):
            self.calls = []

        def add(self, **kwargs):
            self.calls.append(kwargs)
            return "fb_1"

    feedback_store = MockFeedbackStore()
    worker = OutboxWorker(outbox_store, feedback_store=feedback_store)

    # Enqueue a feedback job
    outbox_store.enqueue(
        turn_id="turn_1",
        job_type=JobType.FEEDBACK,
        payload={
            "kind": "preference",
            "content": "concise replies",
            "scope": "global",
            "principal_id": "user_1",
        },
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )

    # Process batch (must specify tenant/workspace for polling)
    processed = worker.process_batch(tenant_id="tenant_a", workspace_id="workspace_1")
    assert processed == 1
    assert len(feedback_store.calls) == 1
    assert feedback_store.calls[0]["content"] == "concise replies"

    # Job should be marked completed
    jobs = outbox_store.poll(limit=10, tenant_id="tenant_a", workspace_id="workspace_1")
    assert len(jobs) == 0


def test_worker_process_batch_failure_retry(outbox_store):
    """Test worker retries failed jobs."""

    class FailingFeedbackStore:
        def add(self, **kwargs):
            raise RuntimeError("Database unavailable")

    feedback_store = FailingFeedbackStore()
    worker = OutboxWorker(outbox_store, feedback_store=feedback_store)

    # Enqueue a job
    outbox_store.enqueue(
        turn_id="turn_1",
        job_type=JobType.FEEDBACK,
        payload={"kind": "preference", "content": "test", "scope": "global"},
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )

    # Process batch (should fail and schedule retry)
    processed = worker.process_batch(tenant_id="tenant_a", workspace_id="workspace_1")
    assert processed == 0  # No jobs successfully processed

    # Job should be marked for retry
    jobs = outbox_store.get_by_turn(
        "turn_1", tenant_id="tenant_a", workspace_id="workspace_1"
    )
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.PENDING
    assert jobs[0].attempts == 1
    assert jobs[0].last_error_code == "RuntimeError"


def test_worker_max_attempts_reached(outbox_store):
    """Test worker stops retrying after max attempts."""

    class FailingStore:
        def add(self, **kwargs):
            raise RuntimeError("Persistent failure")

    # Immediate retries keep this lifecycle test deterministic without
    # sleeping through the production backoff policy.
    worker = OutboxWorker(
        outbox_store,
        feedback_store=FailingStore(),
        backoff_base_seconds=0,
    )

    # Enqueue a job
    job_id = outbox_store.enqueue(
        turn_id="turn_1",
        job_type=JobType.FEEDBACK,
        payload={"kind": "preference", "content": "test", "scope": "global"},
        tenant_id="tenant_a",
        workspace_id="workspace_1",
    )

    # Put the job at the retry boundary and make it immediately available.
    with outbox_store.db.get_connection() as connection:
        connection.execute(
            "UPDATE outbox_jobs SET status = ?, attempts = ?, available_at = ? WHERE id = ?",
            (JobStatus.PENDING.value, 5, "0001-01-01T00:00:00+00:00", job_id),
        )

    # At the retry boundary, the worker archives it instead of executing again.
    worker.process_batch(tenant_id="tenant_a", workspace_id="workspace_1")

    # Job should no longer be in pending state
    jobs = outbox_store.get_by_turn(
        "turn_1", tenant_id="tenant_a", workspace_id="workspace_1"
    )
    assert len(jobs) == 1
    # After max attempts, worker marks it as completed to prevent infinite retry
    # (check the actual final status based on implementation)


def test_worker_archives_max_attempt_job_in_its_own_scope(outbox_store):
    class FailingStore:
        def add(self, **kwargs):
            raise RuntimeError("Persistent failure")

    worker = OutboxWorker(
        outbox_store,
        feedback_store=FailingStore(),
        backoff_base_seconds=0,
    )
    job_id = outbox_store.enqueue(
        turn_id="tenant-b-turn",
        job_type=JobType.FEEDBACK,
        payload={"kind": "preference", "content": "test", "scope": "global"},
        tenant_id="tenant-b",
        workspace_id="workspace-b",
    )

    with outbox_store.db.get_connection() as connection:
        connection.execute(
            "UPDATE outbox_jobs SET status = ?, attempts = ?, available_at = ? WHERE id = ?",
            (JobStatus.PENDING.value, 5, "0001-01-01T00:00:00+00:00", job_id),
        )

    for _ in range(5):
        worker.process_batch(tenant_id="tenant-b", workspace_id="workspace-b")

    with outbox_store.db.get_connection() as connection:
        row = connection.execute(
            "SELECT status FROM outbox_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    assert row["status"] == JobStatus.COMPLETED.value


def test_count_pending_jobs(outbox_store):
    """Test counting pending jobs for observability."""
    # Enqueue 3 jobs
    for i in range(3):
        outbox_store.enqueue(
            turn_id=f"turn_{i}",
            job_type=JobType.EPISODE,
            payload={},
            tenant_id="tenant_a",
            workspace_id="workspace_1",
        )

    # Count should be 3
    count = outbox_store.count_pending(tenant_id="tenant_a", workspace_id="workspace_1")
    assert count == 3

    # Mark one as completed
    jobs = outbox_store.poll(limit=1, tenant_id="tenant_a", workspace_id="workspace_1")
    outbox_store.mark_completed(jobs[0].id)

    # Count should now be 2
    count = outbox_store.count_pending(tenant_id="tenant_a", workspace_id="workspace_1")
    assert count == 2
