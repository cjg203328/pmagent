"""Chat-facing coordination for deterministic ArtPM workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .engine import WorkflowEngine
from .models import (
    AttachmentSelectionData,
    WorkflowDecision,
    WorkflowExecutionResult,
    WorkflowSelectionContext,
)
from .designer import capability_allowlist_from_skill_metadata
from .risk_policy import DEFAULT_SKILL_CAPABILITIES
from .selector import CapabilityAllowlist, WorkflowSelector
from .store import WorkflowStore


WORKFLOW_CAPABILITY_ALLOWLIST = DEFAULT_SKILL_CAPABILITIES


@dataclass(frozen=True)
class WorkflowChatOutcome:
    """A workflow decision and, when runnable, its persisted execution result."""

    decision: WorkflowDecision
    execution: WorkflowExecutionResult | None = None
    fallback_reason: str | None = None

    @property
    def matched(self) -> bool:
        return self.execution is not None


def _attachment_facts(
    attachments: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> tuple[AttachmentSelectionData, ...]:
    facts: list[AttachmentSelectionData] = []
    for attachment in attachments:
        name = str(
            attachment.get("original_name")
            or attachment.get("name")
            or attachment.get("stored_path")
            or ""
        )
        extension = Path(name).suffix.lower().lstrip(".")
        if not extension:
            continue
        facts.append(
            AttachmentSelectionData(
                extension=extension,
                document_type=(
                    str(attachment["document_type"])
                    if attachment.get("document_type")
                    else None
                ),
            )
        )
    return tuple(facts)


def _task_id_from_prompt(prompt: str) -> str | None:
    match = re.search(
        r"(?:任务|task)\s*[#：:]?\s*([A-Za-z0-9][A-Za-z0-9_.-]{0,63})",
        prompt,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _project_id_from_prompt(prompt: str) -> str | None:
    match = re.search(
        r"(?:项目|project)\s*[#：:]?\s*([A-Za-z0-9][A-Za-z0-9_.-]{0,63})",
        prompt,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _workflow_context(prompt: str, agent_context: Mapping[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key in ("project_id", "project_status", "task_id"):
        value = agent_context.get(key)
        if isinstance(value, (str, int, float, bool)) and value not in ("", None):
            context[key] = value
    if not context.get("task_id"):
        task_id = _task_id_from_prompt(prompt)
        if task_id:
            context["task_id"] = task_id
    if not context.get("project_id"):
        project_id = _project_id_from_prompt(prompt)
        if project_id:
            context["project_id"] = project_id
    return context


class ScopedWorkflowAgent:
    """Expose a request-scoped router while delegating other Agent features."""

    __slots__ = ("base_agent", "router")

    def __init__(self, base_agent: Any, router: Any) -> None:
        self.base_agent = base_agent
        self.router = router

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base_agent, name)


class WorkflowCoordinator:
    """Select and run safe workflows without replacing ordinary model chat."""

    def __init__(
        self,
        store: WorkflowStore,
        agent: Any,
        *,
        capability_allowlist: CapabilityAllowlist | None = None,
        workspace_id: str = "local-default",
        profile_id: str = "local-default",
        tenant_id: str | None = None,
    ) -> None:
        self.store = store
        self.agent = agent
        self.workspace_id = workspace_id
        self.profile_id = profile_id
        self.tenant_id = tenant_id or store.tenant_for_workspace(workspace_id)
        store.ensure_builtins(
            workspace_id=self.workspace_id,
            profile_id=self.profile_id,
            tenant_id=self.tenant_id,
        )
        router = getattr(agent, "router", None)
        execute_skill = getattr(router, "execute_skill", None)
        if not callable(execute_skill):
            raise TypeError("agent must expose router.execute_skill")
        if capability_allowlist is None:
            list_skills = getattr(router, "list_skills", None)
            metadata = list_skills() if callable(list_skills) else ()
            capability_allowlist = capability_allowlist_from_skill_metadata(metadata)
        self.capability_allowlist = capability_allowlist
        self.selector = WorkflowSelector(capability_allowlist)
        self.engine = WorkflowEngine(
            store,
            execute_skill,
            capability_allowlist=capability_allowlist,
            tenant_id=self.tenant_id,
        )

    def create_task_orchestrator(
        self,
        *,
        task_runner: Callable[..., Any] | None = None,
        checkpointer: Any = None,
        session_store: Any = None,
        max_parallelism: int | None = None,
        max_retries: int | None = None,
    ) -> Any:
        """Create the opt-in LangGraph collaboration runtime.

        Existing chat turns continue to use :class:`WorkflowEngine`.  This
        factory is the integration point for future specialist-agent flows;
        callers can supply a runner for agent tasks, or use the built-in
        read-only Skill adapter.  Side-effect Skills intentionally stay on the
        persisted ``WorkflowEngine`` approval path.
        """

        from .task_graph import LangGraphTaskOrchestrator

        runtime_config = getattr(self.agent, "config", None)
        get_config_value = getattr(runtime_config, "get", None)
        configured_framework = (
            str(
                get_config_value("agent_runtime.orchestration_framework", "langgraph")
            ).strip().lower()
            if callable(get_config_value)
            else "langgraph"
        )
        graph_enabled = (
            bool(get_config_value("agent_runtime.langgraph_enabled", True))
            if callable(get_config_value)
            else True
        )
        if not graph_enabled or configured_framework not in {"langgraph", "langgraph-v1"}:
            raise RuntimeError(
                "LangGraph orchestration is disabled by agent_runtime configuration"
            )
        if session_store is None:
            try:
                from artpm_agent.memory.session_store import SessionStore

                session_store = SessionStore(self.store.db_path)
            except Exception:
                # Audit persistence is best effort; a supplied checkpointer
                # remains sufficient for graph execution and testing.
                session_store = None
        configured_parallelism = (
            get_config_value("agent_runtime.langgraph_max_parallelism", 4)
            if callable(get_config_value)
            else 4
        )
        configured_retries = (
            get_config_value("agent_runtime.langgraph_max_retries", 2)
            if callable(get_config_value)
            else 2
        )
        return LangGraphTaskOrchestrator(
            task_runner or self._run_read_only_graph_skill,
            checkpointer=checkpointer,
            session_store=session_store,
            max_parallelism=(
                configured_parallelism if max_parallelism is None else max_parallelism
            ),
            max_retries=(configured_retries if max_retries is None else max_retries),
        )

    def create_collaboration_graph(self, **options: Any) -> Any:
        """Backward-friendly alias for ``create_task_orchestrator``."""

        return self.create_task_orchestrator(**options)

    def _run_read_only_graph_skill(self, task: Any, context: Mapping[str, Any]) -> Any:
        """Run a server-allowlisted read-only Skill from a graph node."""

        skill_id = getattr(task, "skill_id", None)
        capability = getattr(task, "capability", None)
        if not skill_id or not capability:
            raise ValueError(
                "the default graph runner requires task.skill_id and task.capability"
            )
        allowed_capabilities = self.engine.capability_allowlist.get(skill_id, frozenset())
        if capability not in allowed_capabilities:
            raise PermissionError("task capability is outside the server allowlist")
        if not self.engine.risk_policy.is_allowed(capability):
            raise PermissionError("task capability is blocked by the risk policy")
        is_side_effect = capability in self.engine.side_effect_capabilities or (
            self.engine.risk_policy.is_side_effect(
                capability,
                declared_side_effect=bool(getattr(task, "side_effect", False)),
            )
        )
        if is_side_effect:
            raise PermissionError(
                "side-effect Skills must run through WorkflowEngine approval"
            )
        raw_inputs = context.get("inputs", {})
        if not isinstance(raw_inputs, Mapping):
            raise TypeError("graph task inputs must be a mapping for Skill execution")
        result = self.engine.execute_skill(skill_id, dict(raw_inputs))
        if isinstance(result, Mapping) and result.get("success") is False:
            raise RuntimeError(str(result.get("error") or "Skill execution failed"))
        return result

    def _prepare_inputs(
        self,
        prompt: str,
        definition: Any,
        agent_context: dict[str, Any],
    ) -> dict[str, Any]:
        prepare = getattr(self.agent, "prepare_skill_inputs", None)
        prepared: dict[str, Any] = {}
        if callable(prepare):
            seen: set[str] = set()
            for step in definition.steps:
                skill_id = (
                    "reminder_bot"
                    if step.skill_id == "reminder_dispatch"
                    else step.skill_id
                )
                if skill_id in seen:
                    continue
                seen.add(skill_id)
                values = prepare(prompt, skill_id, agent_context)
                if isinstance(values, Mapping):
                    prepared.update(values)
        return prepared

    @staticmethod
    def _inputs_are_complete(
        workflow_id: str,
        input_data: Mapping[str, Any],
        context_data: Mapping[str, Any],
    ) -> bool:
        if workflow_id == "quote_assessment":
            quote = input_data.get("quote_amount")
            cost = input_data.get("cost")
            return (
                isinstance(quote, (int, float))
                and not isinstance(quote, bool)
                and quote > 0
                and isinstance(cost, (int, float))
                and not isinstance(cost, bool)
                and cost >= 0
            )
        if workflow_id == "progress_check":
            return bool(context_data.get("project_id"))
        if workflow_id == "reminder_dispatch":
            return bool(context_data.get("task_id")) and bool(
                input_data.get("recipients")
            )
        return True

    def process(
        self,
        prompt: str,
        *,
        conversation_id: str,
        turn_id: str,
        agent_context: dict[str, Any],
        attachments: list[Mapping[str, Any]] | None = None,
        explicit_workflow_id: str | None = None,
    ) -> WorkflowChatOutcome:
        context_data = _workflow_context(prompt, agent_context)
        selection = WorkflowSelectionContext(
            prompt=prompt,
            conversation_id=conversation_id,
            workspace_id=self.workspace_id,
            profile_id=self.profile_id,
            tenant_id=self.tenant_id,
            explicit_workflow_id=explicit_workflow_id,
            project_status=(
                str(context_data["project_status"])
                if context_data.get("project_status")
                else None
            ),
            attachments=_attachment_facts(attachments or []),
            context_data=context_data,
        )
        decision = self.selector.select(
            selection,
            self.store.list_definitions(
                workspace_id=self.workspace_id,
                profile_id=self.profile_id,
                tenant_id=self.tenant_id,
                enabled_only=True,
            ),
        )
        if decision.definition is None:
            return WorkflowChatOutcome(decision=decision, fallback_reason="no_match")

        input_data = self._prepare_inputs(
            prompt,
            decision.definition,
            agent_context,
        )
        if not self._inputs_are_complete(
            decision.definition.id,
            input_data,
            context_data,
        ):
            return WorkflowChatOutcome(
                decision=decision,
                fallback_reason="missing_required_context",
            )

        execution = self.engine.start(
            decision.definition,
            conversation_id,
            input_data=input_data,
            context_data=context_data,
            turn_id=turn_id,
            idempotency_key=f"chat-turn-{turn_id}",
        )
        return WorkflowChatOutcome(decision=decision, execution=execution)


def format_workflow_result(agent: Any, result: WorkflowExecutionResult) -> str:
    """Turn persisted step output into one concise assistant response."""
    run = result.run
    if run.status == "awaiting_approval":
        preview = run.outputs.get("preview")
        if isinstance(preview, dict):
            formatter = getattr(agent, "format_skill_result", None)
            if callable(formatter):
                rendered = formatter("reminder_bot", preview)
                return f"{rendered}\n\n发送前需要你的确认。"
        return "工作流已准备好，执行下一步前需要你的确认。"

    if run.status == "succeeded":
        rendered_outputs: list[str] = []
        formatter = getattr(agent, "format_skill_result", None)
        for step in run.definition_snapshot.steps:
            output = run.outputs.get(step.resolved_output_key)
            if not isinstance(output, dict):
                continue
            if callable(formatter):
                rendered = formatter(step.skill_id, output)
            else:
                rendered = str(output)
            if rendered and rendered not in rendered_outputs:
                rendered_outputs.append(rendered)
        return "\n\n".join(rendered_outputs) or "工作流已完成。"

    if run.status == "cancelled":
        return "工作流已取消，没有执行后续操作。"
    return f"工作流未完成：{run.error or '请检查运行记录后重试。'}"
