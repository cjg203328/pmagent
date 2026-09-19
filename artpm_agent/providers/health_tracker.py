"""Thread-safe provider health tracking with circuit-breaker compatibility."""

from __future__ import annotations

import time
from threading import RLock
from typing import Any, Dict


class ProviderHealthTracker:
    """Track provider cooldowns and consecutive failures.

    The gateway uses the ``mark_*``/``is_available`` API, while older provider
    integrations use ``record_*``/``is_healthy`` with a failure threshold.  A
    single implementation keeps both contracts consistent and thread-safe.
    """

    def __init__(
        self,
        cooldown_seconds: float = 60.0,
        max_entries: int = 64,
        *,
        failure_threshold: int | None = None,
        recovery_timeout: float | None = None,
    ) -> None:
        self._cooldown_seconds = max(
            0.0,
            float(recovery_timeout if recovery_timeout is not None else cooldown_seconds),
        )
        self._max_entries = max(1, int(max_entries))
        self._failure_threshold = (
            None
            if failure_threshold is None
            else max(0, int(failure_threshold))
        )
        self._unavailable_until: Dict[str, float] = {}
        self._consecutive_failures: Dict[str, int] = {}
        self._last_errors: Dict[str, str] = {}
        self._lock = RLock()

    def is_available(self, model_id: str) -> bool:
        """Check if a model is currently available (not in cooldown)."""
        with self._lock:
            return self._is_available_locked(model_id, time.monotonic())

    def is_healthy(self, model_id: str) -> bool:
        """Compatibility alias for :meth:`is_available`."""
        return self.is_available(model_id)

    def mark_unavailable(self, model_id: str) -> None:
        """Mark a model as unavailable and start its cooldown period."""
        if not model_id:
            return
        with self._lock:
            now = time.monotonic()
            self._prune_locked(now)
            self._unavailable_until[model_id] = now + self._cooldown_seconds
            self._enforce_limit_locked()

    def mark_healthy(self, model_id: str) -> None:
        """Mark a model as healthy and clear cooldown/failure state."""
        with self._lock:
            self._unavailable_until.pop(model_id, None)
            self._consecutive_failures[model_id] = 0
            self._last_errors.pop(model_id, None)

    def record_failure(self, model_id: str, error: BaseException | None = None) -> None:
        """Record one failure and open the circuit at the configured threshold."""
        if not model_id:
            return
        with self._lock:
            now = time.monotonic()
            self._prune_locked(now)
            count = self._consecutive_failures.get(model_id, 0) + 1
            self._consecutive_failures[model_id] = count
            if error is not None:
                self._last_errors[model_id] = type(error).__name__
            threshold = self._failure_threshold
            if threshold is not None and threshold > 0 and count >= threshold:
                self._unavailable_until[model_id] = now + self._cooldown_seconds
                self._enforce_limit_locked()

    def record_success(self, model_id: str) -> None:
        """Record a successful request and reset consecutive failures."""
        self.mark_healthy(model_id)

    def health_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Return per-provider health and failure counters."""
        with self._lock:
            now = time.monotonic()
            self._prune_locked(now)
            keys = set(self._consecutive_failures) | set(self._unavailable_until)
            return {
                key: {
                    "healthy": self._is_available_locked(key, now),
                    "consecutive_failures": self._consecutive_failures.get(key, 0),
                    "unavailable_until": self._unavailable_until.get(key),
                    "last_error": self._last_errors.get(key),
                }
                for key in keys
            }

    def snapshot(self) -> Dict[str, float]:
        """Return a snapshot of current unavailability state."""
        with self._lock:
            self._prune_locked(time.monotonic())
            return dict(self._unavailable_until)

    def _is_available_locked(self, model_id: str, now: float) -> bool:
        expires_at = self._unavailable_until.get(model_id, 0.0)
        if expires_at <= now:
            self._unavailable_until.pop(model_id, None)
            return True
        return False

    def _prune_locked(self, now: float) -> None:
        for model_id, expires_at in tuple(self._unavailable_until.items()):
            if expires_at <= now:
                self._unavailable_until.pop(model_id, None)

    def _enforce_limit_locked(self) -> None:
        while len(self._unavailable_until) > self._max_entries:
            victim = min(
                self._unavailable_until,
                key=self._unavailable_until.__getitem__,
            )
            self._unavailable_until.pop(victim, None)


__all__ = ["ProviderHealthTracker"]
