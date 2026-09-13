"""SQLite-backed, one-shot permission requests for agent side effects.

The model may describe an action, but only this server-owned store can approve
and consume it. Raw resume payloads are validated, size-bounded, and hidden
from public snapshots; a separately persisted redacted view is safe for UI.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from types import MappingProxyType
from typing import Any, Literal, TypeAlias
from uuid import uuid4

from artpm_agent.tenancy.scope import Scope


PermissionStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "executing",
    "completed",
    "failed",
    "expired",
]
PermissionRisk = Literal["low", "medium", "high", "critical", "untrusted"]
PermissionRole = Literal["user", "admin"]
JsonValue: TypeAlias = Any

PERMISSION_STATUSES = frozenset(
    {"pending", "approved", "rejected", "executing", "completed", "failed", "expired"}
)
PERMISSION_RISKS = frozenset({"low", "medium", "high", "critical", "untrusted"})
PERMISSION_ROLES = frozenset({"user", "admin"})
_ROLE_RANK = {"user": 1, "admin": 2}
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_REDACTED = "[REDACTED]"
_MAX_JSON_DEPTH = 32
_MAX_JSON_BYTES = 64 * 1024
_MAX_TEXT_LENGTH = 4_000
_DEFAULT_TTL_SECONDS = 10 * 60
_MAX_TTL_SECONDS = 7 * 24 * 60 * 60

_SENSITIVE_KEYS = frozenset(
    {
        "access_key",
        "access_token",
        "api_key",
        "apikey",
        "auth_token",
        "authorization",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "secret",
        "secret_key",
        "session_token",
        "token",
    }
)
_SENSITIVE_SUFFIXES = (
    "_access_key",
    "_api_key",
    "_auth_token",
    "_client_secret",
    "_password",
    "_private_key",
    "_refresh_token",
    "_secret",
    "_secret_key",
    "_session_token",
)


class PermissionStoreError(RuntimeError):
    """Base error for permission persistence and state transitions."""


class PermissionValidationError(ValueError):
    """Raised when a request crosses the JSON or contract boundary."""


class PermissionNotFoundError(KeyError):
    """Raised when a permission request does not exist."""


class PermissionConflictError(PermissionStoreError):
    """Raised when a compare-and-swap transition loses or is no longer valid."""


class PermissionBindingError(PermissionStoreError):
    """Raised when an approval is presented for a different action payload."""


def _required_text(value: Any, field: str, *, max_length: int = _MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PermissionValidationError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if "\x00" in normalized:
        raise PermissionValidationError(f"{field} cannot contain null characters")
    if len(normalized) > max_length:
        raise PermissionValidationError(
            f"{field} cannot exceed {max_length} characters"
        )
    return normalized


def _state_version(value: Any, field: str = "expected_version") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PermissionValidationError(f"{field} must be a non-negative integer")
    return value


def _normalize_json(
    value: Any,
    *,
    path: str,
    depth: int = 0,
    ancestors: frozenset[int] = frozenset(),
) -> JsonValue:
    if depth > _MAX_JSON_DEPTH:
        raise PermissionValidationError(
            f"{path} exceeds the maximum JSON depth of {_MAX_JSON_DEPTH}"
        )
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PermissionValidationError(f"{path} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in ancestors:
            raise PermissionValidationError(f"{path} contains a reference cycle")
        nested_ancestors = ancestors | {identity}
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PermissionValidationError(f"{path} keys must be strings")
            normalized[key] = _normalize_json(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
                ancestors=nested_ancestors,
            )
        return normalized
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in ancestors:
            raise PermissionValidationError(f"{path} contains a reference cycle")
        nested_ancestors = ancestors | {identity}
        return [
            _normalize_json(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
                ancestors=nested_ancestors,
            )
            for index, item in enumerate(value)
        ]
    raise PermissionValidationError(
        f"{path} must contain only JSON-compatible values"
    )


def _canonical_json(value: JsonValue, *, field: str) -> str:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise PermissionValidationError(f"{field} is not valid JSON") from error
    if len(rendered.encode("utf-8")) > _MAX_JSON_BYTES:
        raise PermissionValidationError(
            f"{field} exceeds the {_MAX_JSON_BYTES}-byte JSON limit"
        )
    return rendered


def _normalized_json(value: Any, *, field: str) -> tuple[JsonValue, str]:
    normalized = _normalize_json(value, path=field)
    return normalized, _canonical_json(normalized, field=field)


def canonical_payload_sha256(payload: Any) -> str:
    """Return a deterministic SHA-256 for one JSON-compatible payload."""

    _, canonical = _normalized_json(payload, field="payload")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


def _redact_normalized(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_sensitive_key(key) else _redact_normalized(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_normalized(item) for item in value]
    return value


def redact_sensitive(value: Any) -> JsonValue:
    """Recursively redact values whose mapping key names are sensitive."""

    normalized, _ = _normalized_json(value, field="value")
    return _redact_normalized(normalized)


def _freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """Immutable snapshot of one exact, one-shot permission request."""

    id: str
    idempotency_key: str
    workspace_id: str
    conversation_id: str
    turn_id: str
    agent_id: str
    source: str
    action: str
    resource: JsonValue
    risk: PermissionRisk
    required_role: PermissionRole
    status: PermissionStatus
    payload: JsonValue = field(repr=False, compare=False)
    redacted_arguments: JsonValue
    payload_sha256: str
    action_sha256: str
    state_version: int
    created_at: str
    expires_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    decided_role: PermissionRole | None = None
    execution_id: str | None = None
    execution_started_at: str | None = None
    completed_at: str | None = None
    expired_at: str | None = None
    result: JsonValue | None = None
    result_sha256: str | None = None
    error: str | None = None
    tenant_id: str = "local"

    def to_dict(self) -> dict[str, Any]:
        public_error = (
            "approved_operation_failed"
            if self.status == "failed" and self.error
            else None
        )
        return {
            "id": self.id,
            "idempotency_key": self.idempotency_key,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "agent_id": self.agent_id,
            "source": self.source,
            "action": self.action,
            "resource": _thaw_json(self.resource),
            "risk": self.risk,
            "required_role": self.required_role,
            "status": self.status,
            "redacted_arguments": _thaw_json(self.redacted_arguments),
            "payload_sha256": self.payload_sha256,
            "action_sha256": self.action_sha256,
            "state_version": self.state_version,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "decided_role": self.decided_role,
            "execution_id": self.execution_id,
            "execution_started_at": self.execution_started_at,
            "completed_at": self.completed_at,
            "expired_at": self.expired_at,
            "result": _thaw_json(self.result),
            "result_sha256": self.result_sha256,
            # Raw executor exceptions are retained for trusted logs/recovery but
            # never cross the public UI/API snapshot boundary.
            "error": public_error,
        }


class PermissionStore:
    """Persist permission requests and enforce their one-shot lifecycle."""

    SCHEMA_VERSION = 2
    BUSY_TIMEOUT_MS = 10_000

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        default_ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        if isinstance(default_ttl_seconds, bool) or not isinstance(
            default_ttl_seconds, int
        ):
            raise PermissionValidationError(
                "default_ttl_seconds must be an integer"
            )
        if not 1 <= default_ttl_seconds <= _MAX_TTL_SECONDS:
            raise PermissionValidationError(
                f"default_ttl_seconds must be between 1 and {_MAX_TTL_SECONDS}"
            )
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        path = Path(db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.default_ttl_seconds = default_ttl_seconds
        self._enable_wal()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=self.BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self.BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA synchronous = NORMAL")
            return connection
        except BaseException:
            # PRAGMA setup can race with another first-use migration. Close
            # the partially initialized handle before propagating the error.
            connection.close()
            raise

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _enable_wal(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")

    def _migrate(self) -> None:
        with self._connection(write=True) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS permission_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version "
                "FROM permission_schema_migrations"
            ).fetchone()
            current = int(row["version"])
            if current > self.SCHEMA_VERSION:
                raise PermissionStoreError(
                    "Permission database schema is newer than this application supports"
                )
            if current < 1:
                connection.executescript(
                    """
                CREATE TABLE permission_requests (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'local',
                    workspace_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_json TEXT NOT NULL,
                    risk TEXT NOT NULL CHECK(risk IN (
                        'low', 'medium', 'high', 'critical', 'untrusted'
                    )),
                    required_role TEXT NOT NULL CHECK(required_role IN ('user', 'admin')),
                    status TEXT NOT NULL CHECK(status IN (
                        'pending', 'approved', 'rejected', 'executing',
                        'completed', 'failed', 'expired'
                    )),
                    payload_json TEXT NOT NULL,
                    redacted_arguments_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
                    action_sha256 TEXT NOT NULL CHECK(length(action_sha256) = 64),
                    state_version INTEGER NOT NULL DEFAULT 0 CHECK(state_version >= 0),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    decided_role TEXT CHECK(decided_role IN ('user', 'admin')),
                    execution_id TEXT,
                    execution_started_at TEXT,
                    completed_at TEXT,
                    expired_at TEXT,
                    result_json TEXT,
                    result_sha256 TEXT CHECK(
                        result_sha256 IS NULL OR length(result_sha256) = 64
                    ),
                    error TEXT,
                    UNIQUE(workspace_id, idempotency_key)
                );

                CREATE INDEX idx_permission_pending_scope
                    ON permission_requests(
                        workspace_id, conversation_id, status, created_at
                    );
                CREATE INDEX idx_permission_expiry
                    ON permission_requests(status, expires_at);
                CREATE UNIQUE INDEX idx_permission_execution_id
                    ON permission_requests(execution_id)
                    WHERE execution_id IS NOT NULL;
                    """
                )
                connection.execute(
                    "INSERT INTO permission_schema_migrations(version, applied_at) "
                    "VALUES (1, ?)",
                    (self._iso(self._now()),),
                )
                current = 1
            if current < 2:
                columns = {
                    str(item[1])
                    for item in connection.execute(
                        "PRAGMA table_info(permission_requests)"
                    )
                }
                if "tenant_id" not in columns:
                    connection.execute(
                        "ALTER TABLE permission_requests ADD COLUMN "
                        "tenant_id TEXT NOT NULL DEFAULT 'local'"
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_permission_tenant_scope "
                    "ON permission_requests(tenant_id, workspace_id, status, created_at)"
                )
                has_workspaces = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspaces'"
                ).fetchone()
                if has_workspaces is not None:
                    connection.execute(
                        "UPDATE permission_requests SET tenant_id = COALESCE(("
                        "SELECT tenant_id FROM workspaces WHERE workspaces.id = permission_requests.workspace_id"
                        "), 'local') WHERE tenant_id = 'local'"
                    )
                connection.execute(
                    "INSERT INTO permission_schema_migrations(version, applied_at) "
                    "VALUES (2, ?)",
                    (self._iso(self._now()),),
                )

    @staticmethod
    def _resolve_scope(
        *,
        scope: Scope | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[str | None, str | None]:
        if scope is not None:
            if not isinstance(scope, Scope):
                raise PermissionValidationError("scope must be a Scope")
            if tenant_id not in (None, "", scope.tenant_id):
                raise PermissionValidationError("tenant_id conflicts with scope")
            if workspace_id not in (None, "", scope.workspace_id):
                raise PermissionValidationError("workspace_id conflicts with scope")
            return scope.tenant_id, scope.workspace_id
        return tenant_id, workspace_id

    @staticmethod
    def _validate_workspace_tenant(
        connection: sqlite3.Connection,
        workspace_id: str,
        tenant_id: str,
    ) -> None:
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspaces'"
        ).fetchone() is None:
            return
        row = connection.execute(
            "SELECT tenant_id FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        if row is not None and row["tenant_id"] not in (None, "", tenant_id):
            raise PermissionBindingError("workspace does not belong to the requested tenant")

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime):
            raise PermissionStoreError("clock must return a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise PermissionStoreError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _decode_json(raw: str | None, field: str) -> JsonValue:
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise PermissionStoreError(
                f"Persisted {field} is not valid JSON"
            ) from error
        normalized, _ = _normalized_json(value, field=field)
        return _freeze_json(normalized)

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> PermissionRequest:
        status_raw = str(row["status"])
        if status_raw not in PERMISSION_STATUSES:
            raise PermissionStoreError(f"Unsupported persisted status: {status_raw}")
        status: PermissionStatus = status_raw  # type: ignore[assignment]
        return PermissionRequest(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            workspace_id=row["workspace_id"],
            conversation_id=row["conversation_id"],
            turn_id=row["turn_id"],
            agent_id=row["agent_id"],
            source=row["source"],
            action=row["action"],
            resource=cls._decode_json(row["resource_json"], "resource"),
            risk=row["risk"],
            required_role=row["required_role"],
            status=status,
            payload=cls._decode_json(row["payload_json"], "payload"),
            redacted_arguments=cls._decode_json(
                row["redacted_arguments_json"], "redacted_arguments"
            ),
            payload_sha256=row["payload_sha256"],
            action_sha256=row["action_sha256"],
            state_version=int(row["state_version"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
            decided_role=row["decided_role"],
            execution_id=row["execution_id"],
            execution_started_at=row["execution_started_at"],
            completed_at=row["completed_at"],
            expired_at=row["expired_at"],
            result=cls._decode_json(row["result_json"], "result"),
            result_sha256=row["result_sha256"],
            error=row["error"],
            tenant_id=row["tenant_id"],
        )

    @staticmethod
    def _request_row(
        connection: sqlite3.Connection,
        request_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> sqlite3.Row:
        tenant_clause = "" if tenant_id is None else " AND tenant_id = ?"
        workspace_clause = "" if workspace_id is None else " AND workspace_id = ?"
        parameters: tuple[Any, ...] = (request_id,)
        if tenant_id is not None:
            parameters += (tenant_id,)
        if workspace_id is not None:
            parameters += (workspace_id,)
        row = connection.execute(
            "SELECT * FROM permission_requests WHERE id = ?"
            + tenant_clause
            + workspace_clause,
            parameters,
        ).fetchone()
        if row is None and tenant_id is not None and workspace_id is not None:
            legacy = connection.execute(
                "SELECT tenant_id FROM permission_requests "
                "WHERE id = ? AND workspace_id = ?",
                (request_id, workspace_id),
            ).fetchone()
            if legacy is not None and legacy["tenant_id"] == "local" and workspace_id != "local-default":
                has_workspaces = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspaces'"
                ).fetchone()
                owner = None
                if has_workspaces is not None:
                    owner = connection.execute(
                        "SELECT tenant_id FROM workspaces WHERE id = ?",
                        (workspace_id,),
                    ).fetchone()
                if owner is None or owner["tenant_id"] in (None, "", tenant_id):
                    connection.execute(
                        "UPDATE permission_requests SET tenant_id = ? "
                        "WHERE id = ? AND workspace_id = ? AND tenant_id = 'local'",
                        (tenant_id, request_id, workspace_id),
                    )
                    row = connection.execute(
                        "SELECT * FROM permission_requests WHERE id = ? "
                        "AND tenant_id = ? AND workspace_id = ?",
                        (request_id, tenant_id, workspace_id),
                    ).fetchone()
        if row is None:
            raise PermissionNotFoundError(f"Unknown permission request: {request_id}")
        return row

    @staticmethod
    def _expire_due(
        connection: sqlite3.Connection,
        now: str,
        *,
        workspace_id: str,
        tenant_id: str | None = None,
        request_id: str | None = None,
    ) -> int:
        params: list[Any] = [now, now]
        tenant_clause = ""
        params.append(workspace_id)
        if tenant_id is not None:
            tenant_clause = " AND tenant_id = ?"
            params.append(tenant_id)
        request_clause = ""
        if request_id is not None:
            request_clause = " AND id = ?"
            params.append(request_id)
        cursor = connection.execute(
            """
            UPDATE permission_requests
            SET status = 'expired', expired_at = ?, state_version = state_version + 1
            WHERE status IN ('pending', 'approved') AND expires_at <= ?
              AND workspace_id = ?
            """
            + tenant_clause
            + request_clause,
            params,
        )
        return int(cursor.rowcount)

    @staticmethod
    def _validate_digest(value: str | None, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
            raise PermissionValidationError(f"{field} must be a lowercase SHA-256")
        return value

    @staticmethod
    def _action_is_allowed(action: str) -> bool:
        """Apply the authoritative static capability deny before consent."""

        try:
            from artpm_agent.workflows.risk_policy import action_is_allowed

            return bool(action_is_allowed(action))
        except (ImportError, RuntimeError, TypeError, ValueError) as error:
            raise PermissionStoreError(
                "server capability risk policy is unavailable"
            ) from error

    def create_request(
        self,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
        conversation_id: str,
        turn_id: str,
        agent_id: str,
        source: str,
        action: str,
        resource: Any,
        risk: PermissionRisk,
        required_role: PermissionRole,
        payload: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        ttl_seconds: int | None = None,
    ) -> PermissionRequest:
        """Create or return the exact request associated with an idempotency key."""

        if scope is not None:
            if not isinstance(scope, Scope):
                raise PermissionValidationError("scope must be a Scope")
            if tenant_id not in (None, "", scope.tenant_id):
                raise PermissionValidationError("tenant_id conflicts with scope")
            if workspace_id not in (None, "", scope.workspace_id):
                raise PermissionValidationError("workspace_id conflicts with scope")
            tenant_id = scope.tenant_id
            workspace_id = scope.workspace_id
        tenant_id = _required_text(tenant_id or "local", "tenant_id", max_length=256)
        workspace_id = _required_text(
            workspace_id or "local-default", "workspace_id", max_length=256
        )
        conversation_id = _required_text(
            conversation_id, "conversation_id", max_length=256
        )
        turn_id = _required_text(turn_id, "turn_id", max_length=256)
        agent_id = _required_text(agent_id, "agent_id", max_length=256)
        source = _required_text(source, "source", max_length=128)
        action = _required_text(action, "action", max_length=512)
        if risk not in PERMISSION_RISKS:
            raise PermissionValidationError(f"unsupported risk: {risk}")
        if required_role not in PERMISSION_ROLES:
            raise PermissionValidationError(
                f"unsupported required_role: {required_role}"
            )
        if risk in {"critical", "untrusted"}:
            # This is an authoritative floor. Callers and plugin metadata may
            # request a stricter role, but can never downgrade severe actions.
            required_role = "admin"
        if payload is not None and not isinstance(payload, Mapping):
            raise PermissionValidationError("payload must be a JSON object")

        normalized_resource, resource_canonical = _normalized_json(
            resource, field="resource"
        )
        normalized_payload, payload_canonical = _normalized_json(
            dict(payload or {}), field="payload"
        )
        if not self._action_is_allowed(action):
            raise PermissionError(
                f"action is blocked by the server risk policy: {action}"
            )
        redacted_resource = _redact_normalized(normalized_resource)
        redacted_payload = _redact_normalized(normalized_payload)
        resource_json = _canonical_json(redacted_resource, field="resource")
        payload_json = payload_canonical
        redacted_arguments_json = _canonical_json(
            redacted_payload, field="redacted_arguments"
        )
        payload_sha256 = hashlib.sha256(payload_canonical.encode("utf-8")).hexdigest()

        action_envelope = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "agent_id": agent_id,
            "source": source,
            "action": action,
            "resource": normalized_resource,
            "risk": risk,
            "required_role": required_role,
            "payload": normalized_payload,
        }
        action_canonical = _canonical_json(action_envelope, field="action envelope")
        action_sha256 = hashlib.sha256(action_canonical.encode("utf-8")).hexdigest()
        idempotency_key = _required_text(
            idempotency_key or action_sha256,
            "idempotency_key",
            max_length=512,
        )

        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, int):
            raise PermissionValidationError("ttl_seconds must be an integer")
        if not 1 <= ttl <= _MAX_TTL_SECONDS:
            raise PermissionValidationError(
                f"ttl_seconds must be between 1 and {_MAX_TTL_SECONDS}"
            )
        now_value = self._now()
        now = self._iso(now_value)
        expires_at = self._iso(now_value + timedelta(seconds=ttl))

        with self._connection(write=True) as connection:
            self._validate_workspace_tenant(connection, workspace_id, tenant_id)
            self._expire_due(
                connection,
                now,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
            )
            existing = connection.execute(
                """
                SELECT * FROM permission_requests
                WHERE tenant_id = ? AND workspace_id = ? AND idempotency_key = ?
                """,
                (tenant_id, workspace_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["action_sha256"] != action_sha256:
                    raise PermissionBindingError(
                        "idempotency key is already bound to a different action"
                    )
                return self._from_row(existing)

            request_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO permission_requests(
                    id, idempotency_key, tenant_id, workspace_id, conversation_id,
                    turn_id, agent_id, source, action, resource_json, risk,
                    required_role, status, payload_json, redacted_arguments_json,
                    payload_sha256, action_sha256, state_version, created_at,
                    expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    request_id,
                    idempotency_key,
                    tenant_id,
                    workspace_id,
                    conversation_id,
                    turn_id,
                    agent_id,
                    source,
                    action,
                    resource_json,
                    risk,
                    required_role,
                    payload_json,
                    redacted_arguments_json,
                    payload_sha256,
                    action_sha256,
                    now,
                    expires_at,
                ),
            )
            row = self._request_row(connection, request_id)
        return self._from_row(row)

    def get(
        self,
        request_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> PermissionRequest | None:
        request_id = _required_text(request_id, "request_id", max_length=256)
        tenant_id, workspace_id = self._resolve_scope(
            scope=scope,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if tenant_id is not None:
            tenant_id = _required_text(tenant_id, "tenant_id", max_length=256)
        if workspace_id is not None:
            workspace_id = _required_text(
                workspace_id, "workspace_id", max_length=256
            )
        now = self._iso(self._now())
        with self._connection(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM permission_requests WHERE id = ?"
                + (" AND tenant_id = ?" if tenant_id is not None else "")
                + (" AND workspace_id = ?" if workspace_id is not None else ""),
                (
                    (request_id,)
                    if tenant_id is None and workspace_id is None
                    else (
                        (request_id, tenant_id)
                        if workspace_id is None
                        else (
                            (request_id, workspace_id)
                            if tenant_id is None
                            else (request_id, tenant_id, workspace_id)
                        )
                    )
                ),
            ).fetchone()
            if existing is not None:
                self._expire_due(
                    connection,
                    now,
                    workspace_id=existing["workspace_id"],
                    tenant_id=existing["tenant_id"],
                    request_id=request_id,
                )
            tenant_clause = "" if tenant_id is None else " AND tenant_id = ?"
            workspace_clause = "" if workspace_id is None else " AND workspace_id = ?"
            parameters: tuple[Any, ...] = (request_id,)
            if tenant_id is not None:
                parameters += (tenant_id,)
            if workspace_id is not None:
                parameters += (workspace_id,)
            row = connection.execute(
                "SELECT * FROM permission_requests WHERE id = ?"
                + tenant_clause
                + workspace_clause,
                parameters,
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_pending(
        self,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
        conversation_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> list[PermissionRequest]:
        """List unexpired pending requests within an optional exact scope."""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise PermissionValidationError("limit must be between 1 and 500")
        tenant_id, workspace_id = self._resolve_scope(
            scope=scope,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        clauses = ["status = 'pending'"]
        params: list[Any] = []
        for field_name, value in (
            ("tenant_id", tenant_id),
            ("workspace_id", workspace_id),
            ("conversation_id", conversation_id),
            ("agent_id", agent_id),
        ):
            if value is None:
                continue
            clauses.append(f"{field_name} = ?")
            params.append(_required_text(value, field_name, max_length=256))
        params.append(limit)
        now = self._iso(self._now())
        with self._connection(write=True) as connection:
            if workspace_id is not None:
                workspace_ids = ((tenant_id, workspace_id),)
            else:
                workspace_ids = tuple(
                    (row["tenant_id"], row["workspace_id"])
                    for row in connection.execute(
                        "SELECT DISTINCT tenant_id, workspace_id FROM permission_requests"
                    ).fetchall()
                )
            for scoped_tenant_id, scoped_workspace_id in workspace_ids:
                self._expire_due(
                    connection,
                    now,
                    workspace_id=scoped_workspace_id,
                    tenant_id=scoped_tenant_id,
                )
            rows = connection.execute(
                "SELECT * FROM permission_requests WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at, id LIMIT ?",
                params,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def decide(
        self,
        request_id: str,
        *,
        decision: Literal["approved", "rejected"],
        actor_id: str,
        actor_role: PermissionRole,
        expected_version: int,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
        acknowledged_risk: PermissionRisk | None = None,
    ) -> PermissionRequest:
        """Atomically approve or reject one still-pending request."""

        request_id = _required_text(request_id, "request_id", max_length=256)
        if decision not in {"approved", "rejected"}:
            raise PermissionValidationError("decision must be approved or rejected")
        actor_id = _required_text(actor_id, "actor_id", max_length=256)
        if actor_role not in PERMISSION_ROLES:
            raise PermissionValidationError(f"unsupported actor_role: {actor_role}")
        if acknowledged_risk is not None and acknowledged_risk not in PERMISSION_RISKS:
            raise PermissionValidationError(
                f"unsupported acknowledged_risk: {acknowledged_risk}"
            )
        expected_version = _state_version(expected_version)
        tenant_id, workspace_id = self._resolve_scope(
            scope=scope,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if tenant_id is not None:
            tenant_id = _required_text(tenant_id, "tenant_id", max_length=256)
        if workspace_id is not None:
            workspace_id = _required_text(
                workspace_id, "workspace_id", max_length=256
            )
        now = self._iso(self._now())

        with self._connection(write=True) as connection:
            current = self._request_row(
                connection,
                request_id,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
            )
            scoped_tenant_id = current["tenant_id"]
            scoped_workspace_id = current["workspace_id"]
            self._expire_due(
                connection,
                now,
                workspace_id=scoped_workspace_id,
                tenant_id=scoped_tenant_id,
                request_id=request_id,
            )
            current = self._request_row(
                connection,
                request_id,
                workspace_id=scoped_workspace_id,
                tenant_id=scoped_tenant_id,
            )
            if current["status"] != "pending" or int(
                current["state_version"]
            ) != expected_version:
                raise PermissionConflictError(
                    "permission request is no longer pending at the expected version"
                )
            if decision == "approved" and _ROLE_RANK[actor_role] < _ROLE_RANK[
                current["required_role"]
            ]:
                raise PermissionError("actor role is below the required role")
            if (
                decision == "approved"
                and current["risk"] in {"high", "critical", "untrusted"}
                and acknowledged_risk != current["risk"]
            ):
                raise PermissionValidationError(
                    "explicit acknowledgement of the current risk is required"
                )
            cursor = connection.execute(
                """
                UPDATE permission_requests
                SET status = ?, decided_at = ?, decided_by = ?, decided_role = ?,
                    state_version = state_version + 1
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                  AND status = 'pending'
                  AND state_version = ?
                """,
                (
                    decision,
                    now,
                    actor_id,
                    actor_role,
                    request_id,
                    scoped_tenant_id,
                    scoped_workspace_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise PermissionConflictError("permission decision lost a race")
            row = self._request_row(
                connection,
                request_id,
                tenant_id=scoped_tenant_id,
                workspace_id=scoped_workspace_id,
            )
        return self._from_row(row)

    def claim_execution(
        self,
        request_id: str,
        *,
        execution_id: str,
        expected_version: int,
        expected_payload_sha256: str | None = None,
        expected_action_sha256: str | None = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> PermissionRequest:
        """Consume an approval exactly once before performing the side effect."""

        request_id = _required_text(request_id, "request_id", max_length=256)
        execution_id = _required_text(execution_id, "execution_id", max_length=256)
        expected_version = _state_version(expected_version)
        tenant_id, workspace_id = self._resolve_scope(
            scope=scope,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if tenant_id is not None:
            tenant_id = _required_text(tenant_id, "tenant_id", max_length=256)
        expected_payload_sha256 = self._validate_digest(
            expected_payload_sha256, "expected_payload_sha256"
        )
        expected_action_sha256 = self._validate_digest(
            expected_action_sha256, "expected_action_sha256"
        )
        if workspace_id is not None:
            workspace_id = _required_text(
                workspace_id, "workspace_id", max_length=256
            )
        now = self._iso(self._now())

        with self._connection(write=True) as connection:
            current = self._request_row(
                connection,
                request_id,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
            )
            scoped_tenant_id = current["tenant_id"]
            scoped_workspace_id = current["workspace_id"]
            self._expire_due(
                connection,
                now,
                workspace_id=scoped_workspace_id,
                tenant_id=scoped_tenant_id,
                request_id=request_id,
            )
            current = self._request_row(
                connection,
                request_id,
                workspace_id=scoped_workspace_id,
                tenant_id=scoped_tenant_id,
            )
            if not self._action_is_allowed(current["action"]):
                raise PermissionError(
                    f"action is blocked by the server risk policy: {current['action']}"
                )
            if (
                expected_payload_sha256 is not None
                and current["payload_sha256"] != expected_payload_sha256
            ):
                raise PermissionBindingError(
                    "approved request does not match the execution payload"
                )
            if (
                expected_action_sha256 is not None
                and current["action_sha256"] != expected_action_sha256
            ):
                raise PermissionBindingError(
                    "approved request does not match the execution action"
                )
            if current["status"] != "approved" or int(
                current["state_version"]
            ) != expected_version:
                raise PermissionConflictError(
                    "permission request is not an unconsumed approval at the expected version"
                )
            try:
                cursor = connection.execute(
                    """
                    UPDATE permission_requests
                        SET status = 'executing', execution_id = ?,
                        execution_started_at = ?, state_version = state_version + 1
                    WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                      AND status = 'approved'
                      AND state_version = ?
                    """,
                    (
                        execution_id,
                        now,
                        request_id,
                        scoped_tenant_id,
                        scoped_workspace_id,
                        expected_version,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise PermissionConflictError(
                    "execution_id is already bound to another permission request"
                ) from error
            if cursor.rowcount != 1:
                raise PermissionConflictError("permission execution claim lost a race")
            row = self._request_row(
                connection,
                request_id,
                tenant_id=scoped_tenant_id,
                workspace_id=scoped_workspace_id,
            )
        return self._from_row(row)

    def complete_execution(
        self,
        request_id: str,
        *,
        execution_id: str,
        success: bool,
        expected_version: int,
        result: Any = None,
        error: str | None = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: Scope | None = None,
    ) -> PermissionRequest:
        """Finalize the exact execution that claimed an approved request."""

        request_id = _required_text(request_id, "request_id", max_length=256)
        execution_id = _required_text(execution_id, "execution_id", max_length=256)
        if not isinstance(success, bool):
            raise PermissionValidationError("success must be a boolean")
        expected_version = _state_version(expected_version)
        tenant_id, workspace_id = self._resolve_scope(
            scope=scope,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if tenant_id is not None:
            tenant_id = _required_text(tenant_id, "tenant_id", max_length=256)
        if workspace_id is not None:
            workspace_id = _required_text(
                workspace_id, "workspace_id", max_length=256
            )
        normalized_result, result_canonical = _normalized_json(result, field="result")
        redacted_result = _redact_normalized(normalized_result)
        result_json = _canonical_json(redacted_result, field="result")
        result_sha256 = hashlib.sha256(result_canonical.encode("utf-8")).hexdigest()
        if error is not None:
            if not isinstance(error, str):
                raise PermissionValidationError("error must be a string or None")
            error = error.strip()[:_MAX_TEXT_LENGTH] or None
        if success:
            error = None
        status = "completed" if success else "failed"
        now = self._iso(self._now())

        with self._connection(write=True) as connection:
            current = self._request_row(
                connection,
                request_id,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
            )
            scoped_tenant_id = current["tenant_id"]
            scoped_workspace_id = current["workspace_id"]
            self._expire_due(
                connection,
                now,
                workspace_id=scoped_workspace_id,
                tenant_id=scoped_tenant_id,
                request_id=request_id,
            )
            current = self._request_row(
                connection,
                request_id,
                workspace_id=scoped_workspace_id,
                tenant_id=scoped_tenant_id,
            )
            if (
                current["status"] != "executing"
                or current["execution_id"] != execution_id
                or int(current["state_version"]) != expected_version
            ):
                raise PermissionConflictError(
                    "permission request is not executing with this id and version"
                )
            cursor = connection.execute(
                """
                UPDATE permission_requests
                SET status = ?, completed_at = ?, result_json = ?,
                    result_sha256 = ?, error = ?, state_version = state_version + 1
                WHERE id = ? AND status = 'executing'
                  AND tenant_id = ? AND workspace_id = ? AND execution_id = ?
                  AND state_version = ?
                """,
                (
                    status,
                    now,
                    result_json,
                    result_sha256,
                    error,
                    request_id,
                    scoped_tenant_id,
                    scoped_workspace_id,
                    execution_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise PermissionConflictError("permission completion lost a race")
            row = self._request_row(
                connection,
                request_id,
                tenant_id=scoped_tenant_id,
                workspace_id=scoped_workspace_id,
            )
        return self._from_row(row)


__all__ = [
    "PermissionBindingError",
    "PermissionConflictError",
    "PermissionNotFoundError",
    "PermissionRequest",
    "PermissionStatus",
    "PermissionStore",
    "PermissionStoreError",
    "PermissionValidationError",
    "canonical_payload_sha256",
    "redact_sensitive",
]
