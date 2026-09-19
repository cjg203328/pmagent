"""Dependency-injected services used by the REST gateway.

The HTTP layer only knows these small contracts.  Deployments can replace the
chat, capability, and permission executors without importing ``ArtPMAgent``;
the default adapter is lazy and exists for the local/CLI deployment.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing, nullcontext
from dataclasses import dataclass, field
from hmac import compare_digest
from pathlib import Path
from typing import Any

from artpm_agent.api.contracts import (
    CapabilityCatalog,
    ConversationStorePort,
    EventBusPort,
    PermissionStorePort,
    WorkflowEnginePort,
    WorkflowStorePort,
)
from artpm_agent.runtime.counters import counter_snapshot
from artpm_agent.security.permission_store import PermissionRequest

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ROLE_VALUES = frozenset({"user", "admin"})
_ACTOR_KINDS = frozenset({"human", "service"})

_STORE_REQUIRED_TABLES: dict[str, tuple[str, ...]] = {
    "conversation_store": ("workspaces", "conversations", "messages"),
    "permission_store": ("permission_requests",),
    "workflow_store": ("workflow_definitions", "workflow_runs"),
}


def _store_status(
    value: Any, *, required_tables: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Probe one SQLite store without creating or mutating its database."""

    if value is None:
        return {"status": "missing"}
    db_path = getattr(value, "db_path", None)
    if not db_path:
        return {"status": "error", "message": "store path is unavailable"}

    path = Path(str(db_path)).expanduser().resolve()
    if not path.is_file():
        return {"status": "error", "message": "store database is missing"}

    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, timeout=1.0, uri=True)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("SELECT 1").fetchone()
            if required_tables:
                placeholders = ", ".join("?" for _ in required_tables)
                rows = connection.execute(
                    f"SELECT name FROM sqlite_master "
                    f"WHERE type = 'table' AND name IN ({placeholders})",
                    required_tables,
                ).fetchall()
                found = {str(row[0]) for row in rows}
                missing = sorted(set(required_tables) - found)
                if missing:
                    return {
                        "status": "error",
                        "message": "store schema is incomplete",
                        "missing_tables": missing,
                    }
    except (OSError, sqlite3.Error):
        return {"status": "error", "message": "store connection failed"}
    return {"status": "ok"}


class GatewayServiceError(RuntimeError):
    """A service contract failure that should be rendered by the gateway."""


class IdentityError(ValueError):
    """Raised when a trusted proxy identity is absent or malformed."""


def validate_identifier(value: Any, field: str, *, max_length: int = 128) -> str:
    """Validate IDs at the API trust boundary.

    Internal stores perform their own validation too; doing it here prevents
    ambiguous tenant/path values from reaching those stores or logs.
    """

    if not isinstance(value, str):
        raise IdentityError(f"{field} must be a string")
    normalized = value.strip()
    if (
        len(normalized) == 0
        or len(normalized) > max_length
        or not _IDENTIFIER_RE.fullmatch(normalized)
    ):
        raise IdentityError(f"{field} has an invalid format")
    return normalized


@dataclass(frozen=True, slots=True)
class RequestPrincipal:
    """Identity resolved by a trusted ingress or dependency override."""

    workspace_id: str
    actor_id: str
    actor_role: str = "user"
    actor_kind: str = "human"
    profile_id: str = "local-default"
    tenant_id: str = "local"

    def __post_init__(self) -> None:
        validate_identifier(self.workspace_id, "workspace_id")
        validate_identifier(self.actor_id, "actor_id")
        validate_identifier(self.profile_id, "profile_id")
        validate_identifier(self.tenant_id, "tenant_id")
        if self.actor_role not in _ROLE_VALUES:
            raise IdentityError("actor_role must be user or admin")
        if self.actor_kind not in _ACTOR_KINDS:
            raise IdentityError("actor_kind must be human or service")

    def tenant_context(self, *, request_id: str | None = None) -> Any:
        """Create the server-owned tenancy object passed to host runtimes."""

        from artpm_agent.tenancy import TenantContext

        return TenantContext(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            principal_id=self.actor_id,
            roles=frozenset({self.actor_role}),
            request_id=request_id,
        )


