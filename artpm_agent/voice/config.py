"""Environment-backed configuration for the optional voice channel."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import importlib.util
import os
import re
import shutil
from typing import Any


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def _provider_list(value: str | None) -> tuple[str, ...]:
    result: list[str] = []
    for item in str(value or "").split(","):
        provider = item.strip().casefold()
        if provider and provider not in result:
            result.append(provider)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    """Immutable voice settings with secret-free status reporting."""

    enabled: bool = False
    transport: str = "livekit"
    livekit_url: str = ""
    livekit_api_key: str = field(default="", repr=False)
    livekit_api_secret: str = field(default="", repr=False)
    room_prefix: str = "artpm"
    agent_name: str = "artpm-voice"
    stt_provider: str = "cartesia"
    stt_model: str = "ink-whisper"
    stt_language: str = "zh"
    tts_provider: str = "minimax"
    tts_fallbacks: tuple[str, ...] = ("minimax", "cartesia", "local", "text")
    tts_model: str = ""
    tts_voice_id: str = ""
    cartesia_api_key: str = field(default="", repr=False)
    minimax_api_key: str = field(default="", repr=False)
    require_approval: bool = True
    max_session_seconds: int = 1800
    fallback: str = "text"
    api_base_url: str = "http://127.0.0.1:8765"
    gateway_secret: str = field(default="", repr=False)
    max_recording_bytes: int = 20 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "VoiceSettings":
        try:
            from dotenv import load_dotenv

            load_dotenv(override=False)
        except (ImportError, OSError):
            pass
        room_prefix = _clean(os.getenv("LIVEKIT_ROOM_PREFIX")) or "artpm"
        if not _SAFE_NAME.fullmatch(room_prefix):
            room_prefix = "artpm"
        agent_name = _clean(os.getenv("ARTPM_VOICE_AGENT_NAME")) or "artpm-voice"
        if not _SAFE_NAME.fullmatch(agent_name):
            agent_name = "artpm-voice"
        provider = (_clean(os.getenv("VOICE_TTS_PROVIDER")) or "minimax").casefold()
        fallbacks = _provider_list(os.getenv("VOICE_TTS_FALLBACKS"))
        if not fallbacks:
            fallbacks = (provider, "cartesia", "local", "text")
        return cls(
            enabled=_bool_env("ARTPM_VOICE_ENABLED"),
            transport=(
                _clean(os.getenv("ARTPM_VOICE_TRANSPORT")) or "livekit"
            ).casefold(),
            livekit_url=_clean(os.getenv("LIVEKIT_URL")),
            livekit_api_key=_clean(os.getenv("LIVEKIT_API_KEY")),
            livekit_api_secret=_clean(os.getenv("LIVEKIT_API_SECRET")),
            room_prefix=room_prefix,
            agent_name=agent_name,
            stt_provider=(
                _clean(os.getenv("VOICE_STT_PROVIDER")) or "cartesia"
            ).casefold(),
            stt_model=_clean(os.getenv("VOICE_STT_MODEL")) or "ink-whisper",
            stt_language=_clean(os.getenv("VOICE_STT_LANGUAGE")) or "zh",
            tts_provider=provider,
            tts_fallbacks=fallbacks,
            tts_model=_clean(os.getenv("VOICE_TTS_MODEL")),
            tts_voice_id=_clean(os.getenv("VOICE_TTS_VOICE_ID")),
            cartesia_api_key=_clean(os.getenv("CARTESIA_API_KEY")),
            minimax_api_key=_clean(os.getenv("MINIMAX_API_KEY")),
            require_approval=_bool_env("VOICE_REQUIRE_APPROVAL", True),
            max_session_seconds=_int_env(
                "VOICE_MAX_SESSION_SECONDS", 1800, minimum=60, maximum=3600
            ),
            fallback=(_clean(os.getenv("VOICE_FALLBACK")) or "text").casefold(),
            api_base_url=(
                _clean(os.getenv("ARTPM_API_BASE_URL"))
                or _clean(os.getenv("ARTPM_API_URL"))
                or "http://127.0.0.1:8765"
            ).rstrip("/"),
            gateway_secret=_clean(os.getenv("ARTPM_GATEWAY_SHARED_SECRET")),
            max_recording_bytes=_int_env(
                "VOICE_MAX_RECORDING_BYTES",
                20 * 1024 * 1024,
                minimum=1024,
                maximum=100 * 1024 * 1024,
            ),
        )

    @property
    def livekit_configured(self) -> bool:
        return bool(
            self.transport == "livekit"
            and self.livekit_url.startswith(("ws://", "wss://"))
            and self.livekit_api_key
            and self.livekit_api_secret
        )

    @property
    def stt_configured(self) -> bool:
        return self.stt_provider == "cartesia" and bool(self.cartesia_api_key)

    @property
    def optional_dependencies_installed(self) -> bool:
        try:
            return importlib.util.find_spec("livekit.agents") is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    @property
    def local_tts_available(self) -> bool:
        if os.name == "nt" and (shutil.which("powershell") or shutil.which("pwsh")):
            return True
        try:
            return importlib.util.find_spec("pyttsx3") is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    def tts_provider_ready(self, provider: str) -> bool:
        provider = str(provider or "").casefold()
        if provider == "minimax":
            return bool(self.minimax_api_key)
        if provider == "cartesia":
            return bool(self.cartesia_api_key)
        if provider == "local":
            return self.local_tts_available
        if provider == "text":
            return True
        return False

    @property
    def speech_output_configured(self) -> bool:
        return any(
            provider != "text" and self.tts_provider_ready(provider)
            for provider in self.tts_fallbacks
        )

    def public_status(self) -> dict[str, Any]:
        """Return health metadata without returning credentials or raw errors."""

        dependencies = self.optional_dependencies_installed
        recording_ready = self.enabled and dependencies and self.stt_configured
        realtime_ready = (
            recording_ready
            and self.livekit_configured
            and self.speech_output_configured
        )
        missing: list[str] = []
        if self.enabled:
            if not dependencies:
                missing.append("voice_dependencies")
            if not self.livekit_configured:
                missing.append("livekit")
            if not self.stt_configured:
                missing.append("stt")
            if not self.speech_output_configured:
                missing.append("tts")
        return {
            "enabled": self.enabled,
            "transport": self.transport,
            "recording_ready": recording_ready,
            "realtime_ready": realtime_ready,
            "text_fallback": "text" in self.tts_fallbacks or self.fallback == "text",
            "approval_required": self.require_approval,
            "stt": {
                "provider": self.stt_provider,
                "model": self.stt_model,
                "language": self.stt_language,
                "configured": self.stt_configured,
            },
            "tts": {
                "provider": self.tts_provider,
                "fallbacks": list(self.tts_fallbacks),
                "configured": self.speech_output_configured,
                "local_available": self.local_tts_available,
            },
            "missing": missing,
        }


@lru_cache(maxsize=1)
def get_voice_settings() -> VoiceSettings:
    return VoiceSettings.from_env()


__all__ = ["VoiceSettings", "get_voice_settings"]
