import inspect
import re
from types import SimpleNamespace
from unittest.mock import Mock

from artpm_agent.ui_style import STYLE_CSS
from artpm_agent.views import chat


def _hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))


def _linear_channel(channel):
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def _contrast(foreground, background):
    fg = _hex_rgb(foreground)
    bg = _hex_rgb(background)
    fg_l = sum(
        weight * _linear_channel(channel)
        for weight, channel in zip((0.2126, 0.7152, 0.0722), fg)
    )
    bg_l = sum(
        weight * _linear_channel(channel)
        for weight, channel in zip((0.2126, 0.7152, 0.0722), bg)
    )
    return (max(fg_l, bg_l) + 0.05) / (min(fg_l, bg_l) + 0.05)


def _token(name):
    match = re.search(
        rf"{re.escape(name)}\s*:\s*(#[0-9a-fA-F]{{6}}|transparent)\s*;", STYLE_CSS
    )
    assert match is not None, f"missing design token: {name}"
    return match.group(1).lower()


def _selector_block(selector):
    pattern = rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\}}"
    match = re.search(pattern, STYLE_CSS, flags=re.DOTALL)
    assert match is not None, f"missing CSS selector: {selector}"
    return match.group("body")


def test_design_tokens_are_defined_and_motion_is_purposeful():
    defined = set(re.findall(r"(--pm-[a-z0-9-]+)\s*:", STYLE_CSS))
    used = set(re.findall(r"var\((--pm-[a-z0-9-]+)", STYLE_CSS))

    assert used <= defined
    assert "transition: all" not in STYLE_CSS
    assert "linear-gradient" not in STYLE_CSS
    assert "radial-gradient" not in STYLE_CSS
    assert "pm-fade-in" not in STYLE_CSS
    assert "pulse-dot" not in STYLE_CSS
    assert _contrast(_token("--pm-accent"), _token("--pm-paper")) >= 4.5
    assert _contrast(_token("--pm-muted"), _token("--pm-canvas")) >= 4.5
    assert _contrast(_token("--pm-focus"), _token("--pm-paper")) >= 3.0


def test_chat_theme_uses_neutral_semantic_surfaces():
    assert (
        _contrast(
            _token("--pm-chat-user-fg"),
            _token("--pm-chat-user-bg"),
        )
        >= 4.5
    )
    assert (
        _contrast(
            _token("--pm-chat-assistant-fg"),
            _token("--pm-paper"),
        )
        >= 4.5
    )
    assert _token("--pm-chat-assistant-bg") == "transparent"

    user_block = _selector_block('[data-testid="stChatMessage"]:has(.chat-role-user)')
    assert "justify-content: flex-end" in user_block
    assistant_content = (
        '[data-testid="stChatMessage"]:has(.chat-role-assistant)\n'
        '    [data-testid="stChatMessageContent"]'
    )
    assistant_block = _selector_block(assistant_content)
    assert "background: var(--pm-chat-assistant-bg)" in assistant_block
    assert "box-shadow: none" in assistant_block
    assert "border: 0" in assistant_block


def test_sidebar_and_error_callback_use_compact_workbench_geometry():
    assert "--pm-sidebar-width:      288px" in STYLE_CSS
    assert "--pm-sidebar-bg:   #f7f8fa" in STYLE_CSS
    assert "max-width: 620px !important" in STYLE_CSS
    assert ".pm-error-summary" in STYLE_CSS
    assert ".st-key-sidebar_footer" in STYLE_CSS
    assert ".brand-tagline" in STYLE_CSS
    assert "flex: 0 0 auto !important" in STYLE_CSS
    assert "opacity: 0" in STYLE_CSS


def test_welcome_suggestion_persists_user_message_before_processing(monkeypatch):
    session_state = {"active_conversation_id": "conversation-1", "messages": []}
    fake_st = SimpleNamespace(session_state=session_state)
    store = Mock()
    store.add_message.return_value = {"id": 42}
    store.get_conversation.return_value = {"id": "conversation-1", "title": "新对话"}
    monkeypatch.setattr(chat, "st", fake_st)
    monkeypatch.setattr(chat, "get_conversation_store", lambda: store)
    monkeypatch.setattr(chat, "load_active_messages", Mock())

    chat._queue_suggested_prompt("分析当前项目")

    store.add_message.assert_called_once()
    assert session_state["pending_prompt"]["user_message_id"] == 42
    assert session_state["pending_prompt"]["conversation_id"] == "conversation-1"


