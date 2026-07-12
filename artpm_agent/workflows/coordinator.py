"""Chat-facing coordination for deterministic ArtPM workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

from .engine import WorkflowEngine
from .models import (
    AttachmentSelectionData,
    WorkflowDecision,
    WorkflowExecutionResult,
    WorkflowSelectionContext,
)
from .risk_policy import DEFAULT_SKILL_CAPABILITIES
from .selector import WorkflowSelector
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


class WorkflowCoordinator:
    """Select and run safe workflows without replacing ordinary model chat."""

    def __init__(self, store: WorkflowStore, agent: Any) -> None:
        self.store = store
        self.agent = agent
        self.selector = WorkflowSelector(WORKFLOW_CAPABILITY_ALLOWLIST)
        router = getattr(agent, "router", None)
        execute_skill = getattr(router, "execute_skill", None)
        if not callable(execute_skill):
            raise TypeError("agent must expose router.execute_skill")
        self.engine = WorkflowEngine(
            store,
            execute_skill,
            capability_allowlist=WORKFLOW_CAPABILITY_ALLOWLIST,
        )

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
            self.store.list_definitions(enabled_only=True),
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
