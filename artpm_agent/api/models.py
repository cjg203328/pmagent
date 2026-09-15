"""Public request models for the ArtPM REST gateway.

The gateway deliberately keeps its HTTP contracts separate from the internal
workflow and harness models.  In particular, tenant and actor identity are
never accepted in these request bodies; they are resolved by the trusted
gateway boundary.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictRequest(BaseModel):
    """Reject unknown fields so clients cannot smuggle authorization data."""

    model_config = ConfigDict(extra="forbid", strict=True)


class AttachmentInput(StrictRequest):
    """Descriptive attachment metadata accepted by the API.

    File upload/storage is intentionally a separate concern.  A request cannot
    provide a local path, command, or arbitrary executable object here.
    """

    name: str = Field(min_length=1, max_length=256)
    media_type: str | None = Field(default=None, max_length=128)
    size_bytes: int | None = Field(default=None, ge=0, le=100 * 1024 * 1024)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("attachment name must be a valid non-empty string")
        return value


class ChatRequest(StrictRequest):
    """One chat turn."""

    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=256)
    title: str | None = Field(default=None, max_length=80)
    attachments: list[AttachmentInput] = Field(default_factory=list, max_length=16)

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


class KnowledgeSearchRequest(StrictRequest):
    """Standalone workspace-scoped retrieval request."""

    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=10, ge=1, le=100)
    resource_types: list[str] = Field(default_factory=list, max_length=20)
    source_types: list[str] = Field(default_factory=list, max_length=20)
    include_rules: bool = True
    max_text_chars: int = Field(default=4000, ge=100, le=20_000)
    confidence_floor: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("resource_types", "source_types")
    @classmethod
    def normalize_filters(cls, values: list[str]) -> list[str]:
        normalized = [item.strip() for item in values if item.strip()]
        return list(dict.fromkeys(normalized))


class ChatStreamRequest(ChatRequest):
    """One chat turn opened as a resumable Server-Sent Event stream."""

    run_id: str | None = Field(default=None, max_length=256)
    turn_id: str | None = Field(default=None, max_length=256)

    @field_validator("run_id", "turn_id")
    @classmethod
    def normalize_stream_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class WorkspaceCreateRequest(StrictRequest):
    """Create a workspace inside the authenticated tenant."""

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=80)
    profile_id: str = Field(default="local-default", min_length=1, max_length=128)
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "name", "profile_id")
    @classmethod
    def normalize_workspace_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("workspace fields must not be blank")
        return value


class EmbedExchangeRequest(StrictRequest):
    """Exchange a server-held publish token for a short-lived token."""

    origin: str = Field(min_length=8, max_length=2048)
    publish_token: str = Field(min_length=1, max_length=4096)


class EmbedSessionRequest(StrictRequest):
    """Create a browser session bound to one exact origin."""

    origin: str = Field(min_length=8, max_length=2048)
    exchange_token: str = Field(min_length=1, max_length=8192)
    conversation_id: str | None = Field(default=None, max_length=256)

    @field_validator("conversation_id")
    @classmethod
    def normalize_conversation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class EmbedChatRequest(StrictRequest):
    """One bounded chat turn from an embed session."""

    origin: str = Field(min_length=8, max_length=2048)
    session_token: str = Field(min_length=1, max_length=8192)
    message: str = Field(min_length=1, max_length=8000)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
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
    "AttachmentInput",
    "ChatRequest",
    "ChatStreamRequest",
    "EmbedChatRequest",
    "EmbedExchangeRequest",
    "EmbedSessionRequest",
    "KnowledgeSearchRequest",
    "PermissionDecisionRequest",
    "VoiceSessionRequest",
    "WorkspaceCreateRequest",
    "WorkflowApprovalRequest",
    "WorkflowDefinitionRequest",
    "WorkflowRunRequest",
    "WorkflowStepRequest",
    "WorkflowTriggerRequest",
]
