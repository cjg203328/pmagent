"""Synchronous, resumable execution for validated linear workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Set
from datetime import datetime, timezone
import inspect
import re
from typing import Any

from .models import (
    ApprovalRequirement,
    ApprovalStatus,
    RetryErrorClass,
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
from artpm_agent.tenancy.scope import Scope


DEFAULT_CAPABILITY_ALLOWLIST = DEFAULT_SKILL_CAPABILITIES
DEFAULT_SIDE_EFFECT_CAPABILITIES = frozenset(
    capability
    for capability, rule in CAPABILITY_RISK_REGISTRY.items()
    if rule.side_effect
)


class WorkflowInputError(ValueError):
    """Raised when a declarative input reference cannot be resolved."""


def classify_workflow_error(error: BaseException) -> RetryErrorClass:
    """Map an execution error to a conservative retry class."""

    message = str(error).casefold()
    if isinstance(error, TimeoutError) or re.search(
        r"timeout|timed out|超时|超時", message
    ):
        return "timeout"
    if re.search(r"429|rate.?limit|too many requests|限流", message):
        return "rate_limit"
    if isinstance(error, ConnectionError) or re.search(
        r"connection reset|connection refused|network|temporary unavailable|网络|網路",
        message,
    ):
        return "network"
    if re.search(r"transient|temporar|暂时|暫時", message):
        return "transient"
    return "unknown"


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
        tenant_id: str | None = None,
        scope: Scope | None = None,
        stale_after_seconds: float = 300.0,
    ) -> None:
        if not callable(execute_skill):
            raise TypeError("execute_skill must be callable")
        if approval_floor not in {"none", "user", "admin"}:
            raise ValueError("unsupported approval floor")
        if (
            isinstance(stale_after_seconds, bool)
            or not isinstance(stale_after_seconds, (int, float))
            or stale_after_seconds < 0
        ):
            raise ValueError("stale_after_seconds must be a non-negative number")
        self.store = store
        self.execute_skill = execute_skill
        self.scope = scope
        self.stale_after_seconds = float(stale_after_seconds)
        source = capability_allowlist or DEFAULT_CAPABILITY_ALLOWLIST
        self.capability_allowlist: dict[str, Set[str]] = {
            skill_id: frozenset(capabilities)
            for skill_id, capabilities in source.items()
        }
        self.approval_floor = approval_floor
        self.risk_policy = risk_policy or DEFAULT_RISK_POLICY
        if scope is not None:
            if not isinstance(scope, Scope):
                raise TypeError("scope must be a Scope")
            if tenant_id not in (None, "", scope.tenant_id):
                raise ValueError("tenant_id conflicts with scope")
            tenant_id = scope.tenant_id
        self.tenant_id = tenant_id or "local"
        self.side_effect_capabilities = frozenset(
            DEFAULT_SIDE_EFFECT_CAPABILITIES
            | frozenset(side_effect_capabilities or ())
        )

    def _workspace_for_request(self, workspace_id: str | None) -> str | None:
        if self.scope is None:
            return workspace_id
        if workspace_id not in (None, self.scope.workspace_id):
            raise PermissionError("workflow request is outside the engine workspace")
        return self.scope.workspace_id

    def _result(
        self,
        run: WorkflowRun,
        approval: WorkflowApproval | None = None,
    ) -> WorkflowExecutionResult:
        return WorkflowExecutionResult(
            run=run,
            steps=tuple(
                self.store.list_steps(
                    run.id,
                    workspace_id=run.workspace_id,
                    tenant_id=run.tenant_id,
                )
            ),
            approval=approval,
        )

    def result(
        self,
        run_id: str,
        *,
        workspace_id: str | None = None,
    ) -> WorkflowExecutionResult:
        """Return the current persisted result without advancing the run."""
        workspace_id = self._workspace_for_request(workspace_id)
        run = self.store.get_run(
            run_id, workspace_id=workspace_id, tenant_id=self.tenant_id
        )
        if run is None:
            raise KeyError("unknown workflow run")
        approvals = self.store.list_approvals(
            run_id, workspace_id=run.workspace_id, tenant_id=run.tenant_id
        )
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
        if definition.tenant_id != self.tenant_id:
            raise PermissionError("workflow definition is outside the engine tenant")
        self._workspace_for_request(definition.workspace_id)
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
        return (
            self.resume(run.id, workspace_id=definition.workspace_id)
            if auto_resume
            else self._result(run)
        )

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

    @staticmethod
    def _retry_delay(step: WorkflowStepDefinition, attempt: int) -> float:
        base = float(step.retry_backoff_seconds)
        if step.retry_backoff == "none" or base <= 0:
            return 0.0
        if step.retry_backoff == "fixed":
            return min(300.0, base)
        return min(300.0, base * (2 ** max(0, attempt - 1)))

    def _can_retry(
        self,
        step: WorkflowStepDefinition,
        step_run: Any,
        error_class: RetryErrorClass,
    ) -> bool:
        return (
            step.on_error == "retry"
            and step.retryable
            and step.idempotent
            and int(step_run.attempt) <= step.max_retries
            and error_class in step.retry_on
        )

    @staticmethod
    def _retry_is_due(next_retry_at: str | None) -> bool:
        if not next_retry_at:
            return True
        try:
            retry_at = datetime.fromisoformat(next_retry_at.replace("Z", "+00:00"))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return False
        return retry_at <= datetime.now(timezone.utc)

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

    def resume(
        self,
        run_id: str,
        *,
        workspace_id: str | None = None,
    ) -> WorkflowExecutionResult:
        """Advance until completion, approval, failure, or a concurrent claim."""
        workspace_id = self._workspace_for_request(workspace_id)
        run = self.store.get_run(
            run_id, workspace_id=workspace_id, tenant_id=self.tenant_id
        )
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
                    error_class="unknown",
                    compensation_skill_id=definition.steps[run.current_step].compensation_skill_id,
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                )
                return self._result(failed)
            return self._result(run)

        if run.status == "running":
            active_step = (
                definition.steps[run.current_step]
                if run.current_step < len(definition.steps)
                else None
            )
            recovered = self.store.recover_stale_run(
                run.id,
                stale_after_seconds=self.stale_after_seconds,
                allow_stale_retry=(
                    active_step is not None and not self._is_side_effect(active_step)
                ),
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
            )
            if recovered is None:
                # A claimed step may still be executing in another request.
                # Never replay it before its lease is demonstrably stale.
                return self._result(run)
            run = recovered
            if run.status in {"succeeded", "failed", "cancelled"}:
                return self._result(run)

        while run.current_step < len(definition.steps):
            step_definition = definition.steps[run.current_step]
            step_run = self.store.get_step(
                run.id,
                run.current_step,
                workspace_id=run.workspace_id,
                tenant_id=run.tenant_id,
            )
            if step_run is None:
                raise KeyError("unknown workflow step")
            if not self._retry_is_due(step_run.next_retry_at):
                return self._result(run)
            try:
                resolved_inputs = self._resolve_inputs(run, step_definition)
            except (TypeError, WorkflowInputError) as error:
                failed = self.store.fail_step(
                    run.id,
                    run.current_step,
                    str(error),
                    error_class=classify_workflow_error(error),
                    compensation_skill_id=step_definition.compensation_skill_id,
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                )
                return self._result(failed)

            requirement = self._effective_approval(step_definition)
            approval = None
            if requirement != "none":
                approval = self.store.get_approval(
                    run.id,
                    run.current_step,
                    requirement,
                    workspace_id=run.workspace_id,
                    tenant_id=run.tenant_id,
                )
                if approval is None:
                    approval = self.store.request_approval(
                        run.id,
                        run.current_step,
                        requirement,
                        tenant_id=run.tenant_id,
                        workspace_id=run.workspace_id,
                    )
                    paused = self.store.get_run(
                        run.id,
                        workspace_id=run.workspace_id,
                        tenant_id=run.tenant_id,
                    )
                    return self._result(paused, approval)
                if approval.status == "pending":
                    paused = self.store.get_run(
                        run.id,
                        workspace_id=run.workspace_id,
                        tenant_id=run.tenant_id,
                    )
                    return self._result(paused, approval)
                if approval.status == "rejected":
                    rejected = self.store.get_run(
                        run.id,
                        workspace_id=run.workspace_id,
                        tenant_id=run.tenant_id,
                    )
                    return self._result(rejected, approval)

            if self._is_side_effect(step_definition):
                if approval is None or approval.status != "approved":
                    raise WorkflowConflictError(
                        "side-effecting step does not have a persisted approval"
                    )
                resolved_inputs["approved"] = True
                resolved_inputs["idempotency_key"] = (
                    f"{run.id}:{step_definition.id}:idempotent"
                )

            try:
                run, step_run = self.store.claim_step(
                    run.id,
                    run.current_step,
                    resolved_inputs,
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                )
            except WorkflowConflictError:
                current = self.store.get_run(
                    run.id,
                    workspace_id=run.workspace_id,
                    tenant_id=run.tenant_id,
                )
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
                error_class = classify_workflow_error(error)
                if self._can_retry(step_definition, step_run, error_class):
                    delay = self._retry_delay(step_definition, step_run.attempt)
                    try:
                        retry_run = self.store.schedule_retry(
                            run.id,
                            run.current_step,
                            str(error),
                            error_class=error_class,
                            tenant_id=run.tenant_id,
                            workspace_id=run.workspace_id,
                            backoff_seconds=delay,
                        )
                    except WorkflowConflictError:
                        current = self.store.get_run(
                            run.id,
                            workspace_id=run.workspace_id,
                            tenant_id=run.tenant_id,
                        )
                        return self._result(current)
                    if delay <= 0:
                        run = retry_run
                        continue
                    return self._result(retry_run)
                failed = self.store.fail_step(
                    run.id,
                    run.current_step,
                    str(error),
                    error_class=error_class,
                    compensation_skill_id=step_definition.compensation_skill_id,
                    tenant_id=run.tenant_id,
                    workspace_id=run.workspace_id,
                )
                return self._result(failed)

            run = self.store.complete_step(
                run.id,
                run.current_step,
                step_definition.resolved_output_key,
                output,
                tenant_id=run.tenant_id,
                workspace_id=run.workspace_id,
            )
            if run.status == "succeeded":
                return self._result(run)

        return self._result(run)

    def decide_approval(
        self,
        run_id: str,
        step_index: int,
        *,
        decision: ApprovalStatus,
        actor: str,
        actor_level: ApprovalRequirement = "user",
        note: str | None = None,
        auto_resume: bool = True,
        workspace_id: str | None = None,
    ) -> WorkflowExecutionResult:
        """Decide the current gate and optionally continue the run."""
        workspace_id = self._workspace_for_request(workspace_id)
        run = self.store.get_run(
            run_id,
            workspace_id=workspace_id,
            tenant_id=self.tenant_id,
        )
        if run is None:
            raise KeyError("unknown workflow run")
        if step_index >= len(run.definition_snapshot.steps):
            raise IndexError("workflow step is out of range")
        requirement = self._effective_approval(
            run.definition_snapshot.steps[step_index]
        )
        if requirement == "none":
            raise ValueError("step does not require approval")
        approval = self.store.get_approval(
            run_id,
            step_index,
            requirement,
            workspace_id=run.workspace_id,
            tenant_id=run.tenant_id,
        )
        if approval is None:
            raise KeyError("approval has not been requested")
        decided = self.store.decide_approval(
            approval.id,
            decision=decision,
            actor=actor,
            actor_level=actor_level,
            note=note,
            workspace_id=run.workspace_id,
            tenant_id=run.tenant_id,
        )
        current = self.store.get_run(
            run_id,
            workspace_id=run.workspace_id,
            tenant_id=run.tenant_id,
        )
        if decision == "approved" and auto_resume:
            return self.resume(run_id, workspace_id=run.workspace_id)
        return self._result(current, decided)

    def cancel(
        self,
        run_id: str,
        *,
        workspace_id: str | None = None,
    ) -> WorkflowExecutionResult:
        workspace_id = self._workspace_for_request(workspace_id)
        run = self.store.get_run(run_id, workspace_id=workspace_id)
        if run is None:
            raise KeyError("unknown workflow run")
        cancelled = self.store.cancel_run(
            run_id,
            expected_version=run.state_version,
            tenant_id=run.tenant_id,
            workspace_id=run.workspace_id,
        )
        return self._result(cancelled)
