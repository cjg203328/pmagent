"""Lazy, process-owned construction for authoritative application stores."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from threading import RLock
from typing import Any

from artpm_agent.runtime.counters import increment_counter
from artpm_agent.runtime.performance import import_module_timed


def _config_get(config: Any, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key, default)
    current: Any = config
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


class StorageRegistry:
    """Construct every persistent store at most once for one process graph."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        vector_store_path: str | Path | None = None,
        config: Any = None,
        enable_vector_search: bool = True,
    ) -> None:
        if config is None and db_path is None:
            config_module = import_module_timed("artpm_agent.config")
            config = config_module.get_config()
        configured_db = db_path or _config_get(
            config,
            "database.conversation_db_path",
            "./data/conversations.db",
        )
        self.db_path = Path(configured_db).expanduser().resolve()
        configured_vectors = vector_store_path or _config_get(
            config,
            "database.vector_db_path",
            self.db_path.parent / "vector_store",
        )
        self.vector_store_path = Path(configured_vectors).expanduser().resolve()
        self.config = config
        self.enable_vector_search = bool(enable_vector_search)
        self._stores: dict[str, Any] = {}
        self._lock = RLock()
        increment_counter("runtime.storage_registry.constructed")

    def _get(self, name: str, factory: Callable[[], Any]) -> Any:
        cached = self._stores.get(name)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._stores.get(name)
            if cached is None:
                cached = factory()
                self._stores[name] = cached
                increment_counter(f"runtime.store.{name}.constructed")
        return cached

    @property
    def conversation(self):
        def build():
            module = import_module_timed("artpm_agent.memory.conversation_store")
            return module.ConversationStore(self.db_path)

        return self._get("conversation", build)

    @property
    def session(self):
        def build():
            module = import_module_timed("artpm_agent.memory.session_store")
            return module.SessionStore(self.conversation)

        return self._get("session", build)

    @property
    def permission(self):
        def build():
            module = import_module_timed("artpm_agent.security.permission_store")
            return module.PermissionStore(self.db_path)

        return self._get("permission", build)

    @property
    def workflow(self):
        def build():
            module = import_module_timed("artpm_agent.workflows.store")
            return module.WorkflowStore(self.db_path)

        return self._get("workflow", build)

    @property
    def knowledge(self):
        def build():
            embeddings = import_module_timed("artpm_agent.memory.embeddings")
            knowledge = import_module_timed(
                "artpm_agent.memory.workspace_knowledge_store"
            )
            embedding_provider = embeddings.create_embedding_provider(
                _config_get(self.config, "memory", {})
            )
            return knowledge.WorkspaceKnowledgeStore(
                self.db_path,
                vector_store_path=self.vector_store_path / "workspace_knowledge",
                embedding_provider=embedding_provider,
                enable_vector_search=self.enable_vector_search,
            )

        return self._get("knowledge", build)

    @property
    def wiki(self):
        def build():
            module = import_module_timed("artpm_agent.memory.wiki_store")
            return module.WorkspaceWikiStore(self.knowledge)

        return self._get("wiki", build)

    @property
    def profile(self):
        def build():
            profiles = import_module_timed("artpm_agent.profiles")
            policy = profiles.QuotePolicy(
                overhead_rate=float(
                    _config_get(self.config, "cost_config.overhead_rate", 0.15)
                ),
                tax_rate=float(_config_get(self.config, "cost_config.tax_rate", 0.06)),
                currency=str(
                    _config_get(self.config, "cost_config.currency", "CNY")
                ).upper(),
            )
            return profiles.AgentProfileStore(
                self.db_path,
                default_identity=profiles.AgentIdentity(),
                default_quote_policy=policy,
            )

        return self._get("profile", build)

    @property
    def chat_attachments(self):
        def build():
            module = import_module_timed("artpm_agent.utils.chat_attachments")
            return module.ChatAttachmentStore(self.db_path.parent / "chat_attachments")

        return self._get("chat_attachment", build)

    @property
    def artifact_generator(self):
        def build():
            module = import_module_timed("artpm_agent.artifacts.generator")
            return module.WorkspaceArtifactGenerator(
                self.db_path.parent / "artifacts" / "local-default"
            )

        return self._get("artifact_generator", build)

    @property
    def lifecycle(self):
        def build():
            module = import_module_timed("artpm_agent.memory.lifecycle")
            return module.MemoryLifecycleService(self)

        return self._get("memory_lifecycle", build)

    def snapshot(self) -> dict[str, bool]:
        with self._lock:
            return {name: True for name in sorted(self._stores)}


__all__ = ["StorageRegistry"]
