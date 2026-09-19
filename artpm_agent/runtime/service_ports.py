"""Protocol contracts for request-scoped runtime and Harness services."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from artpm_agent.runtime.events import AgentEvent


class IntentRouterPort(Protocol):
    def detect(self, user_input: str) -> object: ...


class SkillRouterPort(Protocol):
    def execute_skill(
        self,
        skill_name: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]: ...


class ModelGatewayPort(Protocol):
    def chat_with_failover(
        self,
        prompt: str,
        system_prompt: str,
        history: Any,
        image_paths: list[str] | None = None,
        *,
        task_type: str | None = None,
        cache_scope: str = "local:default",
    ) -> str: ...


class ProfileStorePort(Protocol):
    def propose_change(self, *args: object, **kwargs: object) -> object: ...


class KnowledgeStorePort(Protocol):
    def propose_ingestion(self, *args: object, **kwargs: object) -> object: ...

    def propose_rule(self, *args: object, **kwargs: object) -> object: ...

    def search(self, *args: object, **kwargs: object) -> object: ...


class ArtifactOutcomePort(Protocol):
    matched: bool
    artifact: Mapping[str, Any] | None
    message: str


class ArtifactCoordinatorPort(Protocol):
    def process(
        self,
        *args: object,
        **kwargs: object,
    ) -> ArtifactOutcomePort: ...


class WorkflowCoordinatorPort(Protocol):
    def process(self, *args: object, **kwargs: object) -> object: ...


class ActiveEntryStorePort(Protocol):
    def active(self, *args: object, **kwargs: object) -> Sequence[object]: ...


class FeedbackStorePort(ActiveEntryStorePort, Protocol):
    def add(self, *args: object, **kwargs: object) -> object: ...


class StrategyStorePort(ActiveEntryStorePort, Protocol):
    pass


class EpisodeStorePort(Protocol):
    def record(self, episode: object) -> str: ...

    def set_feedback(self, *args: object, **kwargs: object) -> object: ...

    def count(self, *args: object, **kwargs: object) -> int: ...


class ReflectionSchedulerPort(Protocol):
    def run_if_due(self, *args: object, **kwargs: object) -> object: ...


class ConsolidationSchedulerPort(Protocol):
    def run_if_due(self, *args: object, **kwargs: object) -> object: ...


class MetaMemoryStorePort(Protocol):
    def list_gaps(self, *args: object, **kwargs: object) -> Sequence[object]: ...


class MemoryManagerPort(Protocol):
    def retrieve(self, *args: object, **kwargs: object) -> object: ...


class TencentMemoryPort(Protocol):
    def recall(self, *args: object, **kwargs: object) -> object: ...

    def capture(self, *args: object, **kwargs: object) -> object: ...


class PermissionStorePort(Protocol):
    def create_request(self, *args: object, **kwargs: object) -> object: ...


class EventBusPort(Protocol):
    def publish(self, event: AgentEvent) -> object: ...


class SessionStorePort(Protocol):
    def append_event(self, event: Mapping[str, Any]) -> object: ...


AttachmentParser = Callable[
    [str, Mapping[str, Any]],
    tuple[list[dict[str, Any]], str],
]
VisionAttachmentPreparer = Callable[
    [list[Mapping[str, Any]], bool],
    AbstractContextManager[list[str]],
]
SkillResultFormatter = Callable[[str, dict[str, Any]], str]
WorkflowResultFormatter = Callable[..., str]


__all__ = [
    "ActiveEntryStorePort",
    "ArtifactCoordinatorPort",
    "AttachmentParser",
    "ConsolidationSchedulerPort",
    "EpisodeStorePort",
    "EventBusPort",
    "FeedbackStorePort",
    "IntentRouterPort",
    "KnowledgeStorePort",
    "MemoryManagerPort",
    "MetaMemoryStorePort",
    "ModelGatewayPort",
    "PermissionStorePort",
    "ProfileStorePort",
    "ReflectionSchedulerPort",
    "SessionStorePort",
    "SkillResultFormatter",
    "SkillRouterPort",
    "StrategyStorePort",
    "TencentMemoryPort",
    "VisionAttachmentPreparer",
    "WorkflowCoordinatorPort",
    "WorkflowResultFormatter",
]
