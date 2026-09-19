from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from artpm_agent.runtime.keyed_locks import KeyedLockPool


def test_keyed_lock_pool_bounds_idle_entries_and_expires_them() -> None:
    now = [10.0]
    pool = KeyedLockPool[str](
        max_entries=2,
        ttl_seconds=5,
        clock=lambda: now[0],
    )

    for key in ("a", "b", "c"):
        with pool.handle(key):
            pass

    assert pool.snapshot()["entries"] == 2
    now[0] = 16.0
    assert pool.prune() == 2
    assert pool.snapshot()["entries"] == 0


def test_evicted_handles_still_serialize_on_the_current_keyed_lock() -> None:
    pool = KeyedLockPool[str](max_entries=1, ttl_seconds=60)
    stale_handle = pool.handle("workspace-a")
    pool.handle("workspace-b")
    current_handle = pool.handle("workspace-a")
    entered = Event()
    release = Event()
    second_entered = Event()

    def first() -> None:
        with stale_handle:
            entered.set()
            assert release.wait(2)

    def second() -> None:
        with current_handle:
            second_entered.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first)
        assert entered.wait(2)
        second_future = executor.submit(second)
        assert not second_entered.wait(0.05)
        release.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)

    assert second_entered.is_set()
    assert pool.snapshot()["entries"] == 1
