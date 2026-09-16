"""Provider-neutral model interface used by runtime orchestration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProviderPort(Protocol):
    def chat(
        self,
        message: str,
        *,
        system_prompt: str | None = None,
        history: Iterable[Mapping[str, Any]] | None = None,
        **options: Any,
    ) -> Any: ...


__all__ = ["ProviderPort"]
