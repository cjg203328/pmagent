"""Persisted deterministic workflow routing for the application harness."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .turn_service import TurnContext, TurnResult

logger = logging.getLogger(__name__)


def try_workflow_routing(
    ctx: "TurnContext",
    workflow_coordinator: Optional[Any],
    request_conversation_id: Optional[str],
    attachments: list[Mapping[str, Any]],
    formatter: Optional[Callable[[Any, Any], str]] = None,
) -> Optional["TurnResult"]:
    """Select and execute a persisted workflow before ordinary skill/model chat."""
    if workflow_coordinator is None or not request_conversation_id:
        return None

    try:
        outcome = workflow_coordinator.process(
            ctx.user_input,
            conversation_id=request_conversation_id,
            turn_id=ctx.turn_id,
            agent_context={
                "agent_profile": ctx.agent_profile,
                "knowledge_context": ctx.knowledge_context,
                "conversation_history": ctx.conversation_history,
                **ctx.extra,
            },
            attachments=attachments,
        )
    except Exception:
        logger.exception("工作流匹配或只读执行失败，回退普通对话")
        return None

    if not getattr(outcome, "matched", False):
        return None

    execution = getattr(outcome, "execution", None)
    if execution is None:
        return None

    run = execution.run
    if run.status == "failed":
        logger.warning(
            "工作流 %s 未完成，回退普通对话: %s",
            run.workflow_id,
            run.error,
        )
        return None

    if formatter is None:
        from artpm_agent.workflows.coordinator import format_workflow_result

        formatter = format_workflow_result

    # Formatters receive the public runtime. It exposes format_skill_result()
    # without leaking the legacy agent's private implementation surface.
    response = formatter(ctx.runtime, execution)
    if not isinstance(response, str) or not response.strip():
        raise ValueError("工作流未返回有效回答")

    from .turn_service import TurnResult

    return TurnResult(
        response=response.strip(),
        success=True,
        awaiting_approval=run.status == "awaiting_approval",
        handled_by=f"workflow:{run.workflow_id}",
        metadata={
            "workflow_run_id": run.id,
            "workflow_id": run.workflow_id,
        },
    )
