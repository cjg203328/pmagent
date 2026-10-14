"""Per-scope sharding for the local FAISS index.

Why this exists
---------------
``VectorStore.search`` evaluates metadata filters *after* FAISS ranking, so any
query carrying a ``tenant_id``/``workspace_id`` filter has to search the entire
index and discard other tenants' hits afterwards
(``faiss_vector_store.py``, the ``search_k`` branch). The comment there justifies
this with "small local index", which stops being true once several tenants share
one file: every tenant's query walks every other tenant's vectors.

``ShardedVectorStore`` keeps the same interface but stores one index per
``<tenant>/<workspace>`` scope, so a filtered search touches only that scope's
vectors. Recall is unchanged — the filter is applied by *selection of the index*
rather than by discarding results.

Compatibility
-------------
Sharding is **opt-in** (``VECTOR_SHARD_BY_SCOPE``). Enabling it changes the
on-disk layout, so an existing flat index is not read as if it were sharded.
Instead the store reports ``needs_rebuild`` and the caller rebuilds from the
authoritative SQLite state — which is the existing recovery path used whenever
the manifest does not match. Turning the switch back off restores the flat
layout without data migration.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .faiss_vector_store import VectorStore as FaissVectorStore


DEFAULT_TENANT_ID = "local"
DEFAULT_WORKSPACE_ID = "local-default"

SHARD_ENV_VAR = "VECTOR_SHARD_BY_SCOPE"

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def sharding_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether per-scope sharding was explicitly requested."""
    values = os.environ if env is None else env
    raw = str(values.get(SHARD_ENV_VAR, "")).strip().casefold()
    return raw in {"1", "true", "yes", "on"}


