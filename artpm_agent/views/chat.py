# ruff: noqa: E402,F405 - Streamlit may execute this page as a standalone script
"""聊天页面（从 app.py 拆分）。"""

from collections.abc import Mapping
from html import escape
import sys
import re
import time
from pathlib import Path

# 确保项目根目录在 Python 路径中（页面被 Streamlit 直接作为脚本运行时也能找到包）
# 注意：必须先 resolve(__file__) 为绝对路径再取 parent，否则 __file__ 为相对路径时
# 会多退一层目录（/parent 退到 cwd 而非文件真实父目录），导致找不到 artpm_agent 包。
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from artpm_agent.ui_helpers import *  # noqa: F401,F403
from artpm_agent.utils.document_capabilities import MINERU_SUPPORTED_SUFFIXES

# 通配导入会跳过下划线开头的名称，这里显式补齐被 chat_page 直接调用的内部辅助函数。
from artpm_agent.ui_helpers import (
    _chat_error_message,
    _conversation_title_from_prompt,
    _current_model_id,
    _history_limits,
    _pending_request,
    _render_permission_approvals,
    _render_knowledge_approvals,
    _render_knowledge_ingestion_approvals,
    _render_message_artifacts,
    _render_message_attachments,
    _render_profile_approvals,
    _render_workflow_approvals,
    _response_model_id,
    extract_knowledge_rule,  # For knowledge rule extraction (not yet in harness)
)

from artpm_agent.ui_feedback import build_error_info, render_error_callback
from artpm_agent.ui_feedback import build_attachment_error_info
from artpm_agent.security import (
    ACCESS_MODE_CONTROLLED,
    ACCESS_MODE_FULL,
    normalize_access_mode,
)
from artpm_agent.utils.chat_attachments import DEFAULT_MAX_FILE_SIZE
from artpm_agent.voice import VoiceAudioService, VoiceError

# Phase 1 (记忆激活 · 加深): 聊天页反馈按钮 -> FeedbackStore + Episode
from artpm_agent.harness.memory_retrieval import (
    FEEDBACK_CATEGORIES,
    record_turn_feedback,
)

# 对话页模型选择器
from artpm_agent.views.chat_model_selector import (
    render_model_selector,
    get_current_model,
    apply_model_override_to_agent,
)
from artpm_agent.views.chat_execution import HarnessTurnServices, execute_chat_turn
from artpm_agent.views.chat_feedback import render_turn_feedback as render_feedback_control
from artpm_agent.views.chat_message_rendering import render_completed_message
from artpm_agent.views.chat_turn import queue_suggested_prompt
from artpm_agent.views.chat_welcome import render_welcome_suggestions

_LEGACY_MODEL_RUNTIME_RE = re.compile(
    r"当前配置的生成模型 ID 是 \*\*`(?P<model>[^`]+)`\*\*.*?"
    r"连接状态：\*\*(?P<state>[^*]+)\*\*",
    re.DOTALL,
)
_LEGACY_MODEL_NATURAL_RE = re.compile(
    r"根据当前运行配置，我使用的模型是\s*`?(?P<model>[A-Za-z0-9._:-]+)`?[。.]"
)
_LEGACY_FALLBACK_NOTICE_RE = re.compile(
    r"默认模型 `[^`]+` 暂时不可用，本次临时使用 `(?P<model>[^`]+)` "
    r"生成回答；默认设置未修改。\n\n",
)

_FULL_ACCESS_TTL_SECONDS = 60 * 60
_MAX_PERMISSION_GRANTS = 128
_VOICE_CALLBACK_ACTIVE_TTL_SECONDS = 5 * 60
_VOICE_CALLBACK_TERMINAL_TTL_SECONDS = 45
_VOICE_CALLBACK_STATES = {
    "transcribing": {
        "label": "正在识别语音",
        "icon": "graphic_eq",
        "tone": "active",
        "terminal": False,
    },
    "thinking": {
        "label": "语音已识别，正在生成回复",
        "icon": "neurology",
        "tone": "active",
        "terminal": False,
    },
    "synthesizing": {
        "label": "正在生成语音回复",
        "icon": "graphic_eq",
        "tone": "active",
        "terminal": False,
    },
    "approval": {
        "label": "语音任务等待确认",
        "icon": "shield",
        "tone": "warning",
        "terminal": False,
    },
    "playing": {
        "label": "正在播放语音回复",
        "icon": "volume_up",
        "tone": "success",
        "terminal": True,
    },
    "fallback": {
        "label": "已切换为文字回复",
        "icon": "volume_off",
        "tone": "warning",
        "terminal": True,
    },
    "error": {
        "label": "语音处理未完成",
        "icon": "mic_off",
        "tone": "danger",
        "terminal": True,
    },
}


def _trusted_permission_binding(conversation_id: str | None) -> dict[str, str] | None:
    tenant_context = st.session_state.get("tenant_context")
    if not conversation_id or not isinstance(tenant_context, TenantContext):
        return None
    return {
        "tenant_id": tenant_context.tenant_id,
        "workspace_id": tenant_context.workspace_id,
        "principal_id": tenant_context.principal_id,
        "conversation_id": str(conversation_id),
    }


def _controlled_permission_grant(conversation_id: str | None) -> dict[str, object]:
    return {
        "mode": ACCESS_MODE_CONTROLLED,
        **(_trusted_permission_binding(conversation_id) or {}),
    }


def _conversation_permission_grant(
    conversation_id: str | None,
    *,
    now: float | None = None,
) -> dict[str, object]:
    binding = _trusted_permission_binding(conversation_id)
    if binding is None:
        return _controlled_permission_grant(conversation_id)
    grants = st.session_state.get("conversation_permission_grants", {})
    if not isinstance(grants, dict):
        st.session_state.conversation_permission_grants = {}
        return _controlled_permission_grant(conversation_id)
    entry = grants.get(str(conversation_id))
    if not isinstance(entry, Mapping):
        return _controlled_permission_grant(conversation_id)
    mode = normalize_access_mode(entry.get("mode"))
    expires_at = entry.get("expires_at")
    current_time = time.time() if now is None else float(now)
    binding_matches = all(entry.get(key) == value for key, value in binding.items())
    if (
        mode != ACCESS_MODE_FULL
        or not binding_matches
        or not isinstance(expires_at, (int, float))
        or isinstance(expires_at, bool)
        or float(expires_at) <= current_time
    ):
        grants.pop(str(conversation_id), None)
        return _controlled_permission_grant(conversation_id)
    return dict(entry)


def _set_conversation_permission_mode(
    conversation_id: str,
    mode: str,
    *,
    now: float | None = None,
) -> None:
    normalized = normalize_access_mode(mode)
    grants = st.session_state.get("conversation_permission_grants")
    if not isinstance(grants, dict):
        grants = {}
        st.session_state.conversation_permission_grants = grants
    if normalized == ACCESS_MODE_CONTROLLED:
        grants.pop(str(conversation_id), None)
        return
    binding = _trusted_permission_binding(conversation_id)
    if binding is None or get_permission_store() is None:
        raise RuntimeError("当前会话无法启用完全访问")
    issued_at = time.time() if now is None else float(now)
    grants[str(conversation_id)] = {
        "mode": ACCESS_MODE_FULL,
        **binding,
        "issued_at": issued_at,
        "expires_at": issued_at + _FULL_ACCESS_TTL_SECONDS,
    }
    while len(grants) > _MAX_PERMISSION_GRANTS:
        grants.pop(next(iter(grants)))


