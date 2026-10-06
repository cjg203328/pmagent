"""Public request models for the ArtPM REST gateway.

The gateway deliberately keeps its HTTP contracts separate from the internal
workflow and harness models.  In particular, tenant and actor identity are
never accepted in these request bodies; they are resolved by the trusted
gateway boundary.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from artpm_agent.utils.chat_attachments import (
    DEFAULT_ALLOWED_EXTENSIONS,
    DEFAULT_MAX_FILE_SIZE,
    DEFAULT_MAX_TOTAL_SIZE,
)


def _attachment_extension(name: str) -> str:
    """Return the lower-case extension of one attachment name.

    Both separators are honored so a browser upload that keeps a Windows-style
    display name (``report.pdf`` behind ``C:\\fakepath\\report.pdf``) still
    validates against the same whitelist as the storage layer.
    """
    normalized = name.replace("\\", "/")
    leaf = PurePosixPath(normalized).name or PureWindowsPath(normalized).name
    if "." not in leaf:
        return ""
    return leaf.rsplit(".", 1)[-1].strip().lower()


class StrictRequest(BaseModel):
    """Reject unknown fields so clients cannot smuggle authorization data."""

    model_config = ConfigDict(extra="forbid", strict=True)


class AttachmentInput(StrictRequest):
    """Attachment metadata accepted by the API.

    A request cannot provide a local path, command, or arbitrary executable
    object here.  Files are uploaded first through the conversation-scoped
    attachment endpoint, which returns an ``attachment_id``; chat requests
    reference that id.  The same whitelist and size limits as the storage
    layer are enforced up front so an unsupported file is rejected with a
    precise reason instead of a generic failure later in the pipeline.
    """

    name: str = Field(min_length=1, max_length=256)
    media_type: str | None = Field(default=None, max_length=128)
    size_bytes: int | None = Field(default=None, ge=0, le=DEFAULT_MAX_FILE_SIZE)
    attachment_id: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("attachment name must be a valid non-empty string")
        return value

    @field_validator("attachment_id")
    @classmethod
    def normalize_attachment_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        return value or None

    @model_validator(mode="after")
    def validate_supported_type(self) -> "AttachmentInput":
        extension = _attachment_extension(self.name)
        if not extension:
            raise ValueError("attachment must have a file extension")
        if extension not in DEFAULT_ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(DEFAULT_ALLOWED_EXTENSIONS))
            raise ValueError(
                f"unsupported attachment type: .{extension} (allowed: {allowed})"
            )
        return self


class ChatRequest(StrictRequest):
    """One chat turn."""

    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=256)
    title: str | None = Field(default=None, max_length=80)
    # Keep the HTTP contract aligned with the attachment pipeline's own
    # ``max_files`` (3). A higher limit here would accept requests the
    # pipeline then rejects mid-turn.
    attachments: list[AttachmentInput] = Field(default_factory=list, max_length=3)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value

    @field_validator("conversation_id", "title")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def validate_attachment_batch(self) -> "ChatRequest":
        total = sum(
            item.size_bytes or 0 for item in self.attachments
        )
        if total > DEFAULT_MAX_TOTAL_SIZE:
            raise ValueError(
                "attachments exceed the total size limit of "
                f"{DEFAULT_MAX_TOTAL_SIZE} bytes"
            )
        return self


class ConversationUpdateRequest(StrictRequest):
    """Rename and/or (un)archive one conversation."""

    title: str | None = Field(default=None, max_length=80)
    archived: bool | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def require_one_change(self) -> "ConversationUpdateRequest":
        if self.title is None and self.archived is None:
            raise ValueError("provide title and/or archived")
        return self


class AccessModeRequest(StrictRequest):
    """Switch one conversation between read-only, controlled and full access."""

    mode: str = Field(pattern="^(read_only|controlled|full_access)$")


class ConfigUpdateRequest(StrictRequest):
    """One batch of allowlisted ``.env`` changes from a human operator.

    Key names are validated for shape here; the authoritative allowlist lives in
    ``artpm_agent.api.config_admin`` so this model can never widen the set of
    writable environment variables.
    """

    values: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def validate_values(
        cls, value: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        if not value:
            raise ValueError("values must contain at least one entry")
        for key in value:
            if not isinstance(key, str) or not key.strip():
                raise ValueError("configuration keys must be non-empty strings")
            if not key.replace("_", "").isalnum() or not key[0].isalpha():
                raise ValueError(f"invalid configuration key: {key}")
        return value


class PermissionDecisionRequest(StrictRequest):
    """CAS version supplied by the UI/client for one approval decision."""

    expected_version: int = Field(ge=0, le=2**31 - 1)
    acknowledged_risk: str | None = Field(
        default=None,
        pattern="^(high|critical|untrusted)$",
    )


class VoiceSessionRequest(StrictRequest):
    """Create one tenant-bound real-time voice session."""

    conversation_id: str = Field(min_length=1, max_length=256)

    @field_validator("conversation_id")
    @classmethod
    def normalize_conversation_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("conversation_id must not be blank")
        return value


class WorkflowTriggerRequest(StrictRequest):
    keywords: list[str] = Field(default_factory=list, max_length=20)
    required_context_keys: list[str] = Field(default_factory=list, max_length=20)
    attachment_extensions: list[str] = Field(default_factory=list, max_length=20)
    project_statuses: list[str] = Field(default_factory=list, max_length=20)
    requires_attachment: bool = False
    always: bool = False
    min_keyword_matches: int = Field(default=1, ge=0, le=20)


class WorkflowStepRequest(StrictRequest):
    id: str = Field(min_length=1, max_length=128)
    skill_id: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=1, max_length=256)
    input_map: dict[str, Any] = Field(default_factory=dict)
    output_key: str | None = Field(default=None, max_length=128)
    side_effect: bool = False
    approval: str = Field(default="none", pattern="^(none|user|admin)$")
    on_error: str = Field(default="stop", pattern="^stop$")


class WorkflowDefinitionRequest(StrictRequest):
    """Tenant-owned workflow definition.

    ``workspace_id``, ``profile_id`` and ``source`` are omitted on purpose;
    the server assigns them from the authenticated principal and route.
    """

    id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1, le=2**31 - 1)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    read_only: bool
    enabled: bool = True
    priority: int = Field(default=0, ge=-1000, le=1000)
    trigger: WorkflowTriggerRequest = Field(default_factory=WorkflowTriggerRequest)
    steps: list[WorkflowStepRequest] = Field(min_length=1, max_length=8)


class WorkflowRunRequest(StrictRequest):
    """Start one persisted workflow run."""

    conversation_id: str = Field(min_length=1, max_length=256)
    input_data: dict[str, Any] = Field(default_factory=dict)
    context_data: dict[str, Any] = Field(default_factory=dict)
    turn_id: str | None = Field(default=None, max_length=256)
    idempotency_key: str | None = Field(default=None, max_length=512)
    version: int | None = Field(default=None, ge=1)
    auto_resume: bool = True


class WorkflowApprovalRequest(StrictRequest):
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


__all__ = [
    "AccessModeRequest",
    "AttachmentInput",
    "ChatRequest",
    "ConversationUpdateRequest",
    "PermissionDecisionRequest",
    "VoiceSessionRequest",
    "WorkflowApprovalRequest",
    "WorkflowDefinitionRequest",
    "WorkflowRunRequest",
    "WorkflowStepRequest",
    "WorkflowTriggerRequest",
]
