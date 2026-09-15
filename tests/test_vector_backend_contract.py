from __future__ import annotations

from artpm_agent.memory.contracts import VectorBackend
from artpm_agent.memory.faiss_vector_store import VectorStore as FaissVectorStore
from artpm_agent.memory.vector_store import VectorStore


def _exercise_backend(store: VectorBackend) -> None:
    vector = [1.0, 0.0, 0.0, 0.0]
    store.add("doc-a", vector, {"workspace_id": "workspace-a"})
    store.upsert(
        id="doc-b",
        vector=[0.0, 1.0, 0.0, 0.0],
        metadata={"workspace_id": "workspace-a"},
    )
    assert store.count == 2
    assert store.search(vector, top_k=1)[0]["id"] == "doc-a"
    assert store.delete(["doc-b"]) == 1
    assert [item["id"] for item in store.list_entries()] == ["doc-a"]
    store.replace(
        [{"id": "doc-c", "vector": vector, "metadata": {"version": 1}}]
    )
    assert [item["id"] for item in store.list_entries()] == ["doc-c"]
    assert store.status()["count"] == 1


def test_faiss_backend_satisfies_vector_contract(tmp_path):
    store = FaissVectorStore(tmp_path / "faiss", dimension=4)
    assert isinstance(store, VectorBackend)
    _exercise_backend(store)


def test_vector_facade_satisfies_vector_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("VECTOR_BACKEND", "faiss")
    monkeypatch.delenv("QDRANT_URL", raising=False)
    store = VectorStore(tmp_path / "facade", dimension=4)
    assert isinstance(store, VectorBackend)
    _exercise_backend(store)


def test_vector_facade_uses_packaged_default_when_env_is_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    monkeypatch.delenv("QDRANT_URL", raising=False)
    store = VectorStore(tmp_path / "packaged-default", dimension=4)
    assert store.status()["backend"] == "faiss"