def _pending_permission_grant(
    pending_request: Mapping[str, object],
    conversation_id: str | None,
    *,
    now: float | None = None,
) -> dict[str, object]:
    binding = _trusted_permission_binding(conversation_id)
    grant = pending_request.get("permission_grant")
    if binding is None or not isinstance(grant, Mapping):
        return _controlled_permission_grant(conversation_id)
    mode = normalize_access_mode(grant.get("mode"))
    if mode != ACCESS_MODE_FULL:
        return _controlled_permission_grant(conversation_id)
    expires_at = grant.get("expires_at")
    current_time = time.time() if now is None else float(now)
    if (
        not all(grant.get(key) == value for key, value in binding.items())
        or not isinstance(expires_at, (int, float))
        or isinstance(expires_at, bool)
        or float(expires_at) <= current_time
    ):
        return _controlled_permission_grant(conversation_id)
    return dict(grant)


@st.dialog(
    "启用完全访问",
    width="small",
    dismissible=True,
    icon=":material/admin_panel_settings:",
)
def _confirm_full_access_dialog(conversation_id: str) -> None:
    st.warning("完全访问会预授权当前会话中的受信任低/中风险操作。")
    st.markdown("外发、删除、命令执行、高风险、管理员操作和未知插件仍会逐项确认。")
    acknowledged = st.checkbox(
        "我了解该授权仅限当前会话，并会在 1 小时后失效。",
        key=f"ack_full_access_{conversation_id}",
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button(
            "确认启用",
            key=f"confirm_full_access_{conversation_id}",
            type="primary",
            disabled=not acknowledged,
            width="stretch",
        ):
            try:
                _set_conversation_permission_mode(
                    conversation_id,
                    ACCESS_MODE_FULL,
                )
            except Exception as error:
                render_error_callback(
                    build_error_info(
                        error,
                        context={"operation": "permission_mode_upgrade"},
                    ),
                    key="permission_mode_upgrade_error",
                    retry=False,
                )
                return
            st.rerun()
    with cancel_col:
        if st.button(
            "取消",
            key=f"cancel_full_access_{conversation_id}",
            width="stretch",
        ):
            st.rerun()


def _render_chat_access_control(
    conversation_id: str | None,
    *,
    mode_change_disabled: bool,
) -> str:
    grant = _conversation_permission_grant(conversation_id)
    mode = normalize_access_mode(grant.get("mode"))
    label = "完全访问" if mode == ACCESS_MODE_FULL else "按需确认"
    with st.popover(
        label,
        key="chat_access_popover",
        icon=":material/admin_panel_settings:",
        type="tertiary",
        help="设置当前会话的工具访问权限",
        width="content",
    ):
        description = (
            "受信任的低/中风险操作可直接执行；高风险操作仍需确认。"
            if mode == ACCESS_MODE_FULL
            else "读取操作直接执行；修改、外发和高风险操作先确认。"
        )
        st.markdown(
            '<span class="pm-access-panel-anchor" aria-hidden="true"></span>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="pm-access-panel-copy">'
            '<div class="pm-access-panel-title">工具访问</div>'
            f'<div class="pm-access-panel-description">{description}</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        if mode == ACCESS_MODE_FULL:
            if st.button(
                "切换为按需确认",
                key="use_controlled_access",
                icon=":material/verified_user:",
                disabled=mode_change_disabled,
                width="stretch",
            ):
                _set_conversation_permission_mode(
                    str(conversation_id),
                    ACCESS_MODE_CONTROLLED,
                )
                st.rerun()
        else:
            full_access_available = bool(
                conversation_id
                and get_permission_store() is not None
                and _trusted_permission_binding(conversation_id) is not None
            )
            if st.button(
                "启用完全访问",
                key="request_full_access",
                icon=":material/shield:",
                disabled=mode_change_disabled or not full_access_available,
                width="stretch",
            ):
                _confirm_full_access_dialog(str(conversation_id))
            if not full_access_available:
                st.caption("权限服务未就绪，无法提升访问级别。")
        st.markdown(
            '<div class="pm-access-panel-footnote">'
            "已有待确认请求不会因模式切换自动执行。"
            "</div>",
            unsafe_allow_html=True,
        )
    return mode


def _compact_legacy_assistant_copy(content: str) -> str:
    """Render older stored system copy using the current concise wording."""
    text = str(content or "")
    runtime_match = _LEGACY_MODEL_RUNTIME_RE.search(text)
    if runtime_match:
        return (
            f"当前模型：`{runtime_match.group('model')}`。"
            f"状态：{runtime_match.group('state')}。"
        )

    natural_match = _LEGACY_MODEL_NATURAL_RE.search(text)
    if natural_match:
        return f"当前模型：`{natural_match.group('model')}`。"

    return _LEGACY_FALLBACK_NOTICE_RE.sub(
        lambda match: f"已切换备用模型：`{match.group('model')}`。\n\n",
        text,
        count=1,
    )


def _turn_progress_context(prompt: str, model_id: str, *, local_fast: bool):
    """Show meaningful turn status without adding motion to the chat history."""
    if local_fast:
        return nullcontext()

    label = chat_processing_label(prompt, model_id)
    # Streamlit's status container communicates an ongoing operation and its
    # terminal state. Keep the spinner fallback for older Streamlit builds.
    status_factory = getattr(st, "status", None)
    if callable(status_factory):
        try:
            return status_factory(label, expanded=False, type="compact")
        except TypeError:
            return status_factory(label, expanded=False)
    return st.spinner(label)


def _voice_provider_label(provider: object) -> str:
    return {
        "cartesia": "Cartesia",
        "minimax": "MiniMax",
        "local": "本地语音",
    }.get(str(provider or "").strip().casefold(), "")


def _set_voice_callback(
    state: str,
    conversation_id: str | None,
    *,
    turn_id: str | None = None,
    provider: object = None,
    detail: str = "",
    now: float | None = None,
) -> dict[str, object] | None:
    if state not in _VOICE_CALLBACK_STATES:
        raise ValueError(f"unsupported voice callback state: {state}")
    conversation_id = str(conversation_id or "").strip()
    if not conversation_id:
        return None
    callback = {
        "state": state,
        "conversation_id": conversation_id,
        "turn_id": str(turn_id or "").strip(),
        "provider": str(provider or "").strip().casefold(),
        "detail": str(detail or "").strip()[:160],
        "updated_at": time.time() if now is None else float(now),
    }
    st.session_state.voice_callback = callback
    return callback


def _clear_voice_callback(conversation_id: str | None = None) -> None:
    callback = st.session_state.get("voice_callback")
    if not isinstance(callback, Mapping):
        st.session_state.pop("voice_callback", None)
        return
    if conversation_id and callback.get("conversation_id") != conversation_id:
        return
    st.session_state.pop("voice_callback", None)


def _voice_callback_for(
    conversation_id: str | None,
    *,
    now: float | None = None,
) -> dict[str, object] | None:
    callback = st.session_state.get("voice_callback")
    if not isinstance(callback, Mapping):
        return None
    if not conversation_id or callback.get("conversation_id") != conversation_id:
        return None
    state = str(callback.get("state") or "")
    definition = _VOICE_CALLBACK_STATES.get(state)
    if definition is None:
        _clear_voice_callback(conversation_id)
        return None
    try:
        age = (time.time() if now is None else float(now)) - float(
            callback.get("updated_at", 0)
        )
    except (TypeError, ValueError):
        _clear_voice_callback(conversation_id)
        return None
    ttl = (
        _VOICE_CALLBACK_TERMINAL_TTL_SECONDS
        if definition["terminal"]
        else _VOICE_CALLBACK_ACTIVE_TTL_SECONDS
    )
    if age < 0 or age > ttl:
        _clear_voice_callback(conversation_id)
        return None
    return dict(callback)


def _render_voice_callback(
    conversation_id: str | None,
    *,
    target=None,
) -> None:
    callback = _voice_callback_for(conversation_id)
    target = target or st
    if callback is None:
        empty = getattr(target, "empty", None)
        if callable(empty):
            empty()
        return
    definition = _VOICE_CALLBACK_STATES[str(callback["state"])]
    detail = str(callback.get("detail") or "").strip()
    provider = _voice_provider_label(callback.get("provider"))
    secondary = " · ".join(part for part in (provider, detail) if part)
    target.markdown(
        (
            f'<div class="pm-voice-callback pm-voice-callback--{definition["tone"]}" '
            'role="status" aria-live="polite" aria-atomic="true">'
            f'<span class="material-symbols-rounded pm-voice-callback-icon" '
            f'aria-hidden="true">{escape(str(definition["icon"]))}</span>'
            '<span class="pm-voice-callback-copy">'
            f'<span class="pm-voice-callback-label">{escape(str(definition["label"]))}</span>'
            + (
                f'<span class="pm-voice-callback-detail">{escape(secondary)}</span>'
                if secondary
                else ""
            )
            + "</span></div>"
        ),
        unsafe_allow_html=True,
    )


def _voice_audio_service() -> VoiceAudioService:
    service = st.session_state.get("voice_audio_service")
    if not isinstance(service, VoiceAudioService):
        service = VoiceAudioService()
        st.session_state.voice_audio_service = service
    return service


def _voice_recording_ready() -> bool:
    """Expose the microphone only when recording can complete successfully."""

    try:
        status = _voice_audio_service().status()
    except (VoiceError, OSError, RuntimeError, TypeError, ValueError):
        return False
    return isinstance(status, Mapping) and bool(status.get("recording_ready"))


def _recording_bytes(recording) -> bytes:
    if recording is None:
        return b""
    getvalue = getattr(recording, "getvalue", None)
    if callable(getvalue):
        return bytes(getvalue())
    read = getattr(recording, "read", None)
    if callable(read):
        seek = getattr(recording, "seek", None)
        if callable(seek):
            seek(0)
        return bytes(read())
    raise TypeError("Recorded audio must be a file-like object")


def _voice_error_info(error: Exception) -> dict[str, object]:
    message = str(error).casefold()
    if "not enabled" in message:
        title = "语音输入尚未启用，文字对话仍可正常使用。"
        suggestions = ["启用语音配置后重启服务", "继续输入文字"]
    elif "not configured" in message or "required" in message:
        title = "语音服务尚未配置完成，文字对话仍可正常使用。"
        suggestions = ["检查 Cartesia 语音配置", "继续输入文字"]
    elif "no speech" in message or "too short" in message:
        title = "没有识别到清晰语音，请靠近麦克风后重试。"
        suggestions = ["重新录音", "改用文字输入"]
    else:
        title = "语音处理暂时不可用，已保留文字对话入口。"
        suggestions = ["稍后重新录音", "改用文字输入"]
    return {
        "message": title,
        "suggestions": suggestions,
        "severity": "warning",
        "error_id": f"voice-{uuid4().hex[:8]}",
    }


def _cache_voice_reply(turn_id: str, response: str) -> str | None:
    try:
        audio = _voice_audio_service().synthesize(response)
    except VoiceError as error:
        logger.info("语音回复降级为文字: %s", type(error).__name__)
        st.toast("语音播放暂时不可用，已保留文字回答。", icon=":material/volume_off:")
        return None
    cache = st.session_state.setdefault("voice_reply_audio", {})
    if not isinstance(cache, dict):
        cache = {}
        st.session_state.voice_reply_audio = cache
    cache[str(turn_id)] = {
        "data": audio.data,
        "mime_type": audio.mime_type,
        "sample_rate": audio.sample_rate,
        "provider": audio.provider,
        "played": False,
    }
    while len(cache) > 8:
        cache.pop(next(iter(cache)))
    return audio.provider


def _render_voice_reply(message: Mapping, fallback_key: object) -> None:
    turn_id = str(message.get("turn_id") or fallback_key)
    cache = st.session_state.get("voice_reply_audio")
    if not isinstance(cache, dict):
        return
    audio = cache.get(turn_id)
    if not isinstance(audio, dict) or not audio.get("data"):
        return
    autoplay = not bool(audio.get("played"))
    st.audio(
        audio["data"],
        format=str(audio.get("mime_type") or "audio/wav"),
        sample_rate=audio.get("sample_rate"),
        autoplay=autoplay,
        width="stretch",
    )
    audio["played"] = True


def chat_page():
    """对话页面"""
    st.markdown('<div class="chat-page-marker"></div>', unsafe_allow_html=True)

    # 智能编辑模式：上传/选择表格或文档，用一句话修改，预览后生成新版本
    if st.session_state.get("edit_mode"):
        _render_edit_mode()
        return

    store = get_conversation_store()
    active_id = st.session_state.get("active_conversation_id")
    active_conversation = (
        store.get_conversation(active_id) if store is not None and active_id else None
    )

    if not st.session_state.messages:
        st.markdown('<div class="chat-empty-marker"></div>', unsafe_allow_html=True)
        st.title("有什么可以帮你？")

        # 快捷建议芯片
        _render_welcome_suggestions()
    else:
        with st.container(key="chat_header"):
            # 标题行：对话标题 + 模型选择器 + 清空按钮
            title_col, model_col, action_col = st.columns(
                [0.5, 0.35, 0.08], vertical_alignment="bottom"
            )

            with title_col:
                conversation_title = (
                    active_conversation["title"]
                    if active_conversation
                    else "ArtPM 助手"
                )
                st.title(conversation_title)

            with model_col:
                # 渲染模型选择器
                switched_model = render_model_selector()
                if switched_model:
                    # 用户切换了模型，应用到 Agent
                    agent = st.session_state.get("agent")
                    apply_model_override_to_agent(agent, switched_model)
                    st.toast(f"✅ 已切换到 {switched_model}（临时，不保存到设置）")
                    # 注意：不需要 st.rerun()，toast 会自动触发页面更新
                    # st.rerun() 会导致页面跳转到其他页面

            with action_col:
                if st.button(
                    "清空",
                    key="clear_chat",
                    icon=":material/delete_outline:",
                    help="清空当前对话",
                    width="content",
                ):
                    st.session_state[f"confirm_clear_chat_{active_id}"] = True

            clear_state_key = f"confirm_clear_chat_{active_id}"
            if st.session_state.get(clear_state_key):
                with st.container(key="clear_chat_confirmation", border=True):
                    st.markdown("**确认清空当前对话？**")
                    st.caption("这会删除消息、附件和运行记录，且无法撤销。")
                    confirm_col, cancel_col = st.columns(2)
                    with confirm_col:
                        confirm_clear = st.button(
                            "确认清空",
                            key=f"confirm_clear_chat_action_{active_id}",
                            type="primary",
                            width="stretch",
                        )
                    with cancel_col:
                        cancel_clear = st.button(
                            "取消",
                            key=f"cancel_clear_chat_action_{active_id}",
                            width="stretch",
                        )
                    if confirm_clear:
                        if store is not None and active_id:
                            store.clear_messages(active_id)
                            session_store = get_session_store()
                            if session_store is not None:
                                session_store.clear_conversation_entries(active_id)
                        workflow_store = get_workflow_store()
                        if workflow_store is not None and active_id:
                            workflow_store.clear_conversation_runs(active_id)
                        attachment_store = get_chat_attachment_store()
                        if attachment_store is not None and active_id:
                            attachment_store.remove_conversation(active_id)
                        st.session_state.messages = []
                        st.session_state.pop("pending_prompt", None)
                        st.session_state.pop(clear_state_key, None)
                        st.rerun()
                    if cancel_clear:
                        st.session_state.pop(clear_state_key, None)
                        st.rerun()

    pending_request = _pending_request(st.session_state.get("pending_prompt"))
    with st.container(key="chat_thread"):
        for index, msg in enumerate(st.session_state.messages):
            role = msg["role"]
            content = msg["content"]
            metadata = msg.get("metadata") or {}
            avatar = ":material/person:" if role == "user" else ":material/neurology:"
            with st.chat_message(role, avatar=avatar):
                st.markdown(
                    f'<span class="chat-role-marker chat-role-{role}"></span>',
                    unsafe_allow_html=True,
                )
                if msg.get("status") == "error":
                    # Rendered below through the structured error callback.
                    retry_prompt = msg.get("retry_prompt") or metadata.get(
                        "retry_prompt"
                    )
                    invalid_attachments = bool(metadata.get("invalid_attachments"))
                    message_id = msg.get("id", index)
                    error_info = metadata.get("error_info")
                    if not isinstance(error_info, dict):
                        error_info = {
                            "message": str(content).split("\n", 1)[0],
                            "suggestions": [
                                "点击重试；如果问题持续，请检查模型连接和配置。"
                            ],
                            "severity": "error",
                            "error_id": "legacy",
                        }
                    retry_action = render_error_callback(
                        error_info,
                        key=f"error_callback_{active_id or 'legacy'}_{message_id}",
                        retry=bool(retry_prompt),
                        retry_label=(
                            "移除附件并重试" if invalid_attachments else "重试"
                        ),
                    )
                    if retry_prompt and retry_action == "retry":
                        if store is not None and msg.get("id"):
                            store.delete_message(msg["id"])
                            user_message = next(
                                (
                                    item
                                    for item in reversed(store.list_messages(active_id))
                                    if item.get("role") == "user"
                                    and item.get("turn_id") == msg.get("turn_id")
                                ),
                                None,
                            )
                            load_active_messages()
                        else:
                            st.session_state.messages.pop(index)
                            user_message = next(
                                (
                                    item
                                    for item in reversed(st.session_state.messages)
                                    if item.get("role") == "user"
                                ),
                                None,
                            )
                        st.session_state.pending_prompt = {
                            "prompt": retry_prompt,
                            "conversation_id": active_id,
                            "user_message_id": (
                                user_message.get("id") if user_message else None
                            ),
                            "turn_id": msg.get("turn_id", uuid4().hex),
                            "attachments": (
                                []
                                if invalid_attachments
                                else metadata.get("attachments", [])
                            ),
                            "permission_grant": _conversation_permission_grant(
                                active_id
                            ),
                        }
                        st.rerun()
                else:
                    # 非错误消息的正常渲染
                    render_completed_message(
                        st,
                        role=role,
                        content=content,
                        message=msg,
                        index=index,
                        compact_assistant_copy=_compact_legacy_assistant_copy,
                        render_attachments=_render_message_attachments,
                        render_artifacts=_render_message_artifacts,
                        render_voice_reply=_render_voice_reply,
                        render_feedback=lambda message, position: _render_turn_feedback(
                            message, position, active_id
                        ),
                    )

        _render_permission_approvals(active_id)
        _render_profile_approvals(active_id)
        _render_knowledge_ingestion_approvals(active_id)
        _render_knowledge_approvals(active_id)
        _render_workflow_approvals(active_id)

        if pending_request:
            prompt = pending_request["prompt"]
            request_conversation_id = (
                pending_request.get("conversation_id") or active_id
            )
            if active_id and request_conversation_id != active_id:
                st.warning("该提问所属会话已切换，已忽略上一条待处理消息。")
                st.session_state.pop("pending_prompt", None)
                st.rerun()
            if AVAILABLE and st.session_state.get("agent"):
                agent = st.session_state.agent
                attachments = pending_request.get("attachments", [])
                with st.chat_message("assistant", avatar=":material/neurology:"):
                    st.markdown(
                        '<span class="chat-role-marker chat-role-assistant"></span>',
                        unsafe_allow_html=True,
                    )
                    try:
                        attachments = select_conversation_attachments(
                            prompt,
                            pending_request.get("attachments", []),
                            st.session_state.messages,
                        )
                        local_fast = not attachments and is_local_fast_intent(prompt)
                        history = []
                        if (
                            not local_fast
                            and store is not None
                            and request_conversation_id
                        ):
                            max_messages, max_chars = _history_limits(agent)
                            history = store.build_context(
                                request_conversation_id,
                                max_messages=max_messages,
                                max_chars=max_chars,
                                before_message_id=pending_request.get(
                                    "user_message_id"
                                ),
                            )
                        # ── 附件解析一次性契约 ──
                        # 这里是对话路径上唯一解析附件落盘路径的位置：把持久化的
                        # attachment 元数据解析为已校验存在的本地文件路径（file_paths）。
                        # 解析结果通过 agent_context["file_paths"] 透传给 harness；
                        # harness.run_turn 仅在 file_paths 为空时才从 attachments 兜底
                        # 重推导，因此正常对话路径下解析只发生一次，不会重复 IO。
                        attachment_store = get_chat_attachment_store()
                        file_paths = (
                            attachment_store.resolve_paths(attachments)
                            if attachment_store is not None and attachments
                            else []
                        )

                        # 获取用户临时选择的模型（如果有）
                        user_selected_model = get_current_model()
                        permission_grant = _pending_permission_grant(
                            pending_request,
                            request_conversation_id,
                        )

                        agent_context = {
                            "conversation_id": request_conversation_id,
                            "turn_id": pending_request["turn_id"],
                            "workspace_id": st.session_state.get(
                                "tenant_context", TenantContext.local()
                            ).workspace_id,
                            "tenant_context": st.session_state.get(
                                "tenant_context", TenantContext.local()
                            ),
                            "conversation_history": history,
                            "attachments": attachments,
                            "file_path": file_paths[0] if file_paths else None,
                            "file_paths": file_paths,
                            "agent_profile": get_current_profile(),
                            # Workspace knowledge is assembled by the Harness
                            # so API and UI follow the same retrieval boundary.
                            "knowledge_context": "",
                            "turn_mode": "fast" if local_fast else "standard",
                            # 用户临时选择的模型（如果有）
                            "permission_store": get_permission_store(),
                            "permission_mode": normalize_access_mode(
                                permission_grant.get("mode")
                            ),
                            "agent_id": "artpm-agent",
                            "preferred_model": user_selected_model
                            if user_selected_model
                            else None,
                        }
                        workflow_metadata = {}
                        awaiting_approval = False
                        response_rendered = False
                        turn_error = False
                        if pending_request.get("voice_input"):
                            voice_metadata = pending_request.get("voice")
                            _set_voice_callback(
                                "thinking",
                                request_conversation_id,
                                turn_id=pending_request["turn_id"],
                                provider=(
                                    voice_metadata.get("provider")
                                    if isinstance(voice_metadata, Mapping)
                                    else None
                                ),
                            )
                        progress_context = _turn_progress_context(
                            prompt,
                            _current_model_id(agent),
                            local_fast=local_fast,
                        )
                        streamed_chunks: list[str] = []
                        stream_slot = None if local_fast else st.empty()

                        def render_stream_chunk(chunk: str) -> None:
                            streamed_chunks.append(chunk)
                            if stream_slot is not None:
                                stream_slot.markdown("".join(streamed_chunks))

                        agent_context["response_stream_callback"] = render_stream_chunk
                        with progress_context:
                            harness_result = None
                            # All turns use the canonical local Harness. Its
                            # fast mode is independently revalidated before it
                            # skips memory, workflows, and skills. The UI only
                            # renders the completed TurnResult; it must not
                            # invoke a second legacy chat/stream path here.
                            (
                                response,
                                awaiting_approval,
                                harness_metadata,
                                harness_result,
                            ) = execute_chat_turn(
                                agent,
                                prompt,
                                pending_request["turn_id"],
                                request_conversation_id,
                                agent_context,
                                attachments,
                                file_paths,
                                HarnessTurnServices(
                                    profile_store=get_profile_store(),
                                    knowledge_store=get_knowledge_store(),
                                    artifact_coordinator=get_artifact_coordinator(),
                                    knowledge_rule_extractor=extract_knowledge_rule,
                                    workflow_coordinator=get_workflow_coordinator(),
                                    workflow_formatter=format_workflow_result,
                                    session_store=get_session_store(),
                                    event_bus=get_event_bus(),
                                    episode_store=get_episode_store(),
                                    consolidation_scheduler=get_consolidation_scheduler(),
                                ),
                            )
                            if (
                                harness_result is not None
                                and not getattr(harness_result, "success", True)
                            ):
                                error_kind = str(
                                    getattr(harness_result, "metadata", {}).get(
                                        "error_kind", ""
                                    )
                                )
                                error_markers = {
                                    "empty_response": "模型服务未返回有效回答",
                                    "timeout": "provider read timed out",
                                    "busy": "ResourceExhausted: Worker local total request limit",
                                }
                                if error_kind in error_markers:
                                    response = _chat_error_message(
                                        RuntimeError(error_markers[error_kind]),
                                        _current_model_id(agent),
                                    )
                            workflow_metadata.update(harness_metadata)
                            response_rendered = bool(
                                getattr(
                                    harness_result,
                                    "response_rendered",
                                    False,
                                )
                            )

                        if not response_rendered:
                            st.markdown(response)
                        _render_message_artifacts(
                            workflow_metadata,
                            pending_request["turn_id"],
                        )
                        if pending_request.get("voice_input"):
                            turn_succeeded = not (
                                harness_result is not None
                                and not getattr(harness_result, "success", True)
                            )
                            if awaiting_approval:
                                _set_voice_callback(
                                    "approval",
                                    request_conversation_id,
                                    turn_id=pending_request["turn_id"],
                                    detail="请在上方确认后继续",
                                )
                            elif response and turn_succeeded:
                                _set_voice_callback(
                                    "synthesizing",
                                    request_conversation_id,
                                    turn_id=pending_request["turn_id"],
                                )
                                voice_provider = _cache_voice_reply(
                                    pending_request["turn_id"],
                                    response,
                                )
                                if voice_provider:
                                    workflow_metadata["voice_output_provider"] = (
                                        voice_provider
                                    )
                                    _set_voice_callback(
                                        "playing",
                                        request_conversation_id,
                                        turn_id=pending_request["turn_id"],
                                        provider=voice_provider,
                                    )
                                else:
                                    _set_voice_callback(
                                        "fallback",
                                        request_conversation_id,
                                        turn_id=pending_request["turn_id"],
                                        detail="文字回复已保留",
                                    )
                            else:
                                _set_voice_callback(
                                    "error",
                                    request_conversation_id,
                                    turn_id=pending_request["turn_id"],
                                    detail="请查看上方提示",
                                )
                        if (
                            store is not None
                            and request_conversation_id
                            and not awaiting_approval
                        ):
                            is_error = bool(
                                turn_error
                                or (
                                    harness_result is not None
                                    and not getattr(harness_result, "success", True)
                                )
                            )
                            store.add_message(
                                request_conversation_id,
                                "assistant",
                                response,
                                turn_id=pending_request["turn_id"],
                                model_id=_response_model_id(agent),
                                status="error" if is_error else "complete",
                                metadata={
                                    **workflow_metadata,
                                    **({"retry_prompt": prompt} if is_error else {}),
                                },
                            )
                            load_active_messages()
                        elif store is None and not awaiting_approval:
                            st.session_state.messages.append(
                                {
                                    "role": "assistant",
                                    "content": response,
                                    "time": format_cn_date(
                                        datetime.now(), include_time=True
                                    ),
                                }
                            )
                    except Exception as error:
                        logger.exception("对话处理失败")
                        if pending_request.get("voice_input"):
                            _set_voice_callback(
                                "error",
                                request_conversation_id,
                                turn_id=pending_request["turn_id"],
                                detail="文字错误提示已保留",
                            )
                        error_message = _chat_error_message(
                            error,
                            _current_model_id(agent),
                        )
                        invalid_attachments = isinstance(error, FileNotFoundError)
                        error_info = (
                            build_attachment_error_info(error)
                            if invalid_attachments
                            else build_error_info(
                                error,
                                context={"model_id": _current_model_id(agent)},
                            )
                        )
                        if not invalid_attachments:
                            error_info["message"] = str(error_message).split("\n", 1)[0]
                        error_metadata = {
                            "retry_prompt": prompt,
                            "attachments": attachments,
                            "error_info": error_info,
                            "invalid_attachments": invalid_attachments,
                        }
                        if store is not None and request_conversation_id:
                            try:
                                store.add_message(
                                    request_conversation_id,
                                    "assistant",
                                    error_message,
                                    status="error",
                                    turn_id=pending_request["turn_id"],
                                    model_id=_current_model_id(agent),
                                    metadata=error_metadata,
                                )
                                load_active_messages()
                            except Exception as persist_error:  # noqa: BLE001
                                logger.exception(
                                    "Failed to persist chat error",
                                    exc_info=persist_error,
                                )
                                st.session_state.messages.append(
                                    {
                                        "role": "assistant",
                                        "content": error_message,
                                        "time": format_cn_date(
                                            datetime.now(), include_time=True
                                        ),
                                        "status": "error",
                                        "retry_prompt": prompt,
                                        "metadata": {
                                            **error_metadata,
                                            "persistence_failed": True,
                                        },
                                    }
                                )
                        else:
                            st.session_state.messages.append(
                                {
                                    "role": "assistant",
                                    "content": error_message,
                                    "time": format_cn_date(
                                        datetime.now(), include_time=True
                                    ),
                                    "status": "error",
                                    "retry_prompt": prompt,
                                    "metadata": error_metadata,
                                }
                            )
            else:
                error_message = "Agent 未初始化，请检查设置中的模型配置并重启服务。"
                with st.chat_message("assistant", avatar=":material/neurology:"):
                    render_error_callback(
                        {
                            "message": error_message,
                            "suggestions": ["检查模型配置", "保存设置后刷新页面"],
                            "severity": "error",
                            "error_id": "agent-not-initialized",
                        },
                        key=f"agent_not_initialized_{pending_request['turn_id']}",
                        retry=False,
                    )
                if store is not None and request_conversation_id:
                    store.add_message(
                        request_conversation_id,
                        "assistant",
                        error_message,
                        status="error",
                        turn_id=pending_request["turn_id"],
                        metadata={
                            "retry_prompt": prompt,
                            "attachments": pending_request.get("attachments", []),
                        },
                    )
                    load_active_messages()
                else:
                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": error_message,
                            "time": format_cn_date(datetime.now(), include_time=True),
                            "status": "error",
                            "retry_prompt": prompt,
                            "metadata": {
                                "attachments": pending_request.get("attachments", [])
                            },
                        }
                    )
            st.session_state.pop("pending_prompt", None)
            st.rerun()

    agent_ready = AVAILABLE and st.session_state.get("agent") is not None
    permission_pending = has_pending_permission_requests(active_id)
    voice_input_enabled = _voice_recording_ready()
    with st.bottom:
        with st.container(key="chat_composer_shell"):
            _render_chat_access_control(
                active_id,
                mode_change_disabled=bool(pending_request) or permission_pending,
            )
            user_submission = st.chat_input(
                (
                    "请先处理上方权限请求（允许一次或拒绝）"
                    if permission_pending
                    else "输入消息或添加附件"
                ),
                key="chat_input",
                max_chars=4000,
                max_upload_size=DEFAULT_MAX_FILE_SIZE // (1024 * 1024),
                accept_file="multiple",
                accept_audio=voice_input_enabled,
                audio_sample_rate=16000,
                file_type=sorted(
                    {
                        "xlsx",
                        "xls",
                        "csv",
                        "json",
                        "txt",
                        "md",
                        "pdf",
                        "docx",
                        "png",
                        "jpg",
                        "jpeg",
                        "webp",
                    }
                    | {suffix.lstrip(".") for suffix in MINERU_SUPPORTED_SUFFIXES}
                ),
                disabled=(
                    not agent_ready or bool(pending_request) or permission_pending
                ),
                height=96,
            )
            prompt_text, uploaded_files = normalize_chat_submission(user_submission)
            recorded_audio = chat_submission_audio(user_submission)
            voice_input = recorded_audio is not None
            if voice_input:
                _set_voice_callback(
                    "transcribing",
                    active_id,
                    detail="正在处理刚才的录音",
                )
            elif prompt_text or uploaded_files:
                _clear_voice_callback(active_id)
            with st.container(key="chat_voice_callback"):
                voice_callback_slot = st.empty()
            _render_voice_callback(active_id, target=voice_callback_slot)

    if voice_input:
        try:
            status_factory = getattr(st, "status", None)
            if callable(status_factory):
                try:
                    progress = status_factory(
                        "正在转写语音", expanded=False, type="compact"
                    )
                except TypeError:
                    progress = status_factory("正在转写语音", expanded=False)
            else:
                progress = st.spinner("正在转写语音")
            with progress:
                transcript = _voice_audio_service().transcribe_wav(
                    _recording_bytes(recorded_audio)
                )
            prompt_text = " ".join(
                part for part in (prompt_text, transcript.text) if part
            ).strip()
            st.session_state.last_voice_transcript = {
                "provider": transcript.provider,
                "language": transcript.language,
            }
            _set_voice_callback(
                "thinking",
                active_id,
                provider=transcript.provider,
            )
            _render_voice_callback(active_id, target=voice_callback_slot)
        except (VoiceError, OSError, TypeError, ValueError) as error:
            _set_voice_callback(
                "error",
                active_id,
                detail="请查看语音错误提示",
            )
            _render_voice_callback(active_id, target=voice_callback_slot)
            error_info = _voice_error_info(error)
            render_error_callback(
                error_info,
                key=f"voice_input_error_{error_info['error_id']}",
                retry=False,
            )
            return
    if prompt_text or uploaded_files:
        attachments = []
        attachment_store = None
        if uploaded_files:
            attachment_store = get_chat_attachment_store()
            if attachment_store is None or not active_id:
                error_id = f"attachment-store-{uuid4().hex[:8]}"
                render_error_callback(
                    {
                        "message": "附件存储未就绪，请刷新页面后重试。",
                        "suggestions": ["刷新页面后重试", "确认当前会话仍然有效"],
                        "severity": "error",
                        "error_id": error_id,
                    },
                    key=f"attachment_store_unavailable_{error_id}",
                    retry=False,
                )
                return
            try:
                attachments = attachment_store.save_files(active_id, uploaded_files)
            except (OSError, TypeError, ValueError) as error:
                logger.warning("保存会话附件失败: %s", error)
                error_info = build_attachment_error_info(
                    error,
                    max_files=attachment_store.max_files,
                    max_file_size_mb=attachment_store.max_file_size // (1024 * 1024),
                    max_total_size_mb=attachment_store.max_total_size // (1024 * 1024),
                )
                render_error_callback(
                    error_info,
                    key=(f"attachment_save_error_{active_id}_{error_info['error_id']}"),
                    retry=False,
                )
                return

        prompt = prompt_text or "请分析这些附件，并给出关键结论和下一步建议。"
        turn_id = uuid4().hex
        message_metadata = {"attachments": attachments} if attachments else {}
        if voice_input:
            voice_metadata = st.session_state.pop("last_voice_transcript", {})
            message_metadata.update(
                {
                    "input_mode": "voice",
                    "voice": voice_metadata,
                }
            )
        if store is not None and active_id:
            try:
                user_message = store.add_message(
                    active_id,
                    "user",
                    prompt,
                    turn_id=turn_id,
                    metadata=message_metadata,
                )
            except Exception as error:  # noqa: BLE001 - compensate saved files
                logger.exception("保存用户消息失败")
                if attachment_store is not None and attachments:
                    try:
                        attachment_store.remove_files(attachments)
                    except Exception:  # noqa: BLE001 - original failure wins
                        logger.exception("回滚未绑定的会话附件失败")
                error_info = build_error_info(
                    error,
                    context={"operation": "message_persistence"},
                )
                render_error_callback(
                    error_info,
                    key=f"message_save_error_{active_id}_{error_info['error_id']}",
                    retry=False,
                )
                return
            if active_conversation and active_conversation["title"] == getattr(
                ConversationStore, "DEFAULT_TITLE", "新对话"
            ):
                new_title = _conversation_title_from_prompt(prompt)
                try:
                    store.rename_conversation(active_id, new_title)
                except Exception:  # noqa: BLE001 - message itself is already durable
                    logger.exception("自动更新会话标题失败")
            load_active_messages()
        else:
            user_message = {
                "role": "user",
                "content": prompt,
                "time": format_cn_date(datetime.now(), include_time=True),
                "turn_id": turn_id,
                "metadata": message_metadata,
            }
            st.session_state.messages.append(user_message)
        st.session_state.pending_prompt = {
            "prompt": prompt,
            "conversation_id": active_id,
            "user_message_id": user_message.get("id"),
            "turn_id": turn_id,
            "attachments": attachments,
            "voice_input": voice_input,
            "voice": voice_metadata if voice_input else {},
            "permission_grant": _conversation_permission_grant(active_id),
        }
        st.rerun()

    if not AVAILABLE:
        render_error_callback(
            {
                "message": "Agent 模块加载失败，请查看服务日志并修复依赖后重试。",
                "suggestions": ["检查依赖安装", "查看服务日志后重启项目"],
                "severity": "error",
                "error_id": "agent-module-unavailable",
            },
            key="agent_module_unavailable",
            retry=False,
        )
    elif not agent_ready:
        render_error_callback(
            {
                "message": "Agent 尚未就绪，请检查模型配置后刷新页面。",
                "suggestions": ["检查模型配置", "保存配置后刷新页面"],
                "severity": "warning",
                "error_id": "agent-not-ready",
            },
            key="agent_not_ready",
            retry=False,
        )