def test_feedback_success_requires_a_persisted_feedback_id():
    assert chat._feedback_was_saved({"feedback_id": 7}) is True
    assert chat._feedback_was_saved({"feedback_id": None}) is False
    assert chat._feedback_was_saved(None) is False


def test_permission_panel_has_a_mobile_safe_composer_contract():
    assert "--pm-composer-reserve" in STYLE_CSS
    assert "pm-permission-facts" in STYLE_CSS
    assert "pm-permission-params" in STYLE_CSS
    assert "permission_actions_" in STYLE_CSS
    assert "flex-direction: column" in STYLE_CSS
    assert "textarea:disabled" in STYLE_CSS
    disabled_block = _selector_block('[data-testid="stChatInput"] textarea:disabled')
    assert "color: var(--pm-muted)" in disabled_block
    assert _contrast(_token("--pm-muted"), _token("--pm-paper")) >= 4.5


def test_codex_style_composer_has_stable_icon_button_geometry():
    upload = _selector_block(
        '[data-testid="stChatInputFileUploadButton"] button[aria-label="Upload files"]'
    )
    submit = _selector_block('[data-testid="stChatInputSubmitButton"]')
    facts = _selector_block(".pm-permission-facts")

    assert "height: 36px" in upload
    assert "width: 36px" in upload
    assert "height: 36px" in submit
    assert "width: 36px" in submit
    assert "repeat(3, minmax(0, 1fr))" in facts
    assert '[class*="st-key-permission_actions_"]' in STYLE_CSS


def test_voice_callback_is_visible_accessible_and_mobile_safe():
    callback = _selector_block(".pm-voice-callback")
    callback_icon = _selector_block(".pm-voice-callback-icon")

    assert ".st-key-chat_voice_callback" in STYLE_CSS
    assert "bottom: calc(100% + 10px)" in STYLE_CSS
    assert "max-width: min(460px, calc(100vw - 48px))" in callback
    assert "min-height: 44px" in callback
    assert "flex: 0 0 28px" in callback_icon
    assert 'content: "正在录音"' in STYLE_CSS
    assert 'button[aria-label="Stop recording"]' in STYLE_CSS
    assert ".pm-voice-callback--success" in STYLE_CSS
    assert ".pm-voice-callback--warning" in STYLE_CSS
    assert ".pm-voice-callback--danger" in STYLE_CSS


def test_codex_style_composer_uses_native_two_row_chat_input():
    source = inspect.getsource(chat.chat_page)
    assert 'accept_file="multiple"' in source
    assert "accept_audio=voice_input_enabled" in source
    assert "audio_sample_rate=16000" in source
    assert "height=96" in source

    panel = _selector_block(
        '[data-testid="stPopoverBody"]:has(.pm-access-panel-anchor)'
    )
    assert "width: min(460px, calc(100vw - 32px))" in panel
    assert "max-width: calc(100vw - 32px)" in panel
    assert "box-shadow: var(--pm-shadow-lg)" in panel
    assert ".pm-access-panel-title" in STYLE_CSS
    assert ".pm-access-panel-description" in STYLE_CSS
    assert ".pm-access-panel-footnote" in STYLE_CSS

    stable_upload = _selector_block(
        '[data-testid="stChatInputFileUploadButton"] button[aria-label="Upload files"]'
    )
    assert "height: 36px" in stable_upload
    assert "width: 36px" in stable_upload
    stable_mic = _selector_block('[data-testid="stChatInputMicButton"] button')
    assert "height: 36px" in stable_mic
    assert "width: 36px" in stable_mic


def test_composer_reserves_clickable_space_for_the_access_mode_control():
    shell = _selector_block(".st-key-chat_composer_shell")
    access = _selector_block(".st-key-chat_access_popover")
    access_button = _selector_block(".st-key-chat_access_popover button")
    textarea = _selector_block(
        '.st-key-chat_composer_shell [data-testid="stChatInput"] textarea'
    )

    assert "position: relative" in shell
    assert "position: absolute" in access
    assert "left: 96px" in access
    assert "z-index: 6" in access
    assert "height: 36px" in access_button
    assert "white-space: nowrap" in access_button
    assert "padding-left: 16px" in textarea
    assert "padding-right: 16px" in textarea
    assert 'button[aria-expanded="true"]::after' not in STYLE_CSS

    full_access_confirm = _selector_block(
        '[class*="st-key-confirm_full_access_"] button'
    )
    assert "background: var(--pm-ink)" in full_access_confirm
    assert "color: var(--pm-paper)" in full_access_confirm
