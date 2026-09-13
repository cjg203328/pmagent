"""HTTP adapter for TencentDB Agent Memory's gateway."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
from typing import Any, Mapping
from urllib.parse import urlparse

import requests


class TencentDBAgentMemoryError(RuntimeError):
    """Raised when the TencentDB Agent Memory gateway cannot serve a request."""


class TencentDBAgentMemoryConfigurationError(TencentDBAgentMemoryError):
    """Raised when an enabled TencentDB Agent Memory integration is unsafe."""


def _text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TencentDBAgentMemoryConfigurationError(
            f"{field_name} is required when TencentDB Agent Memory is enabled"
        )
    return text


def _boolean(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _number(value: Any, field_name: str, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise TencentDBAgentMemoryConfigurationError(
            f"{field_name} must be a number"
        ) from error
    if number <= 0:
        raise TencentDBAgentMemoryConfigurationError(
            f"{field_name} must be greater than zero"
        )
    return number


def _limit(value: Any, field_name: str, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise TencentDBAgentMemoryConfigurationError(
            f"{field_name} must be an integer"
        ) from error
    if number <= 0:
        raise TencentDBAgentMemoryConfigurationError(
            f"{field_name} must be greater than zero"
        )
    return number


@dataclass(frozen=True)
class TencentDBAgentMemorySettings:
    """Deployment-owned settings for the TencentDB Agent Memory sidecar."""

    enabled: bool = False
    base_url: str = "http://127.0.0.1:8420"
    api_key: str = ""
    scope_secret: str = ""
    timeout_seconds: float = 3.0
    max_context_chars: int = 2400
    max_capture_chars: int = 12000
    agent_id: str = "artpm-agent"
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        base_url = _text(self.base_url, "base_url").rstrip("/")
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or not host:
            raise TencentDBAgentMemoryConfigurationError(
                "base_url must be an absolute HTTP(S) URL"
            )
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        is_local = host in local_hosts
        if parsed.scheme == "http" and not is_local and not self.allow_insecure_http:
            raise TencentDBAgentMemoryConfigurationError(
                "non-loopback TencentDB Agent Memory URLs must use HTTPS or set allow_insecure_http"
            )
        if not is_local and not str(self.api_key or "").strip():
            raise TencentDBAgentMemoryConfigurationError(
                "api_key is required for a non-loopback TencentDB Agent Memory gateway"
            )
        if not str(self.scope_secret or "").strip():
            raise TencentDBAgentMemoryConfigurationError(
                "scope_secret is required to derive opaque tenant memory keys"
            )
        if self.timeout_seconds <= 0:
            raise TencentDBAgentMemoryConfigurationError(
                "timeout_seconds must be greater than zero"
            )
        if self.max_context_chars <= 0 or self.max_capture_chars <= 0:
            raise TencentDBAgentMemoryConfigurationError(
                "memory size limits must be greater than zero"
            )
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "agent_id", _text(self.agent_id, "agent_id"))

    @classmethod
    def from_config(
        cls, config: Mapping[str, Any] | None = None
    ) -> "TencentDBAgentMemorySettings":
        values = dict(config or {})
        return cls(
            enabled=_boolean(values.get("enabled")),
            base_url=str(values.get("base_url") or "http://127.0.0.1:8420"),
            api_key=str(values.get("api_key") or ""),
            scope_secret=str(values.get("scope_secret") or ""),
            timeout_seconds=_number(
                values.get("timeout_seconds"), "timeout_seconds", 3.0
            ),
            max_context_chars=_limit(
                values.get("max_context_chars"), "max_context_chars", 2400
            ),
            max_capture_chars=_limit(
                values.get("max_capture_chars"), "max_capture_chars", 12000
            ),
            agent_id=str(values.get("agent_id") or "artpm-agent"),
            allow_insecure_http=_boolean(values.get("allow_insecure_http")),
        )


@dataclass(frozen=True)
class TencentDBAgentMemoryScope:
    """Trusted identity values used to derive opaque gateway session keys."""

    tenant_id: str
    workspace_id: str
    principal_id: str
    conversation_id: str
    agent_id: str = "artpm-agent"

    def __post_init__(self) -> None:
        for field_name in (
            "tenant_id",
            "workspace_id",
            "principal_id",
            "conversation_id",
            "agent_id",
        ):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )


class TencentDBAgentMemoryClient:
    """Small, synchronous client for the upstream ``/recall`` and ``/capture`` API."""

    def __init__(
        self,
        settings: TencentDBAgentMemorySettings,
        *,
        session: requests.Session | None = None,
    ) -> None:
        if not settings.enabled:
            raise TencentDBAgentMemoryConfigurationError(
                "TencentDB Agent Memory client requires enabled settings"
            )
        self.settings = settings
        self._session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return True

    def recall(self, query: str, scope: TencentDBAgentMemoryScope) -> str:
        query = str(query or "").strip()
        if not query:
            return ""
        response = self._post(
            "/recall",
            {
                "query": query[:4000],
                "session_key": self._opaque_key("scope", scope),
                "user_id": self._opaque_key("user", scope),
            },
        )
        try:
            code = int(response.get("code", 0) or 0)
        except (TypeError, ValueError):
            code = 1
        if code != 0:
            return ""
        return str(response.get("context") or "").strip()[: self.settings.max_context_chars]

    def capture(
        self,
        user_content: str,
        assistant_content: str,
        scope: TencentDBAgentMemoryScope,
    ) -> None:
        user_content = str(user_content or "").strip()
        assistant_content = str(assistant_content or "").strip()
        if not user_content or not assistant_content:
            return
        self._post(
            "/capture",
            {
                "user_content": user_content[: self.settings.max_capture_chars],
                "assistant_content": assistant_content[: self.settings.max_capture_chars],
                "session_key": self._opaque_key("scope", scope),
                "session_id": self._opaque_key("conversation", scope),
                "user_id": self._opaque_key("user", scope),
            },
        )

    def close(self) -> None:
        self._session.close()

    def _opaque_key(self, kind: str, scope: TencentDBAgentMemoryScope) -> str:
        values = {
            "tenant_id": scope.tenant_id,
            "workspace_id": scope.workspace_id,
            "principal_id": scope.principal_id,
            "agent_id": scope.agent_id,
        }
        if kind == "conversation":
            values["conversation_id"] = scope.conversation_id
        encoded = json.dumps(
            values, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        digest = hmac.new(
            self.settings.scope_secret.encode("utf-8"),
            kind.encode("ascii") + b":" + encoded,
            sha256,
        ).hexdigest()
        return f"artpm-{kind}-v1-{digest}"

    def _post(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        headers = {"Accept": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        try:
            response = self._session.post(
                f"{self.settings.base_url}{path}",
                json=dict(payload),
                headers=headers,
                timeout=self.settings.timeout_seconds,
            )
        except requests.RequestException as error:
            raise TencentDBAgentMemoryError("TencentDB Agent Memory gateway is unavailable") from error
        if not response.ok:
            raise TencentDBAgentMemoryError(
                f"TencentDB Agent Memory gateway returned HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise TencentDBAgentMemoryError(
                "TencentDB Agent Memory gateway returned invalid JSON"
            ) from error
        if not isinstance(body, Mapping):
            raise TencentDBAgentMemoryError(
                "TencentDB Agent Memory gateway returned an invalid response"
            )
        return body


def create_tencentdb_agent_memory_client(
    config: Mapping[str, Any] | None = None,
) -> TencentDBAgentMemoryClient | None:
    """Build the optional client without starting or owning an upstream sidecar."""

    settings = TencentDBAgentMemorySettings.from_config(config)
    if not settings.enabled:
        return None
    return TencentDBAgentMemoryClient(settings)


__all__ = [
    "TencentDBAgentMemoryClient",
    "TencentDBAgentMemoryConfigurationError",
    "TencentDBAgentMemoryError",
    "TencentDBAgentMemoryScope",
    "TencentDBAgentMemorySettings",
    "create_tencentdb_agent_memory_client",
]