def _preceding_user_prompt(index: int) -> str:
    """Best-effort lookup of the user prompt that preceded message ``index``."""
    msgs = st.session_state.get("messages", [])
    for j in range(index - 1, -1, -1):
        m = msgs[j] if 0 <= j < len(msgs) else None
        if m and m.get("role") == "user":
            return str(m.get("content") or m.get("text") or "")
    return ""


def _feedback_was_saved(result) -> bool:
    return isinstance(result, dict) and bool(result.get("feedback_id"))


def _render_turn_feedback(msg: dict, index: int, active_id) -> None:
    render_feedback_control(
        st,
        msg,
        index,
        messages=st.session_state.get("messages", []),
        tenant_context=(
            st.session_state.get("tenant_context") or TenantContext.local()
        ),
        feedback_store=st.session_state.get("feedback_store"),
        episode_store=st.session_state.get("episode_store"),
        record_feedback=record_turn_feedback,
        categories=tuple(FEEDBACK_CATEGORIES),
    )


def _render_edit_mode():
    from artpm_agent.editing import (
        EditableDocument,
        EditFeedbackStore,
        RuleDistiller,
        distill_from_correction,
        make_llm_callable,
        reflect,
        run_edit_instruction,
    )

    """智能编辑模式：上传表格/文档 -> 一句话指令修改 -> 预览 -> 生成新版本。"""
    import copy
    import tempfile

    feedback_store = EditFeedbackStore()
    gen = get_artifact_generator()
    # LLM 增强钩子：仅当对话模型已配置 API key 时才启用，否则退回纯正则（离线）。
    agent = st.session_state.get("agent")
    llm_client = getattr(agent, "llm_client", None) if agent is not None else None
    llm_callable = make_llm_callable(llm_client)

    # 已学规则蒸馏器（把👎纠正沉淀为免费正则规则，跨会话持久化）。
    if "edit_distiller" not in st.session_state:
        st.session_state.edit_distiller = RuleDistiller()
    distiller = st.session_state.edit_distiller

    # 顶部：标题 + 退出
    head_left, head_right = st.columns([1, 0.12])
    with head_left:
        st.title("智能编辑")
        st.caption("上传 Excel 或 Word，用一句话修改，预览后生成新版本。")
    with head_right:
        if st.button(
            "退出", key="exit_edit_mode", icon=":material/close:", width="stretch"
        ):
            st.session_state.edit_mode = False
            st.rerun()

    # 上传
    uploaded = st.file_uploader(
        "上传要编辑的文件",
        type=["xlsx", "xls", "docx"],
        key="edit_uploader",
    )
    if uploaded is not None:
        if (
            st.session_state.get("edit_doc") is None
            or st.session_state.get("edit_uploaded_name") != uploaded.name
        ):
            suffix = Path(uploaded.name).suffix
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(uploaded.getbuffer())
            tmp.close()
            try:
                st.session_state.edit_doc = EditableDocument.load(tmp.name)
                st.session_state.edit_tmp_path = tmp.name
                st.session_state.edit_uploaded_name = uploaded.name
                st.session_state.edit_undo_stack = []
                st.session_state.edit_last_result = None
                st.session_state.edit_sheet = st.session_state.edit_doc.active_sheet
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                render_error_callback(
                    build_error_info(exc, context={"operation": "document_parse"}),
                    key="document_parse_error",
                    retry=False,
                )

    doc = st.session_state.get("edit_doc")
    if doc is None:
        st.info("请先上传一个 Excel（.xlsx/.xls）或 Word（.docx）文件。")
        return

    st.divider()
    st.markdown(f"**{doc.summary()}**")

    # ---- 可编辑区域（写回 doc） ----
    if doc.kind == "excel":
        sheets = doc.sheet_names()
        if len(sheets) > 1:
            st.session_state.edit_sheet = st.selectbox(
                "工作表",
                sheets,
                index=sheets.index(doc.active_sheet),
                key="edit_sheet_sel",
            )
            doc.active_sheet = st.session_state.edit_sheet
        df = doc.active_df
        edited = st.data_editor(
            df,
            key=f"edit_df_{doc.active_sheet}",
            num_rows="dynamic",
            width="stretch",
            height=min(400, 40 * (len(df) + 2) + 60),
        )
        doc.sheets[doc.active_sheet] = edited
    else:
        for i, block in enumerate(doc.blocks):
            if block.get("type") in ("paragraph", "heading"):
                val = st.text_input(
                    f"区块 {i + 1}（{block.get('type')}）",
                    value=block.get("text", ""),
                    key=f"blk_{i}",
                )
                doc.blocks[i]["text"] = val
            elif block.get("type") == "table":
                st.markdown(f"**区块 {i + 1}（表格）**")
                edited = st.data_editor(
                    block.get("rows", []),
                    key=f"tbl_{i}",
                    num_rows="dynamic",
                    width="stretch",
                )
                doc.blocks[i]["rows"] = edited

    # ---- 一句话指令 ----
    st.divider()
    instruction = st.text_input(
        "一句话指令",
        key="edit_instruction_input",
        placeholder="例如：把角色A的单价改成5000 / 删除第3行 / 在末尾加一段：交付说明",
    )
    apply_col, undo_col, gen_col = st.columns([1, 0.5, 0.8])
    with apply_col:
        if st.button("应用指令", key="edit_apply", type="primary", width="stretch"):
            if instruction.strip():
                st.session_state.edit_undo_stack.append(copy.deepcopy(doc))
                result = run_edit_instruction(
                    doc,
                    instruction,
                    feedback_store=feedback_store,
                    llm_callable=llm_callable,
                    rule_store=distiller,
                )
                st.session_state.edit_last_result = result
                if result["errors"]:
                    st.error("；".join(result["errors"]))
                elif not result["ops"]:
                    st.warning(
                        result["warnings"][0] if result["warnings"] else "未理解指令"
                    )
                st.rerun()
            else:
                st.warning("请输入指令")
    with undo_col:
        if st.button(
            "撤销",
            key="edit_undo",
            width="stretch",
            disabled=not st.session_state.get("edit_undo_stack"),
        ):
            if st.session_state.edit_undo_stack:
                st.session_state.edit_doc = st.session_state.edit_undo_stack.pop()
                st.session_state.edit_last_result = None
                st.rerun()
    with gen_col:
        if st.button("生成新版本", key="edit_generate", width="stretch"):
            if gen is None:
                st.error("工件生成器未就绪，请刷新。")
            else:
                try:
                    fmt = "xlsx" if doc.kind == "excel" else "docx"
                    mime = (
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        if fmt == "xlsx"
                        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )
                    out = gen.store_versioned_bytes(
                        doc.original_name,
                        doc.to_bytes(),
                        fmt,
                        mime,
                        {"source": "smart_edit", "edited_from": doc.original_name},
                    )
                    st.success(f"已生成新版本：{out['name']}（v{out['version']}）")
                    st.download_button(
                        "下载新版本",
                        data=doc.to_bytes(),
                        file_name=out["name"],
                        mime=mime,
                        key="edit_download",
                    )
                except Exception as exc:  # noqa: BLE001
                    render_error_callback(
                        build_error_info(
                            exc, context={"operation": "artifact_generate"}
                        ),
                        key="artifact_generate_error",
                        retry=False,
                    )

    # ---- diff 预览 ----
    last = st.session_state.get("edit_last_result")
    if last and last.get("changes"):
        src = last.get("source", "")
        if "rule" in src:
            st.info("已命中本地学习规则，未调用模型。")
        elif "llm" in src:
            st.info("该指令由模型解析；如果结果不对，可以在下方反馈。")
        with st.expander("本次改动预览", expanded=True):
            for change in last["changes"][:30]:
                st.markdown(f"- {change}")
            if len(last["changes"]) > 30:
                st.caption(f"…共 {len(last['changes'])} 处改动")

    # ---- 反馈闭环 ----
    st.divider()
    fb_col1, fb_col2, fb_col3 = st.columns([1, 1, 1])
    with fb_col1:
        if st.button("这次改得对", key="edit_fb_good", width="stretch"):
            feedback_store.record(
                doc_type=doc.kind,
                instruction=instruction,
                plan=last.get("plan", {"ops": []}) if last else {"ops": []},
                accepted=True,
                doc_name=doc.original_name,
            )
            st.toast("已记录这次正确编辑")
    with fb_col2:
        if st.button("改错了", key="edit_fb_bad", width="stretch"):
            st.session_state.edit_fb_show = True
    with fb_col3:
        if st.button("我手动改了", key="edit_fb_manual", width="stretch"):
            feedback_store.record(
                doc_type=doc.kind,
                instruction=instruction,
                plan={"ops": []},
                accepted=False,
                correction="用户手动修改了文档（未通过指令）",
                doc_name=doc.original_name,
            )
            st.toast("已记录你的手动修改，用于后续学习")

    if st.session_state.get("edit_fb_show"):
        with st.container(border=True):
            st.markdown("**请描述正确做法**（用于优化编辑理解）")
            corr = st.text_area(
                "例如：应该是把『角色A』那行的『单价』列改成 5200，而不是整列替换",
                key="edit_corr",
            )
            if st.button("提交纠正", key="edit_corr_submit"):
                feedback_store.record(
                    doc_type=doc.kind,
                    instruction=instruction,
                    plan=last.get("plan", {"ops": []}) if last else {"ops": []},
                    accepted=False,
                    correction=corr or "（未填写具体说明）",
                    doc_name=doc.original_name,
                )
                st.session_state.edit_fb_show = False
                st.toast("纠正已记录，编辑能力会逐步更懂你")

    # ---- 学习反馈（轻量 RL 报告） ----
    with st.expander("学习反馈 / 编辑能力复盘"):
        learn_col, clear_col = st.columns([1, 0.6])
        with learn_col:
            if st.button(
                "从反馈中学习",
                key="edit_learn",
                width="stretch",
                help="把纠正蒸馏成本地规则，之后同类说法减少模型调用",
            ):
                try:
                    n = distill_from_correction(
                        feedback_store,
                        llm_callable=llm_callable,
                        distiller=distiller,
                        doc=doc,
                    )
                    if n:
                        st.success(f"新学 {n} 条规则，已沉淀为本地规则。")
                    else:
                        st.info("暂无可蒸馏的新规则（纠正样本不足，或已学习完毕）。")
                except Exception as exc:  # noqa: BLE001
                    render_error_callback(
                        build_error_info(
                            exc, context={"operation": "feedback_distill"}
                        ),
                        key="feedback_distill_error",
                        retry=False,
                    )
        with clear_col:
            if st.button("清空已学规则", key="edit_clear_rules", width="stretch"):
                distiller.clear()
                st.toast("已清空已学规则")
                st.rerun()

        rep = reflect(feedback_store, learned_rule_count=distiller.count())
        rate = rep["accept_rate"]
        rate_txt = f"{rate * 100:.0f}%" if rate is not None else "—"
        st.markdown(
            f"样本数：**{rep['total']}** ｜ 接受率：**{rate_txt}** ｜ "
            f"已学免费规则：**{rep['learned_rules']}** 条"
        )
        for s in rep["suggestions"]:
            st.markdown(f"- {s}")

        rules = distiller.all()
        if rules:
            with st.container(border=True):
                st.markdown("**已学规则**")
                for r in rules:
                    if r.get("kind") == "normalize":
                        st.caption(
                            f"🔁 归一化：把「{r.get('from')}」当作「{r.get('to')}」"
                            f"（{r.get('doc_type')}）"
                        )
                    else:
                        st.caption(
                            f"🎯 触发「{r.get('trigger')}」→ {len(r.get('ops', []))} 个操作"
                            f"（{r.get('doc_type')}）"
                        )


