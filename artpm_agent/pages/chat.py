"""聊天页面（从 app.py 拆分）。"""
from ui_helpers import *  # noqa: F401,F403
# 通配导入会跳过下划线开头的名称，这里显式补齐被 chat_page 直接调用的内部辅助函数。
from ui_helpers import (
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
)

def chat_page():
    """对话页面"""
    st.markdown('<div class="chat-page-marker"></div>', unsafe_allow_html=True)
    store = get_conversation_store()
    active_id = st.session_state.get("active_conversation_id")
    active_conversation = (
        store.get_conversation(active_id) if store is not None and active_id else None
    )

    if not st.session_state.messages:
        st.markdown('<div class="chat-empty-marker"></div>', unsafe_allow_html=True)
        st.title("今天要推进什么？")
    else:
        with st.container(key="chat_header"):
            header_col, action_col = st.columns([1, 0.08], vertical_alignment="bottom")
            with header_col:
                conversation_title = (
                    active_conversation["title"] if active_conversation else "智能助手"
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
                            use_container_width=True,
                        )
                    with cancel_col:
                        cancel_clear = st.button(
                            "取消",
                            key=f"cancel_clear_chat_action_{active_id}",
                            use_container_width=True,
                        )
                    if confirm_clear:
                        if store is not None and active_id:
                            store.clear_messages(active_id)
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
                    st.markdown(content)
                    if role == "user":
                        _render_message_attachments(metadata.get("attachments", []))
                    else:
                        _render_message_artifacts(
                            metadata,
                            msg.get("id", index),
                        )

        _render_profile_approvals(active_id)
        _render_knowledge_ingestion_approvals(active_id)
        _render_knowledge_approvals(active_id)
        _render_workflow_approvals(active_id)

        if pending_request:
            prompt = pending_request["prompt"]
            request_conversation_id = pending_request.get("conversation_id") or active_id
            if active_id and request_conversation_id != active_id:
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
                        attachment_store = get_chat_attachment_store()
                        file_paths = (
                            attachment_store.resolve_paths(attachments)
                            if attachment_store is not None and attachments
                            else []
                        )
                        agent_context = {
                            "conversation_id": request_conversation_id,
                            "workspace_id": ConversationStore.DEFAULT_WORKSPACE_ID,
                            "conversation_history": history,
                            "attachments": attachments,
                            "file_path": file_paths[0] if file_paths else None,
                            "file_paths": file_paths,
                            "agent_profile": get_current_profile(),
                            "knowledge_context": (
                                "" if local_fast else build_knowledge_context(prompt)
                            ),
                        }
                        workflow_result = None
                        workflow_metadata = {}
                        awaiting_approval = False
                        handled_response = False
                        response_rendered = False
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
                            if local_fast:
                                response = normalize_agent_response(
                                    agent.chat(prompt, context=agent_context)
                                )
                                handled_response = True
                            profile_store = None
                            current_profile = None
                            if not handled_response:
                                profile_store = get_profile_store()
                                current_profile = agent_context["agent_profile"]
                            if (
                                not handled_response
                                and profile_store is not None
                                and current_profile is not None
                                and parse_profile_change is not None
                                and request_conversation_id
                            ):
                                try:
                                    profile_patch = parse_profile_change(
                                        prompt,
                                        current_profile,
                                    )
                                    if profile_patch is not None:
                                        preview = format_profile_changes(
                                            current_profile,
                                            profile_patch,
                                        )
                                        profile_store.propose_change(
                                            request_conversation_id,
                                            profile_patch,
                                            turn_id=pending_request["turn_id"],
                                            summary=preview,
                                            idempotency_key=(
                                                f"profile-turn-"
                                                f"{pending_request['turn_id']}"
                                            ),
                                        )
                                        response = (
                                            f"{preview}\n\n"
                                            "请在当前会话中确认后再应用。"
                                        )
                                        awaiting_approval = True
                                        handled_response = True
                                except ProfileChangeParseError as error:
                                    response = f"这个配置变更暂不能应用：{error}"
                                    handled_response = True
                                except Exception:
                                    logger.exception("创建 Agent Profile 提案失败")
                                    response = "配置提案未能保存，请检查服务日志后重试。"
                                    handled_response = True

                            if (
                                not handled_response
                                and request_conversation_id
                                and is_knowledge_ingestion_request(prompt)
                            ):
                                if not attachments:
                                    response = (
                                        "请先在当前消息中附上需要加入资料库的文件。"
                                    )
                                    handled_response = True
                                else:
                                    try:
                                        resources = build_knowledge_ingestion_resources(
                                            agent,
                                            attachments,
                                            file_paths,
                                            prompt,
                                        )
                                        knowledge_store = get_knowledge_store()
                                        if knowledge_store is None:
                                            raise RuntimeError("资料库存储未就绪")
                                        knowledge_store.propose_ingestion(
                                            request_conversation_id,
                                            pending_request["turn_id"],
                                            resources,
                                            (
                                                f"knowledge-turn-"
                                                f"{pending_request['turn_id']}"
                                            ),
                                        )
                                        names = "、".join(
                                            resource["title"] for resource in resources
                                        )
                                        response = (
                                            f"已解析待入库资料：{names}。\n\n"
                                            "当前尚未写入资料库，请确认后再入库。"
                                        )
                                        awaiting_approval = True
                                        handled_response = True
                                    except Exception as error:
                                        logger.exception("创建资料入库提案失败")
                                        response = f"资料暂不能入库：{error}"
                                        handled_response = True

                            if (
                                not handled_response
                                and request_conversation_id
                            ):
                                rule_statement = extract_knowledge_rule(prompt)
                                knowledge_store = get_knowledge_store()
                                if rule_statement and knowledge_store is not None:
                                    try:
                                        knowledge_store.propose_rule(
                                            rule_statement,
                                            proposed_by="agent",
                                            source_conversation_id=(
                                                request_conversation_id
                                            ),
                                            source_message_id=pending_request["turn_id"],
                                            rule_id=(
                                                f"rule-{pending_request['turn_id']}"
                                            ),
                                            metadata={
                                                "origin": "conversation",
                                                "requires_confirmation": True,
                                            },
                                        )
                                        response = (
                                            "我可以把下面这条规则加入工作区知识：\n\n"
                                            f"> {rule_statement}\n\n"
                                            "当前尚未生效，请确认是否采纳。"
                                        )
                                        awaiting_approval = True
                                        handled_response = True
                                    except Exception:
                                        logger.exception("创建知识规则提案失败")
                                        response = (
                                            "知识提案未能保存，请检查服务日志后重试。"
                                        )
                                        handled_response = True

                            if not handled_response:
                                artifact_coordinator = get_artifact_coordinator()
                                if artifact_coordinator is not None:
                                    artifact_outcome = artifact_coordinator.process(
                                        prompt
                                    )
                                    if artifact_outcome.matched:
                                        response = artifact_outcome.message
                                        handled_response = True
                                        if artifact_outcome.artifact is not None:
                                            artifact = artifact_outcome.artifact
                                            workflow_metadata = {
                                                "artifacts": [
                                                    {
                                                        key: artifact[key]
                                                        for key in (
                                                            "id",
                                                            "name",
                                                            "stored_path",
                                                            "format",
                                                            "mime_type",
                                                            "size",
                                                            "sha256",
                                                            "version",
                                                        )
                                                        if key in artifact
                                                    }
                                                ]
                                            }

                            if not handled_response:
                                coordinator = get_workflow_coordinator()
                                outcome = None
                                if coordinator is not None and request_conversation_id:
                                    try:
                                        outcome = coordinator.process(
                                            prompt,
                                            conversation_id=request_conversation_id,
                                            turn_id=pending_request["turn_id"],
                                            agent_context=agent_context,
                                            attachments=attachments,
                                        )
                                    except Exception:
                                        logger.exception(
                                            "工作流匹配或只读执行失败，回退普通对话"
                                        )
                                if outcome is not None and outcome.matched:
                                    workflow_result = outcome.execution
                                    awaiting_approval = (
                                        workflow_result.run.status
                                        == "awaiting_approval"
                                    )
                                    if workflow_result.run.status == "failed":
                                        logger.warning(
                                            "工作流 %s 未完成，回退普通对话: %s",
                                            workflow_result.run.workflow_id,
                                            workflow_result.run.error,
                                        )
                                        workflow_result = None
                                    else:
                                        response = normalize_agent_response(
                                            format_workflow_result(
                                                agent,
                                                workflow_result,
                                            )
                                        )
                                        workflow_metadata = {
                                            "workflow_run_id": workflow_result.run.id,
                                            "workflow_id": workflow_result.run.workflow_id,
                                        }
                            if workflow_result is None and not handled_response:
                                if not attachments and callable(
                                    getattr(agent, "stream_chat", None)
                                ):
                                    response = stream_agent_response(
                                        agent,
                                        prompt,
                                        agent_context,
                                    )
                                    response_rendered = True
                                else:
                                    response = normalize_agent_response(
                                        agent.chat(
                                            prompt,
                                            context=agent_context,
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
                            store.add_message(
                                request_conversation_id,
                                "assistant",
                                response,
                                turn_id=pending_request["turn_id"],
                                model_id=_response_model_id(agent),
                                metadata=workflow_metadata,
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
                and active_conversation["title"] == ConversationStore.DEFAULT_TITLE
            ):
                new_title = _conversation_title_from_prompt(prompt)
                store.rename_conversation(active_id, new_title)
                st.session_state.pending_conversation_title_sync = {
                    "conversation_id": active_id,
                    "title": new_title,
                }
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
