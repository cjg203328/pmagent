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
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Any, Dict, List, Optional

from .profile_handler import try_profile_proposal
from .knowledge_handler import try_knowledge_ingestion
from .knowledge_rule_handler import try_knowledge_rule_proposal
from .artifact_handler import try_artifact_generation
from .workflow_handler import try_workflow_routing
from .skill_handler import try_skill_routing
from .model_handler import fallback_to_model
from .runtime import (
    HarnessRuntime,
    LegacyAgentRuntimeAdapter,
    LocalHarnessRuntime,
    adapt_runtime,
)
from artpm_agent.runtime.counters import increment_counter
from artpm_agent.runtime.events import AgentEventType
from artpm_agent.runtime.request_services import TurnServiceBundle
from artpm_agent.runtime.turn_events import recorder_for_turn
from artpm_agent.utils.chat_intent import is_local_fast_intent
from artpm_agent.routing.service import IntentDecision
from artpm_agent.tenancy.scope import Scope

logger = logging.getLogger(__name__)

# Stable boundary version for hosts that exchange TurnContext/TurnResult data.
RUN_TURN_CONTRACT_VERSION = "1"


def _runtime_kind(runtime: Any) -> str:
    if isinstance(runtime, LocalHarnessRuntime):
        return "local"
    if isinstance(runtime, LegacyAgentRuntimeAdapter):
        return "legacy_adapter"
    if runtime is None:
        return "missing"
    return "canonical"


def _annotate_turn_result(result: "TurnResult", runtime_kind: str) -> "TurnResult":
    result.metadata.setdefault("harness_contract_version", RUN_TURN_CONTRACT_VERSION)
    result.metadata.setdefault("runtime_kind", runtime_kind)
    return result


def _classify_turn_error(error: BaseException) -> str:
    """Classify failures without storing provider-specific error text."""

    signals: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(signals) < 5:
        seen.add(id(current))
        signals.append(f"{type(current).__name__}: {current}".casefold())
        current = current.__cause__ or current.__context__
    signal_text = " ".join(signals)
    if any(marker in signal_text for marker in ("timeout", "timed out", "超时")):
        return "timeout"
    if any(
        marker in signal_text
        for marker in (
            "resourceexhausted",
            "resource exhausted",
            "rate limit",
            "too many requests",
            "429",
            "worker local total request limit",
            "overloaded",
            "service busy",
            "服务繁忙",
            "capacity",
        )
    ):
        return "busy"
    if any(
        marker in signal_text
        for marker in (
            "no valid response",
            "empty response",
            "未返回有效回答",
            "未收到有效回答",
        )
    ):
        return "empty_response"
    return "generic"