class TrustedHeaderIdentityResolver:
    """Resolve identity from headers set by a trusted reverse proxy.

    ``gateway_secret`` is optional for local development.  When it is absent,
    identity headers are accepted only from a loopback client (or an explicit
    local test client).  This keeps the convenient local setup while preventing
    a remote caller from self-assigning a tenant or the ``admin`` role.  A
    cloud/reverse-proxy deployment must configure ``ARTPM_GATEWAY_SHARED_SECRET``
    and strip any client-supplied identity headers before adding its own values.
    """

    WORKSPACE_HEADER = "x-workspace-id"
    ACTOR_HEADER = "x-actor-id"
    ROLE_HEADER = "x-actor-role"
    KIND_HEADER = "x-actor-kind"
    TENANT_HEADER = "x-tenant-id"
    PROFILE_HEADER = "x-profile-id"
    TOKEN_HEADER = "x-gateway-token"
    _LOCAL_CLIENT_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})
    _FORWARDED_HEADERS = frozenset({"forwarded", "x-forwarded-for", "x-forwarded-host"})

    def __init__(
        self, gateway_secret: str | None = None, *, require_secret: bool = False
    ):
        self.gateway_secret = gateway_secret or os.getenv("ARTPM_GATEWAY_SHARED_SECRET")
        environment = (
            (os.getenv("ARTPM_ENV") or os.getenv("ENV") or "development")
            .strip()
            .lower()
        )
        self.production = environment in {"prod", "production"}
        self.require_secret = bool(
            require_secret or self.gateway_secret or self.production
        )

    def __call__(self, request: Any) -> RequestPrincipal:
        headers = getattr(request, "headers", request)
        token = headers.get(self.TOKEN_HEADER)
        if self.require_secret:
            if (
                not self.gateway_secret
                or not token
                or not compare_digest(str(token), str(self.gateway_secret))
            ):
                raise IdentityError("trusted gateway token is missing or invalid")
        else:
            # Header identity is a convenience for the local desktop app only.
            # Without this guard, a public API listener lets any caller choose
            # an arbitrary workspace/tenant and promote itself to admin.
            client = getattr(request, "client", None)
            client_host = str(getattr(client, "host", "") or "").strip().lower()
            forwarded = any(name in headers for name in self._FORWARDED_HEADERS)
            if client_host not in self._LOCAL_CLIENT_HOSTS or forwarded:
                raise IdentityError(
                    "a gateway token is required for non-local or proxied requests"
                )

        workspace_id = headers.get(self.WORKSPACE_HEADER)
        actor_id = headers.get(self.ACTOR_HEADER)
        actor_role = (headers.get(self.ROLE_HEADER) or "user").strip().lower()
        actor_kind = (headers.get(self.KIND_HEADER) or "human").strip().lower()
        tenant_id = (headers.get(self.TENANT_HEADER) or "local").strip()
        profile_id = (headers.get(self.PROFILE_HEADER) or "local-default").strip()
        if workspace_id is None or actor_id is None:
            raise IdentityError("x-workspace-id and x-actor-id are required")
        return RequestPrincipal(
            workspace_id=validate_identifier(workspace_id, "workspace_id"),
            actor_id=validate_identifier(actor_id, "actor_id"),
            actor_role=actor_role,
            actor_kind=actor_kind,
            profile_id=validate_identifier(profile_id, "profile_id"),
            tenant_id=validate_identifier(tenant_id, "tenant_id"),
        )


