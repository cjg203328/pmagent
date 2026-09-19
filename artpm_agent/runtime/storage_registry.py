"""Lazy, process-owned construction for authoritative application stores."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import RLock
from typing import Any

from artpm_agent.runtime.counters import increment_counter
from artpm_agent.runtime.performance import import_module_timed
from artpm_agent.runtime.storage_contracts import (
    AUTHORITATIVE_STORE_CONTRACTS,
    probe_store,
)


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
        business_db_path: str | Path | None = None,
        business_db_url: str | None = None,
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
        configured_business_path = business_db_path or _config_get(
            config,
            "database.db_path",
            self.db_path.parent / "artpm.db",
        )
        self.business_db_path = Path(configured_business_path).expanduser().resolve()
        self.business_db_url = str(
            business_db_url
            or os.getenv("DATABASE_URL")
            or _config_get(config, "database.url", "")
            or f"sqlite:///{self.business_db_path.as_posix()}"
        )
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
        self._readiness_cache: tuple[float, dict[str, dict[str, object]]] | None = None
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
    def business(self) -> Any:
        def build() -> Any:
            module = import_module_timed("artpm_agent.database.models")
            try:
                return module.DatabaseManager(self.business_db_url)
            except (ImportError, ModuleNotFoundError):
                fallback_enabled = os.getenv(
                    "ARTPM_SQLITE_FALLBACK",
                    "true",
                ).strip().lower() in {"1", "true", "yes", "on"}
                if (
                    not self.business_db_url.startswith("postgresql")
                    or not fallback_enabled
                ):
                    raise
                return module.DatabaseManager(
                    f"sqlite:///{self.business_db_path.as_posix()}"
                )

        return self._get("business", build)

    @property
    def conversation(self) -> Any:
        def build() -> Any:
            module = import_module_timed("artpm_agent.memory.conversation_store")
            return module.ConversationStore(self.db_path)

        return self._get("conversation", build)

    @property
    def session(self) -> Any:
        def build() -> Any:
            module = import_module_timed("artpm_agent.memory.session_store")
            return module.SessionStore(self.conversation)

        return self._get("session", build)

    @property
    def permission(self) -> Any:
        def build() -> Any:
            module = import_module_timed("artpm_agent.security.permission_store")
            return module.PermissionStore(self.db_path)

        return self._get("permission", build)

    @property
    def workflow(self) -> Any:
        def build() -> Any:
            module = import_module_timed("artpm_agent.workflows.store")
            return module.WorkflowStore(self.db_path)

        return self._get("workflow", build)

    @property
    def knowledge(self) -> Any:
        def build() -> Any:
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
    def wiki(self) -> Any:
        def build() -> Any:
            module = import_module_timed("artpm_agent.memory.wiki_store")
            return module.WorkspaceWikiStore(self.knowledge)

        return self._get("wiki", build)

    @property
    def profile(self) -> Any:
        def build() -> Any:
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
    def chat_attachments(self) -> Any:
        def build() -> Any:
            module = import_module_timed("artpm_agent.utils.chat_attachments")
            return module.ChatAttachmentStore(self.db_path.parent / "chat_attachments")

        return self._get("chat_attachment", build)

    @property
    def artifact_generator(self) -> Any:
        def build() -> Any:
            module = import_module_timed("artpm_agent.artifacts.generator")
            return module.WorkspaceArtifactGenerator(
                self.db_path.parent / "artifacts" / "local-default"
            )

        return self._get("artifact_generator", build)

    @property
    def lifecycle(self) -> Any:
        def build() -> Any:
            module = import_module_timed("artpm_agent.memory.lifecycle")
            return module.MemoryLifecycleService(self)

        return self._get("memory_lifecycle", build)

    def snapshot(self) -> dict[str, bool]:
        with self._lock:
            return {name: True for name in sorted(self._stores)}

    def initialize_authoritative_schema(self) -> None:
        """Initialize source-of-truth schemas without constructing an Agent."""

        _ = self.business
        _ = self.conversation
        _ = self.session
        # Knowledge migrations are independent of vector initialization. Run
        # them with vectors disabled so readiness never loads a large index.
        if "knowledge" not in self._stores:
            knowledge = import_module_timed(
                "artpm_agent.memory.workspace_knowledge_store"
            )
            knowledge.WorkspaceKnowledgeStore(
                self.db_path,
                vector_store_path=self.vector_store_path / "workspace_knowledge",
                enable_vector_search=False,
            )

    def readiness(
        self,
        *,
        force: bool = False,
        cache_ttl_seconds: float = 1.0,
    ) -> dict[str, dict[str, object]]:
        """Probe every authority while keeping expensive capabilities lazy."""

        now = time.monotonic()
        with self._lock:
            cached = self._readiness_cache
            if (
                not force
                and cached is not None
                and now - cached[0] <= max(0.0, cache_ttl_seconds)
            ):
                return {name: dict(value) for name, value in cached[1].items()}

        self.initialize_authoritative_schema()
        ephemeral_knowledge: Any | None = None
        knowledge_store = self._stores.get("knowledge")
        if knowledge_store is None:
            module = import_module_timed("artpm_agent.memory.workspace_knowledge_store")
            ephemeral_knowledge = module.WorkspaceKnowledgeStore(
                self.db_path,
                vector_store_path=self.vector_store_path / "workspace_knowledge",
                enable_vector_search=False,
            )
            knowledge_store = ephemeral_knowledge
        stores: dict[str, object] = {
            "business_store": self.business,
            "conversation_store": self.conversation,
            "session_store": self.session,
            # Conversation, Session and Knowledge can share one local SQLite
            # file, but readiness probes the real authority object for each
            # contract rather than treating physical co-location as ownership.
            "knowledge_store": knowledge_store,
        }
        try:
            result = {
                name: probe_store(stores[name], contract).to_dict()
                for name, contract in AUTHORITATIVE_STORE_CONTRACTS.items()
            }
        finally:
            close = getattr(ephemeral_knowledge, "close", None)
            if callable(close):
                close()
        with self._lock:
            self._readiness_cache = (now, result)
        return {name: dict(value) for name, value in result.items()}

    def close(self) -> None:
        """Close all process-owned stores exactly once where supported."""

        with self._lock:
            stores = list(self._stores.values())
            self._stores.clear()
            self._readiness_cache = None
        closed: set[int] = set()
        for store in stores:
            if id(store) in closed:
                continue
            closed.add(id(store))
            close = getattr(store, "close", None) or getattr(store, "dispose", None)
            if callable(close):
                close()


__all__ = ["StorageRegistry"]
