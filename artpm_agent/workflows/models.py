"""Strict data contracts for the declarative workflow runtime."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)


DEFAULT_WORKSPACE_ID = "local-default"
DEFAULT_PROFILE_ID = "local-default"
MAX_WORKFLOW_STEPS = 8

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]

ApprovalRequirement = Literal["none", "user", "admin"]
ApprovalStatus = Literal["pending", "approved", "rejected"]
RunStatus = Literal[
    "pending",
    "awaiting_approval",
    "running",
    "succeeded",
    "failed",
    "cancelled",
]
StepStatus = Literal[
    "pending",
    "awaiting_approval",
    "running",
    "succeeded",
    "failed",
    "skipped",
]

APPROVAL_RANK: dict[ApprovalRequirement, int] = {
    "none": 0,
    "user": 1,
    "admin": 2,
}


def strongest_approval(
    first: ApprovalRequirement,
    second: ApprovalRequirement,
) -> ApprovalRequirement:
    """Return the stronger of two approval requirements."""
    return first if APPROVAL_RANK[first] >= APPROVAL_RANK[second] else second


class StrictModel(BaseModel):
    """Shared Pydantic v2 policy for persisted runtime contracts."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class WorkflowTrigger(StrictModel):
    """Deterministic matching rules; no executable expressions are accepted."""

    keywords: tuple[NonEmptyText, ...] = ()
    required_context_keys: tuple[Identifier, ...] = ()
    attachment_extensions: tuple[Identifier, ...] = ()
    project_statuses: tuple[NonEmptyText, ...] = ()
    requires_attachment: bool = False
    always: bool = False
    min_keyword_matches: int = Field(default=1, ge=0, le=20)

    @model_validator(mode="after")
    def validate_trigger(self) -> WorkflowTrigger:
        if self.min_keyword_matches > len(self.keywords) and self.keywords:
            raise ValueError("min_keyword_matches cannot exceed keyword count")
        return self


class WorkflowStepDefinition(StrictModel):
    """One allowlisted Skill invocation in a linear workflow."""

    id: Identifier
    skill_id: Identifier
    capability: Identifier
    input_map: dict[Identifier, JsonValue] = Field(default_factory=dict)
    output_key: Identifier | None = None
    side_effect: bool = False
    approval: ApprovalRequirement = "none"
    on_error: Literal["stop"] = "stop"

    @property
    def resolved_output_key(self) -> str:
        return self.output_key or self.id


class WorkflowDefinition(StrictModel):
    """Immutable, versioned workflow definition."""

    id: Identifier
    version: int = Field(ge=1)
    name: NonEmptyText
    description: NonEmptyText
    workspace_id: Identifier = DEFAULT_WORKSPACE_ID
    profile_id: Identifier = DEFAULT_PROFILE_ID
    source: Literal["builtin", "custom"]
    read_only: bool
    enabled: bool = True
    priority: int = Field(default=0, ge=-1000, le=1000)
    trigger: WorkflowTrigger = Field(default_factory=WorkflowTrigger)
    steps: tuple[WorkflowStepDefinition, ...] = Field(
        min_length=1,
        max_length=MAX_WORKFLOW_STEPS,
    )

    @model_validator(mode="after")
    def validate_definition(self) -> WorkflowDefinition:
        if self.source == "builtin" and not self.read_only:
            raise ValueError("builtin workflows must be read-only")
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("workflow step IDs must be unique")
        output_keys = [step.resolved_output_key for step in self.steps]
        if len(output_keys) != len(set(output_keys)):
            raise ValueError("workflow output keys must be unique")
        return self


class WorkflowOverride(StrictModel):
    """Workspace/profile activation settings without mutating a definition."""

    workflow_id: Identifier
    workflow_version: int = Field(ge=1)
    workspace_id: Identifier = DEFAULT_WORKSPACE_ID
    profile_id: Identifier = DEFAULT_PROFILE_ID
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=-1000, le=1000)

    @model_validator(mode="after")
    def require_an_override(self) -> WorkflowOverride:
        if self.enabled is None and self.priority is None:
            raise ValueError("override must change enabled or priority")
        return self


class AttachmentSelectionData(StrictModel):
    """Non-executable attachment facts used during workflow selection."""

    extension: Identifier
    document_type: NonEmptyText | None = None


class WorkflowSelectionContext(StrictModel):
    """Bounded facts that the deterministic selector may inspect."""

    prompt: str = Field(max_length=8000)
    conversation_id: Identifier
    workspace_id: Identifier = DEFAULT_WORKSPACE_ID
    profile_id: Identifier = DEFAULT_PROFILE_ID
    explicit_workflow_id: Identifier | None = None
    explicit_workflow_version: int | None = Field(default=None, ge=1)
    project_status: NonEmptyText | None = None
    attachments: tuple[AttachmentSelectionData, ...] = ()
    context_data: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def explicit_version_needs_id(self) -> WorkflowSelectionContext:
        if (
            self.explicit_workflow_version is not None
            and self.explicit_workflow_id is None
        ):
            raise ValueError("explicit_workflow_version requires explicit_workflow_id")
        return self


class WorkflowDecision(StrictModel):
    """Selector output; it can only reference a supplied definition."""

    definition: WorkflowDefinition | None = None
    score: float = Field(ge=0)
    explicit: bool = False
    reasons: tuple[str, ...] = ()


class WorkflowRun(StrictModel):
    """Persisted run including the exact resolved definition snapshot."""

    id: Identifier
    idempotency_key: Identifier
    workflow_id: Identifier
    workflow_version: int = Field(ge=1)
    workspace_id: Identifier
    profile_id: Identifier
    conversation_id: Identifier
    turn_id: Identifier | None = None
    status: RunStatus
    state_version: int = Field(ge=0)
    current_step: int = Field(ge=0, le=MAX_WORKFLOW_STEPS)
    definition_snapshot: WorkflowDefinition
    input_data: dict[str, JsonValue] = Field(default_factory=dict)
    context_data: dict[str, JsonValue] = Field(default_factory=dict)
    outputs: dict[str, JsonValue] = Field(default_factory=dict)
    error: str | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None


class WorkflowStepRun(StrictModel):
    """Persisted execution state for one workflow step."""

    run_id: Identifier
    step_index: int = Field(ge=0, lt=MAX_WORKFLOW_STEPS)
    step_id: Identifier
    skill_id: Identifier
    status: StepStatus
    state_version: int = Field(ge=0)
    attempt: int = Field(ge=1)
    input_data: dict[str, JsonValue] = Field(default_factory=dict)
    output_data: dict[str, JsonValue] = Field(default_factory=dict)
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class WorkflowApproval(StrictModel):
    """Persisted approval decision for a side-effecting step."""

    id: Identifier
    run_id: Identifier
    step_index: int = Field(ge=0, lt=MAX_WORKFLOW_STEPS)
    requirement: ApprovalRequirement
    status: ApprovalStatus
    actor: str | None = None
    actor_level: ApprovalRequirement | None = None
    note: str | None = Field(default=None, max_length=2000)
    created_at: str
    decided_at: str | None = None


class WorkflowEvent(StrictModel):
    """Append-only audit event."""

    id: int = Field(ge=1)
    run_id: Identifier
    event_type: Identifier
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: str


class WorkflowExecutionResult(StrictModel):
    """Engine response after starting, resuming, or approving a run."""

    run: WorkflowRun
    steps: tuple[WorkflowStepRun, ...]
    approval: WorkflowApproval | None = None


def json_mapping(value: Any, field: str) -> dict[str, JsonValue]:
    """Validate arbitrary callable output as a strict JSON object."""
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be a dictionary")
    return value
