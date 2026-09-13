"""
Helper function to integrate run_turn() into chat.py (Phase 3 Stage 4).

This wraps run_turn() with chat.py-specific context extraction and result handling.
"""

from collections.abc import Callable
import logging
from typing import Any, Dict, List, Optional, Tuple
from artpm_agent.harness import TurnContext, complete_turn_lifecycle, run_turn
from artpm_agent.harness.runtime import LegacyAgentRuntimeAdapter

logger = logging.getLogger(__name__)

# Keep injected long-term context bounded even when a caller does not provide
# an explicit budget. The value is intentionally below the model output limit;
# confirmed facts remain useful while prompt latency and cost stay predictable.
DEFAULT_MEMORY_CONTEXT_MAX_TOKENS = 1200


def resolve_memory_context_token_budget(
    agent: Any,
    explicit: Optional[int] = None,
) -> int:
    """Resolve the per-turn memory budget from an explicit value or config.

    ``None`` means "use runtime configuration"; an explicit ``0`` preserves
    the historical opt-out. Invalid values fail closed to no injected token
    budget rather than making the chat entrypoint fail.
    """
    candidate: Any = explicit
    if candidate is None:
        config = getattr(agent, "config", None)
        getter = getattr(config, "get", None)
        if callable(getter):
            candidate = getter(
                "agent_runtime.memory_context_max_tokens",
                DEFAULT_MEMORY_CONTEXT_MAX_TOKENS,
            )
        else:
            candidate = DEFAULT_MEMORY_CONTEXT_MAX_TOKENS
    if isinstance(candidate, bool):
        return 0
    try:
        value = int(candidate)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    # Prevent a malformed environment/config value from reintroducing an
    # unbounded prompt while still allowing deliberate small test budgets.
    return min(value, 8192)


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
    except Exception as error:  # noqa: BLE001 - telemetry must never crash a turn
        logger.warning(
            "Evolution telemetry recording failed (non-fatal): %s",
            error,
            exc_info=True,
        )
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
    memory_inject_max_tokens: Optional[int] = None,
    session_store: Optional[Any] = None,
    memory_manager: Optional[Any] = None,
    tencentdb_memory: Optional[Any] = None,
    feedback_store: Optional[Any] = None,
    strategy_store: Optional[Any] = None,
    episode_store: Optional[Any] = None,
    reflection_scheduler: Optional[Any] = None,
    meta_memory_store: Optional[Any] = None,
    consolidation_scheduler: Optional[Any] = None,
) -> Tuple[str, bool, Dict[str, Any], Any]:
    """
    Execute a turn using the unified harness (run_turn()).

    This is the Phase 3 Stage 4 integration point for chat.py.

    Args:
        agent: Legacy application agent or a compatible host object
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
    from artpm_agent.runtime.request_services import TurnServiceBundle

    if memory_manager is None:
        memory_manager = getattr(agent, "memory", None)
    if tencentdb_memory is None:
        tencentdb_memory = getattr(agent, "tencentdb_memory", None)
    if feedback_store is None:
        try:
            from artpm_agent.memory.feedback_store import get_default_feedback_store

            feedback_store = get_default_feedback_store()
        except Exception as error:  # noqa: BLE001 - optional memory service
            logger.warning(
                "Default feedback store unavailable (non-fatal): %s",
                error,
                exc_info=True,
            )
            feedback_store = None
    if strategy_store is None:
        try:
            from artpm_agent.evolution.strategy_store import get_default_strategy_store

            strategy_store = get_default_strategy_store()
        except Exception as error:  # noqa: BLE001 - optional evolution service
            logger.warning(
                "Default strategy store unavailable (non-fatal): %s",
                error,
                exc_info=True,
            )
            strategy_store = None
    if episode_store is None:
        try:
            from artpm_agent.harness.outcome_recorder import default_episode_db_path
            from artpm_agent.memory.episode_store import EpisodeStore

            episode_store = EpisodeStore(default_episode_db_path())
        except Exception as error:  # noqa: BLE001 - optional evolution service
            logger.warning(
                "Default episode store unavailable (non-fatal): %s",
                error,
                exc_info=True,
            )
            episode_store = None
    if reflection_scheduler is None:
        try:
            from artpm_agent.evolution.scheduler import get_default_scheduler

            reflection_scheduler = get_default_scheduler()
        except Exception as error:  # noqa: BLE001 - optional evolution service
            logger.warning(
                "Default reflection scheduler unavailable (non-fatal): %s",
                error,
                exc_info=True,
            )
            reflection_scheduler = None
    if meta_memory_store is None:
        try:
            from artpm_agent.evolution.meta_memory import get_default_meta_memory_store

            meta_memory_store = get_default_meta_memory_store()
        except Exception as error:  # noqa: BLE001 - optional evolution service
            logger.warning(
                "Default meta-memory store unavailable (non-fatal): %s",
                error,
                exc_info=True,
            )
            meta_memory_store = None
    if consolidation_scheduler is None:
        try:
            from artpm_agent.memory.consolidation import ConsolidationScheduler

            consolidation_scheduler = ConsolidationScheduler()
        except Exception as error:  # noqa: BLE001 - optional evolution service
            logger.warning(
                "Default consolidation scheduler unavailable (non-fatal): %s",
                error,
                exc_info=True,
            )
            consolidation_scheduler = None

    turn_services = TurnServiceBundle(
        profile_store=profile_store,
        knowledge_store=knowledge_store,
        artifact_coordinator=artifact_coordinator,
        workflow_coordinator=workflow_coordinator,
        workflow_formatter=workflow_formatter,
        permission_store=agent_context.get("permission_store"),
        session_store=session_store,
        memory_manager=memory_manager,
        tencentdb_memory=tencentdb_memory,
        feedback_store=feedback_store,
        strategy_store=strategy_store,
        episode_store=episode_store,
        reflection_scheduler=reflection_scheduler,
        meta_memory_store=meta_memory_store,
        consolidation_scheduler=consolidation_scheduler,
    )
    turn_ctx = TurnContext(
        turn_id=turn_id,
        conversation_id=conversation_id or "",
        user_input=prompt,
        attachments=attachments,
        agent_profile=agent_context.get("agent_profile"),
        knowledge_context=agent_context.get("knowledge_context", ""),
        conversation_history=agent_context.get("conversation_history", []),
        agent=agent,
        runtime=LegacyAgentRuntimeAdapter(agent),
        services=turn_services,
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
        resolved_token_budget = resolve_memory_context_token_budget(
            agent,
            memory_inject_max_tokens,
        )
        if resolved_token_budget > 0:
            turn_ctx.extra["memory_inject_max_tokens"] = resolved_token_budget
        if not auto_activate_memory:
            turn_ctx.extra["disable_memory_injection"] = True

    # Execute unified turn
    turn_result = run_turn(
        turn_ctx,
        services=turn_services,
        profile_store=profile_store,
        knowledge_store=knowledge_store,
        artifact_coordinator=artifact_coordinator,
        request_conversation_id=conversation_id,
        knowledge_rule_extractor=knowledge_rule_extractor,
        workflow_coordinator=workflow_coordinator,
        workflow_formatter=workflow_formatter,
        response_handler=response_handler,
        feedback=user_feedback,
        auto_reflect=auto_reflect,
    )

    # Normal runs finalize inside run_turn(). Keep this compatibility fallback
    # for hosts that replace the runner with a legacy test adapter.
    if not turn_result.metadata.get("lifecycle_managed"):
        complete_turn_lifecycle(
            turn_ctx,
            turn_result,
            services=turn_services,
            feedback=user_feedback,
            auto_reflect=auto_reflect,
        )

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
