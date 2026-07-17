# ruff: noqa: E402,F405 - Streamlit may execute this page as a standalone script
"""聊天页面（从 app.py 拆分）。"""
import sys
import re
from pathlib import Path

# 确保项目根目录在 Python 路径中（页面被 Streamlit 直接作为脚本运行时也能找到包）
# 注意：必须先 resolve(__file__) 为绝对路径再取 parent，否则 __file__ 为相对路径时
# 会多退一层目录（/parent 退到 cwd 而非文件真实父目录），导致找不到 artpm_agent 包。
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from artpm_agent.ui_helpers import *  # noqa: F401,F403
from artpm_agent.memory import SessionStore
from artpm_agent.editing import (
    EditableDocument,
    run_edit_instruction,
    make_llm_callable,
    EditFeedbackStore,
    reflect,
    RuleDistiller,
    distill_from_correction,
)
# 通配导入会跳过下划线开头的名称，这里显式补齐被 chat_page 直接调用的内部辅助函数。
from artpm_agent.ui_helpers import (
    _chat_error_message,
    _conversation_title_from_prompt,
    _current_model_id,
    _history_limits,
    _pending_request,
    _render_knowledge_approvals,
    _render_knowledge_ingestion_approvals,
    _render_message_artifacts,
    _render_message_attachments,
    _render_profile_approvals,
    _render_workflow_approvals,
    _response_model_id,
    extract_knowledge_rule,  # For knowledge rule extraction (not yet in harness)
)
# Phase 3 Stage 4: Import harness integration helper
from artpm_agent.internal.chat_harness_integration import execute_turn_with_harness
# Phase 1 (记忆激活 · 加深): 聊天页反馈按钮 -> FeedbackStore + Episode
from artpm_agent.harness.memory_retrieval import (
    FEEDBACK_CATEGORIES,
    record_turn_feedback,
)

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
            header_col, action_col = st.columns([1, 0.08], vertical_alignment="bottom")
            with header_col:
                conversation_title = (
                    active_conversation["title"] if active_conversation else "ArtPM 助手"
                )
                st.title(conversation_title)
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
                            SessionStore(store).clear_conversation_entries(active_id)
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
                    st.error(content)
                    retry_prompt = msg.get("retry_prompt") or metadata.get("retry_prompt")
                    message_id = msg.get("id", index)
                    if retry_prompt and st.button(
                        "重新生成",
                        key=f"retry_{active_id or 'legacy'}_{message_id}",
                    ):
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
                            "attachments": metadata.get("attachments", []),
                        }
                        st.rerun()
                else:
                    # 非错误消息的正常渲染
                    display_content = (
                        _compact_legacy_assistant_copy(content)
                        if role == "assistant"
                        else content
                    )
                    st.markdown(display_content)
                    if role == "user":
                        _render_message_attachments(metadata.get("attachments", []))
                    else:
                        _render_message_artifacts(
                            metadata,
                            msg.get("id", index),
                        )
                        # Phase 1 (加深): 每条成功的助手消息下方提供
                        # 👍/👎 反馈控件，让 Agent 即时「记住」用户偏好。
                        _render_turn_feedback(msg, index, active_id)

        _render_profile_approvals(active_id)
        _render_knowledge_ingestion_approvals(active_id)
        _render_knowledge_approvals(active_id)
        _render_workflow_approvals(active_id)

        if pending_request:
            prompt = pending_request["prompt"]
            request_conversation_id = pending_request.get("conversation_id") or active_id
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
                        if not local_fast and store is not None and request_conversation_id:
                            max_messages, max_chars = _history_limits(agent)
                            history = store.build_context(
                                request_conversation_id,
                                max_messages=max_messages,
                                max_chars=max_chars,
                                before_message_id=pending_request.get("user_message_id"),
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
                        agent_context = {
                            "conversation_id": request_conversation_id,
                            "turn_id": pending_request["turn_id"],
                            "workspace_id": getattr(
                                ConversationStore, "DEFAULT_WORKSPACE_ID", "local-default"
                            ),
                            "conversation_history": history,
                            "attachments": attachments,
                            "file_path": file_paths[0] if file_paths else None,
                            "file_paths": file_paths,
                            "agent_profile": get_current_profile(),
                            "knowledge_context": (
                                "" if local_fast else build_knowledge_context(prompt)
                            ),
                        }
                        workflow_metadata = {}
                        awaiting_approval = False
                        response_rendered = False
                        turn_error = False
                        progress_context = (
                            nullcontext()
                            if local_fast
                            else st.spinner(
                                chat_processing_label(
                                    prompt,
                                    _current_model_id(agent),
                                )
                            )
                        )
                        with progress_context:
                            harness_result = None
                            # v0.2 单一回合主链：统一 harness（run_turn）为唯一非极速路径；
                            # 原先散落在 chat.py 内的知识规则/工件/工作流/直接模型 fallback
                            # 分支已全部移除，每回合只走一条可预测主链。
                            if local_fast:
                                # Fast mode: bypass harness for performance
                                response = normalize_agent_response(
                                    agent.chat(prompt, context=agent_context)
                                )
                            else:
                                def respond_from_public_agent_api(_turn_ctx):
                                    if not attachments and callable(
                                        getattr(agent, "stream_chat", None)
                                    ):
                                        return (
                                            stream_agent_response(
                                                agent,
                                                prompt,
                                                agent_context,
                                            ),
                                            True,
                                        )
                                    return (
                                        normalize_agent_response(
                                            agent.chat(
                                                prompt,
                                                context=agent_context,
                                            )
                                        ),
                                        False,
                                    )

                                # Use harness for unified turn execution
                                response, awaiting_approval, harness_metadata, harness_result = (
                                    execute_turn_with_harness(
                                        agent,
                                        prompt,
                                        pending_request["turn_id"],
                                        request_conversation_id,
                                        agent_context,
                                        attachments,
                                        file_paths,
                                        get_profile_store(),
                                        get_knowledge_store(),
                                        get_artifact_coordinator(),
                                        knowledge_rule_extractor=extract_knowledge_rule,
                                        workflow_coordinator=get_workflow_coordinator(),
                                        workflow_formatter=format_workflow_result,
                                        response_handler=respond_from_public_agent_api,
                                    )
                                )
                                # Merge harness metadata into workflow_metadata
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
                                    **(
                                        {"retry_prompt": prompt}
                                        if is_error
                                        else {}
                                    ),
                                },
                            )
                            load_active_messages()
                        elif store is None and not awaiting_approval:
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": response,
                                "time": format_cn_date(datetime.now(), include_time=True),
                            })
                    except Exception as error:
                        logger.exception("对话处理失败")
                        error_message = _chat_error_message(
                            error,
                            _current_model_id(agent),
                        )
                        st.error(error_message)
                        if store is not None and request_conversation_id:
                            store.add_message(
                                request_conversation_id,
                                "assistant",
                                error_message,
                                status="error",
                                turn_id=pending_request["turn_id"],
                                model_id=_current_model_id(agent),
                                metadata={
                                    "retry_prompt": prompt,
                                    "attachments": attachments,
                                },
                            )
                            load_active_messages()
                        else:
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": error_message,
                                "time": format_cn_date(datetime.now(), include_time=True),
                                "status": "error",
                                "retry_prompt": prompt,
                                "metadata": {"attachments": attachments},
                            })
            else:
                error_message = "Agent 未初始化，请检查设置中的模型配置并重启服务。"
                with st.chat_message("assistant", avatar=":material/neurology:"):
                    st.error(error_message)
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
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": error_message,
                        "time": format_cn_date(datetime.now(), include_time=True),
                        "status": "error",
                        "retry_prompt": prompt,
                        "metadata": {
                            "attachments": pending_request.get("attachments", [])
                        },
                    })
            st.session_state.pop("pending_prompt", None)
            st.rerun()

    user_submission = st.chat_input(
        "输入消息或添加附件",
        key="chat_input",
        max_chars=4000,
        accept_file="multiple",
        file_type=[
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
        ],
        disabled=not AVAILABLE or bool(pending_request),
    )

    prompt_text, uploaded_files = normalize_chat_submission(user_submission)
    if prompt_text or uploaded_files:
        attachments = []
        if uploaded_files:
            attachment_store = get_chat_attachment_store()
            if attachment_store is None or not active_id:
                st.error("附件存储未就绪，请刷新页面后重试。")
                return
            try:
                attachments = attachment_store.save_files(active_id, uploaded_files)
            except (OSError, TypeError, ValueError) as error:
                logger.warning("保存会话附件失败: %s", error)
                st.error(f"附件未能保存：{error}")
                return

        prompt = prompt_text or "请分析这些附件，并给出关键结论和下一步建议。"
        turn_id = uuid4().hex
        message_metadata = {"attachments": attachments} if attachments else {}
        if store is not None and active_id:
            user_message = store.add_message(
                active_id,
                "user",
                prompt,
                turn_id=turn_id,
                metadata=message_metadata,
            )
            if (
                active_conversation
                and active_conversation["title"]
                == getattr(ConversationStore, "DEFAULT_TITLE", "新对话")
            ):
                new_title = _conversation_title_from_prompt(prompt)
                store.rename_conversation(active_id, new_title)
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
        }
        st.rerun()

    if not AVAILABLE:
        st.error("Agent 模块加载失败，请查看服务日志并修复依赖后重试。")


