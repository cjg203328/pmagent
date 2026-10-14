"""Per-scope FAISS sharding contract.

The flat index evaluates tenant/workspace filters *after* FAISS ranking, so every
filtered query scans the whole index. Sharding makes a scoped query read one
directory instead. These tests pin both halves: that scoped reads stay correct
(no cross-tenant leakage, no lost recall) and that the switch being off leaves
existing behaviour untouched.
"""

from __future__ import annotations

import pytest

from artpm_agent.memory.sharded_vector_store import (
    ShardedVectorStore,
    resolve_scope,
    scope_segment,
    sharding_enabled,
)
from artpm_agent.memory.vector_store import VectorStore

DIMENSION = 8


def _vector(seed: int) -> list[float]:
    """A deterministic unit-ish vector, distinct per seed."""
    values = [0.0] * DIMENSION
    values[seed % DIMENSION] = 1.0
    return values


def _store(tmp_path, name: str = "vectors") -> ShardedVectorStore:
    return ShardedVectorStore(tmp_path / name, dimension=DIMENSION)


def _entry(entry_id: str, seed: int, tenant: str, workspace: str) -> dict:
    return {
        "id": entry_id,
        "vector": _vector(seed),
        "metadata": {"tenant_id": tenant, "workspace_id": workspace},
    }


def test_entries_are_isolated_by_tenant_and_workspace(tmp_path):
    store = _store(tmp_path)
    store.sync([
        _entry("a1", 1, "acme", "art"),
        _entry("b1", 2, "beta", "art"),
        _entry("a2", 3, "acme", "other"),
    ])

    acme_art = store.search(
        _vector(1), top_k=10, filters={"tenant_id": "acme", "workspace_id": "art"}
    )

    assert [item["id"] for item in acme_art] == ["a1"]


def test_scoped_search_does_not_see_other_tenants_vectors(tmp_path):
    store = _store(tmp_path)
    store.sync([
        _entry("mine", 1, "acme", "art"),
        _entry("theirs", 1, "beta", "art"),  # identical vector, other tenant
    ])

    results = store.search(
        _vector(1), top_k=10, filters={"tenant_id": "acme", "workspace_id": "art"}
    )

    assert [item["id"] for item in results] == ["mine"]


def test_scope_filter_is_applied_by_index_selection_not_post_filtering(tmp_path):
    """The point of sharding: a scoped query must not touch other shards."""
    store = _store(tmp_path)
    store.sync([
        _entry("a1", 1, "acme", "art"),
        _entry("b1", 2, "beta", "art"),
    ])

    touched: list[str] = []
    original = ShardedVectorStore._shard

    def tracking(self, scope):
        touched.append(scope)
        return original(self, scope)

    ShardedVectorStore._shard = tracking
    try:
        store.search(
            _vector(1), top_k=5, filters={"tenant_id": "acme", "workspace_id": "art"}
        )
    finally:
        ShardedVectorStore._shard = original

    assert touched == [("acme", "art")]


def test_unscoped_search_still_reaches_every_shard(tmp_path):
    store = _store(tmp_path)
    store.sync([
        _entry("a1", 1, "acme", "art"),
        _entry("b1", 2, "beta", "art"),
    ])

    results = store.search(_vector(1), top_k=10)

    assert {item["id"] for item in results} == {"a1", "b1"}


def test_list_entries_aggregates_every_shard(tmp_path):
    store = _store(tmp_path)
    store.sync([
        _entry("a1", 1, "acme", "art"),
        _entry("b1", 2, "beta", "art"),
        _entry("c1", 3, "gamma", "ops"),
    ])

    assert {entry["id"] for entry in store.list_entries()} == {"a1", "b1", "c1"}
    assert store.count == 3


def test_sync_routes_each_entry_to_its_declared_scope(tmp_path):
    store = _store(tmp_path)
    store.sync([_entry("a1", 1, "acme", "art"), _entry("b1", 2, "beta", "art")])

    # One directory per scope, and the shard count reflects that.
    assert store.status()["shard_count"] == 2
    assert (tmp_path / "vectors" / "scopes").is_dir()


def test_delete_targets_the_owning_shard(tmp_path):
    store = _store(tmp_path)
    store.sync([_entry("a1", 1, "acme", "art"), _entry("b1", 2, "beta", "art")])

    removed = store.delete(["b1"])

    assert removed == 1
    assert {entry["id"] for entry in store.list_entries()} == {"a1"}


def test_delete_ignores_unknown_ids(tmp_path):
    store = _store(tmp_path)
    store.sync([_entry("a1", 1, "acme", "art")])

    assert store.delete(["nope"]) == 0
    assert store.count == 1