def _render_welcome_suggestions():
    """渲染欢迎页快捷建议芯片。"""
    def enter_edit_mode() -> None:
        st.session_state.edit_mode = True
        st.session_state.pop("edit_doc", None)
        st.session_state.pop("edit_last_result", None)

    render_welcome_suggestions(
        st,
        on_prompt=_queue_suggested_prompt,
        on_edit=enter_edit_mode,
    )


def _queue_suggested_prompt(prompt: str) -> None:
    """Persist a welcome suggestion exactly like a typed chat submission."""
    store = get_conversation_store()
    active_id = st.session_state.get("active_conversation_id")
    pending = queue_suggested_prompt(
        prompt,
        store=store,
        active_id=active_id,
        messages=st.session_state.get("messages", []),
        default_title=getattr(ConversationStore, "DEFAULT_TITLE", "新对话"),
        title_from_prompt=_conversation_title_from_prompt,
        load_active_messages=load_active_messages,
    )
    pending["permission_grant"] = _conversation_permission_grant(active_id)
    st.session_state["pending_prompt"] = pending


# P2 compatibility facade: state, feedback and welcome projections live in
# side-effect-free modules.  Existing private imports keep working while the
# Streamlit lifecycle remains in this page module.
from artpm_agent.views import chat_state as _chat_state  # noqa: E402

_compact_legacy_assistant_copy = _chat_state.compact_legacy_assistant_copy
_voice_provider_label = _chat_state.voice_provider_label
_preceding_user_prompt = lambda index: _chat_state.preceding_user_prompt(  # noqa: E731
    st.session_state.get("messages", []), index
)
_feedback_was_saved = _chat_state.feedback_was_saved