def _preceding_user_prompt(index: int) -> str:
    """Best-effort lookup of the user prompt that preceded message ``index``."""
    msgs = st.session_state.get("messages", [])
    for j in range(index - 1, -1, -1):
        m = msgs[j] if 0 <= j < len(msgs) else None
        if m and m.get("role") == "user":
            return str(m.get("content") or m.get("text") or "")
    return ""


def _render_turn_feedback(msg: dict, index: int, active_id) -> None:
    """Render a 👍/👎 control under one assistant message.

    Clicking 👍 records an approval; 👎 opens an inline text area so the user
    can describe the problem. The signal is persisted via
    ``record_turn_feedback`` (writes to both ``FeedbackStore`` and the
    ``Episode``), and the UI remembers that this message was already rated.
    Fully best-effort: any failure is swallowed so the chat never breaks.
    """
    message_id = str(msg.get("id", index))
    turn_id = msg.get("turn_id") or message_id

    given = (st.session_state.get("feedback_given") or {}).get(message_id)
    if given:
        st.caption(
            "✅ 已记录你的反馈（%s），我会记住并在下次改进。"
            % ("👍" if given == "up" else "👎")
        )
        return

    user_prompt = _preceding_user_prompt(index)

    up_col, down_col = st.columns([0.1, 0.1])
    with up_col:
        if st.button("👍", key=f"fb_up_{message_id}", help="这个回答有帮助"):
            record_turn_feedback(
                turn_id,
                True,
                user_prompt=user_prompt,
                assistant_content=str(msg.get("content", "")),
            )
            st.session_state.setdefault("feedback_given", {})[message_id] = "up"
            st.toast("👍 已记录，我会延续这个方向")
            st.rerun()
    with down_col:
        if st.button("👎", key=f"fb_down_{message_id}", help="这个回答有问题"):
            st.session_state.setdefault("feedback_pending", {})[message_id] = True
            st.rerun()

    if (st.session_state.get("feedback_pending") or {}).get(message_id):
        with st.container(border=True):
            st.markdown("**这次哪里不好？** 选一个分类，补充描述会变成我的长期偏好。")
            category = st.selectbox(
                "反馈分类",
                options=list(FEEDBACK_CATEGORIES),
                index=len(FEEDBACK_CATEGORIES) - 1,  # default "other"
                format_func=lambda c: {
                    "too_verbose": "太啰嗦",
                    "too_brief": "太简短",
                    "not_direct": "没直接回答",
                    "ignored_context": "忽略上下文",
                    "factual_error": "事实错误",
                    "wrong_format": "格式不对",
                    "wrong_tool": "用错工具",
                    "other": "其他",
                }.get(c, c),
                key=f"fb_cat_{message_id}",
                label_visibility="collapsed",
            )
            reason = st.text_area(
                "可选：具体描述问题",
                key=f"fb_reason_{message_id}",
                placeholder="例如：回答太啰嗦 / 漏掉了报价金额 / 应该用表格而不是文字",
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button(
                    "提交反馈",
                    key=f"fb_submit_{message_id}",
                    type="primary",
                    width="stretch",
                ):
                    record_turn_feedback(
                        turn_id,
                        False,
                        user_prompt=user_prompt,
                        assistant_content=str(msg.get("content", "")),
                        correction=reason,
                        category=category,
                    )
                    st.session_state.setdefault("feedback_given", {})[message_id] = "down"
                    st.session_state.get("feedback_pending", {}).pop(message_id, None)
                    st.toast("👎 已记录，下次我会注意")
                    st.rerun()
            with c2:
                if st.button(
                    "取消", key=f"fb_cancel_{message_id}", width="stretch"
                ):
                    st.session_state.get("feedback_pending", {}).pop(message_id, None)
                    st.rerun()


def _render_edit_mode():
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
        if st.button("退出", key="exit_edit_mode", icon=":material/close:", width="stretch"):
            st.session_state.edit_mode = False
            st.rerun()

    # 上传
    uploaded = st.file_uploader(
        "上传要编辑的文件",
        type=["xlsx", "xls", "docx"],
        key="edit_uploader",
    )
    if uploaded is not None:
        if st.session_state.get("edit_doc") is None or st.session_state.get(
            "edit_uploaded_name"
        ) != uploaded.name:
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
                st.error(f"文件解析失败：{exc}")

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
                "工作表", sheets, index=sheets.index(doc.active_sheet), key="edit_sheet_sel"
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
                    doc, instruction, feedback_store=feedback_store,
                    llm_callable=llm_callable, rule_store=distiller,
                )
                st.session_state.edit_last_result = result
                if result["errors"]:
                    st.error("；".join(result["errors"]))
                elif not result["ops"]:
                    st.warning(result["warnings"][0] if result["warnings"] else "未理解指令")
                st.rerun()
            else:
                st.warning("请输入指令")
    with undo_col:
        if st.button("撤销", key="edit_undo", width="stretch", disabled=not st.session_state.get("edit_undo_stack")):
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
                        doc.original_name, doc.to_bytes(), fmt, mime,
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
                    st.error(f"生成失败：{exc}")

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
                doc_type=doc.kind, instruction=instruction,
                plan=last.get("plan", {"ops": []}) if last else {"ops": []},
                accepted=True, doc_name=doc.original_name,
            )
            st.toast("已记录这次正确编辑")
    with fb_col2:
        if st.button("改错了", key="edit_fb_bad", width="stretch"):
            st.session_state.edit_fb_show = True
    with fb_col3:
        if st.button("我手动改了", key="edit_fb_manual", width="stretch"):
            feedback_store.record(
                doc_type=doc.kind, instruction=instruction,
                plan={"ops": []}, accepted=False,
                correction="用户手动修改了文档（未通过指令）",
                doc_name=doc.original_name,
            )
            st.toast("已记录你的手动修改，用于后续学习")

    if st.session_state.get("edit_fb_show"):
        with st.container(border=True):
            st.markdown("**请描述正确做法**（用于优化编辑理解）")
            corr = st.text_area("例如：应该是把『角色A』那行的『单价』列改成 5200，而不是整列替换", key="edit_corr")
            if st.button("提交纠正", key="edit_corr_submit"):
                feedback_store.record(
                    doc_type=doc.kind, instruction=instruction,
                    plan=last.get("plan", {"ops": []}) if last else {"ops": []},
                    accepted=False, correction=corr or "（未填写具体说明）",
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
                    st.error(f"学习失败：{exc}")
        with clear_col:
            if st.button("清空已学规则", key="edit_clear_rules", width="stretch"):
                distiller.clear()
                st.toast("已清空已学规则")
                st.rerun()

        rep = reflect(feedback_store, learned_rule_count=distiller.count())
        rate = rep["accept_rate"]
        rate_txt = f"{rate*100:.0f}%" if rate is not None else "—"
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
    suggestions = [
        {"icon": ":material/analytics:", "text": "分析利润率", "prompt": "帮我分析当前项目的利润率和成本结构"},
        {"icon": ":material/request_quote:", "text": "创建报价", "prompt": "帮我创建一个新的项目报价，包含客户、报价金额和工期"},
        {"icon": ":material/query_stats:", "text": "项目概览", "prompt": "查看所有项目的整体经营概览和统计数据"},
        {"icon": ":material/rule:", "text": "评估需求", "prompt": "我有一个新的产品需求，帮我评估技术可行性和成本"},
        {"icon": ":material/summarize:", "text": "生成周报", "prompt": "根据近期项目数据，生成一份本周工作总结报告"},
        {"icon": ":material/tune:", "text": "优化流程", "prompt": "根据现有工作流，给出优化项目管理的具体建议"},
    ]

    # 用容器包裹 + CSS 类，让建议按钮区域独立
    st.markdown('<div class="welcome-suggestions">', unsafe_allow_html=True)
    for row_idx in range(0, len(suggestions), 3):
        row = suggestions[row_idx : row_idx + 3]
        cols = st.columns(len(row), gap="small")
        for col_idx, s in enumerate(row):
            with cols[col_idx]:
                if st.button(
                    s["text"],
                    key=f"suggest_{row_idx}_{col_idx}",
                    icon=s["icon"],
                    help=f"点击发送：{s['prompt']}",
                    width="stretch",
                ):
                    st.session_state.pending_prompt = {
                        "prompt": s["prompt"],
                        "conversation_id": st.session_state.get("active_conversation_id"),
                        "user_message_id": None,
                        "turn_id": uuid4().hex,
                        "attachments": [],
                    }
                    st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
