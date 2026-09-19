"""Single-purpose Agent construction boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from artpm_agent.runtime.performance import import_module_timed


class AgentFactory:
    """Create an Agent without exposing construction details to hosts."""

    def __init__(self, builder: Callable[[], Any] | None = None) -> None:
        self._builder = builder

    def create(self, *, business_store: Any = None) -> Any:
        if self._builder is not None:
            return self._builder()
        module = import_module_timed("artpm_agent.agent")
        return module.ArtPMAgent(business_store=business_store)


__all__ = ["AgentFactory"]
