"""Optional voice channel for ArtPM Agent.

The package deliberately keeps LiveKit and provider imports lazy. Importing the
core application therefore does not require the optional ``voice`` extra.
"""

from .config import VoiceSettings, get_voice_settings
from .contracts import (
    SpeechAudio,
    Transcript,
    VoiceConfigurationError,
    VoiceError,
    VoiceProviderError,
    VoiceUnavailableError,
)
from .session import VoiceSessionBroker, VoiceSessionContext, VoiceSessionDetails
from .service import VoiceAudioService, speakable_text

__all__ = [
    "SpeechAudio",
    "Transcript",
    "VoiceConfigurationError",
    "VoiceAudioService",
    "VoiceError",
    "VoiceProviderError",
    "VoiceSessionBroker",
    "VoiceSessionContext",
    "VoiceSessionDetails",
    "VoiceSettings",
    "VoiceUnavailableError",
    "get_voice_settings",
    "speakable_text",
]
