"""Thread-safe lazy boundary for optional runtime capabilities."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Any


class LazyCapability:
    """Instantiate an optional adapter only when its behavior is requested."""

    def __init__(self, factory: Callable[[], Any], *, name: str) -> None:
        self._factory = factory
        self._name = name
        self._value: Any = None
        self._lock = RLock()

    def get(self) -> Any:
        if self._value is not None:
            return self._value
        with self._lock:
            if self._value is None:
                self._value = self._factory()
        return self._value

    @property
    def loaded(self) -> bool:
        return self._value is not None

    def close(self) -> None:
        value = self._value
        close = getattr(value, "close", None)
        if callable(close):
            close()

    def __getattr__(self, name: str) -> Any:
        try:
            return getattr(self.get(), name)
        except ImportError as error:
            raise RuntimeError(f"optional capability unavailable: {self._name}") from error


__all__ = ["LazyCapability"]
