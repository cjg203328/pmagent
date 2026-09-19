"""
Model fallback handler for Phase 3 Stage 3.

Handles LLM chat with model failover and OCR transparent degradation.
Migrated from agent.py chat() method (lines 1220-1292).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from .turn_service import TurnContext, TurnResult

logger = logging.getLogger(__name__)


def fallback_to_model(
    ctx: "TurnContext",
    parsed_files: List[Dict[str, Any]],
    attachment_context: str,
) -> "TurnResult":
    """
    Last resort: direct LLM chat with model failover and OCR degradation.

    Args:
        ctx: Turn context with user input and agent reference
        parsed_files: List of parsed attachment results (for OCR degradation)
        attachment_context: Formatted attachment context for model prompt

    Returns:
        TurnResult with LLM response or OCR fallback

    Logic:
        1. Check if LLM is configured
        2. If not, return offline mode message (or parsed file results)
        3. Build system prompt with profile and knowledge context
        4. Build model prompt (add attachment context if present)
        5. Call runtime.chat_with_failover() with vision attachments
        6. If model fails AND OCR text exists, return OCR text (transparent degradation)
        7. Otherwise propagate the error

    This preserves the existing model failover and OCR degradation logic
    while moving it into a dedicated handler module.
    """

    from .turn_service import TurnResult

    runtime = ctx.runtime
    if runtime is None:
        return TurnResult(
            response="⚠️ Agent not initialized",
            success=False,
            error="Missing agent reference",
            handled_by="model_fallback_error",
        )

    # Check if LLM is configured - safe attribute check
    if not runtime.model_available:
        # Offline mode: return parsed file results if available
        if parsed_files:
            responses = []
            for item in parsed_files:
                if item.get("success"):
                    formatted = runtime.format_skill_result(
                        "document_classifier_parser",
                        {
                            "success": True,
                            "document_type": item.get("document_type", "未知"),
                            "extracted_data": item.get("extracted_data", {}),
                            "raw_text": item.get("raw_text", ""),
                        },
                    )
                    if formatted:
                        responses.append(formatted)

            if responses:
                return TurnResult(
                    response="\n\n".join(responses),
                    success=True,
                    handled_by="offline_file_parsing",
                    metadata={
                        "turn_id": ctx.turn_id,
                        "mode": "offline",
                        "file_count": len(responses),
                    },
                )

        # No LLM and no files: return offline mode help
        return TurnResult(
            response=(
                "模型未配置。请在设置中填写 API Key 后重试；离线功能仍可处理报价、"
                "任务、进度和文档解析。"
            ),
            success=True,
            handled_by="offline_mode_help",
            metadata={
                "turn_id": ctx.turn_id,
                "mode": "offline",
            },
        )

    # LLM is configured, proceed with model chat
    profile = ctx.agent_profile

    # Build system prompt
    system_prompt = runtime.build_system_prompt(
        profile,
        ctx.knowledge_context,
    )

    # Build history
    history = ctx.conversation_history
    if not history:
        history = ctx.extra.get("history", [])

    # Build model prompt (add attachment context if present)
    model_prompt = ctx.user_input
    if attachment_context:
        model_prompt = (
            f"{ctx.user_input}\n\n{attachment_context}\n"
            "请严格基于附件数据回答当前问题；信息不足时指出缺失项。"
        )

    # Determine if visual semantics needed
    visual_semantics_requested = runtime.needs_visual_semantics(ctx.user_input)

    # Try model chat with failover
    try:
        with runtime.vision_attachment_paths(
            parsed_files,
            visual_semantics_requested,
        ) as image_paths:
            chat_kwargs: Dict[str, Any] = {"image_paths": image_paths}
            if getattr(runtime, "supports_response_cache_scope", False):
                from artpm_agent.providers.response_cache import (
                    response_cache_namespace,
                )

                chat_kwargs["cache_scope"] = response_cache_namespace(ctx.extra)

            model_tool_method = getattr(runtime, "run_model_tool_turn", None)
            if callable(model_tool_method):
                tool_context = dict(ctx.extra)
                tool_context["conversation_id"] = ctx.conversation_id
                tool_context["agent_profile"] = ctx.agent_profile
                tool_context["knowledge_context"] = ctx.knowledge_context
                tenant_context = tool_context.get("tenant_context")
                scope = getattr(ctx, "scope", None)
                tenant_id = str(
                    getattr(scope, "tenant_id", "")
                    or getattr(tenant_context, "tenant_id", "")
                )
                workspace_id = str(
                    getattr(scope, "workspace_id", "")
                    or getattr(tenant_context, "workspace_id", "")
                )
                tool_context["tenant_id"] = tenant_id
                tool_context["workspace_id"] = workspace_id
                if isinstance(ctx.agent_profile, Mapping):
                    profile_id = ctx.agent_profile.get("profile_id")
                else:
                    profile_id = getattr(ctx.agent_profile, "profile_id", None)
                if profile_id is not None and str(profile_id).strip():
                    tool_context["profile_id"] = str(profile_id).strip()
                services = getattr(ctx, "services", None)
                session_store = getattr(services, "session_store", None)
                model_tool_result = model_tool_method(
                    model_prompt,
                    conversation_id=ctx.conversation_id,
                    workspace_id=workspace_id,
                    history=history,
                    context=tool_context,
                    session_store=session_store,
                )
                if model_tool_result is not None:
                    response_text, tool_metadata = model_tool_result
                    return TurnResult(
                        response=response_text,
                        success=True,
                        handled_by="model_tool_loop",
                        metadata={
                            "turn_id": ctx.turn_id,
                            "conversation_id": ctx.conversation_id,
                            "model": tool_metadata.get("model")
                            or getattr(runtime, "last_response_model", None),
                            "fallback_from": getattr(
                                runtime,
                                "last_model_fallback_from",
                                None,
                            ),
                            "structured_tools": True,
                        },
                    )

            # A host may provide a renderer for incremental model deltas. The
            # stream still enters through the canonical model handler and the
            # provider gateway; it never calls the legacy ``stream_chat`` or
            # ``chat`` facade a second time. The callback is intentionally an
            # ephemeral turn extra and is never persisted in result metadata.
            stream_callback = ctx.extra.get("response_stream_callback")
            stream_method = getattr(runtime, "stream_with_failover", None)
            if callable(stream_callback) and callable(stream_method):
                streamed: list[str] = []
                try:
                    source = stream_method(
                        model_prompt,
                        system_prompt,
                        history,
                        context=ctx.extra,
                        **chat_kwargs,
                    )
                    try:
                        for chunk in source:
                            if not isinstance(chunk, str) or not chunk:
                                continue
                            streamed.append(chunk)
                            stream_callback(chunk)
                    finally:
                        close = getattr(source, "close", None)
                        if callable(close):
                            close()
                    response_text = "".join(streamed)
                    if not response_text.strip():
                        raise RuntimeError("妯″瀷鏈嶅姟鏈繑鍥炴湁鏁堝洖绛?")
                    return TurnResult(
                        response=response_text,
                        success=True,
                        handled_by="model_chat",
                        response_rendered=True,
                        metadata={
                            "turn_id": ctx.turn_id,
                            "conversation_id": ctx.conversation_id,
                            "model": getattr(runtime, "last_response_model", None),
                            "fallback_from": getattr(
                                runtime, "last_model_fallback_from", None
                            ),
                            "streamed": True,
                        },
                    )
                except Exception as stream_error:
                    # Once a provider has yielded text, retrying through the
                    # non-streaming path would execute the model twice. Keep
                    # the visible partial answer and surface a structured
                    # failure instead.
                    if streamed:
                        logger.warning("Model stream ended after partial output")
                        return TurnResult(
                            response="".join(streamed),
                            success=False,
                            error=str(stream_error) or "model stream failed",
                            handled_by="model_stream_error",
                            response_rendered=True,
                            metadata={"turn_id": ctx.turn_id, "streamed": True},
                        )
                    raise
            response_text = runtime.chat_with_failover(
                model_prompt,
                system_prompt,
                history,
                **chat_kwargs,
            )

            return TurnResult(
                response=response_text,
                success=True,
                handled_by="model_chat",
                metadata={
                    "turn_id": ctx.turn_id,
                    "conversation_id": ctx.conversation_id,
                    "model": getattr(runtime, "last_response_model", None),
                    "fallback_from": getattr(
                        runtime, "last_model_fallback_from", None
                    ),
                },
            )

    except Exception as error:
        logger.warning("Model request failed, checking for OCR degradation")

        # OCR transparent degradation: if OCR succeeded but model failed,
        # return the OCR text instead of failing completely
        ocr_texts = [
            str(item.get("raw_text", "")).strip()
            for item in parsed_files
            if item.get("raw_text")
        ]

        if ocr_texts:
            # OCR has already produced a bounded, local result. Return it as
            # a transparent degradation instead of losing the user's answer
            # when the separate generation model times out.
            return TurnResult(
                response=(
                    "生成模型暂时不可用，已保留附件提取结果。以下内容可继续分析：\n\n"
                    + "\n\n---\n\n".join(ocr_texts)
                ),
                success=True,  # Degraded success
                handled_by="ocr_degradation",
                metadata={
                    "turn_id": ctx.turn_id,
                    "degraded": True,
                    "ocr_count": len(ocr_texts),
                },
            )

        # No OCR fallback available, propagate the error
        if "服务繁忙" in str(error):
            return TurnResult(
                response="模型当前服务繁忙，请稍后重试",
                success=False,
                error="Service busy",
                handled_by="model_busy",
            )

        return TurnResult(
            response="模型请求失败",
            success=False,
            error=str(error),
            handled_by="model_error",
        )
