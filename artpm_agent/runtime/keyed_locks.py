"""Bounded keyed re-entrant locks for tenant/workspace execution."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from threading import RLock
from typing import Generic, Literal, TypeVar

KeyT = TypeVar("KeyT", bound=Hashable)


@dataclass(slots=True)
class _LockEntry:
    lock: RLock
    users: int
    touched_at: float


class ScopedLockHandle(Generic[KeyT]):
    """Stable compatibility handle that resolves the current keyed lock."""

    def __init__(self, pool: KeyedLockPool[KeyT], key: KeyT) -> None:
        self._pool = pool
        self._key = key

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        entry = self._pool._checkout(self._key)
        try:
            if not blocking:
                acquired = entry.lock.acquire(blocking=False)
            elif timeout == -1:
                acquired = entry.lock.acquire()
            else:
                acquired = entry.lock.acquire(timeout=timeout)
        except BaseException:
            self._pool._cancel_checkout(self._key, entry)
            raise
        if not acquired:
            self._pool._cancel_checkout(self._key, entry)
        return acquired

    def release(self) -> None:
        self._pool._release(self._key)

    def __enter__(self) -> ScopedLockHandle[KeyT]:
        if not self.acquire():  # pragma: no cover - blocking acquisition
            raise TimeoutError("workspace lock acquisition failed")
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> Literal[False]:
        self.release()
        return False


class KeyedLockPool(Generic[KeyT]):
    """Keep idle lock state bounded while preserving active serialization."""

    def __init__(
        self,
        *,
        max_entries: int = 128,
        ttl_seconds: float = 1800,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.max_entries = int(max_entries)
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._guard = RLock()
        self._entries: OrderedDict[KeyT, _LockEntry] = OrderedDict()
        self._handles: OrderedDict[KeyT, ScopedLockHandle[KeyT]] = OrderedDict()

    def handle(self, key: KeyT) -> ScopedLockHandle[KeyT]:
        with self._guard:
            handle = self._handles.get(key)
            if handle is None:
                handle = ScopedLockHandle(self, key)
                self._handles[key] = handle
            else:
                self._handles.move_to_end(key)
            while len(self._handles) > self.max_entries:
                self._handles.popitem(last=False)
            return handle

    def _checkout(self, key: KeyT) -> _LockEntry:
        with self._guard:
            self._prune_locked()
            entry = self._entries.get(key)
            if entry is None:
                entry = _LockEntry(RLock(), 0, self._clock())
                self._entries[key] = entry
            entry.users += 1
            entry.touched_at = self._clock()
            self._entries.move_to_end(key)
            self._enforce_limit_locked(exclude={key})
            return entry

    def _cancel_checkout(self, key: KeyT, entry: _LockEntry) -> None:
        with self._guard:
            current = self._entries.get(key)
            if current is entry:
                entry.users = max(0, entry.users - 1)
                entry.touched_at = self._clock()
                self._enforce_limit_locked()

    def _release(self, key: KeyT) -> None:
        with self._guard:
            entry = self._entries.get(key)
            if entry is None or entry.users < 1:
                raise RuntimeError("workspace lock is not acquired")
            # Release while the pool guard prevents the entry from being
            # removed between lookup and the underlying RLock transition.
            entry.lock.release()
            entry.users -= 1
            entry.touched_at = self._clock()
            self._entries.move_to_end(key)
            self._prune_locked()
            self._enforce_limit_locked()

    def prune(self) -> int:
        with self._guard:
            removed = self._prune_locked()
            removed += self._enforce_limit_locked()
            return removed

    def clear(self) -> int:
        """Drop idle entries; active locks remain until their final release."""

        with self._guard:
            removed = 0
            for key, entry in tuple(self._entries.items()):
                if entry.users == 0:
                    self._entries.pop(key, None)
                    removed += 1
            self._handles.clear()
            return removed

    def snapshot(self) -> dict[str, int | float]:
        with self._guard:
            return {
                "entries": len(self._entries),
                "active": sum(entry.users for entry in self._entries.values()),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
            }

    def _prune_locked(self) -> int:
        now = self._clock()
        removed = 0
        for key, entry in tuple(self._entries.items()):
            if entry.users == 0 and now - entry.touched_at >= self.ttl_seconds:
                self._entries.pop(key, None)
                removed += 1
        return removed

    def _enforce_limit_locked(self, *, exclude: set[KeyT] | None = None) -> int:
        removed = 0
        excluded = exclude or set()
        while len(self._entries) > self.max_entries:
            victim = next(
                (
                    key
                    for key, entry in self._entries.items()
                    if key not in excluded and entry.users == 0
                ),
                None,
            )
            if victim is None:
                # Active locks cannot be evicted safely. Their temporary count
                # is bounded by active request concurrency and shrinks on exit.
                break
            self._entries.pop(victim, None)
            removed += 1
        return removed


__all__ = ["KeyedLockPool", "ScopedLockHandle"]
