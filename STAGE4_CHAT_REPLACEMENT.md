# Phase 3 Stage 4: Chat.py Integration with run_turn()
# 
# This file contains the replacement logic for chat.py line 216-435
# to use run_turn() instead of inline Profile/Knowledge/Artifact handling.

# REPLACE lines 216-435 with:

with progress_context:
    if local_fast:
        # Fast mode: use legacy agent.chat() for now
        response = normalize_agent_response(
            agent.chat(prompt, context=agent_context)
        )
        handled_response = True
    
    # Phase 3 Stage 4: Use run_turn() for unified handler chain
    if not handled_response:
        # Build TurnContext from current request
        turn_ctx = TurnContext(
            turn_id=pending_request["turn_id"],
            conversation_id=request_conversation_id or "",
            user_input=prompt,
            attachments=attachments,
            agent_profile=agent_context.get("agent_profile"),
            knowledge_context=agent_context.get("knowledge_context", ""),
            conversation_history=agent_context.get("conversation_history", []),
            agent=agent,
            extra={
                **agent_context,
                "attachments": attachments,
                "file_paths": file_paths,
                # These will be computed by agent's methods
                "parsed_files": [],
                "attachment_context": "",
            },
        )

        # Get stores for handlers
        profile_store = get_profile_store()
        knowledge_store = get_knowledge_store()
        artifact_coordinator = get_artifact_coordinator()

        # Execute unified turn
        turn_result = run_turn(
            turn_ctx,
            profile_store=profile_store,
            knowledge_store=knowledge_store,
            artifact_coordinator=artifact_coordinator,
            request_conversation_id=request_conversation_id,
        )

        # Extract results
        response = turn_result.response
        awaiting_approval = turn_result.awaiting_approval
        handled_response = True

        # Handle artifacts if present
        if turn_result.artifacts:
            workflow_metadata = {
                "artifacts": turn_result.artifacts,
            }

    # Knowledge rule extraction (not yet in harness, keep inline)
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

    # Workflow handling (keep existing code)
    if not handled_response:
        # ... existing workflow code ...
        pass

    # Final fallback to agent.chat() if still not handled
    # (This should never happen with run_turn(), but keep for safety)
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