@dataclass(frozen=True, slots=True)
class TurnScope(Scope):
    """Trusted request scope carried alongside the conversational payload."""

    tenant_id: str = "local"
    workspace_id: str = "local-default"
    @property
    def actor_id(self) -> str:
        return self.principal_id

    @classmethod
    def from_tenant_context(cls, context: Any) -> "TurnScope":
        scope = Scope.from_context(context)
        return cls(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            principal_id=scope.principal_id,
            actor_role=scope.actor_role,
            request_id=scope.request_id,
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "TurnScope":
        tenant_context = values.get("tenant_context")
        if all(
            hasattr(tenant_context, name)
            for name in ("tenant_id", "workspace_id", "principal_id")
        ):
            scope = cls.from_tenant_context(tenant_context)
            actor_role = str(values.get("actor_role") or scope.actor_role)
            return cls(
                tenant_id=scope.tenant_id,
                workspace_id=scope.workspace_id,
                principal_id=scope.principal_id,
                actor_role=actor_role,
                request_id=values.get("request_id"),
            )
        return cls(
            tenant_id=str(
                values.get("tenant_id")
                or getattr(tenant_context, "tenant_id", None)
                or "local"
            ),
            workspace_id=str(
                values.get("workspace_id")
                or getattr(tenant_context, "workspace_id", None)
                or "local-default"
            ),
            principal_id=str(
                values.get("actor_id")
                or values.get("principal_id")
                or getattr(tenant_context, "principal_id", None)
                or "local-user"
            ),
            actor_role=str(values.get("actor_role") or "user"),
            request_id=values.get("request_id"),
        )


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

    # Deprecated compatibility boundary. New hosts should pass ``runtime``.
    agent: Any = None

    # Additional context (file_paths, project_id, etc.)
    extra: Dict[str, Any] = field(default_factory=dict)

    # Provider-neutral execution interface. This can be implemented by an
    # API worker, plugin host, or any other process without a concrete agent.
    runtime: Optional[HarnessRuntime] = None

    # Explicit request boundaries. ``extra`` remains a compatibility bag for
    # older hosts, but new orchestration code reads these fields first.
    scope: Optional[TurnScope] = None
    services: Optional[TurnServiceBundle] = None
    intent: Optional[str] = None
    intent_decision: Optional[IntentDecision] = None
    intent_checked: bool = False
    memory_injected: bool = False
    # Cache attachment parsing for the lifetime of one turn, including empty
    # or failed parser results.
    attachments_prepared: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.extra, dict):
            self.extra = dict(self.extra or {})
        if self.scope is None:
            try:
                from artpm_agent.tenancy import TenantContextManager

                current_context = TenantContextManager.get_current()
            except Exception:  # pragma: no cover - tenancy is optional at import time
                current_context = None
            self.scope = (
                TurnScope.from_tenant_context(current_context)
                if current_context is not None and "tenant_context" not in self.extra
                else TurnScope.from_mapping(self.extra)
            )
        elif not isinstance(self.scope, TurnScope):
            raise TypeError("scope must be a TurnScope")
        self.runtime = adapt_runtime(self.runtime, self.agent)
        # Allocate the one-shot execution lock while the context is still
        # single-thread owned; lazy creation inside ``run_turn`` would allow
        # two first callers to race before they share the same lock.
        self._run_turn_lock = RLock()
        self._run_turn_state = "pending"
        self._run_turn_result: Optional[TurnResult] = None


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


def _begin_turn_execution(ctx: "TurnContext") -> TurnResult | None:
    """Claim a context once and return a cached result for replayed calls.

    ``run_turn`` is intentionally a one-shot boundary. Hosts may retry a
    completed request after a transport interruption, so a completed context
    returns its original result without publishing another lifecycle stream.
    Concurrent calls receive a stable structured error instead of entering the
    handler chain twice.
    """

    lock = getattr(ctx, "_run_turn_lock", None)
    if lock is None:
        lock = RLock()
        ctx._run_turn_lock = lock
    with lock:
        state = getattr(ctx, "_run_turn_state", "pending")
        if state == "completed":
            cached = getattr(ctx, "_run_turn_result", None)
            if isinstance(cached, TurnResult):
                increment_counter("harness.turns.duplicate_replays")
                return replace(
                    cached,
                    metadata={
                        **cached.metadata,
                        "idempotent_replay": True,
                    },
                )
        if state == "running":
            increment_counter("harness.turns.concurrent_duplicates")
            return TurnResult(
                response="This turn is already being processed.",
                success=False,
                error="turn_in_progress",
                handled_by="harness_duplicate",
                metadata={
                    "turn_id": getattr(ctx, "turn_id", ""),
                    "idempotent_replay": False,
                },
            )
        ctx._run_turn_state = "running"
    return None


def _finish_turn_execution(ctx: "TurnContext", result: TurnResult) -> TurnResult:
    """Publish the terminal result to the context's one-shot state."""

    lock = getattr(ctx, "_run_turn_lock", None)
    if lock is None:
        lock = RLock()
        ctx._run_turn_lock = lock
    with lock:
        ctx._run_turn_result = result
        ctx._run_turn_state = "completed"
    return result


