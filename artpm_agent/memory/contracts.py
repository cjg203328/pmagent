"""Contracts shared by local and remote memory backends."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class VectorBackend(Protocol):
    """Minimal durable vector-store contract used by memory services."""

    @property
    def count(self) -> int: ...

    @property
    def available(self) -> bool: ...

    @property
    def needs_rebuild(self) -> bool: ...

    def add(self, id: str, vector: list[float], metadata: Mapping[str, Any]) -> None: ...

    def upsert(self, *, id: str, vector: list[float], metadata: Mapping[str, Any]) -> None: ...

    def sync(
        self,
        entries: Iterable[Mapping[str, Any]],
        *,
        delete_ids: Iterable[str] = (),
    ) -> None: ...

    def replace(self, entries: Iterable[Mapping[str, Any]]) -> None: ...

    def delete(self, ids: Iterable[str]) -> int: ...

    def list_entries(self) -> list[dict[str, Any]]: ...

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        *,
        filters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    def clear(self) -> None: ...

    def status(self) -> dict[str, Any]: ...


__all__ = ["VectorBackend"]
