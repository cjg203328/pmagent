"""Small process-local counters for runtime migration observability.

The counters intentionally stay independent from SQLite, OpenTelemetry, and
the provider stack. They are safe to update on worker threads and expose a
bounded snapshot for health/debug surfaces. Durable telemetry remains the
source for historical analysis; these values answer "what is happening in
this process right now?".
"""

from __future__ import annotations

from collections import Counter
from threading import RLock
from typing import Mapping


class RuntimeCounters:
    """Thread-safe monotonic counters with an explicit reset for tests."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._lock = RLock()

    def increment(self, name: str, value: int = 1) -> int:
        """Increment ``name`` and return its new value.

        Invalid or non-positive updates are ignored so diagnostics cannot
        become an accidental failure path when called from exception handlers.
        """

        key = str(name or "").strip()
        if not key:
            return 0
        try:
            amount = int(value)
        except (TypeError, ValueError, OverflowError):
            return self.get(key)
        if amount <= 0:
            return self.get(key)
        with self._lock:
            self._counts[key] += amount
            return self._counts[key]

    def get(self, name: str) -> int:
        key = str(name or "").strip()
        if not key:
            return 0
        with self._lock:
            return int(self._counts.get(key, 0))

    def snapshot(self) -> dict[str, int]:
        """Return an ordinary, deterministic copy suitable for JSON output."""

        with self._lock:
            return dict(sorted((name, int(value)) for name, value in self._counts.items()))

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


runtime_counters = RuntimeCounters()


def increment_counter(name: str, value: int = 1) -> int:
    """Increment the process-wide runtime counter."""

    return runtime_counters.increment(name, value)


def counter_snapshot() -> Mapping[str, int]:
    """Return a snapshot of process-wide runtime counters."""

    return runtime_counters.snapshot()


def reset_counters() -> None:
    """Clear process-wide counters; intended for isolated tests."""

    runtime_counters.reset()


__all__ = [
    "RuntimeCounters",
    "counter_snapshot",
    "increment_counter",
    "reset_counters",
    "runtime_counters",
]
