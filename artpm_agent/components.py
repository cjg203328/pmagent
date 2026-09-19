"""Agent component ownership and construction boundaries."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class ComponentRegistry(Mapping[str, Any]):
    """Immutable view of components owned by one agent instance."""

    _components: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_components", MappingProxyType(dict(self._components)))

    def __getitem__(self, name: str) -> Any:
        return self._components[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._components)

    def __len__(self) -> int:
        return len(self._components)

    def get_required(self, name: str) -> Any:
        try:
            return self._components[name]
        except KeyError as error:
            raise LookupError(f"required agent component is missing: {name}") from error

    def as_dict(self) -> dict[str, Any]:
        return dict(self._components)


class ComponentFactory:
    """Build all dependencies once and return them as a registry."""

    @staticmethod
    def create(
        config: Any = None,
        *,
        orchestrator_class: type | None = None,
        business_store: Any = None,
    ) -> ComponentRegistry:
        if orchestrator_class is None:
            from artpm_agent.request_orchestrator import RequestOrchestrator

            orchestrator_class = RequestOrchestrator
        if business_store is None:
            orchestrator = orchestrator_class(config)
        else:
            orchestrator = orchestrator_class(
                config,
                business_store=business_store,
            )
        return ComponentRegistry(dict(orchestrator.__dict__))


__all__ = ["ComponentFactory", "ComponentRegistry"]
