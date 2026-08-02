from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_voice_extra_uses_one_livekit_plugin_series_without_legacy_minimax():
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    voice = manifest["project"]["optional-dependencies"]["voice"]

    assert "livekit-agents==1.6.6" in voice
    assert "livekit-api>=1.2.0,<2.0.0" in voice
    assert "livekit-plugins-cartesia==1.6.6" in voice
    assert "livekit-plugins-minimax-ai==1.6.6" in voice
    assert "livekit-plugins-silero==1.6.6" in voice
    assert "pyttsx3==2.99" in voice
    assert all("turn-detector" not in item for item in voice)
    assert all("livekit-plugins-minimax==" not in item for item in voice)


def test_voice_configuration_is_opt_in_and_preserves_text_fallback():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "ARTPM_VOICE_ENABLED=false" in env
    assert "LIVEKIT_URL=" in env
    assert "LIVEKIT_API_KEY=" in env
    assert "LIVEKIT_API_SECRET=" in env
    assert "VOICE_STT_PROVIDER=cartesia" in env
    assert "VOICE_STT_MODEL=ink-whisper" in env
    assert "VOICE_STT_LANGUAGE=zh" in env
    assert "VOICE_TTS_PROVIDER=minimax" in env
    assert "VOICE_TTS_FALLBACKS=minimax,cartesia,local,text" in env
    assert "DEEPGRAM_API_KEY=" not in env
    assert "VOICE_TTS_PROVIDER=cartesia" not in env
    assert "MINIMAX_API_KEY=" in env
    assert "VOICE_FALLBACK=text" in env
    assert "VOICE_REQUIRE_APPROVAL=true" in env
