"""
Helper function to integrate run_turn() into chat.py (Phase 3 Stage 4).

This wraps run_turn() with chat.py-specific context extraction and result handling.
"""

from collections.abc import Callable
import logging
from typing import Any, Dict, List, Optional, Tuple
from artpm_agent.harness import TurnContext, run_turn

logger = logging.getLogger(__name__)


def _record_evolution_event(
    stage: str, level: str, message: str, turn_id: Optional[str] = None
) -> None:
    """Best-effort telemetry capture for the evolution loop; never raises.

    Surfaces outcome-record / auto-reflect / auto-consolidate / feedback failures
    to the observability panel instead of letting them vanish into server logs.
    """
    try:
        from artpm_agent.runtime.telemetry import (
            AgentTelemetry,
            default_telemetry_db_path,
        )

        AgentTelemetry(db_path=default_telemetry_db_path()).record_event(
            stage=stage, level=level, message=message, turn_id=turn_id
        )
    except Exception:  # noqa: BLE001 - telemetry must never crash a turn
        pass


def execute_turn_with_harness(
    agent: Any,
    prompt: str,
    turn_id: str,
    conversation_id: Optional[str],
    agent_context: Dict[str, Any],
    attachments: List[Dict[str, Any]],
    file_paths: List[str],
    profile_store: Optional[Any],
    knowledge_store: Optional[Any],
    artifact_coordinator: Optional[Any],
    *,
    knowledge_rule_extractor: Optional[
        Callable[[str], Optional[str]]
    ] = None,
    workflow_coordinator: Optional[Any] = None,
    workflow_formatter: Optional[Callable[[Any, Any], str]] = None,
    response_handler: Optional[Callable[[TurnContext], Any]] = None,
    user_feedback: Optional[str] = None,
    auto_activate_memory: bool = True,
    auto_reflect: bool = True,
    memory_inject_token_budget: int = 0,
    memory_inject_max_tokens: int = 0,
) -> Tuple[str, bool, Dict[str, Any], Any]:
    """
    Execute a turn using the unified harness (run_turn()).

    This is the Phase 3 Stage 4 integration point for chat.py.

    Args:
        agent: ArtPMAgent instance
        prompt: User input text
        turn_id: Unique turn identifier
        conversation_id: Optional conversation ID for approvals
        agent_context: Full context dict (profile, knowledge, history, etc.)
        attachments: List of attachment metadata dicts
        file_paths: List of file paths on disk
        profile_store: ProfileStore instance
        knowledge_store: WorkspaceKnowledgeStore instance
        artifact_coordinator: ArtifactCoordinator instance

    Returns:
        Tuple of (response_text, awaiting_approval, workflow_metadata)

    Example:
        response, awaiting_approval, metadata = execute_turn_with_harness(
            agent,
            prompt,
            turn_id,
            conversation_id,
            agent_context,
            attachments,
            file_paths,
            get_profile_store(),
            get_knowledge_store(),
            get_artifact_coordinator(),
        )
    """

    # Build TurnContext
    turn_ctx = TurnContext(
        turn_id=turn_id,
        conversation_id=conversation_id or "",
        user_input=prompt,
        attachments=attachments,
        agent_profile=agent_context.get("agent_profile"),
        knowledge_context=agent_context.get("knowledge_context", ""),
        conversation_history=agent_context.get("conversation_history", []),
        agent=agent,
        extra={
            # Pass through full context for compatibility
            **agent_context,
            # Handler-specific keys
            "attachments": attachments,
            "file_paths": file_paths,
            # These will be computed by handlers if needed
            "parsed_files": [],
            "attachment_context": "",
        },
    )

    # 记忆注入已统一收敛到 run_turn() 的 Step 0（单一入口、幂等）：
    # 每回合只注入一次，避免 chat 预注入与 run_turn 内注入重复检索。
    # 这里仅把可选预算参数透传给统一入口（run_turn 读取 ctx.extra）。
    if isinstance(turn_ctx.extra, dict):
        if memory_inject_token_budget:
            turn_ctx.extra["memory_inject_max_chars"] = int(memory_inject_token_budget)
        if memory_inject_max_tokens:
            turn_ctx.extra["memory_inject_max_tokens"] = int(memory_inject_max_tokens)

    # Execute unified turn
    turn_result = run_turn(
        turn_ctx,
        profile_store=profile_store,
        knowledge_store=knowledge_store,
        artifact_coordinator=artifact_coordinator,
        request_conversation_id=conversation_id,
        knowledge_rule_extractor=knowledge_rule_extractor,
        workflow_coordinator=workflow_coordinator,
        workflow_formatter=workflow_formatter,
        response_handler=response_handler,
    )

    # 用户即时反馈：写入偏好库（下一回合即生效），并随 Episode 记录供复盘使用。
    if user_feedback:
        try:
            from artpm_agent.memory.feedback_store import (
                get_default_feedback_store,
            )
            _fb = get_default_feedback_store()
            if _fb is not None:
                _kind = (
                    "avoid"
                    if ("👎" in user_feedback or "差" in user_feedback)
                    else "preference"
                )
                _fb.add(
                    kind=_kind,
                    content=user_feedback,
                    scope=turn_result.handled_by or "global",
                )
        except Exception as exc:  # noqa: BLE001 - 反馈写入失败不应中断主流程
            logger.warning("即时反馈写入失败（非致命）: %s", exc, exc_info=True)
            _record_evolution_event("user_feedback", "warning", str(exc))

    # Phase 0 (记忆+进化): 记录回合结果，供后续学习闭环使用（纯增量、非阻塞）
    try:
        from artpm_agent.harness.outcome_recorder import record_outcome
        record_outcome(turn_ctx, turn_result, feedback=user_feedback)
    except Exception as exc:  # noqa: BLE001 - 结果记录失败不应中断主流程
        logger.warning("回合结果记录失败（非致命）: %s", exc, exc_info=True)
        _record_evolution_event("outcome_record", "warning", str(exc))

    # Phase 3 (进化闭环): 按调度器自动复盘，仅自动应用低风险提案（best-effort）。
    if auto_reflect:
        try:
            from artpm_agent.memory.episode_store import EpisodeStore
            from artpm_agent.harness.outcome_recorder import default_episode_db_path
            from artpm_agent.memory.feedback_store import get_default_feedback_store
            from artpm_agent.evolution.strategy_store import (
                get_default_strategy_store,
            )
            from artpm_agent.evolution.scheduler import get_default_scheduler

            _sched = get_default_scheduler()
            if _sched is not None:
                _ep = EpisodeStore(default_episode_db_path())
                _fb = get_default_feedback_store()
                _st = get_default_strategy_store()
                if _ep is not None and _fb is not None and _st is not None:
                    _report = _sched.run_if_due(_ep, _fb, _st)
                    if _report is not None:
                        logger.info("Auto-reflection run:\n%s", _report)
        except Exception as exc:  # noqa: BLE001 - 自动复盘失败不应中断主流程
            logger.warning("自动复盘失败（非致命）: %s", exc, exc_info=True)
            _record_evolution_event("auto_reflect", "warning", str(exc))

    # Phase 2 (知识炼化): 按调度器周期性对知识库做去重/矛盾/衰减（best-effort）。
    if auto_reflect and knowledge_store is not None:
        try:
            from artpm_agent.memory.consolidation import (
                ConsolidationScheduler,
                ConsolidationService,
            )

            _csched = ConsolidationScheduler()
            _svc = ConsolidationService(knowledge_store)
            _creport = _csched.run_if_due(
                _svc,
                workspace_id=getattr(
                    knowledge_store,
                    "DEFAULT_WORKSPACE_ID",
                    "local-default",
                ),
            )
            if _creport is not None:
                logger.info("Auto-consolidation run:\n%s", _creport.as_dict())
        except Exception as exc:  # noqa: BLE001 - 知识炼化失败不应中断主流程
            logger.warning("知识炼化失败（非致命）: %s", exc, exc_info=True)
            _record_evolution_event("auto_consolidate", "warning", str(exc))

    # 闭环心跳（成功运行标记）：让可观测面板「最后运行」反映真实运行时间，
    # 而非仅记录失败。仅成功走完各阶段（均为独立 try/except）才会到达此处。
    _record_evolution_event("loop_run", "info", "回合闭环完成", turn_id=turn_id)

    # Extract response and approval state
    response_text = turn_result.response
    awaiting_approval = turn_result.awaiting_approval

    # Build workflow_metadata for UI rendering
    workflow_metadata = {}
    if turn_result.artifacts:
        workflow_metadata["artifacts"] = turn_result.artifacts

    # Add handler metadata for debugging
    if turn_result.metadata:
        workflow_metadata.update(turn_result.metadata)

    # Surface the full TurnResult so chat.py can honour success/error status
    # (e.g. render retryable errors with status="error" + retry_prompt) rather
    # than always treating a non-empty response string as a success.
    return response_text, awaiting_approval, workflow_metadata, turn_result