@dataclass(frozen=True, slots=True)
class ChatCommand:
    """Internal, authenticated chat request passed to a host adapter."""

    principal: RequestPrincipal
    conversation_id: str
    turn_id: str
    message: str
    attachments: tuple[Mapping[str, Any], ...] = ()
    tenant_context: Any = None
    before_message_id: int | None = None
    # Stable identifiers for streaming/replay adapters.  Kept optional for
    # compatibility with hosts that construct ChatCommand positionally.
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatOutcome:
    """Normalized output expected from a host chat adapter."""

    response: str
    success: bool = True
    awaiting_approval: bool = False
    handled_by: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[Mapping[str, Any], ...] = ()
    error: str | None = None


@dataclass
class GatewayServices:
    """All mutable dependencies behind :func:`artpm_agent.api.create_app`."""

    conversations: ConversationStorePort
    permissions: PermissionStorePort
    workflows: WorkflowStorePort
    chat_handler: Callable[[ChatCommand], object]
    capability_provider: Callable[[], CapabilityCatalog]
    chat_async_handler: Callable[[ChatCommand], object] | None = None
    workflow_capability_provider: Callable[[], Mapping[str, frozenset[str]]] | None = (
        None
    )
    permission_executor: Callable[..., object] | None = None
    permission_executor_sources: frozenset[str] | None = None
    workflow_engine: WorkflowEnginePort | None = None
    workflow_engine_factory: Callable[..., WorkflowEnginePort] | None = None
    health_handler: Callable[[], Mapping[str, Any]] | None = None
    close_handler: Callable[[], None] | None = None
    event_bus: EventBusPort | None = None
    # Keep newly introduced optional providers after the historical positional
    # fields. Some embedders construct GatewayServices positionally and rely on
    # the pre-retrieval ordering remaining stable.
    knowledge_search_handler: (
        Callable[[RequestPrincipal, Mapping[str, Any]], object] | None
    ) = None
    deployment_capability_provider: Callable[[], CapabilityCatalog] | None = None
    # Optional provider-neutral stream adapter.  It is intentionally appended
    # after all historical fields so positional GatewayServices construction
    # remains compatible with older hosts.
    chat_stream_handler: Callable[[ChatCommand], object] | None = None

    def get_workflow_engine(
        self,
        tenant_context: object = None,
        *,
        profile_id: str = "local-default",
    ) -> WorkflowEnginePort:
        engine = self.workflow_engine
        if engine is not None:
            return engine
        if self.workflow_engine_factory is not None:
            factory = self.workflow_engine_factory
            try:
                has_parameters = bool(inspect.signature(factory).parameters)
                accepts_profile = "profile_id" in inspect.signature(factory).parameters
            except (TypeError, ValueError):
                has_parameters = False
                accepts_profile = False
            if tenant_context is not None and accepts_profile:
                engine = factory(tenant_context, profile_id=profile_id)
            elif tenant_context is not None and has_parameters:
                engine = factory(tenant_context)
            else:
                engine = factory()
        if engine is None:
            raise GatewayServiceError("workflow runtime is not configured")
        return engine

    def health(self) -> dict[str, Any]:
        if self.health_handler is not None:
            result = dict(self.health_handler())
            result.setdefault("runtime_counters", dict(counter_snapshot()))
            return result

        checks: dict[str, Any] = {}
        for name, value in (
            ("conversation_store", self.conversations),
            ("permission_store", self.permissions),
            ("workflow_store", self.workflows),
        ):
            checks[name] = _store_status(
                value,
                required_tables=_STORE_REQUIRED_TABLES[name],
            )
        status = (
            "ok"
            if all(item["status"] == "ok" for item in checks.values())
            else "degraded"
        )
        return {
            "status": status,
            "checks": checks,
            "runtime_counters": dict(counter_snapshot()),
        }

    def close(self) -> None:
        if self.close_handler is not None:
            self.close_handler()

    async def chat_async(self, command: ChatCommand) -> Any:
        """Run chat without blocking the gateway event loop.

        Deployments may provide a native async handler. The legacy synchronous
        handler is isolated in a worker thread until the provider stack is
        migrated to async APIs.
        """
        handler = self.chat_async_handler or self.chat_handler
        call = getattr(handler, "__call__", None)
        is_async = inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(
            call
        )
        if is_async:
            result = handler(command)
            return await result if inspect.isawaitable(result) else result
        result = await asyncio.to_thread(handler, command)
        if inspect.isawaitable(result):
            return await result
        return result