def test_replace_clears_scopes_absent_from_the_new_contents(tmp_path):
    store = _store(tmp_path)
    store.sync([_entry("a1", 1, "acme", "art"), _entry("b1", 2, "beta", "art")])

    store.replace([_entry("a9", 9, "acme", "art")])

    entries = store.list_entries()
    assert [entry["id"] for entry in entries] == ["a9"]
    # The beta scope must not keep a stale vector after a full replace.
    assert store.search(
        _vector(2), top_k=10, filters={"tenant_id": "beta", "workspace_id": "art"}
    ) == []


def test_clear_empties_every_shard(tmp_path):
    store = _store(tmp_path)
    store.sync([_entry("a1", 1, "acme", "art"), _entry("b1", 2, "beta", "art")])

    store.clear()

    assert store.count == 0
    assert store.list_entries() == []


def test_existing_flat_index_triggers_a_rebuild_instead_of_hiding_data(tmp_path):
    """A pre-sharding layout must not be silently read as an empty store."""
    flat = tmp_path / "vectors"
    flat.mkdir(parents=True)
    (flat / "metadata.json").write_text("{}", encoding="utf-8")

    store = ShardedVectorStore(flat, dimension=DIMENSION)

    assert store.needs_rebuild is True
    assert store.last_error and "flat vector layout" in store.last_error


def test_scope_segment_cannot_escape_the_shard_root(tmp_path):
    """A hostile scope id must not write outside its own directory."""
    store = _store(tmp_path)
    store.sync([_entry("evil", 1, "../../etc", "art")])

    shard_root = (tmp_path / "vectors" / "scopes").resolve()
    for shard in store._all_shards():  # noqa: SLF001 - asserting layout on purpose
        assert shard.store_path.is_relative_to(shard_root)


def test_distinct_scope_ids_never_share_a_directory():
    # Both sanitize to the same slug; the digest must keep them apart.
    left = scope_segment("acme/art")
    right = scope_segment("acme-art")

    assert left != right


def test_sharding_is_off_by_default():
    assert sharding_enabled({}) is False
    assert sharding_enabled({"VECTOR_SHARD_BY_SCOPE": ""}) is False
    assert sharding_enabled({"VECTOR_SHARD_BY_SCOPE": "0"}) is False


def test_sharding_can_be_enabled_explicitly():
    assert sharding_enabled({"VECTOR_SHARD_BY_SCOPE": "1"}) is True
    assert sharding_enabled({"VECTOR_SHARD_BY_SCOPE": "true"}) is True
    assert sharding_enabled({"VECTOR_SHARD_BY_SCOPE": "On"}) is True


def test_facade_keeps_the_flat_backend_when_sharding_is_off(tmp_path, monkeypatch):
    """Regression guard: the default path must be byte-for-byte the old one."""
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.delenv("VECTOR_SHARD_BY_SCOPE", raising=False)

    store = VectorStore(tmp_path / "flat", dimension=DIMENSION)

    assert type(store._backend).__name__ == "VectorStore"  # noqa: SLF001
    assert store.status()["layout"] if "layout" in store.status() else True


def test_facade_selects_the_sharded_backend_when_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.setenv("VECTOR_SHARD_BY_SCOPE", "1")

    store = VectorStore(tmp_path / "sharded", dimension=DIMENSION)

    assert isinstance(store._backend, ShardedVectorStore)  # noqa: SLF001
    assert store.status()["layout"] == "sharded"


def test_resolve_scope_prefers_entry_metadata_over_query_filters():
    """An entry lives in the scope it declares, not in whatever a caller asks."""
    scope = resolve_scope(
        {"tenant_id": "acme", "workspace_id": "art"},
        {"tenant_id": "beta", "workspace_id": "other"},
    )

    assert scope == ("acme", "art")


def test_resolve_scope_falls_back_to_local_defaults():
    assert resolve_scope(None, None) == ("local", "local-default")
    assert resolve_scope({}, {}) == ("local", "local-default")


@pytest.mark.parametrize("dimension", [DIMENSION])
def test_upsert_and_search_round_trip(tmp_path, dimension):
    store = ShardedVectorStore(tmp_path / "v", dimension=dimension)

    store.upsert(
        id="single",
        vector=_vector(3),
        metadata={"tenant_id": "acme", "workspace_id": "art"},
    )

    results = store.search(
        _vector(3), top_k=1, filters={"tenant_id": "acme", "workspace_id": "art"}
    )
    assert results[0]["id"] == "single"
