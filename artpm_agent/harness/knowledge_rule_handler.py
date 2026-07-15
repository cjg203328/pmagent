"""Explicit Workspace knowledge-rule proposal handling."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .turn_service import TurnContext, TurnResult

logger = logging.getLogger(__name__)


def try_knowledge_rule_proposal(
    ctx: "TurnContext",
    knowledge_store: Optional[Any],
    request_conversation_id: Optional[str],
    rule_extractor: Optional[Callable[[str], Optional[str]]],
) -> Optional["TurnResult"]:
    """Create an approval-gated proposal for an explicit long-term rule."""
    if (
        knowledge_store is None
        or not request_conversation_id
        or not callable(rule_extractor)
    ):
        return None

    statement = rule_extractor(ctx.user_input)
    if not statement:
        return None

    from .turn_service import TurnResult

    try:
        knowledge_store.propose_rule(
            statement,
            proposed_by="agent",
            source_conversation_id=request_conversation_id,
            source_message_id=ctx.turn_id,
            rule_id=f"rule-{ctx.turn_id}",
            metadata={
                "origin": "conversation",
                "requires_confirmation": True,
            },
        )
    except Exception as error:
        logger.exception("创建知识规则提案失败")
        return TurnResult(
            response="知识提案未能保存，请检查服务日志后重试。",
            success=False,
            error=str(error),
            handled_by="knowledge_rule_proposal_error",
        )

    return TurnResult(
        response=(
            "我可以把下面这条规则加入工作区知识：\n\n"
            f"> {statement}\n\n"
            "当前尚未生效，请确认是否采纳。"
        ),
        success=True,
        awaiting_approval=True,
        handled_by="knowledge_rule_proposal",
        metadata={
            "turn_id": ctx.turn_id,
            "conversation_id": request_conversation_id,
            "proposal_type": "knowledge_rule",
        },
    )
