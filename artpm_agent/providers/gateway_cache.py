"""Thread-safe bounded lifecycle for lazily constructed provider clients."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Generic, TypeVar

ClientT = TypeVar("ClientT")
logger = logging.getLogger(__name__)


class _CacheSnapshot(dict):
    """Mapping-compatible snapshot with entry-list compatibility.

    New gateway code consumes aggregate counters (``entries`` and
    ``max_entries``), while older integrations inspect entries as a sequence.
    This small adapter supports both without duplicating cache state.
    """

    def __init__(self, entry_list: list[dict[str, object]], **metadata: object) -> None:
        super().__init__(metadata)
        self._entries = entry_list
        self._by_key = {entry["key"]: entry for entry in entry_list}

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._by_key or dict.__contains__(self, key)

    def __getitem__(self, key: object):  # type: ignore[override]
        if isinstance(key, int):
            return self._entries[key]
        if key in self._by_key:
            return self._by_key[key]
        return dict.__getitem__(self, key)

    def values(self):  # type: ignore[override]
        return self._entries.values() if hasattr(self._entries, "values") else self._entries

    def items(self):  # type: ignore[override]
        return [(entry["key"], entry) for entry in self._entries]


class _SnapshotEntries(list[dict[str, object]]):
    """List helper exposing ``values`` for legacy snapshot consumers."""

    def values(self):
        return self


@dataclass(slots=True)
class _ClientEntry(Generic[ClientT]):
    client: ClientT
    expires_at: float
    leases: int = 0
    retire_when_idle: bool = False


class BoundedClientCache(MutableMapping[str, ClientT], Generic[ClientT]):
    """A small LRU/TTL cache that never closes a leased client.

    Mapping methods are retained for compatibility with integrations that
    inspect ``ModelGateway._clients``. Gateway request paths should use
    :meth:`lease` so eviction can defer cleanup until an in-flight call ends.
    """

    def __init__(
        self,
        *,
        max_entries: int | None = None,
        capacity: int | None = None,
        ttl_seconds: float,
        close_client: Callable[[ClientT], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries is None:
            max_entries = capacity
        if max_entries is None:
            raise TypeError("max_entries or capacity is required")
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.max_entries = int(max_entries)
        self.ttl_seconds = float(ttl_seconds)
        self._close_client = close_client or self._default_close_client
        self._clock = clock
        self._entries: OrderedDict[str, _ClientEntry[ClientT]] = OrderedDict()
        self._lock = RLock()
        self._closed = False

    def __getitem__(self, key: str) -> ClientT:
        clients_to_close: list[ClientT] = []
        with self._lock:
            self._ensure_open()
            clients_to_close.extend(self._prune_locked())
            entry = self._entries.get(key)
            if entry is None or entry.retire_when_idle:
                raise KeyError(key)
            self._touch_locked(key, entry)
            client = entry.client
        self._close_many(clients_to_close)
        return client

    def __setitem__(self, key: str, value: ClientT) -> None:
        clients_to_close: list[ClientT] = []
        with self._lock:
            self._ensure_open()
            clients_to_close.extend(self._prune_locked())
            previous = self._entries.pop(key, None)
            if previous is not None and previous.client is not value:
                if previous.leases:
                    previous.retire_when_idle = True
                    # A replacement with the same key cannot share the public
                    # cache slot; the active value is closed on lease release.
                else:
                    clients_to_close.append(previous.client)
            self._entries[key] = _ClientEntry(
                client=value,
                expires_at=self._clock() + self.ttl_seconds,
            )
            clients_to_close.extend(self._enforce_limit_locked(exclude={key}))
        self._close_many(clients_to_close)

    def __delitem__(self, key: str) -> None:
        client: ClientT | None = None
        with self._lock:
            entry = self._entries.pop(key)
            if entry.leases:
                entry.retire_when_idle = True
            else:
                client = entry.client
        if client is not None:
            self._close_many([client])

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(tuple(self._entries))

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def peek(self, key: str) -> ClientT | None:
        """Return a cached client without extending its TTL."""

        clients_to_close: list[ClientT] = []
        with self._lock:
            if self._closed:
                return None
            clients_to_close.extend(self._prune_locked())
            entry = self._entries.get(key)
            client = None if entry is None or entry.retire_when_idle else entry.client
        self._close_many(clients_to_close)
        return client

    def get_or_create(self, key: str, factory: Callable[[], ClientT]) -> ClientT:
        """Return one cached instance; concurrent construction is serialized."""

        clients_to_close: list[ClientT] = []
        with self._lock:
            self._ensure_open()
            clients_to_close.extend(self._prune_locked())
            entry = self._entries.get(key)
            if entry is not None and not entry.retire_when_idle:
                self._touch_locked(key, entry)
                client = entry.client
            else:
                client = factory()
                self._entries[key] = _ClientEntry(
                    client=client,
                    expires_at=self._clock() + self.ttl_seconds,
                )
                clients_to_close.extend(
                    self._enforce_limit_locked(exclude={key})
                )
        self._close_many(clients_to_close)
        return client

    @contextmanager
    def lease(
        self,
        key: str,
        factory: Callable[[], ClientT],
        *,
        candidate: ClientT | None = None,
    ) -> Iterator[ClientT]:
        """Keep one client alive for the duration of a provider request."""

        clients_to_close: list[ClientT] = []
        ephemeral = False
        entry: _ClientEntry[ClientT] | None = None
        with self._lock:
            self._ensure_open()
            clients_to_close.extend(self._prune_locked())
            entry = self._entries.get(key)
            if entry is None or entry.retire_when_idle:
                client = candidate if candidate is not None else factory()
                if len(self._entries) >= self.max_entries:
                    clients_to_close.extend(
                        self._enforce_limit_locked(reserve_slots=1)
                    )
                if len(self._entries) >= self.max_entries:
                    # Every cached entry is leased. Keep the hard cache bound
                    # and close this one as soon as its request completes.
                    ephemeral = True
                else:
                    entry = _ClientEntry(
                        client=client,
                        expires_at=self._clock() + self.ttl_seconds,
                        leases=1,
                    )
                    self._entries[key] = entry
            else:
                client = entry.client
                entry.leases += 1
                self._touch_locked(key, entry)
        self._close_many(clients_to_close)

        try:
            yield client
        finally:
            clients_to_close = []
            with self._lock:
                if ephemeral:
                    clients_to_close.append(client)
                elif entry is not None:
                    entry.leases = max(0, entry.leases - 1)
                    if entry.leases == 0 and (
                        entry.retire_when_idle or entry.expires_at <= self._clock()
                    ):
                        cached = self._entries.get(key)
                        if cached is entry:
                            self._entries.pop(key, None)
                        clients_to_close.append(entry.client)
                    clients_to_close.extend(self._enforce_limit_locked())
            self._close_many(clients_to_close)

    def prune(self) -> int:
        """Close expired idle clients and return the number removed."""

        with self._lock:
            clients_to_close = self._prune_locked()
        self._close_many(clients_to_close)
        return len(clients_to_close)

    def close_all(self) -> None:
        """Reject new clients and close every distinct cached client."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            clients = [entry.client for entry in self._entries.values()]
            self._entries.clear()
        self._close_many(clients)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            entries = _SnapshotEntries(
                {
                    "key": key,
                    "created_at": entry.expires_at - self.ttl_seconds,
                    "last_used": entry.expires_at - self.ttl_seconds,
                    "expires_at": entry.expires_at,
                    "leases": entry.leases,
                }
                for key, entry in self._entries.items()
                if not entry.retire_when_idle
            )
            leased = sum(entry.leases for entry in self._entries.values())
            return _CacheSnapshot(
                entries,
                entries=len(self._entries),
                max_entries=self.max_entries,
                ttl_seconds=self.ttl_seconds,
                active_leases=leased,
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("model client cache is closed")

    def _touch_locked(self, key: str, entry: _ClientEntry[ClientT]) -> None:
        entry.expires_at = self._clock() + self.ttl_seconds
        self._entries.move_to_end(key)

    def _prune_locked(self) -> list[ClientT]:
        now = self._clock()
        clients: list[ClientT] = []
        for key, entry in tuple(self._entries.items()):
            if entry.expires_at > now:
                continue
            if entry.leases:
                entry.retire_when_idle = True
                continue
            self._entries.pop(key, None)
            clients.append(entry.client)
        return clients

    def _enforce_limit_locked(
        self,
        *,
        exclude: set[str] | None = None,
        reserve_slots: int = 0,
    ) -> list[ClientT]:
        clients: list[ClientT] = []
        excluded = exclude or set()
        target_size = max(0, self.max_entries - reserve_slots)
        while len(self._entries) > target_size:
            victim = next(
                (
                    key
                    for key, entry in self._entries.items()
                    if key not in excluded and entry.leases == 0
                ),
                None,
            )
            if victim is None:
                break
            entry = self._entries.pop(victim)
            clients.append(entry.client)
        return clients

    def _close_many(self, clients: list[ClientT]) -> None:
        closed: set[int] = set()
        for client in clients:
            identity = id(client)
            if identity in closed:
                continue
            closed.add(identity)
            try:
                self._close_client(client)
            except Exception as error:  # noqa: BLE001 - cleanup is best effort
                # Cleanup must not mask the request result that triggered it.
                logger.warning("Failed to close cached model client: %s", error)

    @staticmethod
    def _default_close_client(client: ClientT) -> None:
        close = getattr(client, "close", None)
        if callable(close):
            close()


__all__ = ["BoundedClientCache"]