def _plain_json(value: Any, *, depth: int = 0) -> Any:
    """Convert frozen permission JSON back to ordinary JSON containers."""

    if depth > 16:
        raise GatewayServiceError("permission payload exceeds nesting limit")
    if isinstance(value, Mapping):
        return {
            str(key): _plain_json(item, depth=depth + 1) for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_plain_json(item, depth=depth + 1) for item in value]
    return value


class DefaultGatewayRuntime:
    """Lazy local adapter; cloud deployments should inject their own services."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        runtime_factory: Any = None,
    ) -> None:
        from artpm_agent.runtime.factory import RuntimeFactory
        from artpm_agent.runtime.storage_registry import StorageRegistry

        self.runtime_factory = runtime_factory or RuntimeFactory(
            storage=StorageRegistry(db_path=db_path)
        )
        storage = self.runtime_factory.storage
        storage.initialize_authoritative_schema()
        self.db_path = str(storage.db_path)
        self.business_store = storage.business
        self.conversations = storage.conversation
        self.session_store = storage.session
        self.permissions = storage.permission
        self.workflows = storage.workflow
        self.profile_store = storage.profile
        self.knowledge_store: Any = None
        self.episode_store: Any = None
        self.feedback_store: Any = None
        self.strategy_store: Any = None
        self.reflection_scheduler: Any = None
        self.consolidation_scheduler: Any = None
        self.meta_memory_store: Any = None
        self._learning_initialized = False
        self._agent: Any = None
        self._coordinator: Any = None
        self._engine: Any = None
        # The factory owns one Agent and one workspace lock per tenant scope.
        # Different workspaces can progress concurrently without sharing an
        # unsafe mutable request context.
        self.event_bus = self.runtime_factory.event_bus()

    def _ensure_knowledge_store(self) -> Any:
        """Build the workspace knowledge backend once for the local gateway."""

        if not hasattr(self, "knowledge_store"):
            return None
        if self.knowledge_store is not None:
            return self.knowledge_store
        try:
            self.knowledge_store = self.runtime_factory.storage.knowledge
        except Exception as error:  # noqa: BLE001 - knowledge is an optional enhancer
            logger.warning("workspace knowledge store unavailable: %s", error)
            self.knowledge_store = None
        return self.knowledge_store

    def search_knowledge(
        self,
        principal: RequestPrincipal,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Run one bounded, tenant/workspace-scoped retrieval plan."""

        store = self._ensure_knowledge_store()
        if store is None:
            raise GatewayServiceError("workspace knowledge retrieval is unavailable")
        from artpm_agent.retrieval import (
            RetrievalPlan,
            SearchTarget,
            WorkspaceRetriever,
        )

        target = SearchTarget(
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            resource_types=tuple(payload.get("resource_types") or ()),
            source_types=tuple(payload.get("source_types") or ()),
            include_rules=bool(payload.get("include_rules", True)),
        )
        plan = RetrievalPlan(
            query=str(payload.get("query") or ""),
            target=target,
            limit=int(payload.get("limit", 10)),
            max_text_chars=int(payload.get("max_text_chars", 4000)),
            confidence_floor=float(payload.get("confidence_floor", 0.0)),
        )
        return [hit.to_dict() for hit in WorkspaceRetriever(store).search(plan)]

    def _ensure_learning_services(self) -> None:
        """Build the learning services once for the local gateway host."""

        if getattr(self, "_learning_initialized", False):
            return
        self._learning_initialized = True
        factory = getattr(self, "runtime_factory", None)
        if factory is not None:
            for name, service in factory.learning_services().items():
                setattr(self, name, service)
            return

        # Compatibility for injected adapters that predate RuntimeFactory and
        # intentionally bypass ``__init__``. Production hosts always use the
        # factory path above.
        from artpm_agent.runtime.factory import RuntimeFactory

        compatibility_factory = RuntimeFactory(agent_factory=self._ensure_agent)
        for name, service in compatibility_factory.learning_services().items():
            setattr(self, name, service)
        if not hasattr(self, "event_bus"):
            self.event_bus = compatibility_factory.event_bus()

    def _ensure_agent(self) -> Any:
        factory = getattr(self, "runtime_factory", None)
        if factory is not None:
            self._agent = factory.agent()
            return self._agent
        if self._agent is None:
            from artpm_agent.agent import ArtPMAgent

            self._agent = ArtPMAgent()
        return self._agent

    def _ensure_workflow_runtime(
        self,
        tenant_context: Any,
        *,
        profile_id: str = "local-default",
    ) -> tuple[Any, Any]:
        factory = getattr(self, "runtime_factory", None)
        if factory is not None:
            coordinator = factory.workflow_coordinator(
                tenant_context,
                profile_id=profile_id,
            )
            return coordinator.engine, coordinator
        agent = self._ensure_agent()
        workspace_id = tenant_context.require_workspace()
        self.workflows.ensure_builtins(
            workspace_id=workspace_id,
            profile_id=profile_id,
            tenant_id=tenant_context.tenant_id,
        )
        scoped_router = agent.router.for_tenant(tenant_context)
        from artpm_agent.workflows.designer import (
            capability_allowlist_from_skill_metadata,
        )

        allowlist = capability_allowlist_from_skill_metadata(
            scoped_router.list_skills()
        )
        from artpm_agent.workflows.coordinator import (
            ScopedWorkflowAgent,
            WorkflowCoordinator,
        )

        coordinator = WorkflowCoordinator(
            self.workflows,
            ScopedWorkflowAgent(agent, scoped_router),
            capability_allowlist=allowlist,
            workspace_id=workspace_id,
            profile_id=profile_id,
            tenant_id=tenant_context.tenant_id,
        )
        return coordinator.engine, coordinator

    def _ensure_artifact_coordinator(
        self,
        tenant_context: Any,
        *,
        profile_id: str = "local-default",
    ) -> Any:
        factory = getattr(self, "runtime_factory", None)
        if factory is None:
            return None
        return factory.artifact_coordinator(
            tenant_context,
            profile_id=profile_id,
        )

    def workflow_capabilities(self) -> Mapping[str, frozenset[str]]:
        """Return the policy-intersected workflow catalog for this deployment."""

        from artpm_agent.workflows.designer import (
            capability_allowlist_from_skill_metadata,
        )

        agent = self._ensure_agent()
        return capability_allowlist_from_skill_metadata(agent.router.list_skills())

    def chat(self, command: ChatCommand) -> ChatOutcome:
        factory = getattr(self, "runtime_factory", None)
        lock = (
            factory.workspace_lock(
                command.principal.tenant_id,
                command.principal.workspace_id,
            )
            if factory is not None
            else getattr(self, "_lock", nullcontext())
        )
        with lock:
            return self._chat_scoped(command)

    def _chat_scoped(self, command: ChatCommand) -> ChatOutcome:
        agent = self._ensure_agent()
        self._ensure_learning_services()
        from artpm_agent.harness import TurnContext

        history = self.conversations.build_context(
            command.conversation_id,
            before_message_id=command.before_message_id,
            workspace_id=command.principal.workspace_id,
        )
        tenant_context = command.tenant_context
        if tenant_context is None:
            raise GatewayServiceError("tenant context is required for chat")
        profile_id = command.principal.profile_id
        _engine, coordinator = self._ensure_workflow_runtime(
            tenant_context,
            profile_id=profile_id,
        )
        artifact_coordinator = self._ensure_artifact_coordinator(
            tenant_context,
            profile_id=profile_id,
        )
        knowledge_store = self._ensure_knowledge_store()
        profile_store = getattr(self, "profile_store", None)
        if profile_store is None:
            factory = getattr(self, "runtime_factory", None)
            storage = getattr(factory, "storage", None)
            profile_store = getattr(storage, "profile", None)
        agent_profile = (
            profile_store.get_effective_profile(scope=tenant_context.to_scope())
            if profile_store is not None
            else None
        )
        from artpm_agent.runtime.request_services import TurnServiceBundle

        turn_services = TurnServiceBundle(
            profile_store=profile_store,
            knowledge_store=knowledge_store,
            permission_store=self.permissions,
            artifact_coordinator=artifact_coordinator,
            workflow_coordinator=coordinator,
            session_store=getattr(self, "session_store", None),
            memory_manager=getattr(agent, "memory", None),
            tencentdb_memory=getattr(agent, "tencentdb_memory", None),
            feedback_store=self.feedback_store,
            strategy_store=self.strategy_store,
            episode_store=self.episode_store,
            reflection_scheduler=self.reflection_scheduler,
            meta_memory_store=self.meta_memory_store,
            consolidation_scheduler=self.consolidation_scheduler,
            event_bus=getattr(self, "event_bus", None),
        )
        # API requests use the canonical local Harness host. It owns the
        # legacy facade translation and binds a request-scoped router
        # without mutating the process-wide agent.
        from artpm_agent.harness.runtime import LocalHarnessRuntime

        runtime = LocalHarnessRuntime(agent, services=turn_services).for_tenant(
            tenant_context
        )
        context = {
            "tenant_id": command.principal.tenant_id,
            "workspace_id": command.principal.workspace_id,
            "profile_id": profile_id,
            "conversation_id": command.conversation_id,
            "turn_id": command.turn_id,
            "run_id": command.run_id or command.turn_id,
            "actor_id": command.principal.actor_id,
            "actor_role": command.principal.actor_role,
            "agent_id": "artpm-agent",
            "permission_store": self.permissions,
            "attachments": list(command.attachments),
            "file_paths": [
                str(item.get("file_path") or item.get("stored_path"))
                for item in command.attachments
                if isinstance(item, Mapping)
                and (item.get("file_path") or item.get("stored_path"))
            ],
            "tenant_context": command.tenant_context,
        }
        turn_context = TurnContext(
            turn_id=command.turn_id,
            conversation_id=command.conversation_id,
            user_input=command.message,
            attachments=list(command.attachments),
            agent_profile=agent_profile,
            conversation_history=history,
            runtime=runtime,
            services=turn_services,
            extra=context,
        )
        result = runtime.run_turn(
            turn_context,
            services=turn_services,
            request_conversation_id=command.conversation_id,
        )
        result.metadata.setdefault("profile_id", profile_id)
        # A canonical run_turn() owns the learning tail. Keep the adapter
        # fallback for injected legacy runners used by downstream hosts.
        if not result.metadata.get("lifecycle_managed"):
            self._record_turn_learning(turn_context, result, turn_services)
        return ChatOutcome(
            response=result.response,
            success=result.success,
            awaiting_approval=result.awaiting_approval,
            handled_by=result.handled_by,
            metadata=result.metadata,
            artifacts=tuple(result.artifacts),
            error=result.error,
        )

    def _record_turn_learning(
        self,
        turn_context: Any,
        result: Any,
        services: Any,
    ) -> None:
        """Reuse the canonical Harness lifecycle for legacy API runners."""
        try:
            from artpm_agent.harness.turn_service import complete_turn_lifecycle

            complete_turn_lifecycle(
                turn_context,
                result,
                services=services,
                auto_reflect=True,
            )
        except Exception as error:  # noqa: BLE001 - learning is non-fatal
            logger.warning(
                "API turn lifecycle recording failed: %s", error, exc_info=True
            )

    def execute_permission(
        self,
        request: PermissionRequest,
        tenant_context: Any = None,
    ) -> Mapping[str, Any]:
        """Execute only a server-created Skill request after CAS claim."""

        if request.source != "skill" or not request.action.startswith("skill."):
            raise GatewayServiceError(
                "permission source is not executable by this host"
            )
        payload = _plain_json(request.payload)
        if not isinstance(payload, Mapping):
            raise GatewayServiceError("permission payload is malformed")
        skill_name = str(payload.get("skill_name") or "").strip()
        if request.action != f"skill.{skill_name}":
            raise GatewayServiceError("permission action binding failed")
        inputs = payload.get("inputs", {})
        if not isinstance(inputs, Mapping):
            raise GatewayServiceError("permission skill inputs are malformed")
        safe_inputs = dict(inputs)
        # These values are server-owned.  Any values persisted from model
        # output are discarded before the router is invoked.
        safe_inputs.pop("approved", None)
        safe_inputs.pop("confirmation_token", None)
        safe_inputs["approved"] = True
        safe_inputs["confirmation_token"] = f"gateway:{request.id}"
        safe_inputs["idempotency_key"] = f"permission:{request.id}"
        if tenant_context is None:
            raise GatewayServiceError(
                "tenant context is required for permission execution"
            )
        if tenant_context.workspace_id != request.workspace_id:
            raise GatewayServiceError(
                "permission workspace does not match tenant context"
            )
        agent = self._ensure_agent()
        scoped_router = agent.router.for_tenant(tenant_context)
        raw_metadata: object = getattr(scoped_router, "list_skills", lambda: [])()
        metadata_source = (
            raw_metadata
            if isinstance(raw_metadata, Iterable)
            and not isinstance(raw_metadata, (str, bytes, Mapping))
            else ()
        )
        metadata: list[Mapping[str, object]] = [
            item for item in metadata_source if isinstance(item, Mapping)
        ]
        known = {item.get("name") for item in metadata}
        if skill_name not in known:
            raise GatewayServiceError(f"unknown Skill: {skill_name}")
        result = scoped_router.execute_skill(skill_name, safe_inputs)
        if not isinstance(result, Mapping):
            raise GatewayServiceError("Skill returned a non-object result")
        if result.get("success") is False:
            raise GatewayServiceError(
                str(result.get("error") or "Skill execution failed")
            )
        return result

    def capabilities(self) -> list[dict[str, Any]]:
        # The router is the authoritative merged registry (built-ins, MCP, and
        # deployment-provided plugins).  A static metadata fallback keeps the
        # endpoint useful if optional agent initialization fails.
        try:
            agent = self._ensure_agent()
            raw_items = agent.router.list_skills()
        except (ImportError, ModuleNotFoundError) as error:
            # Optional agent dependencies may be absent in a minimal gateway
            # process.  Do not hide runtime/configuration bugs behind a stale
            # static catalog; those should surface through the API error path.
            logger.warning(
                "agent capability registry unavailable; using static catalog: %s", error
            )
            from artpm_agent.skills.skill_router import SKILL_METADATA

            raw_items = [
                {"name": name, **dict(metadata)}
                for name, metadata in SKILL_METADATA.items()
                if isinstance(metadata, Mapping)
            ]

        entries = []
        for item in raw_items or []:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            is_plugin = bool(item.get("is_plugin_skill"))
            is_mcp = bool(item.get("is_mcp_skill"))
            source = "plugin" if is_plugin else ("mcp" if is_mcp else "builtin")
            entry = {
                "name": name,
                "description": str(item.get("description") or ""),
                "version": str(item.get("version") or "unknown"),
                "risk": str(item.get("risk") or "untrusted"),
                "read_only": bool(item.get("read_only", False)),
                "requires_approval": bool(
                    item.get("requires_approval") or item.get("read_only") is False
                ),
                "source": source,
            }
            for key in ("plugin_id", "plugin_version", "capabilities"):
                if key in item:
                    entry[key] = _plain_json(item[key])
            entries.append(entry)
        return sorted(entries, key=lambda item: item["name"])

    def deployment_capabilities(self) -> list[dict[str, Any]]:
        """Report capability readiness without changing the skill catalog."""

        db_path = getattr(self, "db_path", "")
        try:
            from artpm_agent.api.embed import EmbedSettings

            embed_settings = EmbedSettings.from_env()
            embed_enabled = embed_settings.enabled
            embed_configured = bool(embed_settings.secret and embed_settings.channels)
        except Exception:  # noqa: BLE001 - diagnostics must remain non-fatal
            embed_enabled = False
            embed_configured = False
        return [
            {
                "name": "workspace_retrieval",
                "kind": "capability",
                "source": "core",
                "enabled": True,
                "configured": True,
                "ready": bool(db_path) and Path(db_path).is_file(),
                "reason": "SQLite + workspace knowledge store",
            },
            {
                "name": "embed",
                "kind": "capability",
                "source": "optional",
                "enabled": embed_enabled,
                "configured": embed_configured,
                "ready": embed_enabled and embed_configured,
                "reason": (
                    "HMAC embed channel ready"
                    if embed_enabled and embed_configured
                    else "secure embed channel is opt-in and not enabled by default"
                ),
            },
        ]

    def health(self) -> Mapping[str, Any]:
        """Cheap readiness probe; it never initializes an LLM or OCR model."""

        factory = getattr(self, "runtime_factory", None)
        storage = getattr(factory, "storage", None)
        if storage is not None and callable(getattr(storage, "readiness", None)):
            checks = storage.readiness()
        else:
            checks = {
                name: _store_status(
                    value,
                    required_tables=_STORE_REQUIRED_TABLES[name],
                )
                for name, value in (
                    ("conversation_store", self.conversations),
                    ("permission_store", self.permissions),
                    ("workflow_store", self.workflows),
                )
            }
        for name, value in (
            ("permission_store", self.permissions),
            ("workflow_store", self.workflows),
        ):
            checks[name] = _store_status(
                value,
                required_tables=_STORE_REQUIRED_TABLES[name],
            )
        checks["agent"] = {"status": "lazy"}
        status = (
            "ok"
            if all(check.get("status") in {"ok", "lazy"} for check in checks.values())
            else "degraded"
        )
        from artpm_agent.runtime.performance import performance_snapshot

        return {
            "status": status,
            "checks": checks,
            "runtime_counters": dict(counter_snapshot()),
            "performance": performance_snapshot(),
        }

    def close(self) -> None:
        factory = getattr(self, "runtime_factory", None)
        if factory is not None:
            factory.close()
            return
        agent = self._agent
        close = getattr(agent, "close", None)
        if callable(close):
            close()


