"""Per-model request concurrency limits with a legacy global mode."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Condition, RLock
from typing import Generator


@dataclass
class _SlotState:
    active: int = 0
    limit: int = 0


class ModelRequestLimiter:
    """Limit concurrent requests without sharing semaphores across models.

    ``acquire_slot()`` retains the gateway's original global limiter contract.
    Callers that need independent model limits can pass ``model_id`` and
    ``max_concurrent``; both forms share the same timeout and are observable
    through :meth:`slot_snapshot`.
    """

    def __init__(
        self,
        max_concurrent: int = 8,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._max_concurrent = int(max_concurrent)
        self._timeout_seconds = max(0.0, float(timeout_seconds))
        self._condition = Condition(RLock())
        self._slots: dict[str, _SlotState] = {}

    @contextmanager
    def acquire_slot(
        self,
        model_id: str | None = None,
        *,
        max_concurrent: int | None = None,
    ) -> Generator[None, None, None]:
        """Acquire a slot, waiting until the selected model has capacity."""
        key = "__default__" if model_id is None else str(model_id)
        limit = self._max_concurrent if max_concurrent is None else int(max_concurrent)
        deadline = time.monotonic() + self._timeout_seconds
        with self._condition:
            state = self._slots.setdefault(key, _SlotState())
            state.limit = limit
            while limit > 0 and state.active >= limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "Could not acquire model request slot within "
                        f"{self._timeout_seconds}s"
                    )
                self._condition.wait(timeout=remaining)
                # A concurrent caller may have updated the configured limit.
                limit = state.limit
            state.active += 1
        try:
            yield
        finally:
            with self._condition:
                active_state = self._slots.get(key)
                if active_state is not None:
                    active_state.active = max(0, active_state.active - 1)
                self._condition.notify_all()

    def slot_snapshot(self) -> dict[str, dict[str, int]]:
        """Return active slots and limits for every model touched so far."""
        with self._condition:
            return {
                key: {"active": state.active, "limit": state.limit}
                for key, state in self._slots.items()
            }

    @property
    def max_concurrent(self) -> int:
        """Return the default maximum number of concurrent requests."""
        return self._max_concurrent

    @property
    def timeout_seconds(self) -> float:
        """Return the slot acquisition timeout in seconds."""
        return self._timeout_seconds


__all__ = ["ModelRequestLimiter"]
