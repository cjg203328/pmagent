"""Focused contracts for the current Streamlit chat view.

The end-to-end Streamlit suite owns page composition and conversation persistence.
These tests keep the view's stateful helpers deterministic and fast.
"""

from __future__ import annotations

from io import BytesIO
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from artpm_agent.security import ACCESS_MODE_CONTROLLED, ACCESS_MODE_FULL
from artpm_agent.tenancy import TenantContext
from artpm_agent.views import chat
from artpm_agent.voice import SpeechAudio, VoiceError


class SessionState(dict):
    """Small attribute-compatible stand-in for Streamlit session state."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name: str, value) -> None:
        self[name] = value


def _fake_streamlit(state: SessionState | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        session_state=state if state is not None else SessionState(),
        audio=Mock(),
        toast=Mock(),
    )


def _tenant_context(
    *,
    tenant_id: str = "tenant-a",
    workspace_id: str = "workspace-a",
    principal_id: str = "alice",
) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=principal_id,
        roles=frozenset({"user"}),
    )


def _full_grant(
    context: TenantContext,
    conversation_id: str,
    *,
    issued_at: float = 100.0,
) -> dict[str, object]:
    return {
        "mode": ACCESS_MODE_FULL,
        "tenant_id": context.tenant_id,
        "workspace_id": context.workspace_id,
        "principal_id": context.principal_id,
        "conversation_id": conversation_id,
        "issued_at": issued_at,
        "expires_at": issued_at + chat._FULL_ACCESS_TTL_SECONDS,
    }


@pytest.mark.parametrize(
    ("recording_ready", "expected"),
    [(True, True), (False, False)],
)
def test_voice_recording_ready_follows_service_capability(
    monkeypatch,
    recording_ready: bool,
    expected: bool,
):
    service = Mock()
    service.status.return_value = {"recording_ready": recording_ready}
    monkeypatch.setattr(chat, "_voice_audio_service", lambda: service)

    assert chat._voice_recording_ready() is expected


def test_voice_recording_ready_hides_fake_entry_when_status_fails(monkeypatch):
    service = Mock()
    service.status.side_effect = VoiceError("voice service unavailable")
    monkeypatch.setattr(chat, "_voice_audio_service", lambda: service)

    assert chat._voice_recording_ready() is False


def test_recording_bytes_supports_none_and_getvalue_without_moving_cursor():
    recording = BytesIO(b"RIFF-recording")
    recording.seek(4)

    assert chat._recording_bytes(None) == b""
    assert chat._recording_bytes(recording) == b"RIFF-recording"
    assert recording.tell() == 4


def test_recording_bytes_rewinds_read_only_stream_before_reading():
    class ReadOnlyRecording:
        def __init__(self) -> None:
            self.position = 7
            self.seek_calls: list[int] = []

        def seek(self, position: int) -> None:
            self.position = position
            self.seek_calls.append(position)

        def read(self) -> bytes:
            return b"audio-from-start" if self.position == 0 else b"truncated"

    recording = ReadOnlyRecording()

    assert chat._recording_bytes(recording) == b"audio-from-start"
    assert recording.seek_calls == [0]


def test_recording_bytes_rejects_non_file_objects():
    with pytest.raises(TypeError, match="file-like object"):
        chat._recording_bytes(object())


@pytest.mark.parametrize(
    ("error_message", "expected_message", "expected_suggestion"),
    [
        (
            "voice channel is not enabled",
            "语音输入尚未启用",
            "启用语音配置后重启服务",
        ),
        (
            "CARTESIA_API_KEY is required",
            "语音服务尚未配置完成",
            "检查 Cartesia 语音配置",
        ),
        (
            "no speech detected",
            "没有识别到清晰语音",
            "重新录音",
        ),
        (
            "recording is too short",
            "没有识别到清晰语音",
            "重新录音",
        ),
        (
            "provider timed out",
            "语音处理暂时不可用",
            "稍后重新录音",
        ),
    ],
)
def test_voice_errors_map_to_actionable_text_fallbacks(
    error_message: str,
    expected_message: str,
    expected_suggestion: str,
):
    info = chat._voice_error_info(VoiceError(error_message))

    assert expected_message in info["message"]
    assert expected_suggestion in info["suggestions"]
    assert info["severity"] == "warning"
    assert re.fullmatch(r"voice-[0-9a-f]{8}", str(info["error_id"]))


def test_cache_voice_reply_stores_provider_audio_for_deferred_render(monkeypatch):
    state = SessionState()
    fake_st = _fake_streamlit(state)
    service = Mock()
    service.synthesize.return_value = SpeechAudio(
        data=b"RIFF-audio",
        mime_type="audio/wav",
        provider="local",
        sample_rate=24_000,
    )
    monkeypatch.setattr(chat, "st", fake_st)
    monkeypatch.setattr(chat, "_voice_audio_service", lambda: service)

    provider = chat._cache_voice_reply("turn-1", "可以开始执行。")

    assert provider == "local"
    service.synthesize.assert_called_once_with("可以开始执行。")
    assert state["voice_reply_audio"]["turn-1"] == {
        "data": b"RIFF-audio",
        "mime_type": "audio/wav",
        "sample_rate": 24_000,
        "provider": "local",
        "played": False,
    }


def test_cache_voice_reply_is_bounded_to_the_eight_newest_turns(monkeypatch):
    existing = {
        f"turn-{index}": {"data": bytes([index]), "played": True} for index in range(8)
    }
    state = SessionState(voice_reply_audio=existing)
    fake_st = _fake_streamlit(state)
    service = Mock()
    service.synthesize.return_value = SpeechAudio(
        data=b"new",
        mime_type="audio/mpeg",
        provider="minimax",
    )
    monkeypatch.setattr(chat, "st", fake_st)
    monkeypatch.setattr(chat, "_voice_audio_service", lambda: service)

    chat._cache_voice_reply("turn-8", "最新回答")

    assert list(state["voice_reply_audio"]) == [
        "turn-1",
        "turn-2",
        "turn-3",
        "turn-4",
        "turn-5",
        "turn-6",
        "turn-7",
        "turn-8",
    ]


def test_cache_voice_reply_degrades_to_text_when_synthesis_fails(monkeypatch):
    state = SessionState()
    fake_st = _fake_streamlit(state)
    service = Mock()
    service.synthesize.side_effect = VoiceError("TTS unavailable")
    monkeypatch.setattr(chat, "st", fake_st)
    monkeypatch.setattr(chat, "_voice_audio_service", lambda: service)

    assert chat._cache_voice_reply("turn-1", "文字回答仍然可用") is None
    assert "voice_reply_audio" not in state
    fake_st.toast.assert_called_once_with(
        "语音播放暂时不可用，已保留文字回答。",
        icon=":material/volume_off:",
    )


def test_render_voice_reply_autoplays_only_once(monkeypatch):
    audio = {
        "data": b"RIFF-audio",
        "mime_type": "audio/wav",
        "sample_rate": 16_000,
        "provider": "cartesia",
        "played": False,
    }
    fake_st = _fake_streamlit(SessionState(voice_reply_audio={"turn-1": audio}))
    monkeypatch.setattr(chat, "st", fake_st)

    chat._render_voice_reply({"turn_id": "turn-1"}, fallback_key="message-1")
    chat._render_voice_reply({"turn_id": "turn-1"}, fallback_key="message-1")

    assert fake_st.audio.call_count == 2
    assert fake_st.audio.call_args_list[0].kwargs == {
        "format": "audio/wav",
        "sample_rate": 16_000,
        "autoplay": True,
        "width": "stretch",
    }
    assert fake_st.audio.call_args_list[1].kwargs["autoplay"] is False
    assert audio["played"] is True


def test_render_voice_reply_uses_fallback_key_and_ignores_missing_audio(monkeypatch):
    fake_st = _fake_streamlit(
        SessionState(
            voice_reply_audio={
                "message-7": {
                    "data": b"MP3",
                    "mime_type": "audio/mpeg",
                    "played": False,
                }
            }
        )
    )
    monkeypatch.setattr(chat, "st", fake_st)

    chat._render_voice_reply({}, fallback_key="message-7")
    chat._render_voice_reply({}, fallback_key="missing")

    fake_st.audio.assert_called_once()
    assert fake_st.audio.call_args.kwargs["format"] == "audio/mpeg"


def test_voice_callback_is_conversation_bound_and_ttl_limited(monkeypatch):
    state = SessionState()
    monkeypatch.setattr(chat, "st", _fake_streamlit(state))

    callback = chat._set_voice_callback(
        "thinking",
        "conversation-a",
        turn_id="turn-a",
        provider="cartesia",
        now=100.0,
    )

    assert callback["turn_id"] == "turn-a"
    assert chat._voice_callback_for("conversation-a", now=101.0)["state"] == "thinking"
    assert chat._voice_callback_for("conversation-b", now=101.0) is None
    assert (
        chat._voice_callback_for(
            "conversation-a",
            now=100.0 + chat._VOICE_CALLBACK_ACTIVE_TTL_SECONDS + 1,
        )
        is None
    )
    assert "voice_callback" not in state


def test_terminal_voice_callback_expires_without_affecting_other_conversations(
    monkeypatch,
):
    state = SessionState()
    monkeypatch.setattr(chat, "st", _fake_streamlit(state))
    chat._set_voice_callback(
        "playing",
        "conversation-a",
        provider="local",
        now=100.0,
    )

    chat._clear_voice_callback("conversation-b")
    assert "voice_callback" in state
    assert (
        chat._voice_callback_for(
            "conversation-a",
            now=100.0 + chat._VOICE_CALLBACK_TERMINAL_TTL_SECONDS + 1,
        )
        is None
    )
    assert "voice_callback" not in state


def test_voice_callback_renderer_is_accessible_and_escapes_detail(monkeypatch):
    state = SessionState()
    monkeypatch.setattr(chat, "st", _fake_streamlit(state))
    chat._set_voice_callback(
        "playing",
        "conversation-a",
        provider="minimax",
        detail='<script>alert("x")</script>',
        now=100.0,
    )
    target = SimpleNamespace(markdown=Mock(), empty=Mock())

    monkeypatch.setattr(chat.time, "time", lambda: 101.0)
    chat._render_voice_callback("conversation-a", target=target)

    target.markdown.assert_called_once()
    rendered = target.markdown.call_args.args[0]
    assert 'role="status"' in rendered
    assert 'aria-live="polite"' in rendered
    assert "MiniMax" in rendered
    assert "正在播放语音回复" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert target.markdown.call_args.kwargs == {"unsafe_allow_html": True}


def test_voice_callback_rejects_unknown_state(monkeypatch):
    monkeypatch.setattr(chat, "st", _fake_streamlit(SessionState()))

    with pytest.raises(ValueError, match="unsupported voice callback state"):
        chat._set_voice_callback("unknown", "conversation-a")


def test_trusted_permission_binding_comes_only_from_tenant_session(monkeypatch):
    context = _tenant_context()
    state = SessionState(tenant_context=context)
    monkeypatch.setattr(chat, "st", _fake_streamlit(state))

    assert chat._trusted_permission_binding("conversation-a") == {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "principal_id": "alice",
        "conversation_id": "conversation-a",
    }

    state["tenant_context"] = {"tenant_id": "forged-client-value"}
    assert chat._trusted_permission_binding("conversation-a") is None
    assert chat._trusted_permission_binding(None) is None


def test_full_access_grant_is_ttl_bound_and_conversation_isolated(monkeypatch):
    context = _tenant_context()
    state = SessionState(
        tenant_context=context,
        conversation_permission_grants={},
    )
    monkeypatch.setattr(chat, "st", _fake_streamlit(state))
    monkeypatch.setattr(chat, "get_permission_store", lambda: object())

    chat._set_conversation_permission_mode(
        "conversation-a",
        ACCESS_MODE_FULL,
        now=100.0,
    )

    grant = state["conversation_permission_grants"]["conversation-a"]
    assert grant == _full_grant(context, "conversation-a")
    assert (
        chat._conversation_permission_grant("conversation-a", now=101.0)["mode"]
        == ACCESS_MODE_FULL
    )
    assert chat._conversation_permission_grant("conversation-b", now=101.0) == {
        "mode": ACCESS_MODE_CONTROLLED,
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "principal_id": "alice",
        "conversation_id": "conversation-b",
    }
    assert "conversation-a" in state["conversation_permission_grants"]


def test_expired_or_rebound_full_access_grants_are_removed(monkeypatch):
    context = _tenant_context()
    valid = _full_grant(context, "conversation-a")
    rebound = _full_grant(context, "conversation-b")
    rebound["workspace_id"] = "workspace-other"
    state = SessionState(
        tenant_context=context,
        conversation_permission_grants={
            "conversation-a": valid,
            "conversation-b": rebound,
        },
    )
    monkeypatch.setattr(chat, "st", _fake_streamlit(state))

    assert (
        chat._conversation_permission_grant(
            "conversation-a",
            now=100.0 + chat._FULL_ACCESS_TTL_SECONDS,
        )["mode"]
        == ACCESS_MODE_CONTROLLED
    )
    assert (
        chat._conversation_permission_grant("conversation-b", now=101.0)["mode"]
        == ACCESS_MODE_CONTROLLED
    )
    assert state["conversation_permission_grants"] == {}


def test_pending_request_cannot_reuse_another_conversations_full_access(monkeypatch):
    context = _tenant_context()
    state = SessionState(tenant_context=context)
    monkeypatch.setattr(chat, "st", _fake_streamlit(state))
    pending = {"permission_grant": _full_grant(context, "conversation-a")}

    assert (
        chat._pending_permission_grant(pending, "conversation-a", now=101.0)["mode"]
        == ACCESS_MODE_FULL
    )
    isolated = chat._pending_permission_grant(pending, "conversation-b", now=101.0)
    assert isolated["mode"] == ACCESS_MODE_CONTROLLED
    assert isolated["conversation_id"] == "conversation-b"


def test_full_access_requires_ready_permission_store(monkeypatch):
    state = SessionState(
        tenant_context=_tenant_context(),
        conversation_permission_grants={},
    )
    monkeypatch.setattr(chat, "st", _fake_streamlit(state))
    monkeypatch.setattr(chat, "get_permission_store", lambda: None)

    with pytest.raises(RuntimeError, match="无法启用完全访问"):
        chat._set_conversation_permission_mode(
            "conversation-a",
            ACCESS_MODE_FULL,
            now=100.0,
        )

    assert state["conversation_permission_grants"] == {}


def test_switching_to_controlled_access_removes_only_current_conversation(monkeypatch):
    context = _tenant_context()
    state = SessionState(
        tenant_context=context,
        conversation_permission_grants={
            "conversation-a": _full_grant(context, "conversation-a"),
            "conversation-b": _full_grant(context, "conversation-b"),
        },
    )
    monkeypatch.setattr(chat, "st", _fake_streamlit(state))

    chat._set_conversation_permission_mode(
        "conversation-a",
        ACCESS_MODE_CONTROLLED,
    )

    assert set(state["conversation_permission_grants"]) == {"conversation-b"}


@pytest.mark.parametrize(
    ("legacy", "current"),
    [
        (
            "根据当前运行配置，我使用的模型是 `deepseek-v4-pro`。",
            "当前模型：`deepseek-v4-pro`。",
        ),
        (
            "默认模型 `primary` 暂时不可用，本次临时使用 `backup` "
            "生成回答；默认设置未修改。\n\n后续内容",
            "已切换备用模型：`backup`。\n\n后续内容",
        ),
        ("普通回答不需要改写", "普通回答不需要改写"),
    ],
)
def test_legacy_model_copy_is_compacted_without_changing_normal_answers(
    legacy: str,
    current: str,
):
    assert chat._compact_legacy_assistant_copy(legacy) == current
