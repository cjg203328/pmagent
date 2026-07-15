"""
Model fallback handler for Phase 3 Stage 3.

Handles LLM chat with model failover and OCR transparent degradation.
Migrated from agent.py chat() method (lines 1220-1292).
"""

from __future__ import annotations

import logging
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
        5. Call _chat_with_model_failover() with vision attachments
        6. If model fails AND OCR text exists, return OCR text (transparent degradation)
        7. Otherwise propagate the error

    This preserves the existing model failover and OCR degradation logic
    while moving it into a dedicated handler module.
    """

    from .turn_service import TurnResult

    if ctx.agent is None:
        return TurnResult(
            response="⚠️ Agent not initialized",
            success=False,
            error="Missing agent reference",
            handled_by="model_fallback_error",
        )

    # Check if LLM is configured - safe attribute check
    if not hasattr(ctx.agent, 'llm_client') or ctx.agent.llm_client is None:
        # Offline mode: return parsed file results if available
        if parsed_files:
            responses = []
            for item in parsed_files:
                if item.get("success"):
                    formatted = ctx.agent._format_skill_result(
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
    system_prompt = ctx.agent._build_system_prompt(
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
    visual_semantics_requested = ctx.agent._needs_visual_semantics(ctx.user_input)

    # Try model chat with failover
    try:
        with ctx.agent._vision_attachment_paths(
            parsed_files,
            visual_semantics_requested,
        ) as image_paths:
            gateway = getattr(ctx.agent, "model_gateway", None)
            if gateway is not None:
                # Task-aware model selection + cache + telemetry live in the
                # gateway. Passing task_type=None lets the gateway lazily
                # classify the task (opt-in LLM classifier on the cheap
                # routing-tier model) and pick the right-sized model. When the
                # classifier is disabled it defaults to "chat" — identical to
                # the prior behaviour.
                response_text = gateway.chat_with_failover(
                    model_prompt,
                    system_prompt,
                    history,
                    image_paths=image_paths,
                    task_type=None,
                )
            else:
                response_text = ctx.agent._chat_with_model_failover(
                    model_prompt,
                    system_prompt,
                    history,
                    image_paths=image_paths,
                )

            return TurnResult(
                response=response_text,
                success=True,
                handled_by="model_chat",
                metadata={
                    "turn_id": ctx.turn_id,
                    "conversation_id": ctx.conversation_id,
                    "model": getattr(ctx.agent, "last_response_model", None),
                    "fallback_from": getattr(
                        ctx.agent, "last_model_fallback_from", None
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