def scope_segment(value: str) -> str:
    """Turn a tenant/workspace id into a filesystem-safe directory name.

    Ids are operator-controlled but must not be able to escape the shard root:
    a value like ``../../etc`` or one containing a path separator would otherwise
    let a scope write outside its directory. Unsafe characters collapse to ``-``
    and a short digest of the original keeps distinct ids distinct, so two ids
    that sanitize to the same slug never share an index.
    """
    text = str(value or "").strip()
    if not text:
        text = "unknown"
    slug = _SAFE_SEGMENT.sub("-", text).strip(".-") or "unknown"
    # Bound the segment length so deep ids cannot exceed path limits.
    slug = slug[:48]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def resolve_scope(
    metadata: Mapping[str, Any] | None,
    filters: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Resolve the (tenant, workspace) pair for an entry or a query.

    Entry metadata wins over filters: an entry must live in the scope it declares,
    even when a caller passes filters that name a different one. Missing values
    fall back to the local defaults, matching the rest of the memory layer.
    """
    for source in (metadata, filters):
        if not source:
            continue
        tenant = str(source.get("tenant_id") or "").strip()
        workspace = str(source.get("workspace_id") or "").strip()
        if tenant or workspace:
            return (
                tenant or DEFAULT_TENANT_ID,
                workspace or DEFAULT_WORKSPACE_ID,
            )
    return (DEFAULT_TENANT_ID, DEFAULT_WORKSPACE_ID)


class ShardedVectorStore:
    """A ``VectorStore``-compatible facade backed by one index per scope.

    Only scopes that actually hold data get a directory; the shard map is built
    lazily and cached. All methods mirror ``FaissVectorStore`` so the knowledge
    store and memory manager need no changes.
    """

    def __init__(
        self,
        store_path: str | Path,
        *,
        dimension: int = 1536,
        embedding_fingerprint: str = "unspecified:1536",
    ) -> None:
        self.store_path = Path(store_path).expanduser().resolve()
        self.shard_root = self.store_path / "scopes"
        self.dimension = dimension
        self.embedding_fingerprint = embedding_fingerprint
        # Keyed by resolved path, not by scope id: directory names are sanitized
        # slugs, so keying by id would hand out two instances for one directory
        # and let their in-memory state drift apart.
        self._shards: dict[str, FaissVectorStore] = {}
        self.fallback_reason: str | None = None
        self.last_error: str | None = None
        self.needs_rebuild = self._flat_layout_present()

    # ---- scope handling -------------------------------------------------

    def _flat_layout_present(self) -> bool:
        """Detect a pre-sharding flat index in the store root.

        Reading it as if it were sharded would hide all existing vectors, so the
        store asks for a rebuild instead. The rebuild path already exists: the
        knowledge store and memory manager both rebuild from SQLite when
        ``needs_rebuild`` is set.
        """
        for name in ("index.faiss", "index.bin", "metadata.json", "manifest.json"):
            if (self.store_path / name).exists():
                self.last_error = (
                    "flat vector layout detected; per-scope sharding requires a "
                    "rebuild before the existing index can be read"
                )
                return True
        return False

    def _shard_path(self, scope: tuple[str, str]) -> Path:
        tenant, workspace = scope
        return self.shard_root / scope_segment(tenant) / scope_segment(workspace)

    def _shard_at(self, path: Path) -> FaissVectorStore:
        key = str(path.resolve())
        shard = self._shards.get(key)
        if shard is None:
            shard = FaissVectorStore(
                path,
                dimension=self.dimension,
                embedding_fingerprint=self.embedding_fingerprint,
            )
            self._shards[key] = shard
        return shard

    def _shard(self, scope: tuple[str, str]) -> FaissVectorStore:
        return self._shard_at(self._shard_path(scope))

    def _all_shards(self) -> list[FaissVectorStore]:
        """Every shard on disk, including ones created by an earlier process."""
        if self.shard_root.is_dir():
            for tenant_dir in sorted(self.shard_root.iterdir()):
                if not tenant_dir.is_dir():
                    continue
                for workspace_dir in sorted(tenant_dir.iterdir()):
                    if workspace_dir.is_dir():
                        self._shard_at(workspace_dir)
        return list(self._shards.values())

    @staticmethod
    def _entry_scope(item: Mapping[str, Any]) -> tuple[str, str]:
        return resolve_scope(item.get("metadata"))

    # ---- read surface ---------------------------------------------------

    @property
    def count(self) -> int:
        return sum(shard.count for shard in self._all_shards())

    @property
    def available(self) -> bool:
        return any(shard.available for shard in self._all_shards())

    @available.setter
    def available(self, value: bool) -> None:
        for shard in self._shards.values():
            shard.available = bool(value)

    def status(self) -> dict[str, Any]:
        shards = self._all_shards()
        return {
            "available": self.available,
            "count": sum(shard.count for shard in shards),
            "dimension": self.dimension,
            "metric": FaissVectorStore.METRIC,
            "embedding_fingerprint": self.embedding_fingerprint,
            "needs_rebuild": self.needs_rebuild,
            "last_error": self.last_error,
            "path": str(self.store_path),
            "layout": "sharded",
            "shard_count": len(shards),
        }

    def list_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for shard in self._all_shards():
            for entry in shard.list_entries():
                entries.append(entry)
        return entries

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        *,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Search only the scopes a query can legitimately match.

        With a tenant/workspace filter this reads a single shard, which is the
        entire point of sharding. Without one the query is unscoped, so every
        shard is searched and the merged hits are ranked by score.
        """
        if top_k <= 0:
            return []
        scope = resolve_scope(filters) if filters else None
        if scope is not None and self._scope_is_pinned(filters):
            shard = self._shard(scope)
            return shard.search(query_vector, top_k, filters=filters)

        merged: list[dict[str, Any]] = []
        for shard in self._all_shards():
            merged.extend(shard.search(query_vector, top_k, filters=filters))
        merged.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        return merged[:top_k]

    @staticmethod
    def _scope_is_pinned(filters: Mapping[str, Any] | None) -> bool:
        """True when the filter names both scope fields, so one shard suffices."""
        if not filters:
            return False
        return bool(
            str(filters.get("tenant_id") or "").strip()
            and str(filters.get("workspace_id") or "").strip()
        )

    # ---- write surface --------------------------------------------------

    def add(self, id: str, vector: list[float], metadata: Mapping[str, Any]) -> None:
        self.upsert(id=id, vector=vector, metadata=metadata)

    def upsert(
        self,
        *,
        id: str,
        vector: list[float],
        metadata: Mapping[str, Any],
    ) -> None:
        scope = resolve_scope(metadata)
        self._shard(scope).upsert(id=id, vector=vector, metadata=metadata)

    def sync(
        self,
        entries: Iterable[Mapping[str, Any]],
        *,
        delete_ids: Iterable[str] = (),
    ) -> None:
        """Route upserts to their declared scope and removals to their owner.

        Upserts and removals for the same scope go into a single write so each
        shard is updated atomically, and a removal is sent to the shard that
        actually holds the id rather than assumed to be the default scope.
        """
        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for item in entries:
            grouped.setdefault(self._entry_scope(item), []).append(item)

        removals_by_scope = self._locate_ids({str(value) for value in delete_ids})
        for scope in removals_by_scope:
            grouped.setdefault(scope, [])

        for scope, items in grouped.items():
            self._shard(scope).sync(
                items, delete_ids=removals_by_scope.get(scope, set())
            )
        self.needs_rebuild = False

    def replace(self, entries: Iterable[Mapping[str, Any]]) -> None:
        """Replace the whole store: each scope is rewritten, stale ones cleared."""
        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for item in entries:
            grouped.setdefault(self._entry_scope(item), []).append(item)

        # A scope absent from the new contents must be emptied, not left behind.
        for shard in self._all_shards():
            shard.replace([])
        for scope, items in grouped.items():
            self._shard(scope).replace(items)
        self.needs_rebuild = False
        self.last_error = None

    def delete(self, ids: Iterable[str]) -> int:
        removals = {str(value) for value in ids}
        if not removals:
            return 0
        removed = 0
        for scope, scoped_ids in self._locate_ids(removals).items():
            removed += self._shard(scope).delete(scoped_ids)
        return removed

    def clear(self) -> None:
        for shard in self._all_shards():
            shard.clear()
        self.needs_rebuild = False

    def _locate_ids(self, ids: set[str]) -> dict[tuple[str, str], set[str]]:
        """Map logical ids to the shard that holds them."""
        located: dict[tuple[str, str], set[str]] = {}
        for shard in self._all_shards():
            for entry in shard.list_entries():
                logical_id = str(entry.get("id") or "")
                if logical_id in ids:
                    scope = resolve_scope(entry.get("metadata"))
                    located.setdefault(scope, set()).add(logical_id)
        return located


__all__ = [
    "DEFAULT_TENANT_ID",
    "DEFAULT_WORKSPACE_ID",
    "SHARD_ENV_VAR",
    "ShardedVectorStore",
    "resolve_scope",
    "scope_segment",
    "sharding_enabled",
]
