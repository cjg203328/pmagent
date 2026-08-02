from __future__ import annotations

from dataclasses import replace
import asyncio
from io import BytesIO
import re
from types import SimpleNamespace
import wave

import pytest

from artpm_agent.voice import (
    SpeechAudio,
    VoiceAudioService,
    VoiceSessionBroker,
    VoiceSessionContext,
    VoiceProviderError,
    VoiceSettings,
    VoiceUnavailableError,
)
from artpm_agent.voice.bridge import PMAgentVoiceBridge, VoiceTurnOutcome, spoken_chunks
from artpm_agent.voice.service import _validated_wav, _wav_pcm, speakable_text


def _settings(**changes):
    base = VoiceSettings(
        enabled=True,
        livekit_url="wss://voice.example.test",
        livekit_api_key="livekit-key",
        livekit_api_secret="livekit-secret",
        cartesia_api_key="cartesia-secret",
        minimax_api_key="minimax-secret",
    )
    return replace(base, **changes)


def _wav(sample_rate=16000, channels=1, duration_ms=300):
    output = BytesIO()
    sample_count = sample_rate * duration_ms // 1000
    with wave.open(output, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\0\0" * sample_count * channels)
    return output.getvalue()


def test_voice_status_never_exposes_secrets():
    settings = _settings()

    status = settings.public_status()

    rendered = repr(settings) + repr(status)
    assert "livekit-secret" not in rendered
    assert "cartesia-secret" not in rendered
    assert "minimax-secret" not in rendered
    assert status["stt"]["model"] == "ink-whisper"
    assert status["stt"]["language"] == "zh"


def test_session_broker_binds_server_owned_identity_and_short_ttl():
    captured = {}

    def issue(context, room_name, ttl):
        captured.update(context=context, room_name=room_name, ttl=ttl)
        return "short-lived-token"

    broker = VoiceSessionBroker(_settings(max_session_seconds=600), token_issuer=issue)
    principal = SimpleNamespace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="alice",
        actor_role="user",
    )

    details = broker.create(principal=principal, conversation_id="conversation-a")

    assert details.participant_token == "short-lived-token"
    assert details.server_url == "wss://voice.example.test"
    assert details.room_name.startswith("artpm-")
    assert captured["ttl"] == 600
    assert captured["context"].workspace_id == "workspace-a"
    assert captured["context"].conversation_id == "conversation-a"
    assert captured["context"].actor_id == "alice"


def test_disabled_session_broker_fails_before_token_issuer():
    calls = []
    broker = VoiceSessionBroker(
        _settings(enabled=False),
        token_issuer=lambda *_args: calls.append(True),
    )

    with pytest.raises(VoiceUnavailableError, match="not enabled"):
        broker.create(
            principal=SimpleNamespace(
                tenant_id="local",
                workspace_id="workspace-a",
                actor_id="alice",
                actor_role="user",
            ),
            conversation_id="conversation-a",
        )

    assert calls == []


def test_voice_session_context_rejects_untrusted_identifiers():
    with pytest.raises(Exception, match="workspace_id"):
        VoiceSessionContext.from_json(
            {
                "session_id": "session-a",
                "tenant_id": "tenant-a",
                "workspace_id": "../other",
                "conversation_id": "conversation-a",
                "actor_id": "alice",
                "actor_role": "user",
            }
        )


def test_audio_service_disabled_does_not_call_provider():
    calls = []
    service = VoiceAudioService(
        _settings(enabled=False),
        stt_factory=lambda _settings: calls.append(True),
    )

    with pytest.raises(VoiceUnavailableError, match="not enabled"):
        service.transcribe_wav(_wav())

    assert calls == []


def test_audio_service_uses_local_tts_after_cloud_providers_are_unavailable():
    service = VoiceAudioService(
        SimpleNamespace(
            enabled=True,
            tts_fallbacks=("local", "text"),
            tts_provider_ready=lambda provider: provider == "local",
        ),
        local_synthesizer=lambda text: SpeechAudio(
            data=text.encode(), mime_type="audio/wav", provider="local"
        ),
    )
    result = service.synthesize("**项目进度正常。**")

    assert result.provider == "local"
    assert result.data.decode() == "项目进度正常。"


def test_wav_normalization_downmixes_and_resamples():
    pcm, sample_rate, channels = _wav_pcm(_wav(48000, channels=2), target_rate=16000)

    assert sample_rate == 16000
    assert channels == 1
    assert 9000 <= len(pcm) <= 10000


def test_local_tts_validation_rejects_header_only_wav():
    with pytest.raises(VoiceProviderError, match="empty audio"):
        _validated_wav(_wav(duration_ms=0))


def test_local_tts_validation_accepts_real_pcm():
    sample_rate, channels = _validated_wav(_wav(duration_ms=100))

    assert sample_rate == 16000
    assert channels == 1


def test_speech_cleanup_and_sentence_chunking():
    text = speakable_text("## 结果\n```python\nsecret()\n```\n**完成。** 下一步继续！")

    assert "secret" not in text
    assert "*" not in text
    assert "结果" in text
    assert "".join(spoken_chunks(text)).replace(" ", "") == re.sub(r"\s+", "", text)


def test_voice_bridge_emits_approval_event_without_approving_it():
    events = []
    context = VoiceSessionContext(
        session_id="session-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        conversation_id="conversation-a",
        actor_id="alice",
        actor_role="user",
    )
    bridge = PMAgentVoiceBridge(_settings(), context, event_sink=events.append)

    async def fake_chat(_message):
        return VoiceTurnOutcome(
            response="需要确认后才能继续。",
            conversation_id="conversation-a",
            turn_id="turn-a",
            awaiting_approval=True,
            permission_request_id="permission-a",
        )

    bridge.chat = fake_chat

    async def collect():
        return [part async for part in bridge.stream_reply("执行操作")]

    chunks = asyncio.run(collect())

    assert chunks == ["需要确认后才能继续。"]
    assert events == [
        {
            "type": "approval.required",
            "conversation_id": "conversation-a",
            "turn_id": "turn-a",
            "generation": 1,
            "permission_request_id": "permission-a",
        }
    ]


def test_voice_bridge_awaits_async_event_sink():
    events = []
    context = VoiceSessionContext(
        session_id="session-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        conversation_id="conversation-a",
        actor_id="alice",
        actor_role="user",
    )

    async def record_event(event):
        await asyncio.sleep(0)
        events.append(event)

    bridge = PMAgentVoiceBridge(_settings(), context, event_sink=record_event)

    async def fake_chat(_message):
        return VoiceTurnOutcome(
            response="已完成。",
            conversation_id="conversation-a",
            turn_id="turn-a",
        )

    bridge.chat = fake_chat

    async def collect():
        return [part async for part in bridge.stream_reply("查询进度")]

    chunks = asyncio.run(collect())

    assert chunks == ["已完成。"]
    assert events == [
        {
            "type": "turn.completed",
            "conversation_id": "conversation-a",
            "turn_id": "turn-a",
            "generation": 1,
        }
    ]
