"""Canonical process runtime and workspace-scoped service factory."""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from types import ModuleType
from typing import Any

from artpm_agent.runtime.agent_factory import AgentFactory
from artpm_agent.runtime.counters import increment_counter
from artpm_agent.runtime.keyed_locks import KeyedLockPool, ScopedLockHandle
from artpm_agent.runtime.performance import import_module_timed
from artpm_agent.runtime.storage_registry import StorageRegistry

_SCOPE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(slots=True)
class _ScopedCacheEntry:
    value: Any
    touched_at: float


@dataclass(slots=True)
class _ArtifactRuntime:
    generator: Any
    coordinator: Any = None


class _UnavailableArtifactPlanner:
    """Keep deterministic artifact plans available without a live model."""

    def chat(self, *_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("model service is unavailable")


class RuntimeFactory:
    """Own one Agent/StorageRegistry and isolate workspace execution locks."""

    def __init__(
        self,
        *,
        storage: StorageRegistry | None = None,
        agent_factory: Callable[[], Any] | None = None,
        scoped_cache_max_entries: int = 128,
        scoped_cache_ttl_seconds: float = 30 * 60,
    ) -> None:
        if (
            isinstance(scoped_cache_max_entries, bool)
            or not isinstance(scoped_cache_max_entries, int)
            or scoped_cache_max_entries < 1
        ):
            raise ValueError("scoped_cache_max_entries must be a positive integer")
        if (
            isinstance(scoped_cache_ttl_seconds, bool)
            or not isinstance(scoped_cache_ttl_seconds, (int, float))
            or scoped_cache_ttl_seconds <= 0
        ):
            raise ValueError("scoped_cache_ttl_seconds must be positive")
        self.storage = storage or StorageRegistry()
        self._agent_factory = AgentFactory(agent_factory)
        self._agent: Any = None
        self._agent_lock = RLock()
        self._scoped_cache_max_entries = scoped_cache_max_entries
        self._scoped_cache_ttl_seconds = float(scoped_cache_ttl_seconds)
        self._scoped_cache_lock = RLock()
        self._clock: Callable[[], float] = monotonic
        self._workspace_locks = KeyedLockPool[tuple[str, str]](
            max_entries=scoped_cache_max_entries,
            ttl_seconds=float(scoped_cache_ttl_seconds),
            clock=lambda: self._clock(),
        )
        self._coordinators: OrderedDict[tuple[str, str, str], _ScopedCacheEntry] = (
            OrderedDict()
        )
        self._artifact_runtimes: OrderedDict[
            tuple[str, str, str], _ScopedCacheEntry
        ] = OrderedDict()
        self._learning_services: dict[str, Any] = {}
        self._learning_lock = RLock()
        self._event_bus: Any = None
        increment_counter("runtime.factory.constructed")

    @staticmethod
    def _scope_identifier(value: Any, field: str) -> str:
        resolved = str(value or "").strip()
        if not _SCOPE_IDENTIFIER.fullmatch(resolved):
            raise ValueError(f"{field} has an invalid format")
        return resolved

    def _scope_key(
        self,
        tenant_context: Any,
        profile_id: str,
    ) -> tuple[str, str, str]:
        tenant_id = self._scope_identifier(
            getattr(tenant_context, "tenant_id", None),
            "tenant_id",
        )
        require_workspace = getattr(tenant_context, "require_workspace", None)
        if not callable(require_workspace):
            raise TypeError("tenant_context must expose require_workspace")
        workspace_id = self._scope_identifier(
            require_workspace(),
            "workspace_id",
        )
        return (
            tenant_id,
            workspace_id,
            self._scope_identifier(profile_id or "local-default", "profile_id"),
        )

    @staticmethod
    def _path_segment(value: str) -> str:
        # Colon is valid in runtime identifiers but not in Windows paths.
        return value.replace(":", "%3A")

    @staticmethod
    def _close_cached(value: Any) -> None:
        values = (
            (value.coordinator, value.generator)
            if isinstance(value, _ArtifactRuntime)
            else (value,)
        )
        for item in values:
            close = getattr(item, "close", None)
            if callable(close):
                close()

    def _cache_get(
        self,
        cache: OrderedDict[tuple[str, str, str], _ScopedCacheEntry],
        key: tuple[str, str, str],
    ) -> Any:
        expired: list[Any] = []
        now = self._clock()
        with self._scoped_cache_lock:
            for candidate, candidate_entry in tuple(cache.items()):
                if now - candidate_entry.touched_at >= self._scoped_cache_ttl_seconds:
                    expired.append(cache.pop(candidate).value)
            entry = cache.get(key)
            if entry is not None:
                entry.touched_at = now
                cache.move_to_end(key)
                value = entry.value
            else:
                value = None
        for item in expired:
            self._close_cached(item)
        return value

    def _cache_put(
        self,
        cache: OrderedDict[tuple[str, str, str], _ScopedCacheEntry],
        key: tuple[str, str, str],
        value: Any,
    ) -> Any:
        evicted: list[Any] = []
        with self._scoped_cache_lock:
            existing = cache.get(key)
            if existing is not None:
                existing.touched_at = self._clock()
                cache.move_to_end(key)
                winner = existing.value
            else:
                cache[key] = _ScopedCacheEntry(value=value, touched_at=self._clock())
                while len(cache) > self._scoped_cache_max_entries:
                    _, entry = cache.popitem(last=False)
                    evicted.append(entry.value)
                winner = value
        if winner is not value:
            self._close_cached(value)
        for item in evicted:
            self._close_cached(item)
        return winner

    def prune_scoped_runtime_cache(self) -> int:
        """Remove expired workflow/artifact runtimes and return the count."""

        expired: list[Any] = []
        now = self._clock()
        with self._scoped_cache_lock:
            for cache in (self._coordinators, self._artifact_runtimes):
                for key, entry in tuple(cache.items()):
                    if now - entry.touched_at >= self._scoped_cache_ttl_seconds:
                        expired.append(cache.pop(key).value)
        for item in expired:
            self._close_cached(item)
        self._workspace_locks.prune()
        return len(expired)

    def clear_scoped_runtime_cache(self) -> None:
        """Close and forget every cached workspace/profile runtime."""

        with self._scoped_cache_lock:
            cached = [
                entry.value
                for cache in (self._coordinators, self._artifact_runtimes)
                for entry in cache.values()
            ]
            self._coordinators.clear()
            self._artifact_runtimes.clear()
        for item in cached:
            self._close_cached(item)
        self._workspace_locks.clear()

    def agent(self) -> Any:
        cached = self._agent
        if cached is not None:
            return cached
        with self._agent_lock:
            if self._agent is None:
                self._agent = self._agent_factory.create(
                    business_store=self.storage.business,
                )
                memory = getattr(self._agent, "memory", None)
                bind_knowledge = getattr(
                    memory,
                    "bind_workspace_knowledge_store",
                    None,
                )
                if callable(bind_knowledge):
                    bind_knowledge(self.storage.knowledge)
                increment_counter("runtime.agent.constructed")
        return self._agent

    def workspace_lock(
        self,
        tenant_id: str,
        workspace_id: str,
    ) -> ScopedLockHandle[tuple[str, str]]:
        key = (
            self._scope_identifier(tenant_id or "local", "tenant_id"),
            self._scope_identifier(
                workspace_id or "local-default",
                "workspace_id",
            ),
        )
        lock = self._workspace_locks.handle(key)
        increment_counter("runtime.workspace_lock.requested")
        return lock

    def event_bus(self) -> Any:
        if self._event_bus is None:
            with self._learning_lock:
                if self._event_bus is None:
                    module = import_module_timed("artpm_agent.runtime.event_bus")
                    self._event_bus = module.EventBus()
                    increment_counter("runtime.event_bus.constructed")
        return self._event_bus

    def learning_service(self, name: str) -> Any:
        """Return one optional learning service without constructing its peers."""

        if name in self._learning_services:
            return self._learning_services[name]
        factories: dict[str, tuple[str, Callable[[ModuleType], Any]]] = {
            "episode_store": (
                "artpm_agent.harness.outcome_recorder",
                lambda module: import_module_timed(
                    "artpm_agent.memory.episode_store"
                ).EpisodeStore(module.default_episode_db_path()),
            ),
            "feedback_store": (
                "artpm_agent.memory.feedback_store",
                lambda module: module.get_default_feedback_store(),
            ),
            "strategy_store": (
                "artpm_agent.evolution.strategy_store",
                lambda module: module.get_default_strategy_store(),
            ),
            "reflection_scheduler": (
                "artpm_agent.evolution.scheduler",
                lambda module: module.get_default_scheduler(),
            ),
            "meta_memory_store": (
                "artpm_agent.evolution.meta_memory",
                lambda module: module.get_default_meta_memory_store(),
            ),
            "consolidation_scheduler": (
                "artpm_agent.memory.consolidation",
                lambda module: module.ConsolidationScheduler(),
            ),
            "outbox_store": (
                "artpm_agent.memory.outbox_store",
                lambda module: module.OutboxStore(str(self.storage.db_path)),
            ),
        }
        if name not in factories:
            raise KeyError(f"unknown runtime service: {name}")
        with self._learning_lock:
            if name not in self._learning_services:
                module_name, build = factories[name]
                try:
                    service = build(import_module_timed(module_name))
                    increment_counter(f"runtime.service.{name}.constructed")
                except Exception:  # noqa: BLE001 - optional learning capability
                    service = None
                self._learning_services[name] = service
        return self._learning_services[name]

    def learning_services(self) -> dict[str, Any]:
        names = (
            "episode_store",
            "feedback_store",
            "strategy_store",
            "reflection_scheduler",
            "meta_memory_store",
            "consolidation_scheduler",
            "outbox_store",
        )
        return {name: self.learning_service(name) for name in names}

    def memory_lifecycle(self) -> Any:
        """Return lifecycle governance bound to the existing learning stores."""
        lifecycle = self.storage.lifecycle
        learning = self.learning_services()
        lifecycle.bind_auxiliary_stores(
            episode_store=learning.get("episode_store"),
            feedback_store=learning.get("feedback_store"),
            strategy_store=learning.get("strategy_store"),
            meta_memory_store=learning.get("meta_memory_store"),
        )
        return lifecycle

    def workflow_coordinator(
        self,
        tenant_context: Any,
        *,
        profile_id: str = "local-default",
    ) -> Any:
        key = self._scope_key(tenant_context, profile_id)
        tenant_id, workspace_id, resolved_profile_id = key
        cached = self._cache_get(self._coordinators, key)
        if cached is not None:
            return cached
        with self.workspace_lock(tenant_id, workspace_id):
            cached = self._cache_get(self._coordinators, key)
            if cached is not None:
                return cached
            agent = self.agent()
            scoped_router = agent.router.for_tenant(tenant_context)
            designer = import_module_timed("artpm_agent.workflows.designer")
            coordinator_module = import_module_timed(
                "artpm_agent.workflows.coordinator"
            )
            allowlist = designer.capability_allowlist_from_skill_metadata(
                scoped_router.list_skills()
            )
            cached = coordinator_module.WorkflowCoordinator(
                self.storage.workflow,
                coordinator_module.ScopedWorkflowAgent(agent, scoped_router),
                capability_allowlist=allowlist,
                workspace_id=workspace_id,
                profile_id=resolved_profile_id,
                tenant_id=tenant_id,
            )
            cached = self._cache_put(self._coordinators, key, cached)
            increment_counter("runtime.workflow_coordinator.constructed")
            return cached

    def artifact_generator(
        self,
        tenant_context: Any,
        *,
        profile_id: str = "local-default",
    ) -> Any:
        key = self._scope_key(tenant_context, profile_id)
        runtime = self._cache_get(self._artifact_runtimes, key)
        if runtime is not None:
            return runtime.generator
        tenant_id, workspace_id, resolved_profile_id = key
        with self.workspace_lock(tenant_id, workspace_id):
            runtime = self._cache_get(self._artifact_runtimes, key)
            if runtime is None:
                module = import_module_timed("artpm_agent.artifacts.generator")
                root = self.storage.db_path.parent / "artifacts"
                root = root.joinpath(
                    self._path_segment(tenant_id),
                    self._path_segment(workspace_id),
                    self._path_segment(resolved_profile_id),
                )
                runtime = _ArtifactRuntime(
                    generator=module.WorkspaceArtifactGenerator(root)
                )
                runtime = self._cache_put(self._artifact_runtimes, key, runtime)
                increment_counter("runtime.artifact_generator.constructed")
            return runtime.generator

    def artifact_coordinator(
        self,
        tenant_context: Any,
        *,
        profile_id: str = "local-default",
    ) -> Any:
        key = self._scope_key(tenant_context, profile_id)
        tenant_id, workspace_id, _resolved_profile_id = key
        with self.workspace_lock(tenant_id, workspace_id):
            runtime = self._cache_get(self._artifact_runtimes, key)
            if runtime is None:
                self.artifact_generator(
                    tenant_context,
                    profile_id=profile_id,
                )
                runtime = self._cache_get(self._artifact_runtimes, key)
            if runtime is None:  # pragma: no cover - defensive cache invariant
                raise RuntimeError("artifact runtime could not be constructed")
            if runtime.coordinator is None:
                module = import_module_timed("artpm_agent.artifacts.coordinator")
                llm_client = getattr(self.agent(), "llm_client", None)
                if not callable(getattr(llm_client, "chat", None)):
                    llm_client = _UnavailableArtifactPlanner()
                runtime.coordinator = module.ArtifactCoordinator(
                    runtime.generator,
                    llm_client,
                )
                increment_counter("runtime.artifact_coordinator.constructed")
            return runtime.coordinator

    def turn_services(
        self,
        tenant_context: Any,
        *,
        profile_id: str = "local-default",
    ) -> Any:
        request_services = import_module_timed("artpm_agent.runtime.request_services")
        learning = self.learning_services()
        agent = self.agent()
        return request_services.TurnServiceBundle(
            profile_store=self.storage.profile,
            knowledge_store=self.storage.knowledge,
            permission_store=self.storage.permission,
            artifact_coordinator=self.artifact_coordinator(
                tenant_context,
                profile_id=profile_id,
            ),
            workflow_coordinator=self.workflow_coordinator(
                tenant_context,
                profile_id=profile_id,
            ),
            session_store=self.storage.session,
            memory_manager=getattr(agent, "memory", None),
            tencentdb_memory=getattr(agent, "tencentdb_memory", None),
            event_bus=self.event_bus(),
            **learning,
        )

    def harness_runtime(
        self,
        tenant_context: Any,
        *,
        profile_id: str = "local-default",
    ) -> Any:
        runtime_module = import_module_timed("artpm_agent.harness.runtime")
        services = self.turn_services(tenant_context, profile_id=profile_id)
        return runtime_module.LocalHarnessRuntime(
            self.agent(),
            services=services,
            tenant_context=tenant_context,
        ).for_tenant(tenant_context)

    def close(self) -> None:
        self.clear_scoped_runtime_cache()
        agent = self._agent
        self._agent = None
        close = getattr(agent, "close", None)
        if callable(close):
            close()
        with self._learning_lock:
            learning_services = list(self._learning_services.values())
            self._learning_services.clear()
            event_bus = self._event_bus
            self._event_bus = None
        for service in learning_services:
            close = getattr(service, "close", None)
            if callable(close):
                close()
        clear = getattr(event_bus, "clear", None)
        if callable(clear):
            clear()
        self.storage.close()

    def reset_agent(self) -> None:
        """Replace the process Agent after a confirmed configuration change."""

        with self._agent_lock:
            current = self._agent
            self._agent = None
        self.clear_scoped_runtime_cache()
        close = getattr(current, "close", None)
        if callable(close):
            close()


_DEFAULT_FACTORY: RuntimeFactory | None = None
_DEFAULT_FACTORY_LOCK = RLock()


def get_runtime_factory() -> RuntimeFactory:
    global _DEFAULT_FACTORY
    if _DEFAULT_FACTORY is not None:
        return _DEFAULT_FACTORY
    with _DEFAULT_FACTORY_LOCK:
        if _DEFAULT_FACTORY is None:
            _DEFAULT_FACTORY = RuntimeFactory()
    return _DEFAULT_FACTORY


def reset_runtime_factory() -> None:
    global _DEFAULT_FACTORY
    with _DEFAULT_FACTORY_LOCK:
        current = _DEFAULT_FACTORY
        _DEFAULT_FACTORY = None
    if current is not None:
        current.close()


__all__ = [
    "RuntimeFactory",
    "get_runtime_factory",
    "reset_runtime_factory",
]
