"""Small, opt-in embed gateway for trusted website integrations.

The embed surface deliberately stops at a signed session boundary. A publish
token is accepted only during server-to-server exchange; browser requests use
short-lived HMAC-bound session tokens and an exact Origin allowlist.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import base64
import binascii
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4

from .services import validate_identifier


class EmbedError(ValueError):
    """A safe, client-facing embed contract error."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class EmbedChannel:
    name: str
    tenant_id: str
    workspace_id: str
    profile_id: str
    publish_token: str
    allowed_origins: tuple[str, ...]


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _origin(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    try:
        parsed = urlparse(value)
    except ValueError as error:
        raise EmbedError(400, "invalid_embed_origin", "embed origin must be an http(s) origin") from error
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EmbedError(400, "invalid_embed_origin", "embed origin must be an http(s) origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise EmbedError(400, "invalid_embed_origin", "embed origin must not include a path")
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def _allowed_origins(raw: Any) -> tuple[str, ...]:
    values = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").split(",")
    normalized = []
    for value in values:
        if str(value).strip():
            normalized.append(_origin(str(value)))
    return tuple(dict.fromkeys(normalized))


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


@dataclass(frozen=True, slots=True)
class EmbedSettings:
    enabled: bool
    secret: str
    channels: Mapping[str, EmbedChannel]
    session_ttl: int = 3600
    exchange_ttl: int = 120
    rate_limit: int = 30

    @classmethod
    def from_env(cls) -> "EmbedSettings":
        enabled = os.getenv("ARTPM_EMBED_ENABLED", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        secret = os.getenv("ARTPM_EMBED_SECRET", "").strip()
        if not enabled:
            return cls(enabled=False, secret=secret, channels={})
        channels: dict[str, EmbedChannel] = {}
        raw = os.getenv("ARTPM_EMBED_CHANNELS", "").strip()
        definitions: Mapping[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as error:
                raise EmbedError(500, "embed_configuration_invalid", "embed channel configuration is invalid") from error
            if not isinstance(parsed, Mapping):
                raise EmbedError(500, "embed_configuration_invalid", "embed channel configuration is invalid")
            definitions = parsed
        elif secret:
            definitions = {
                os.getenv("ARTPM_EMBED_CHANNEL", "default"): {
                    "tenant_id": os.getenv("ARTPM_EMBED_TENANT_ID", "local"),
                    "workspace_id": os.getenv("ARTPM_EMBED_WORKSPACE_ID", "local-default"),
                    "profile_id": os.getenv("ARTPM_EMBED_PROFILE_ID", "local-default"),
                    "publish_token": os.getenv("ARTPM_EMBED_PUBLISH_TOKEN", secret),
                    "allowed_origins": os.getenv("ARTPM_EMBED_ALLOWED_ORIGINS", ""),
                }
            }
        for raw_name, raw_config in definitions.items():
            try:
                name = validate_identifier(str(raw_name), "channel", max_length=64)
            except (TypeError, ValueError) as error:
                raise EmbedError(500, "embed_configuration_invalid", "embed channel configuration is invalid") from error
            if not isinstance(raw_config, Mapping):
                raise EmbedError(500, "embed_configuration_invalid", "embed channel configuration is invalid")
            try:
                channel = EmbedChannel(
                    name=name,
                    tenant_id=validate_identifier(str(raw_config.get("tenant_id") or "local"), "tenant_id"),
                    workspace_id=validate_identifier(str(raw_config["workspace_id"]), "workspace_id"),
                    profile_id=validate_identifier(str(raw_config.get("profile_id") or "local-default"), "profile_id"),
                    publish_token=str(raw_config.get("publish_token") or secret).strip(),
                    allowed_origins=_allowed_origins(raw_config.get("allowed_origins", ())),
                )
            except (KeyError, ValueError) as error:
                raise EmbedError(500, "embed_configuration_invalid", "embed channel configuration is invalid") from error
            if not channel.publish_token or not channel.allowed_origins:
                raise EmbedError(500, "embed_configuration_invalid", "embed channel configuration is incomplete")
            channels[name] = channel
        return cls(
            enabled=enabled,
            secret=secret,
            channels=channels,
            session_ttl=_bounded_env_int("ARTPM_EMBED_SESSION_TTL", 3600, 300, 86_400),
            exchange_ttl=_bounded_env_int("ARTPM_EMBED_EXCHANGE_TTL", 120, 30, 900),
            rate_limit=_bounded_env_int("ARTPM_EMBED_RATE_LIMIT", 30, 1, 600),
        )


class EmbedGateway:
    """Issue and verify signed embed tokens with a bounded in-memory limiter."""

    def __init__(self, settings: EmbedSettings | None = None) -> None:
        self.settings = settings or EmbedSettings.from_env()
        self._lock = threading.RLock()
        self._requests: dict[str, deque[float]] = {}

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def _channel(self, name: str) -> EmbedChannel:
        if not self.enabled:
            raise EmbedError(404, "embed_not_found", "embed channel is not available")
        channel = self.settings.channels.get(str(name).strip())
        if channel is None:
            raise EmbedError(404, "embed_not_found", "embed channel is not available")
        return channel

    @staticmethod
    def _check_origin(channel: EmbedChannel, value: str) -> str:
        origin = _origin(value)
        if origin not in channel.allowed_origins:
            raise EmbedError(403, "embed_origin_denied", "embed origin is not allowed")
        return origin

    def _limit(self, key: str) -> None:
        now = time.monotonic()
        window = 60.0
        with self._lock:
            values = self._requests.setdefault(key, deque())
            while values and now - values[0] >= window:
                values.popleft()
            if len(values) >= self.settings.rate_limit:
                raise EmbedError(429, "embed_rate_limited", "embed request rate limit exceeded")
            values.append(now)
            if len(self._requests) > 4096:
                oldest_key = min(
                    self._requests,
                    key=lambda item: self._requests[item][0] if self._requests[item] else now,
                )
                self._requests.pop(oldest_key, None)

    def _sign(self, payload: Mapping[str, Any]) -> str:
        if not self.settings.secret:
            raise EmbedError(503, "embed_unconfigured", "embed signing secret is not configured")
        body = _b64(json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode())
        signature = _b64(hmac.new(self.settings.secret.encode(), body.encode(), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def _verify(self, token: str, *, kind: str) -> dict[str, Any]:
        try:
            body, signature = str(token or "").split(".", 1)
            expected = _b64(hmac.new(self.settings.secret.encode(), body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(_unb64(body))
            if not isinstance(payload, dict) or payload.get("kind") != kind:
                raise ValueError
            if float(payload.get("exp", 0)) <= time.time():
                raise ValueError
            validate_identifier(str(payload.get("channel") or ""), "channel", max_length=64)
            return payload
        except (
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            binascii.Error,
            OverflowError,
        ):
            raise EmbedError(401, "embed_token_invalid", "embed token is invalid or expired") from None

    def public_config(self, channel_name: str, origin: str) -> dict[str, Any]:
        channel = self._channel(channel_name)
        origin = self._check_origin(channel, origin)
        return {
            "channel": channel.name,
            "tenant_id": channel.tenant_id,
            "workspace_id": channel.workspace_id,
            "allowed_origins": list(channel.allowed_origins),
            "origin": origin,
            "exchange_ttl_seconds": self.settings.exchange_ttl,
            "session_ttl_seconds": self.settings.session_ttl,
        }

    def exchange(self, channel_name: str, *, origin: str, publish_token: str, rate_key: str) -> dict[str, Any]:
        channel = self._channel(channel_name)
        origin = self._check_origin(channel, origin)
        self._limit(f"exchange:{channel.name}:{rate_key}")
        if not hmac.compare_digest(str(publish_token or ""), channel.publish_token):
            raise EmbedError(401, "embed_publish_token_invalid", "embed publish token is invalid")
        now = time.time()
        return {
            "exchange_token": self._sign(
                {
                    "kind": "exchange",
                    "channel": channel.name,
                    "origin": origin,
                    "tenant_id": channel.tenant_id,
                    "workspace_id": channel.workspace_id,
                    "profile_id": channel.profile_id,
                    "exp": now + self.settings.exchange_ttl,
                    "nonce": uuid4().hex,
                }
            ),
            "expires_in": self.settings.exchange_ttl,
        }

    def create_session(
        self,
        channel_name: str,
        *,
        exchange_token: str,
        origin: str,
        conversation_id: str | None,
        rate_key: str,
    ) -> dict[str, Any]:
        channel = self._channel(channel_name)
        origin = self._check_origin(channel, origin)
        self._limit(f"session:{channel.name}:{rate_key}")
        self.verify_exchange(channel_name, exchange_token=exchange_token, origin=origin)
        session_id = uuid4().hex
        actor_id = f"embed-{channel.name}-{session_id[:16]}"
        return {
            "session_token": self._sign(
                {
                    "kind": "session",
                    "channel": channel.name,
                    "origin": origin,
                    "tenant_id": channel.tenant_id,
                    "workspace_id": channel.workspace_id,
                    "profile_id": channel.profile_id,
                    "actor_id": actor_id,
                    "session_id": session_id,
                    "conversation_id": conversation_id,
                    "exp": time.time() + self.settings.session_ttl,
                }
            ),
            "session_id": session_id,
            "expires_in": self.settings.session_ttl,
            "workspace_id": channel.workspace_id,
            "conversation_id": conversation_id,
        }

    def verify_exchange(self, channel_name: str, *, exchange_token: str, origin: str) -> dict[str, Any]:
        """Verify an exchange token before a host allocates persistence."""

        channel = self._channel(channel_name)
        origin = self._check_origin(channel, origin)
        payload = self._verify(exchange_token, kind="exchange")
        if (
            payload.get("channel") != channel.name
            or payload.get("origin") != origin
            or payload.get("workspace_id") != channel.workspace_id
            or payload.get("tenant_id") != channel.tenant_id
        ):
            raise EmbedError(401, "embed_token_invalid", "embed token scope is invalid")
        return payload

    def verify_session(self, channel_name: str, *, token: str, origin: str, rate_key: str) -> dict[str, Any]:
        channel = self._channel(channel_name)
        origin = self._check_origin(channel, origin)
        payload = self._verify(token, kind="session")
        if payload.get("channel") != channel.name or payload.get("origin") != origin:
            raise EmbedError(401, "embed_token_invalid", "embed token is not bound to this origin")
        if payload.get("workspace_id") != channel.workspace_id or payload.get("tenant_id") != channel.tenant_id:
            raise EmbedError(401, "embed_token_invalid", "embed token scope is invalid")
        self._limit(f"chat:{channel.name}:{payload.get('session_id')}:{rate_key}")
        return payload

    @staticmethod
    def response_headers(channel: EmbedChannel) -> dict[str, str]:
        frame_ancestors = " ".join(channel.allowed_origins)
        return {
            "Content-Security-Policy": f"frame-ancestors {frame_ancestors}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        }


__all__ = ["EmbedChannel", "EmbedError", "EmbedGateway", "EmbedSettings"]
