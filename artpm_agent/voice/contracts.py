"""Provider-neutral contracts used by desktop and real-time voice hosts."""

from __future__ import annotations

from dataclasses import dataclass


class VoiceError(RuntimeError):
    """Base class for safe voice-channel failures."""


class VoiceConfigurationError(VoiceError):
    """Raised when voice was enabled with an invalid configuration."""


class VoiceUnavailableError(VoiceError):
    """Raised when an optional voice capability is not currently available."""


class VoiceProviderError(VoiceError):
    """Raised after a provider request fails without exposing provider secrets."""


@dataclass(frozen=True, slots=True)
class Transcript:
    """Final speech recognition result. Interim text is never persisted."""

    text: str
    provider: str
    language: str = "zh"
    is_final: bool = True


@dataclass(frozen=True, slots=True)
class SpeechAudio:
    """Synthesized speech payload suitable for ``st.audio`` or an API response."""

    data: bytes
    mime_type: str
    provider: str
    sample_rate: int | None = None


__all__ = [
    "SpeechAudio",
    "Transcript",
    "VoiceConfigurationError",
    "VoiceError",
    "VoiceProviderError",
    "VoiceUnavailableError",
]
