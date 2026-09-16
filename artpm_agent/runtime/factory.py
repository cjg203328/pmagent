"""Canonical process runtime and workspace-scoped service factory."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Any

from artpm_agent.runtime.counters import increment_counter
from artpm_agent.runtime.agent_factory import AgentFactory
from artpm_agent.runtime.performance import import_module_timed
from artpm_agent.runtime.storage_registry import StorageRegistry


class RuntimeFactory:
    """Own one Agent/StorageRegistry and isolate workspace execution locks."""

    def __init__(
        self,
        *,
        storage: StorageRegistry | None = None,
        agent_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.storage = storage or StorageRegistry()
        self._agent_factory = AgentFactory(agent_factory)
        self._agent: Any = None
        self._agent_lock = RLock()
        self._workspace_locks: dict[tuple[str, str], RLock] = {}
        self._workspace_locks_guard = RLock()
        self._coordinators: dict[tuple[str, str, str], Any] = {}
        self._learning_services: dict[str, Any] = {}
        self._learning_lock = RLock()
        self._event_bus: Any = None
        increment_counter("runtime.factory.constructed")

    def agent(self) -> Any:
        cached = self._agent
        if cached is not None:
            return cached
        with self._agent_lock:
            if self._agent is None:
                self._agent = self._agent_factory.create()
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

    def workspace_lock(self, tenant_id: str, workspace_id: str) -> RLock:
        key = (str(tenant_id or "local"), str(workspace_id or "local-default"))
        with self._workspace_locks_guard:
            lock = self._workspace_locks.get(key)
            if lock is None:
                lock = RLock()
                self._workspace_locks[key] = lock
                increment_counter("runtime.workspace_lock.constructed")
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
        factories = {
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
        }
        if name not in factories:
            raise KeyError(f"unknown runtime service: {name}")
        with self._learning_lock:
            if name not in self._learning_services:
                module_name, build = factories[name]
                try:
                    service = build(import_module_timed(module_name))
                    increment_counter(f"runtime.service.{name}.constructed")
                except Exception:
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
        )
        return {name: self.learning_service(name) for name in names}

    def workflow_coordinator(
        self,
        tenant_context: Any,
        *,
        profile_id: str = "local-default",
    ) -> Any:
        tenant_id = tenant_context.tenant_id
        workspace_id = tenant_context.require_workspace()
        key = (tenant_id, workspace_id, str(profile_id or "local-default"))
        cached = self._coordinators.get(key)
        if cached is not None:
            return cached
        with self.workspace_lock(tenant_id, workspace_id):
            cached = self._coordinators.get(key)
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
                profile_id=key[2],
                tenant_id=tenant_id,
            )
            self._coordinators[key] = cached
            increment_counter("runtime.workflow_coordinator.constructed")
            return cached

    def turn_services(self, tenant_context: Any) -> Any:
        request_services = import_module_timed("artpm_agent.runtime.request_services")
        learning = self.learning_services()
        agent = self.agent()
        return request_services.TurnServiceBundle(
            profile_store=self.storage.profile,
            knowledge_store=self.storage.knowledge,
            permission_store=self.storage.permission,
            workflow_coordinator=self.workflow_coordinator(tenant_context),
            session_store=self.storage.session,
            memory_manager=getattr(agent, "memory", None),
            tencentdb_memory=getattr(agent, "tencentdb_memory", None),
            event_bus=self.event_bus(),
            **learning,
        )

    def harness_runtime(self, tenant_context: Any) -> Any:
        runtime_module = import_module_timed("artpm_agent.harness.runtime")
        services = self.turn_services(tenant_context)
        return runtime_module.LocalHarnessRuntime(
            self.agent(),
            services=services,
            tenant_context=tenant_context,
        ).for_tenant(tenant_context)

    def close(self) -> None:
        agent = self._agent
        close = getattr(agent, "close", None)
        if callable(close):
            close()

    def reset_agent(self) -> None:
        """Replace the process Agent after a confirmed configuration change."""

        with self._agent_lock:
            current = self._agent
            self._agent = None
            self._coordinators.clear()
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
