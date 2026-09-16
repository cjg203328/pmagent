from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock

from artpm_agent.memory.knowledge_projector import KnowledgeVectorProjector
from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore


class ToggleEmbedding:
    dimension = 8
    fingerprint = "toggle-embedding-v1"

    def __init__(self, *, failing: bool = False) -> None:
        self.failing = failing

    def embed(self, _text: str) -> list[float]:
        if self.failing:
            raise RuntimeError("embedding unavailable")
        return [1.0] + [0.0] * (self.dimension - 1)


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def test_projector_retries_with_backoff_then_exposes_dead_letter(tmp_path):
    embedding = ToggleEmbedding(failing=True)
    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        vector_store_path=tmp_path / "vectors",
        embedding_provider=embedding,
    )
    store.ingest_resource(
        resource_id="resource-a",
        title="Resource A",
        searchable_text="authoritative content",
    )

    clock = MutableClock()
    projector = KnowledgeVectorProjector(
        store,
        clock=clock,
        max_attempts=2,
        base_backoff_seconds=1,
    )
    initial = projector.metrics()
    assert initial["pending"] == 1
    assert initial["next_retry_at"] is not None

    clock.advance(2)
    failed = projector.run_once()
    assert failed["status"] == "error"
    assert failed["dead"] == 1
    assert store.vector_status()["outbox_dead_letters"] == 1

    embedding.failing = False
    assert projector.retry_dead_letters() == 1
    recovered = projector.run_once()
    assert recovered["processed"] == 1
    assert recovered["pending"] == 0
    assert recovered["dead"] == 0
    assert store.vector_status()["count"] == 1


def test_projector_claims_are_disjoint_across_workers(tmp_path):
    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        vector_store_path=tmp_path / "vectors",
        embedding_provider=ToggleEmbedding(),
    )
    for resource_id in ("resource-a", "resource-b"):
        store.ingest_resource(
            resource_id=resource_id,
            title=resource_id,
            searchable_text=resource_id,
        )
    with store._connection(write=True) as connection:
        connection.execute("DELETE FROM knowledge_index_outbox")
        for resource_id in ("resource-a", "resource-b"):
            store._enqueue_index_work(
                connection,
                tenant_id="local",
                workspace_id="local-default",
                resource_id=resource_id,
                operation="upsert",
            )

    barrier = Barrier(2)
    guard = Lock()
    claimed: list[frozenset[str]] = []

    def project(resource_ids):
        with guard:
            claimed.append(frozenset(resource_ids))
        barrier.wait(timeout=5)

    store._sync_vector_resources = project
    workers = [
        KnowledgeVectorProjector(store, worker_id=f"worker-{index}")
        for index in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = list(executor.map(lambda worker: worker.run_once(limit=1), workers))

    assert sum(report["processed"] for report in reports) == 2
    assert claimed == [frozenset({"resource-a"}), frozenset({"resource-b"})]
    with store._connection() as connection:
        rows = connection.execute(
            "SELECT status, attempts FROM knowledge_index_outbox ORDER BY id"
        ).fetchall()
    assert [(row["status"], row["attempts"]) for row in rows] == [
        ("done", 1),
        ("done", 1),
    ]


def test_expired_projector_lease_is_reclaimed(tmp_path):
    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        vector_store_path=tmp_path / "vectors",
        embedding_provider=ToggleEmbedding(),
    )
    store.ingest_resource(
        resource_id="resource-a",
        title="Resource A",
        searchable_text="content",
    )
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with store._connection(write=True) as connection:
        connection.execute(
            "UPDATE knowledge_index_outbox SET status = 'processing', "
            "lease_owner = 'dead-worker', lease_expires_at = ?, completed_at = NULL",
            (past,),
        )

    report = KnowledgeVectorProjector(store, worker_id="replacement").run_once()

    assert report["processed"] == 1
    assert report["processing"] == 0
