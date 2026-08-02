"""Async bridge from LiveKit turns to the existing PMAgent REST gateway."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import re
from typing import Any, AsyncIterator, Callable, Mapping

from .config import VoiceSettings
from .contracts import VoiceProviderError
from .session import VoiceSessionContext
from .service import speakable_text


_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])")


@dataclass(frozen=True, slots=True)
class VoiceTurnOutcome:
    response: str
    conversation_id: str
    turn_id: str
    awaiting_approval: bool = False
    permission_request_id: str | None = None


def spoken_chunks(value: str, *, target_chars: int = 90) -> list[str]:
    """Split a reply at sentence boundaries for low-latency TTS."""

    text = speakable_text(value)
    if not text:
        return []
    text = re.sub(r"\s+", " ", text).strip()
    chunks: list[str] = []
    pending = ""
    for part in _SENTENCE_END.split(text):
        part = part.strip()
        if not part:
            continue
        if pending and len(pending) + len(part) > target_chars:
            chunks.append(pending)
            pending = part
        else:
            pending = f"{pending}{part}"
        while len(pending) > target_chars * 2:
            chunks.append(pending[:target_chars])
            pending = pending[target_chars:]
    if pending:
        chunks.append(pending)
    return chunks


class PMAgentVoiceBridge:
    """Call the authenticated chat API without creating a second agent brain."""

    def __init__(
        self,
        settings: VoiceSettings,
        context: VoiceSessionContext,
        *,
        http_session: Any | None = None,
        event_sink: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.context = context
        self._http_session = http_session
        self._event_sink = event_sink

    async def _emit(self, event: Mapping[str, Any]) -> None:
        if self._event_sink is None:
            return
        result = self._event_sink(dict(event))
        if inspect.isawaitable(result):
            await result

    def _headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "x-tenant-id": self.context.tenant_id,
            "x-workspace-id": self.context.workspace_id,
            "x-actor-id": self.context.actor_id,
            "x-actor-role": self.context.actor_role,
            # The signed LiveKit session represents the human participant. The
            # worker still cannot approve permissions; approval endpoints also
            # require an explicit UI action and CAS version.
            "x-actor-kind": "human",
            "x-request-id": f"voice-{self.context.session_id}-{self.context.generation}",
        }
        if self.settings.gateway_secret:
            headers["x-gateway-token"] = self.settings.gateway_secret
        return headers

    async def chat(self, message: str) -> VoiceTurnOutcome:
        message = str(message or "").strip()
        if not message:
            raise VoiceProviderError("voice turn did not contain a final transcript")
        try:
            import aiohttp
        except ImportError as error:  # pragma: no cover - LiveKit depends on aiohttp
            raise VoiceProviderError("voice HTTP runtime is not installed") from error

        owns_session = self._http_session is None
        session = self._http_session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=150, connect=10)
        )
        try:
            async with session.post(
                f"{self.settings.api_base_url}/v1/chat",
                headers=self._headers(),
                json={
                    "message": message,
                    "conversation_id": self.context.conversation_id,
                    "attachments": [],
                },
            ) as response:
                raw = await response.text()
                try:
                    payload = json.loads(raw)
                except ValueError as error:
                    raise VoiceProviderError("PMAgent returned an invalid response") from error
                if response.status not in {200, 202} or not isinstance(payload, Mapping):
                    raise VoiceProviderError("PMAgent could not complete the voice turn")
        finally:
            if owns_session:
                await session.close()

        response_text = str(payload.get("response") or "").strip()
        if not response_text:
            raise VoiceProviderError("PMAgent returned an empty response")
        return VoiceTurnOutcome(
            response=response_text,
            conversation_id=str(payload.get("conversation_id") or self.context.conversation_id),
            turn_id=str(payload.get("turn_id") or ""),
            awaiting_approval=bool(payload.get("awaiting_approval")),
            permission_request_id=(
                str(payload["permission_request_id"])
                if payload.get("permission_request_id")
                else None
            ),
        )

    async def stream_reply(self, message: str) -> AsyncIterator[str]:
        outcome = await self.chat(message)
        event = {
            "type": "approval.required" if outcome.awaiting_approval else "turn.completed",
            "conversation_id": outcome.conversation_id,
            "turn_id": outcome.turn_id,
            "generation": self.context.generation,
        }
        if outcome.permission_request_id:
            event["permission_request_id"] = outcome.permission_request_id
        await self._emit(event)
        chunks = spoken_chunks(outcome.response)
        for chunk in chunks:
            yield chunk
        if outcome.awaiting_approval and not any(
            phrase in outcome.response for phrase in ("确认", "批准", "授权")
        ):
            yield "请在对话界面确认这项操作。"


__all__ = ["PMAgentVoiceBridge", "VoiceTurnOutcome", "spoken_chunks"]