def _is_fast_response_turn(ctx: TurnContext) -> bool:
    """Return whether this is a host-requested, read-only meta response.

    Hosts may request the lightweight path, but the Harness independently
    validates the input. This prevents a caller from using ``turn_mode=fast``
    to bypass approvals, workflows, or skills for an arbitrary action.
    """
    extra = ctx.extra
    return bool(
        isinstance(extra, Mapping)
        and extra.get("turn_mode") == "fast"
        and is_local_fast_intent(ctx.user_input)
    )


def _run_turn_core(
    ctx: TurnContext,
    *,
    services: Optional[TurnServiceBundle] = None,
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

    runtime = ctx.runtime
    if runtime is None:
        return TurnResult(
            response="⚠️ Agent not initialized",
            success=False,
            error="Agent not initialized: missing harness runtime in TurnContext",
            handled_by="harness_error",
        )
    capabilities = getattr(runtime, "capabilities", None)
    if capabilities is None:
        return TurnResult(
            response="Harness runtime configuration is invalid.",
            success=False,
            error="Harness runtime does not declare capabilities",
            handled_by="harness_runtime_error",
        )

    if _is_fast_response_turn(ctx):
        if response_handler is not None:
            result = _run_response_handler(
                ctx,
                response_handler,
                handled_by="fast_response",
            )
            result.metadata["turn_mode"] = "fast"
            return result

        # External callers that opt into fast mode without a host response
        # adapter retain the legacy direct-response compatibility path.
        result = _run_thin_runtime(ctx)
        result.metadata["turn_mode"] = "fast"
        return result

    # ── Step 0: 记忆系统 — 对话压缩 + 跨会话记忆注入 ──
    # 在所有 handler 之前执行，确保 ctx.knowledge_context 携带完整记忆。
    # best-effort：任何环节失败不阻塞主流程。
    _inject_memory_context(
        ctx,
        knowledge_store,
        feedback_store=(services.feedback_store if services else None),
        strategy_store=(services.strategy_store if services else None),
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

    # All later handlers consume this one parser snapshot. Knowledge ingestion
    # intentionally stays before it because ingestion has its own durable
    # proposal payload and must not parse the same files twice.
    _prepare_turn_attachments(ctx)

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

    # Host-level handlers run for thin runtimes too. A minimal host can expose
    # direct chat without claiming the complete skill/model contract.
    if not bool(getattr(capabilities, "turn_processing", False)):
        return _run_thin_runtime(ctx, response_handler=response_handler)

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


def run_turn(
    ctx: TurnContext,
    *,
    services: Optional[TurnServiceBundle] = None,
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
    feedback: Optional[str] = None,
    auto_reflect: bool = True,
) -> TurnResult:
    """Run one turn through the single canonical orchestration boundary."""

    increment_counter("harness.turns.total")
    runtime_kind = _runtime_kind(getattr(ctx, "runtime", None))
    increment_counter(f"harness.turns.{runtime_kind}")
    replay = _begin_turn_execution(ctx)
    if replay is not None:
        return _annotate_turn_result(replay, runtime_kind)

    resolved_services = services or ctx.services
    if resolved_services is not None:
        profile_store = profile_store or resolved_services.profile_store
        knowledge_store = knowledge_store or resolved_services.knowledge_store
        artifact_coordinator = artifact_coordinator or resolved_services.artifact_coordinator
        workflow_coordinator = workflow_coordinator or resolved_services.workflow_coordinator
        workflow_formatter = workflow_formatter or resolved_services.workflow_formatter
        ctx.services = resolved_services
    request_conversation_id = request_conversation_id or ctx.conversation_id

    # Scope is authenticated before the lifecycle stream starts. A rejected
    # request must not look like a valid turn in audit or replay data.
    try:
        ctx.scope = get_turn_scope(ctx)
    except Exception as error:  # noqa: BLE001 - reject forged request scope
        return _finish_turn_execution(
            ctx,
            _annotate_turn_result(
                _scope_rejection_result(ctx, error),
                runtime_kind,
            ),
        )

    recorder = recorder_for_turn(ctx)
    recorder.emit(ctx, AgentEventType.TURN_START)
    try:
        result = _run_turn_core(
            ctx,
            services=resolved_services,
            profile_store=profile_store,
            knowledge_store=knowledge_store,
            artifact_coordinator=artifact_coordinator,
            request_conversation_id=request_conversation_id,
            knowledge_rule_extractor=knowledge_rule_extractor,
            workflow_coordinator=workflow_coordinator,
            workflow_formatter=workflow_formatter,
            response_handler=response_handler,
        )
        _capture_tencentdb_memory(ctx, result)
        lifecycle = complete_turn_lifecycle(
            ctx,
            result,
            services=resolved_services,
            feedback=feedback,
            auto_reflect=auto_reflect,
        )
        if lifecycle:
            result.metadata.setdefault("lifecycle", {}).update(lifecycle)
        if resolved_services is not None:
            result.metadata["lifecycle_managed"] = True
    except Exception as error:
        result = TurnResult(
            response="本次请求处理失败，请稍后重试。",
            success=False,
            error=str(error) or error.__class__.__name__,
            handled_by="harness_error",
            metadata={
                "turn_id": ctx.turn_id,
                "error_code": "turn_processing_failed",
                "error_kind": _classify_turn_error(error),
            },
        )
        if resolved_services is not None:
            result.metadata["lifecycle_managed"] = True
        try:
            lifecycle = complete_turn_lifecycle(
                ctx,
                result,
                services=resolved_services,
                feedback=feedback,
                auto_reflect=auto_reflect,
            )
            if lifecycle:
                result.metadata.setdefault("lifecycle", {}).update(lifecycle)
        except Exception:  # noqa: BLE001 - lifecycle must not mask turn errors
            logger.warning("failed to finalize failed turn", exc_info=True)

    result.metadata.setdefault("turn_id", ctx.turn_id)
    if ctx.intent_checked:
        result.metadata.setdefault("intent", ctx.intent)
        result.metadata.setdefault("intent_checked", True)
    recorder.emit(
        ctx,
        AgentEventType.TURN_END,
        error=result.error if not result.success else None,
        metadata={
            "success": bool(result.success),
            "handled_by": result.handled_by,
            "awaiting_approval": bool(result.awaiting_approval),
            "intent": ctx.intent,
            "intent_checked": bool(ctx.intent_checked),
        },
    )
    return _finish_turn_execution(
        ctx,
        _annotate_turn_result(result, runtime_kind),
    )


def _feedback_kind(feedback: str) -> str:
    return (
        "avoid"
        if any(marker in feedback for marker in ("👎", "差", "错误", "不对"))
        else "preference"
    )


def complete_turn_lifecycle(
    ctx: TurnContext,
    result: TurnResult,
    *,
    services: Optional[TurnServiceBundle] = None,
    feedback: Optional[str] = None,
    auto_reflect: bool = True,
) -> dict[str, Any]:
    """Persist the post-turn learning tail at the canonical boundary.

    Every operation is independently best-effort. A learning or observability
    outage must never turn a completed user response into a failed request.
    """
    resolved_services = services or ctx.services
    if resolved_services is None:
        return {}

    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    if extra.get("_lifecycle_finalized"):
        return {"already_finalized": True}
    extra["_lifecycle_finalized"] = True

    status: dict[str, Any] = {}
    scope = ctx.scope or TurnScope()
    feedback_text = feedback.strip() if isinstance(feedback, str) else ""
    feedback_store = resolved_services.feedback_store
    if feedback_text and feedback_store is not None:
        try:
            feedback_store.add(
                kind=_feedback_kind(feedback_text),
                content=feedback_text,
                scope=result.handled_by or "global",
                tenant_id=scope.tenant_id,
                workspace_id=scope.workspace_id,
                principal_id=scope.actor_id,
            )
            status["feedback_recorded"] = True
        except Exception:  # noqa: BLE001 - learning must not block a turn
            logger.warning("turn feedback recording failed", exc_info=True)
            status["feedback_recorded"] = False

    episode_store = resolved_services.episode_store
    if episode_store is not None:
        try:
            from .outcome_recorder import record_outcome

            episode_id = record_outcome(
                ctx,
                result,
                store=episode_store,
                feedback=feedback_text or None,
            )
            status["outcome_recorded"] = episode_id is not None
        except Exception:  # noqa: BLE001 - recorder already fails soft
            logger.warning("turn outcome recording failed", exc_info=True)
            status["outcome_recorded"] = False

    if auto_reflect:
        reflection_scheduler = resolved_services.reflection_scheduler
        if (
            reflection_scheduler is not None
            and episode_store is not None
            and feedback_store is not None
            and resolved_services.strategy_store is not None
        ):
            try:
                report = reflection_scheduler.run_if_due(
                    episode_store,
                    feedback_store,
                    resolved_services.strategy_store,
                    tenant_id=scope.tenant_id,
                    workspace_id=scope.workspace_id,
                    all_principals=True,
                )
                status["reflection_ran"] = report is not None
            except Exception:  # noqa: BLE001 - reflection is asynchronous policy
                logger.warning("turn reflection failed", exc_info=True)
                status["reflection_ran"] = False

        knowledge_store = resolved_services.knowledge_store
        consolidation_scheduler = resolved_services.consolidation_scheduler
        if knowledge_store is not None and consolidation_scheduler is not None:
            try:
                from artpm_agent.memory.consolidation import ConsolidationService

                report = consolidation_scheduler.run_if_due(
                    ConsolidationService(knowledge_store),
                    tenant_id=scope.tenant_id,
                    workspace_id=scope.workspace_id,
                )
                status["consolidation_ran"] = report is not None
            except Exception:  # noqa: BLE001 - consolidation is asynchronous policy
                logger.warning("turn knowledge consolidation failed", exc_info=True)
                status["consolidation_ran"] = False

    status["finalized"] = True
    return status


def _scope_rejection_result(ctx: TurnContext, error: Exception) -> TurnResult:
    return TurnResult(
        response="租户或工作区范围校验失败，未处理本次请求。",
        success=False,
        error=str(error) or error.__class__.__name__,
        handled_by="tenant_scope_gate",
        metadata={
            "turn_id": ctx.turn_id,
            "conversation_id": ctx.conversation_id,
            "error_code": "workspace_access_denied",
        },
    )


def _capture_tencentdb_memory(ctx: TurnContext, result: TurnResult) -> None:
    if result.metadata.get("turn_mode") == "fast" or _is_fast_response_turn(ctx):
        return
    if not result.success or result.awaiting_approval or not result.response.strip():
        return
    services = getattr(ctx, "services", None)
    memory = getattr(services, "tencentdb_memory", None)
    if memory is None:
        runtime = ctx.runtime
        memory = getattr(runtime, "tencentdb_memory", None)
    if memory is None or not callable(getattr(memory, "capture", None)):
        return
    try:
        from artpm_agent.harness.memory_retrieval import (
            tencentdb_scope_from_context,
        )

        scope = tencentdb_scope_from_context(ctx, memory)
        if scope is not None:
            memory.capture(ctx.user_input, result.response, scope)
    except Exception:  # noqa: BLE001 - persistence is best-effort
        logger.warning("TencentDB Agent Memory capture skipped", exc_info=True)


def _run_thin_runtime(
    ctx: "TurnContext",
    *,
    response_handler: Optional[Callable[[TurnContext], Any]] = None,
) -> "TurnResult":
    """Fallback path for minimal runtimes that only expose ``chat()``.

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

    runtime = ctx.runtime
    if runtime is None or not runtime.capabilities.direct_response:
        return TurnResult(
            response="No response runtime is available for this turn.",
            success=False,
            error="Harness runtime does not support direct responses",
            handled_by="harness_runtime_error",
            metadata={"turn_id": ctx.turn_id},
        )
    response_context = dict(ctx.extra or {})
    response_context.update(
        {
            "conversation_id": ctx.conversation_id,
            "turn_id": ctx.turn_id,
            "conversation_history": list(ctx.conversation_history or []),
            "agent_profile": ctx.agent_profile,
            "knowledge_context": ctx.knowledge_context,
            "intent": ctx.intent,
            "intent_checked": ctx.intent_checked,
            "harness_managed": True,
        }
    )

    stream_method = getattr(runtime, "stream_with_failover", None)
    stream_target = getattr(runtime, "target", None)
    if callable(stream_method) and callable(getattr(stream_target, "stream_chat", None)):
        streamed: list[str] = []
        try:
            source = stream_method(
                ctx.user_input,
                "",
                ctx.conversation_history or [],
                image_paths=[],
                context=response_context,
            )
            for chunk in source:
                if isinstance(chunk, str) and chunk:
                    streamed.append(chunk)
            response = "".join(streamed)
            if not response.strip():
                raise ValueError("model returned an empty response")
            return TurnResult(
                response=response.strip(),
                success=True,
                handled_by="thin_agent_stream",
                metadata={"turn_id": ctx.turn_id, "streamed": True},
                response_rendered=True,
            )
        except Exception as error:  # noqa: BLE001 - normalize adapter failures
            if streamed:
                return TurnResult(
                    response="".join(streamed),
                    success=False,
                    error=str(error) or error.__class__.__name__,
                    handled_by="thin_agent_stream_error",
                    metadata={
                        "turn_id": ctx.turn_id,
                        "error_kind": _classify_turn_error(error),
                        "streamed": True,
                    },
                    response_rendered=True,
                )
            logger.debug("thin stream unavailable; falling back to chat", exc_info=True)

    try:
        response = runtime.chat(ctx.user_input, context=response_context)
    except Exception as error:  # noqa: BLE001 - surface agent failures uniformly
        logger.warning("Thin-agent chat() raised: %s", error)
        return TurnResult(
            response="模型请求失败，请稍后重试",
            success=False,
            error=str(error),
            handled_by="thin_agent_error",
            metadata={
                "turn_id": ctx.turn_id,
                "error_kind": _classify_turn_error(error),
            },
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


def _inject_memory_context(
    ctx: "TurnContext",
    knowledge_store: Optional[Any],
    *,
    feedback_store: Optional[Any] = None,
    strategy_store: Optional[Any] = None,
    meta_memory_store: Optional[Any] = None,
) -> None:
    """Step 0: 单一记忆注入入口（幂等）。

    统一调用 ``memory_retrieval.inject_memory_context``，组合：
      1. 调用方已确认的 Workspace 知识（ctx.knowledge_context）
      2. 对话压缩摘要（长对话保连续性，best-effort）
      3. 跨会话长期记忆 + 工作区资料检索
      4. 用户偏好 / 纠正（FeedbackStore）
      5. 进化策略（StrategyStore）
      6. 元记忆缺口建议

    通过 ``ctx.extra["memory_injected"]`` 保证每回合只注入一次；任何异常仅
    记录日志，不阻塞主流程。
    """
    try:
        from artpm_agent.harness.memory_retrieval import (
            inject_memory_context,
        )

        extra = ctx.extra if isinstance(ctx.extra, dict) else {}
        if extra.get("disable_memory_injection"):
            return
        services = getattr(ctx, "services", None)
        if knowledge_store is None and services is not None:
            knowledge_store = services.knowledge_store
        if feedback_store is None and services is not None:
            feedback_store = services.feedback_store
        if strategy_store is None and services is not None:
            strategy_store = services.strategy_store
        if meta_memory_store is None and services is not None:
            meta_memory_store = services.meta_memory_store
        memory_manager = services.memory_manager if services is not None else None
        tencentdb_memory = services.tencentdb_memory if services is not None else None
        inject_memory_context(ctx,
            memory_manager=memory_manager,
            tencentdb_memory=tencentdb_memory,
            knowledge_store=knowledge_store,
            feedback_store=feedback_store,
            strategy_store=strategy_store,
            meta_memory_store=meta_memory_store,
            max_context_chars=int(extra.get("memory_inject_max_chars") or 0),
            max_context_tokens=int(extra.get("memory_inject_max_tokens") or 0),
        )
    except Exception as exc:  # noqa: BLE001 - injection must never break a turn
        logger.warning("记忆系统 Step 0 跳过 (非致命): %s", exc, exc_info=True)


def get_turn_scope(ctx: Any) -> TurnScope:
    """Return the canonical scope and reject conflicts with trusted tenancy."""

    extra = getattr(ctx, "extra", None)
    extra = extra if isinstance(extra, Mapping) else {}
    scope = getattr(ctx, "scope", None)
    if not isinstance(scope, TurnScope):
        scope = TurnScope.from_mapping(extra)

    from artpm_agent.tenancy import (
        TenantContextManager,
        tenant_context_from_host,
    )

    host_context = tenant_context_from_host(extra)
    current_context = TenantContextManager.get_current()
    if (
        host_context is not None
        and current_context is not None
        and (
            host_context.tenant_id != current_context.tenant_id
            or host_context.workspace_id != current_context.workspace_id
        )
    ):
        raise ValueError("tenant contexts conflict")
    trusted_context = host_context or current_context
    if trusted_context is not None:
        trusted_scope = TurnScope.from_tenant_context(trusted_context)
        if (
            scope.tenant_id != trusted_scope.tenant_id
            or scope.workspace_id != trusted_scope.workspace_id
            or scope.actor_id != trusted_scope.actor_id
            or scope.actor_role != trusted_scope.actor_role
        ):
            raise ValueError("turn scope conflicts with authenticated tenant context")
    return scope


def _prepare_turn_attachments(ctx: TurnContext) -> None:
    """Create one normalized attachment snapshot for every handler in a turn."""

    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    if getattr(ctx, "attachments_prepared", False):
        increment_counter("harness.attachments.parse_duplicate_attempts")
        increment_counter("harness.attachments.parse_cache_hits")
        increment_counter("harness.attachments.parse_reused")
        return
    if extra.get("_attachments_prepared") is True:
        ctx.attachments_prepared = True
        increment_counter("harness.attachments.parse_duplicate_attempts")
        increment_counter("harness.attachments.parse_cache_hits")
        increment_counter("harness.attachments.parse_reused")
        return
    # Hosts may provide a pre-parsed snapshot in the compatibility bag. An
    # explicitly empty snapshot is still a valid result and must not be parsed
    # again by a later handler.
    if "parsed_files" in extra and "attachment_context" in extra:
        ctx.attachments_prepared = True
        extra["_attachments_prepared"] = True
        increment_counter("harness.attachments.parse_cache_hits")
        increment_counter("harness.attachments.parse_reused")
        return
    runtime = ctx.runtime
    if runtime is None or not runtime.capabilities.attachment_parsing:
        ctx.attachments_prepared = True
        extra["_attachments_prepared"] = True
        return
    file_paths = list(extra.get("file_paths") or [])
    if not file_paths:
        for attachment in list(ctx.attachments or extra.get("attachments", [])):
            if isinstance(attachment, Mapping):
                path = attachment.get("file_path") or attachment.get("stored_path")
                if path:
                    file_paths.append(str(path))
    if not file_paths:
        ctx.attachments_prepared = True
        extra["_attachments_prepared"] = True
        return
    # Mark before invoking the parser so re-entrant calls cannot duplicate the
    # potentially expensive document processing.
    ctx.attachments_prepared = True
    extra["_attachments_prepared"] = True
    extra["file_paths"] = file_paths
    increment_counter("harness.attachments.parse_calls")
    try:
        parsed_files, attachment_context = runtime.parse_attachments(
            ctx.user_input,
            {**extra, "file_paths": file_paths},
        )
        extra["parsed_files"] = parsed_files
        extra["attachment_context"] = attachment_context
    except Exception:  # noqa: BLE001 - model fallback can still explain the failure
        increment_counter("harness.attachments.parse_failures")
        logger.warning("统一附件预处理失败", exc_info=True)
