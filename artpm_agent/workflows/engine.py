"""Synchronous, resumable execution for validated linear workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Set
import inspect
from typing import Any

from .models import (
    ApprovalRequirement,
    WorkflowApproval,
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowRun,
    WorkflowStepDefinition,
    json_mapping,
    strongest_approval,
)
from .risk_policy import (
    CAPABILITY_RISK_REGISTRY,
    DEFAULT_RISK_POLICY,
    DEFAULT_SKILL_CAPABILITIES,
    CapabilityRiskPolicy,
)
from .selector import CapabilityAllowlist, definition_is_allowed
from .store import WorkflowConflictError, WorkflowStore


DEFAULT_CAPABILITY_ALLOWLIST = DEFAULT_SKILL_CAPABILITIES
DEFAULT_SIDE_EFFECT_CAPABILITIES = frozenset(
    capability
    for capability, rule in CAPABILITY_RISK_REGISTRY.items()
    if rule.side_effect
)


class WorkflowInputError(ValueError):
    """Raised when a declarative input reference cannot be resolved."""


def _lookup_reference(state: Mapping[str, Any], reference: str) -> Any:
    if not reference.startswith("$") or len(reference) == 1:
        return reference
    parts = reference[1:].split(".")
    if parts[0] not in {"input", "context", "steps"}:
        raise WorkflowInputError(f"unsupported reference root: {reference}")
    current: Any = state
    for part in parts:
        if isinstance(current, Mapping):
            if part not in current:
                raise WorkflowInputError(
                    f"missing workflow input reference: {reference}"
                )
            current = current[part]
            continue
        if isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise WorkflowInputError(
                    f"reference index is out of range: {reference}"
                )
            current = current[index]
            continue
        raise WorkflowInputError(f"invalid workflow input reference: {reference}")
    return current


def _resolve_value(value: Any, state: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return _lookup_reference(state, value)
    if isinstance(value, dict):
        return {key: _resolve_value(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, state) for item in value]
    return value


class WorkflowEngine:
    """Execute only server-allowlisted Skills with persisted approval gates."""

    def __init__(
        self,
        store: WorkflowStore,
        execute_skill: Callable[[str, dict[str, Any]], Mapping[str, Any]],
        *,
        capability_allowlist: CapabilityAllowlist | None = None,
        approval_floor: ApprovalRequirement = "user",
        side_effect_capabilities: Set[str] | None = None,
        risk_policy: CapabilityRiskPolicy | None = None,
    ) -> None:
        if not callable(execute_skill):
            raise TypeError("execute_skill must be callable")
        if approval_floor not in {"none", "user", "admin"}:
            raise ValueError("unsupported approval floor")
        self.store = store
        self.execute_skill = execute_skill
        source = capability_allowlist or DEFAULT_CAPABILITY_ALLOWLIST
        self.capability_allowlist: dict[str, Set[str]] = {
            skill_id: frozenset(capabilities)
            for skill_id, capabilities in source.items()
        }
        self.approval_floor = approval_floor
        self.risk_policy = risk_policy or DEFAULT_RISK_POLICY
        self.side_effect_capabilities = frozenset(
            DEFAULT_SIDE_EFFECT_CAPABILITIES
            | frozenset(side_effect_capabilities or ())
        )

    def _result(
        self,
        run: WorkflowRun,
        approval: WorkflowApproval | None = None,
    ) -> WorkflowExecutionResult:
        return WorkflowExecutionResult(
            run=run,
            steps=tuple(self.store.list_steps(run.id)),
            approval=approval,
        )

    def result(self, run_id: str) -> WorkflowExecutionResult:
        """Return the current persisted result without advancing the run."""
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError("unknown workflow run")
        approvals = self.store.list_approvals(run_id)
        current_approval = next(
            (
                approval
                for approval in reversed(approvals)
                if approval.status == "pending"
            ),
            None,
        )
        return self._result(run, current_approval)

    def start(
        self,
        definition: WorkflowDefinition,
        conversation_id: str,
        *,
        input_data: dict[str, Any] | None = None,
        context_data: dict[str, Any] | None = None,
        turn_id: str | None = None,
        idempotency_key: str | None = None,
        auto_resume: bool = True,
    ) -> WorkflowExecutionResult:
        """Create an idempotent run from an installed, resolved definition."""
        if not definition.enabled:
            raise PermissionError("workflow is disabled")
        if not self._definition_is_allowed(definition):
            raise PermissionError(
                "workflow requests a capability outside the allowlist"
            )
        run = self.store.create_run(
            definition,
            conversation_id,
            input_data=input_data,
            context_data=context_data,
            turn_id=turn_id,
            idempotency_key=idempotency_key,
        )
        return self.resume(run.id) if auto_resume else self._result(run)

    def _effective_approval(
        self,
        step: WorkflowStepDefinition,
    ) -> ApprovalRequirement:
        side_effect = self._is_side_effect(step)
        policy_approval = self.risk_policy.effective_approval(
            step.capability,
            declared_side_effect=side_effect,
            declared_approval=step.approval,
            approval_floor=self.approval_floor if side_effect else "none",
        )
        return strongest_approval(step.approval, policy_approval)

    def _is_side_effect(self, step: WorkflowStepDefinition) -> bool:
        return (
            step.capability in self.side_effect_capabilities
            or self.risk_policy.is_side_effect(
                step.capability,
                declared_side_effect=step.side_effect,
            )
        )

    def _definition_is_allowed(self, definition: WorkflowDefinition) -> bool:
        return definition_is_allowed(definition, self.capability_allowlist) and all(
            self.risk_policy.is_allowed(step.capability)
            for step in definition.steps
        )

    @staticmethod
    def _resolve_inputs(
        run: WorkflowRun,
        step: WorkflowStepDefinition,
    ) -> dict[str, Any]:
        state = {
            "input": run.input_data,
            "context": run.context_data,
            "steps": run.outputs,
        }
        resolved = {
            key: _resolve_value(value, state) for key, value in step.input_map.items()
        }
        return json_mapping(resolved, "resolved step inputs")

    def resume(self, run_id: str) -> WorkflowExecutionResult:
        """Advance until completion, approval, failure, or a concurrent claim."""
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError("unknown workflow run")
        if run.status in {"succeeded", "failed", "cancelled"}:
            return self._result(run)
        definition = run.definition_snapshot
        if not self._definition_is_allowed(definition):
            if run.status in {"pending", "running"}:
                failed = self.store.fail_step(
                    run.id,
                    run.current_step,
                    "workflow capability is no longer allowed",
                )
                return self._result(failed)
            return self._result(run)

        if run.status == "running":
            # A claimed step may still be executing in another request. Never replay it.
            return self._result(run)

        while run.current_step < len(definition.steps):
            step_definition = definition.steps[run.current_step]
            try:
                resolved_inputs = self._resolve_inputs(run, step_definition)
            except (TypeError, WorkflowInputError) as error:
                failed = self.store.fail_step(run.id, run.current_step, str(error))
                return self._result(failed)

            requirement = self._effective_approval(step_definition)
            approval = None
            if requirement != "none":
                approval = self.store.get_approval(
                    run.id,
                    run.current_step,
                    requirement,
                )
                if approval is None:
                    approval = self.store.request_approval(
                        run.id,
                        run.current_step,
                        requirement,
                    )
                    paused = self.store.get_run(run.id)
                    return self._result(paused, approval)
                if approval.status == "pending":
                    paused = self.store.get_run(run.id)
                    return self._result(paused, approval)
                if approval.status == "rejected":
                    rejected = self.store.get_run(run.id)
                    return self._result(rejected, approval)

            if self._is_side_effect(step_definition):
                if approval is None or approval.status != "approved":
                    raise WorkflowConflictError(
                        "side-effecting step does not have a persisted approval"
                    )
                step_run = self.store.get_step(run.id, run.current_step)
                if step_run is None:
                    raise KeyError("unknown workflow step")
                resolved_inputs["approved"] = True
                resolved_inputs["idempotency_key"] = (
                    f"{run.id}:{step_definition.id}:{step_run.attempt}"
                )

            try:
                run, _ = self.store.claim_step(
                    run.id,
                    run.current_step,
                    resolved_inputs,
                )
            except WorkflowConflictError:
                current = self.store.get_run(run.id)
                return self._result(current)

            try:
                raw_result = self.execute_skill(
                    step_definition.skill_id,
                    resolved_inputs,
                )
                if inspect.isawaitable(raw_result):
                    raise TypeError("execute_skill must be synchronous")
                output = json_mapping(dict(raw_result), "skill output")
                if output.get("success") is False:
                    raise RuntimeError(
                        str(output.get("error") or "Skill execution failed")
                    )
            except Exception as error:
                failed = self.store.fail_step(run.id, run.current_step, str(error))
                return self._result(failed)

            run = self.store.complete_step(
                run.id,
                run.current_step,
                step_definition.resolved_output_key,
                output,
            )
            if run.status == "succeeded":
                return self._result(run)

        return self._result(run)

    def decide_approval(
        self,
        run_id: str,
        step_index: int,
        *,
        decision: str,
        actor: str,
        actor_level: ApprovalRequirement = "user",
        note: str | None = None,
        auto_resume: bool = True,
    ) -> WorkflowExecutionResult:
        """Decide the current gate and optionally continue the run."""
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError("unknown workflow run")
        if step_index >= len(run.definition_snapshot.steps):
            raise IndexError("workflow step is out of range")
        requirement = self._effective_approval(
            run.definition_snapshot.steps[step_index]
        )
        if requirement == "none":
            raise ValueError("step does not require approval")
        approval = self.store.get_approval(run_id, step_index, requirement)
        if approval is None:
            raise KeyError("approval has not been requested")
        decided = self.store.decide_approval(
            approval.id,
            decision=decision,
            actor=actor,
            actor_level=actor_level,
            note=note,
        )
        current = self.store.get_run(run_id)
        if decision == "approved" and auto_resume:
            return self.resume(run_id)
        return self._result(current, decided)

    def cancel(self, run_id: str) -> WorkflowExecutionResult:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError("unknown workflow run")
        cancelled = self.store.cancel_run(run_id, expected_version=run.state_version)
        return self._result(cancelled)
