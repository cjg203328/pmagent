"""Dependency-injected services used by the REST gateway.

The HTTP layer only knows these small contracts.  Deployments can replace the
chat, capability, and permission executors without importing ``ArtPMAgent``;
the default adapter is lazy and exists for the local/CLI deployment.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from hmac import compare_digest
import logging
import os
from pathlib import Path
import re
import inspect
import sqlite3
from typing import Any

from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.security.permission_store import PermissionRequest, PermissionStore
from artpm_agent.workflows.store import WorkflowStore

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ROLE_VALUES = frozenset({"user", "admin"})
_ACTOR_KINDS = frozenset({"human", "service"})

_STORE_REQUIRED_TABLES: dict[str, tuple[str, ...]] = {
    "conversation_store": ("workspaces", "conversations", "messages"),
    "permission_store": ("permission_requests",),
    "workflow_store": ("workflow_definitions", "workflow_runs"),
}


def _store_status(value: Any, *, required_tables: tuple[str, ...] = ()) -> dict[str, Any]:
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
    value = value.strip()
    if len(value) == 0 or len(value) > max_length or not _IDENTIFIER_RE.fullmatch(value):
        raise IdentityError(f"{field} has an invalid format")
    return value


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
    TOKEN_HEADER = "x-gateway-token"
    _LOCAL_CLIENT_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})
    _FORWARDED_HEADERS = frozenset({"forwarded", "x-forwarded-for", "x-forwarded-host"})

    def __init__(self, gateway_secret: str | None = None, *, require_secret: bool = False):
        self.gateway_secret = gateway_secret or os.getenv("ARTPM_GATEWAY_SHARED_SECRET")
        environment = (os.getenv("ARTPM_ENV") or os.getenv("ENV") or "development").strip().lower()
        self.production = environment in {"prod", "production"}
        self.require_secret = bool(require_secret or self.gateway_secret or self.production)

    def __call__(self, request: Any) -> RequestPrincipal:
        headers = getattr(request, "headers", request)
        token = headers.get(self.TOKEN_HEADER)
        if self.require_secret:
            if not self.gateway_secret or not token or not compare_digest(
                str(token), str(self.gateway_secret)
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
        if workspace_id is None or actor_id is None:
            raise IdentityError("x-workspace-id and x-actor-id are required")
        return RequestPrincipal(
            workspace_id=validate_identifier(workspace_id, "workspace_id"),
            actor_id=validate_identifier(actor_id, "actor_id"),
            actor_role=actor_role,
            actor_kind=actor_kind,
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

    conversations: Any
    permissions: Any
    workflows: Any
    chat_handler: Callable[[ChatCommand], Any]
    capability_provider: Callable[[], Any]
    workflow_capability_provider: Callable[[], Any] | None = None
    permission_executor: Callable[[PermissionRequest], Any] | None = None
    permission_executor_sources: frozenset[str] | None = None
    workflow_engine: Any | None = None
    workflow_engine_factory: Callable[[], Any] | None = None
    health_handler: Callable[[], Mapping[str, Any]] | None = None
    close_handler: Callable[[], None] | None = None

    def get_workflow_engine(self, tenant_context: Any = None) -> Any:
        engine = self.workflow_engine
        if engine is not None:
            return engine
        if self.workflow_engine_factory is not None:
            factory = self.workflow_engine_factory
            try:
                parameters = inspect.signature(factory).parameters
            except (TypeError, ValueError):
                parameters = {}
            if tenant_context is not None and parameters:
                engine = factory(tenant_context)
            else:
                engine = factory()
        if engine is None:
            raise GatewayServiceError("workflow runtime is not configured")
        return engine

    def health(self) -> dict[str, Any]:
        if self.health_handler is not None:
            return dict(self.health_handler())

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
        status = "ok" if all(item["status"] == "ok" for item in checks.values()) else "degraded"
        return {"status": status, "checks": checks}

    def close(self) -> None:
        if self.close_handler is not None:
            self.close_handler()


def _plain_json(value: Any, *, depth: int = 0) -> Any:
    """Convert frozen permission JSON back to ordinary JSON containers."""

    if depth > 16:
        raise GatewayServiceError("permission payload exceeds nesting limit")
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_json(item, depth=depth + 1) for item in value]
    return value


class DefaultGatewayRuntime:
    """Lazy local adapter; cloud deployments should inject their own services."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            try:
                from artpm_agent.config import get_config

                db_path = get_config().get(
                    "database.conversation_db_path", "./data/conversations.db"
                )
            except Exception:
                db_path = "./data/conversations.db"
        self.db_path = str(Path(db_path).expanduser())
        self.conversations = ConversationStore(self.db_path)
        self.permissions = PermissionStore(self.db_path)
        self.workflows = WorkflowStore(self.db_path)
        self._agent: Any = None
        self._coordinator: Any = None
        self._engine: Any = None
        # ArtPMAgent is not assumed to be thread-safe.  The adapter lock keeps
        # a local deployment deterministic while cloud hosts can inject a pool.
        import threading

        self._lock = threading.RLock()

    def _ensure_agent(self) -> Any:
        with self._lock:
            if self._agent is None:
                from artpm_agent.agent import ArtPMAgent

                self._agent = ArtPMAgent()
        return self._agent

    def _ensure_workflow_runtime(self, tenant_context: Any) -> tuple[Any, Any]:
        agent = self._ensure_agent()
        scoped_router = agent.router.for_tenant(tenant_context)
        from artpm_agent.workflows.designer import (
            capability_allowlist_from_skill_metadata,
        )
        from artpm_agent.workflows.engine import WorkflowEngine

        allowlist = capability_allowlist_from_skill_metadata(
            scoped_router.list_skills()
        )
        # A workflow engine captures its executor. Never reuse one bound to
        # another request principal/workspace.
        engine = WorkflowEngine(
            self.workflows,
            scoped_router.execute_skill,
            capability_allowlist=allowlist,
        )

        from artpm_agent.workflows.coordinator import WorkflowCoordinator

        class _TenantAgentProxy:
            def __init__(self, base: Any, router: Any):
                self._base = base
                self.router = router

            def __getattr__(self, name: str) -> Any:
                return getattr(self._base, name)

        coordinator = WorkflowCoordinator(
            self.workflows,
            _TenantAgentProxy(agent, scoped_router),
            capability_allowlist=allowlist,
        )
        return engine, coordinator

    def workflow_capabilities(self) -> Mapping[str, frozenset[str]]:
        """Return the policy-intersected workflow catalog for this deployment."""

        from artpm_agent.workflows.designer import (
            capability_allowlist_from_skill_metadata,
        )

        agent = self._ensure_agent()
        return capability_allowlist_from_skill_metadata(agent.router.list_skills())

    def chat(self, command: ChatCommand) -> ChatOutcome:
        with self._lock:
            agent = self._ensure_agent()
            from artpm_agent.harness import TurnContext, run_turn

            history = self.conversations.build_context(
                command.conversation_id,
                before_message_id=command.before_message_id,
                workspace_id=command.principal.workspace_id,
            )
            tenant_context = command.tenant_context
            if tenant_context is None:
                raise GatewayServiceError("tenant context is required for chat")
            engine, coordinator = self._ensure_workflow_runtime(tenant_context)
            context = {
                "workspace_id": command.principal.workspace_id,
                "conversation_id": command.conversation_id,
                "turn_id": command.turn_id,
                "actor_id": command.principal.actor_id,
                "actor_role": command.principal.actor_role,
                "agent_id": "artpm-agent",
                "permission_store": self.permissions,
                "attachments": list(command.attachments),
                "tenant_context": command.tenant_context,
            }
            turn_context = TurnContext(
                turn_id=command.turn_id,
                conversation_id=command.conversation_id,
                user_input=command.message,
                attachments=list(command.attachments),
                conversation_history=history,
                agent=agent,
                extra=context,
            )
            result = run_turn(
                turn_context,
                request_conversation_id=command.conversation_id,
                workflow_coordinator=coordinator,
            )
            return ChatOutcome(
                response=result.response,
                success=result.success,
                awaiting_approval=result.awaiting_approval,
                handled_by=result.handled_by,
                metadata=result.metadata,
                artifacts=tuple(result.artifacts),
                error=result.error,
            )

    def execute_permission(
        self,
        request: PermissionRequest,
        tenant_context: Any = None,
    ) -> Mapping[str, Any]:
        """Execute only a server-created Skill request after CAS claim."""

        if request.source != "skill" or not request.action.startswith("skill."):
            raise GatewayServiceError("permission source is not executable by this host")
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
            raise GatewayServiceError("tenant context is required for permission execution")
        if tenant_context.workspace_id != request.workspace_id:
            raise GatewayServiceError("permission workspace does not match tenant context")
        agent = self._ensure_agent()
        scoped_router = agent.router.for_tenant(tenant_context)
        metadata = getattr(scoped_router, "list_skills", lambda: [])()
        known = {item.get("name") for item in metadata if isinstance(item, Mapping)}
        if skill_name not in known:
            raise GatewayServiceError(f"unknown Skill: {skill_name}")
        result = scoped_router.execute_skill(skill_name, safe_inputs)
        if not isinstance(result, Mapping):
            raise GatewayServiceError("Skill returned a non-object result")
        if result.get("success") is False:
            raise GatewayServiceError(str(result.get("error") or "Skill execution failed"))
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
            logger.warning("agent capability registry unavailable; using static catalog: %s", error)
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

    def health(self) -> Mapping[str, Any]:
        """Cheap readiness probe; it never initializes an LLM or OCR model."""

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
        checks["agent"] = {"status": "lazy"}
        status = (
            "ok"
            if all(checks[name]["status"] == "ok" for name in _STORE_REQUIRED_TABLES)
            else "degraded"
        )
        return {"status": status, "checks": checks}

    def close(self) -> None:
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
        workflow_capability_provider=runtime.workflow_capabilities,
        permission_executor=runtime.execute_permission,
        permission_executor_sources=frozenset({"skill"}),
        workflow_engine_factory=lambda tenant_context=None: runtime._ensure_workflow_runtime(tenant_context)[0],
        health_handler=runtime.health,
        close_handler=runtime.close,
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
