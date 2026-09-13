"""Unified capability registry: aggregates built-in and plugin contributions.

Mirrors the deepseek-harness idea of a registry where every model-facing
capability (tool / handler / provider) is registered instead of hard-wired:
plugins contribute through the loader, the registry exposes typed lookups, and
runtime seams (e.g. ``create_llm_client``) consult it as an extension point.

The registry only records contributions; it never executes plugin code and
never mutates process-global registries by itself. Plugin tool/handler classes
still require explicit host integration before use, exactly like plugin skills.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from .loader import (
    PluginLoadReport,
)

#: Callable contract for a provider factory: ``(config) -> client``.
ProviderFactory = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class CapabilityEntry:
    """One registered capability with its provenance metadata."""

    name: str
    kind: str  # "tool" | "handler" | "provider" | "skill"
    factory: Any
    metadata: Mapping[str, Any] = MappingProxyType({})
    plugin_id: str = ""
    plugin_version: str = ""


class CapabilityRegistry:
    """Thread-safe registry of tools, handlers, and providers."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._tools: dict[str, CapabilityEntry] = {}
        self._handlers: dict[str, CapabilityEntry] = {}
        self._providers: dict[str, CapabilityEntry] = {}
        self._skills: dict[str, CapabilityEntry] = {}

    # ── registration ──

    def register_tool(
        self,
        name: str,
        factory: Any,
        *,
        metadata: Mapping[str, Any] | None = None,
        plugin_id: str = "",
        plugin_version: str = "",
    ) -> None:
        """Register a tool class/factory under a unique name."""
        self._register(self._tools, name, "tool", factory, metadata, plugin_id, plugin_version)

    def register_handler(
        self,
        name: str,
        factory: Any,
        *,
        metadata: Mapping[str, Any] | None = None,
        plugin_id: str = "",
        plugin_version: str = "",
    ) -> None:
        self._register(self._handlers, name, "handler", factory, metadata, plugin_id, plugin_version)

    def register_provider(
        self,
        name: str,
        factory: ProviderFactory,
        *,
        metadata: Mapping[str, Any] | None = None,
        plugin_id: str = "",
        plugin_version: str = "",
    ) -> None:
        if not callable(factory):
            raise TypeError("provider factory must be callable")
        self._register(self._providers, name, "provider", factory, metadata, plugin_id, plugin_version)

    def register_skill(
        self,
        name: str,
        skill_class: type,
        *,
        metadata: Mapping[str, Any] | None = None,
        plugin_id: str = "",
        plugin_version: str = "",
    ) -> None:
        self._register(self._skills, name, "skill", skill_class, metadata, plugin_id, plugin_version)

    @staticmethod
    def _register(
        store: dict[str, CapabilityEntry],
        name: str,
        kind: str,
        factory: Any,
        metadata: Mapping[str, Any] | None,
        plugin_id: str,
        plugin_version: str,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{kind} name must be a non-empty string")
        normalized = name.strip()
        with RLock():
            if normalized in store:
                raise ValueError(f"duplicate {kind} registration: {normalized}")
            store[normalized] = CapabilityEntry(
                name=normalized,
                kind=kind,
                factory=factory,
                metadata=MappingProxyType(dict(metadata or {})),
                plugin_id=plugin_id,
                plugin_version=plugin_version,
            )

    def merge_plugin_report(self, report: PluginLoadReport) -> int:
        """Register every contribution from a plugin load report.

        Returns the number of newly registered capabilities. Duplicate names
        fail the whole merge so a bad plugin cannot half-register.
        """
        merged = 0
        with self._lock:
            for item in report.registrations:
                self.register_skill(
                    item.name,
                    item.skill_class,
                    metadata=item.metadata,
                    plugin_id=item.plugin_id,
                    plugin_version=item.plugin_version,
                )
                merged += 1
            for item in report.tool_registrations:
                self.register_tool(
                    item.name,
                    item.tool_class,
                    metadata=item.metadata,
                    plugin_id=item.plugin_id,
                    plugin_version=item.plugin_version,
                )
                merged += 1
            for item in report.handler_registrations:
                self.register_handler(
                    item.name,
                    item.handler_class,
                    metadata=item.metadata,
                    plugin_id=item.plugin_id,
                    plugin_version=item.plugin_version,
                )
                merged += 1
            for item in report.provider_registrations:
                self.register_provider(
                    item.name,
                    item.provider_class,
                    metadata=item.metadata,
                    plugin_id=item.plugin_id,
                    plugin_version=item.plugin_version,
                )
                merged += 1
        return merged

    # ── lookup ──

    def tool(self, name: str) -> Optional[CapabilityEntry]:
        return self._tools.get(name.strip())

    def handler(self, name: str) -> Optional[CapabilityEntry]:
        return self._handlers.get(name.strip())

    def provider(self, name: str) -> Optional[CapabilityEntry]:
        return self._providers.get(name.strip())

    def skill(self, name: str) -> Optional[CapabilityEntry]:
        return self._skills.get(name.strip())

    def tools(self) -> tuple[CapabilityEntry, ...]:
        return tuple(sorted(self._tools.values(), key=lambda item: item.name))

    def handlers(self) -> tuple[CapabilityEntry, ...]:
        return tuple(sorted(self._handlers.values(), key=lambda item: item.name))

    def providers(self) -> tuple[CapabilityEntry, ...]:
        return tuple(sorted(self._providers.values(), key=lambda item: item.name))

    def skills(self) -> tuple[CapabilityEntry, ...]:
        return tuple(sorted(self._skills.values(), key=lambda item: item.name))

    def all(self) -> tuple[CapabilityEntry, ...]:
        return (
            self.tools() + self.handlers() + self.providers() + self.skills()
        )

    def clear(self) -> None:
        with self._lock:
            self._tools.clear()
            self._handlers.clear()
            self._providers.clear()
            self._skills.clear()


_registry: Optional[CapabilityRegistry] = None
_registry_lock = RLock()


def get_capability_registry() -> CapabilityRegistry:
    """Return the process-wide capability registry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = CapabilityRegistry()
    return _registry


def reset_capability_registry() -> None:
    """Replace the singleton (used by tests to isolate registrations)."""
    global _registry
    with _registry_lock:
        _registry = CapabilityRegistry()