def build_default_services(db_path: str | Path | None = None) -> GatewayServices:
    """Build the local service graph without initializing the LLM eagerly."""

    runtime = DefaultGatewayRuntime(db_path)
    return GatewayServices(
        conversations=runtime.conversations,
        permissions=runtime.permissions,
        workflows=runtime.workflows,
        chat_handler=runtime.chat,
        capability_provider=runtime.capabilities,
        knowledge_search_handler=runtime.search_knowledge,
        deployment_capability_provider=runtime.deployment_capabilities,
        workflow_capability_provider=runtime.workflow_capabilities,
        permission_executor=runtime.execute_permission,
        permission_executor_sources=frozenset({"skill"}),
        workflow_engine_factory=lambda tenant_context=None, profile_id="local-default": (
            runtime._ensure_workflow_runtime(
                tenant_context,
                profile_id=profile_id,
            )[0]
        ),
        health_handler=runtime.health,
        close_handler=runtime.close,
        event_bus=runtime.event_bus,
    )


__all__ = [
    "ChatCommand",
    "ChatOutcome",
    "DefaultGatewayRuntime",
    "GatewayServiceError",
    "GatewayServices",
    "IdentityError",
    "RequestPrincipal",
    "TrustedHeaderIdentityResolver",
    "build_default_services",
    "validate_identifier",
]
