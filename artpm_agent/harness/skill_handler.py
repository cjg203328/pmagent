"""
Skill routing handler for Phase 3 Stage 3.

Routes to deterministic skills based on intent detection.
Migrated from agent.py chat() method skill routing logic (lines 1199-1218).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .turn_service import TurnContext, TurnResult

logger = logging.getLogger(__name__)


def try_skill_routing(
    ctx: "TurnContext",
    has_attachments: bool = False,
) -> Optional["TurnResult"]:
    """
    Route to a deterministic skill if intent matches.

    Args:
        ctx: Turn context with user input and agent reference
        has_attachments: Whether request has attachments (skips intent detection)

    Returns:
        TurnResult if skill was routed and executed, None otherwise

    Logic:
        1. Skip intent detection if attachments present
           (attachment context takes priority)
        2. Call agent._detect_intent() for three-tier routing
           (keyword → embedding → LLM)
        3. If intent matches a registered skill:
           a. Build skill input with conversation history
           b. Extract structured inputs for the skill
           c. Execute skill via router
           d. Format result
        4. If skill execution succeeds, return formatted result
        5. If skill fails or no intent matched, return None

    This preserves the existing three-tier intent routing and skill execution
    logic while moving it into a dedicated handler module.
    """

    if ctx.agent is None:
        return None

    # Skip intent detection if attachments present
    if has_attachments:
        return None

    # Try intent detection (three-tier: keyword → embedding → LLM)
    try:
        intent = ctx.agent._detect_intent(ctx.user_input)
    except Exception as error:
        logger.warning(f"Intent detection failed: {error}")
        return None

    if not intent:
        return None

    if intent not in ctx.agent.router.skills:
        return None

    # Intent matched, try executing the skill
    try:
        # Build skill input with conversation history
        skill_input = ctx.agent._skill_input_with_history(
            ctx.user_input,
            {
                "agent_profile": ctx.agent_profile,
                "knowledge_context": ctx.knowledge_context,
                "conversation_history": ctx.conversation_history,
                **ctx.extra,
            },
        )

        # Extract structured inputs
        inputs = ctx.agent._extract_inputs(
            skill_input,
            intent,
            {
                "agent_profile": ctx.agent_profile,
                **ctx.extra,
            },
        )

        # Execute skill
        result = ctx.agent.router.execute_skill(intent, inputs)

        if not result.get("success"):
            # Skill execution failed, let it fall back to LLM
            logger.warning(
                f"Skill {intent} execution failed, will fallback to model: "
                f"{result.get('error', 'unknown error')}"
            )
            return None

        # Format result
        formatted = ctx.agent._format_skill_result(intent, result)

        if not formatted:
            # Formatting failed, let it fall back to LLM
            logger.warning(f"Skill {intent} result formatting returned empty")
            return None

        # Success!
        from .turn_service import TurnResult

        return TurnResult(
            response=formatted,
            success=True,
            handled_by=f"skill:{intent}",
            metadata={
                "turn_id": ctx.turn_id,
                "conversation_id": ctx.conversation_id,
                "skill_name": intent,
                "skill_success": True,
            },
        )

    except Exception as error:
        logger.warning(
            f"Skill {intent} routing failed, will fallback to model: {error}"
        )
        # Don't return error result, just return None to fall back to LLM
        return None
