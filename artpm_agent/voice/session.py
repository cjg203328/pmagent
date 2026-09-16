"""Secure, tenant-bound LiveKit session creation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from .config import VoiceSettings, get_voice_settings
from .contracts import VoiceConfigurationError, VoiceUnavailableError

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")


def _identifier(value: Any, name: str, *, maximum: int = 256) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or not _IDENTIFIER_RE.fullmatch(text):
        raise VoiceConfigurationError(f"invalid {name}")
    return text


@dataclass(frozen=True, slots=True)
class VoiceSessionContext:
    """Trusted context sent to the worker through LiveKit agent dispatch."""

    session_id: str
    tenant_id: str
    workspace_id: str
    conversation_id: str
    actor_id: str
    actor_role: str
    generation: int = 1

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str | bytes | Mapping[str, Any]) -> VoiceSessionContext:
        if isinstance(value, Mapping):
            payload = dict(value)
        else:
            try:
                payload = json.loads(value)
            except (TypeError, ValueError) as error:
                raise VoiceConfigurationError("voice session metadata is invalid") from error
        if not isinstance(payload, Mapping):
            raise VoiceConfigurationError("voice session metadata is invalid")
        role = str(payload.get("actor_role") or "user").strip().casefold()
        if role not in {"user", "admin"}:
            raise VoiceConfigurationError("invalid actor_role")
        try:
            generation = int(payload.get("generation", 1))
        except (TypeError, ValueError) as error:
            raise VoiceConfigurationError("invalid generation") from error
        if not 1 <= generation <= 2**31 - 1:
            raise VoiceConfigurationError("invalid generation")
        return cls(
            session_id=_identifier(payload.get("session_id"), "session_id"),
            tenant_id=_identifier(payload.get("tenant_id"), "tenant_id", maximum=128),
            workspace_id=_identifier(payload.get("workspace_id"), "workspace_id", maximum=128),
            conversation_id=_identifier(
                payload.get("conversation_id"), "conversation_id", maximum=256
            ),
            actor_id=_identifier(payload.get("actor_id"), "actor_id", maximum=128),
            actor_role=role,
            generation=generation,
        )


@dataclass(frozen=True, slots=True)
class VoiceSessionDetails:
    server_url: str
    participant_token: str
    room_name: str
    session_id: str
    expires_at: str
    agent_name: str
    features: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VoiceSessionBroker:
    """Mint short-lived LiveKit credentials from server-owned identity."""

    def __init__(
        self,
        settings: VoiceSettings | None = None,
        *,
        token_issuer: Callable[[VoiceSessionContext, str, int], str] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings or get_voice_settings()
        self._custom_token_issuer = token_issuer is not None
        self._token_issuer = token_issuer or self._issue_livekit_token
        self._now = now or (lambda: datetime.now(timezone.utc))

    def status(self) -> dict[str, Any]:
        return self.settings.public_status()

    def create(
        self,
        *,
        principal: Any,
        conversation_id: str,
    ) -> VoiceSessionDetails:
        status = self.status()
        if not status["enabled"]:
            raise VoiceUnavailableError("voice channel is not enabled")
        if not self.settings.livekit_configured:
            raise VoiceUnavailableError("LiveKit is not configured")
        if (
            not self._custom_token_issuer
            and not self.settings.optional_dependencies_installed
        ):
            raise VoiceUnavailableError("voice dependencies are not installed")
        if not self.settings.stt_configured:
            raise VoiceUnavailableError("speech recognition is not configured")
        if not self.settings.speech_output_configured:
            raise VoiceUnavailableError("speech output is not configured")

        context = VoiceSessionContext(
            session_id=uuid4().hex,
            tenant_id=_identifier(getattr(principal, "tenant_id", ""), "tenant_id", maximum=128),
            workspace_id=_identifier(
                getattr(principal, "workspace_id", ""), "workspace_id", maximum=128
            ),
            conversation_id=_identifier(conversation_id, "conversation_id", maximum=256),
            actor_id=_identifier(getattr(principal, "actor_id", ""), "actor_id", maximum=128),
            actor_role=str(getattr(principal, "actor_role", "user") or "user").casefold(),
        )
        room_name = f"{self.settings.room_prefix}-{context.session_id}"
        ttl = self.settings.max_session_seconds
        token = self._token_issuer(context, room_name, ttl)
        if not isinstance(token, str) or not token:
            raise VoiceConfigurationError("voice token issuer returned an invalid token")
        expires = self._now() + timedelta(seconds=ttl)
        return VoiceSessionDetails(
            server_url=self.settings.livekit_url,
            participant_token=token,
            room_name=room_name,
            session_id=context.session_id,
            expires_at=expires.isoformat().replace("+00:00", "Z"),
            agent_name=self.settings.agent_name,
            features={
                "barge_in": True,
                "partial_transcripts": True,
                "approval_in_ui": self.settings.require_approval,
                "text_fallback": True,
            },
        )

    def _issue_livekit_token(
        self,
        context: VoiceSessionContext,
        room_name: str,
        ttl_seconds: int,
    ) -> str:
        try:
            from livekit import api
        except ImportError as error:  # pragma: no cover - optional dependency
            raise VoiceUnavailableError(
                'install the optional voice dependencies with `pip install -e ".[voice]"`'
            ) from error

        metadata = context.to_json()
        grants = api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            can_update_own_metadata=False,
        )
        dispatch = api.RoomAgentDispatch(
            agent_name=self.settings.agent_name,
            metadata=metadata,
        )
        token = (
            api.AccessToken(
                self.settings.livekit_api_key,
                self.settings.livekit_api_secret,
            )
            .with_identity(f"voice-user-{context.session_id}")
            .with_name("ArtPM voice user")
            .with_metadata(metadata)
            .with_grants(grants)
            .with_room_config(api.RoomConfiguration(agents=[dispatch]))
            .with_ttl(timedelta(seconds=ttl_seconds))
        )
        return token.to_jwt()


__all__ = ["VoiceSessionBroker", "VoiceSessionContext", "VoiceSessionDetails"]
