"""
Turn service - Core orchestration logic for Phase 3.

Provides run_turn() as a single entry point that sequences:
1. Profile proposal detection
2. Knowledge ingestion detection
3. Artifact matching
4. Skill routing
5. Model fallback

This keeps agent.py and pages/chat.py thin by centralizing turn logic.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .profile_handler import try_profile_proposal
from .knowledge_handler import try_knowledge_ingestion
from .knowledge_rule_handler import try_knowledge_rule_proposal
from .artifact_handler import try_artifact_generation
from .workflow_handler import try_workflow_routing
from .skill_handler import try_skill_routing
from .model_handler import fallback_to_model

logger = logging.getLogger(__name__)


@dataclass
class TurnContext:
    """Complete context for a single conversational turn.

    This is the input boundary for run_turn(). All state needed to process
    a user request flows through this immutable snapshot rather than being
    scattered across agent.py private methods and streamlit session state.
    """

    turn_id: str
    conversation_id: str
    user_input: str
    attachments: List[Any] = field(default_factory=list)
    agent_profile: Optional[Any] = None
    knowledge_context: str = ""
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)

    # Reference to the agent instance for skill execution and model calls.
    # Phase 4 will remove this and make the harness independent of ArtPMAgent.
    agent: Any = None

    # Additional context (file_paths, project_id, etc.)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    """Result of processing one turn.

    This is the output boundary. All information the UI needs to render
    the response (text, artifacts, approval gates, metadata) is returned
    in this structured form rather than mixed with side effects.
    """

    response: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    awaiting_approval: bool = False
    artifacts: List[Dict[str, Any]] = field(default_factory=list)

    # Which handler produced this result (for telemetry and debugging)
    handled_by: Optional[str] = None

    # Structured error info (when success=False)
    error: Optional[str] = None
    success: bool = True
    response_rendered: bool = False


def run_turn(
    ctx: TurnContext,
    *,
    profile_store: Optional[Any] = None,
    knowledge_store: Optional[Any] = None,
    artifact_coordinator: Optional[Any] = None,
    request_conversation_id: Optional[str] = None,
    knowledge_rule_extractor: Optional[
        Callable[[str], Optional[str]]
    ] = None,
    workflow_coordinator: Optional[Any] = None,
    workflow_formatter: Optional[Callable[[Any, Any], str]] = None,
    response_handler: Optional[Callable[[TurnContext], Any]] = None,
) -> TurnResult:
    """
    Main turn orchestration service.

    This is Phase 3 Stage 3: integrate Artifact, Skill, and Model handlers.
    All core turn logic is now in dedicated handler modules.

    Args:
        ctx: Complete turn context including user input, conversation state,
             attachments, and agent reference.
        profile_store: Optional ProfileStore for profile change proposals
        knowledge_store: Optional WorkspaceKnowledgeStore for ingestion proposals
        artifact_coordinator: Optional ArtifactCoordinator for artifact generation
        request_conversation_id: Optional conversation ID for approval proposals

    Returns:
        TurnResult with response text, metadata, and control flags.

    Execution order:
        1. Profile proposal detection (profile_handler.py)
        2. Knowledge ingestion detection (knowledge_handler.py)
        3. Artifact generation (artifact_handler.py)
        4. Skill routing (skill_handler.py)
        5. Model fallback (model_handler.py) - always returns a result

    Design notes:
        - Handlers 1-4 return TurnResult | None
        - None means "not handled, try next handler"
        - Successful TurnResult stops the chain
        - Handler 5 (model fallback) always returns TurnResult (terminal)

    Phase 3 staged migration:
        - Stage 1 (done): empty framework, delegates to agent.chat()
        - Stage 2 (done): Profile and Knowledge handlers integrated
        - Stage 3 (current): Artifact/Skill/Model handlers integrated
        - Stage 4 (next): Switch pages/chat.py to use run_turn()
    """

    if ctx.agent is None:
        return TurnResult(
            response="⚠️ Agent not initialized",
            success=False,
            error="Missing agent reference in TurnContext",
            handled_by="harness_error",
        )

    # Handler 1: Profile change proposal
    profile_result = try_profile_proposal(
        ctx, profile_store, request_conversation_id
    )
    if profile_result is not None:
        return profile_result

    # Handler 2: Knowledge ingestion proposal
    attachments = list(ctx.attachments or ctx.extra.get("attachments", []))
    file_paths = list(ctx.extra.get("file_paths", []))
    if attachments and not file_paths:
        file_paths = [
            str(
                attachment.get("file_path")
                or attachment.get("stored_path")
                or ""
            )
            for attachment in attachments
            if isinstance(attachment, Mapping)
        ]

    knowledge_result = try_knowledge_ingestion(
        ctx, knowledge_store, request_conversation_id, attachments, file_paths
    )
    if knowledge_result is not None:
        return knowledge_result

    # Handler 3: Explicit Workspace knowledge-rule proposal
    knowledge_rule_result = try_knowledge_rule_proposal(
        ctx,
        knowledge_store,
        request_conversation_id,
        knowledge_rule_extractor,
    )
    if knowledge_rule_result is not None:
        return knowledge_rule_result

    # Handler 4: Artifact generation
    artifact_result = try_artifact_generation(
        ctx, artifact_coordinator, request_conversation_id
    )
    if artifact_result is not None:
        return artifact_result

    # Handler 5: Persisted deterministic workflow routing
    workflow_result = try_workflow_routing(
        ctx,
        workflow_coordinator,
        request_conversation_id,
        attachments,
        workflow_formatter,
    )
    if workflow_result is not None:
        return workflow_result

    # Host-level handlers must run for thin agents too (for example artifact
    # generation with a test-double LLM). Only the skill/model layer requires
    # ArtPMAgent's private compatibility API.
    if not _agent_supports_harness(ctx.agent):
        return _run_thin_agent(ctx, response_handler=response_handler)

    # Handler 6: Skill routing
    parsed_files = ctx.extra.get("parsed_files", [])
    attachment_context = ctx.extra.get("attachment_context", "")
    has_attachments = bool(attachments)

    skill_result = try_skill_routing(ctx, has_attachments)
    if skill_result is not None:
        return skill_result

    if response_handler is not None:
        return _run_response_handler(
            ctx,
            response_handler,
            handled_by="model_response",
        )

    # Handler 7: Model fallback (terminal - always returns)
    return fallback_to_model(ctx, parsed_files, attachment_context)


def _agent_supports_harness(agent: Any) -> bool:
    """Duck-type whether the agent exposes the full harness-private API.

    The handler chain (skill/model/etc.) relies on ArtPMAgent internals such
    as ``_detect_intent``, ``router``, ``_build_system_prompt`` and
    ``_chat_with_model_failover``. Minimal agents (test doubles, thin facades)
    only implement ``chat()`` and must bypass the chain.
    """
    required = (
        "_detect_intent",
        "router",
        "_build_system_prompt",
        "_chat_with_model_failover",
        "_skill_input_with_history",
        "_extract_inputs",
        "_format_skill_result",
        "_needs_visual_semantics",
        "_vision_attachment_paths",
    )
    return all(hasattr(agent, attr) for attr in required)


def _run_thin_agent(
    ctx: "TurnContext",
    *,
    response_handler: Optional[Callable[[TurnContext], Any]] = None,
) -> "TurnResult":
    """Fallback path for minimal agents that only expose ``chat()``.

    Replicates the pre-harness behaviour of calling the agent directly and
    wrapping the result in a TurnResult. Empty/None responses are surfaced as
    retryable errors so the UI can render them consistently.
    """
    if response_handler is not None:
        return _run_response_handler(
            ctx,
            response_handler,
            handled_by="thin_agent_chat",
        )

    agent = ctx.agent
    try:
        response = agent.chat(ctx.user_input, context=ctx.extra)
    except Exception as error:  # noqa: BLE001 - surface agent failures uniformly
        logger.warning("Thin-agent chat() raised: %s", error)
        return TurnResult(
            response="模型请求失败，请稍后重试",
            success=False,
            error=str(error),
            handled_by="thin_agent_error",
            metadata={"turn_id": ctx.turn_id},
        )

    if not response:
        return TurnResult(
            response="未收到有效回答。请重新生成，或检查模型连接。",
            success=False,
            handled_by="thin_agent_empty",
            metadata={"turn_id": ctx.turn_id},
        )

    return TurnResult(
        response=response,
        success=True,
        handled_by="thin_agent_chat",
        metadata={"turn_id": ctx.turn_id},
    )


def _run_response_handler(
    ctx: "TurnContext",
    response_handler: Callable[[TurnContext], Any],
    *,
    handled_by: str,
) -> "TurnResult":
    """Invoke a host response adapter while preserving its exception chain."""
    outcome = response_handler(ctx)
    rendered = False
    response = outcome
    if isinstance(outcome, tuple) and len(outcome) == 2:
        response, rendered = outcome
    if not isinstance(response, str) or not response.strip():
        raise ValueError("模型服务未返回有效回答")
    return TurnResult(
        response=response.strip(),
        success=True,
        handled_by=handled_by,
        metadata={"turn_id": ctx.turn_id},
        response_rendered=bool(rendered),
    )


# Note: Future handlers (Stage 4+) may include:
# - Workflow approval handler
# - Response formatting/enrichment handler
