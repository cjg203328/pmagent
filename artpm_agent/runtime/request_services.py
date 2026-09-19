"""Explicit request-service composition for agent and turn boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .service_ports import (
    ArtifactCoordinatorPort,
    AttachmentParser,
    ConsolidationSchedulerPort,
    EpisodeStorePort,
    EventBusPort,
    FeedbackStorePort,
    IntentRouterPort,
    KnowledgeStorePort,
    MemoryManagerPort,
    MetaMemoryStorePort,
    ModelGatewayPort,
    PermissionStorePort,
    ProfileStorePort,
    ReflectionSchedulerPort,
    SessionStorePort,
    SkillResultFormatter,
    SkillRouterPort,
    StrategyStorePort,
    TencentMemoryPort,
    VisionAttachmentPreparer,
    WorkflowCoordinatorPort,
    WorkflowResultFormatter,
)


@dataclass(frozen=True, slots=True)
class RequestServiceBundle:
    """Request dependencies grouped by responsibility.

    The bundle is intentionally small and provider-neutral. The orchestrator
    remains the compatibility facade, while new hosts can inspect or replace
    one request concern without rebuilding the whole agent.
    """

    intent_router: IntentRouterPort
    skill_router: SkillRouterPort
    model_gateway: ModelGatewayPort
    parse_attachments: AttachmentParser
    prepare_vision_attachments: VisionAttachmentPreparer
    format_skill_result: SkillResultFormatter

    def detect_intent(self, user_input: str) -> object:
        return self.intent_router.detect(user_input)

    def detect_intent_decision(self, user_input: str) -> object:
        detector = getattr(self.intent_router, "detect_decision", None)
        if callable(detector):
            return detector(user_input)
        return self.detect_intent(user_input)

    def execute_skill(self, skill_name: str, inputs: Mapping[str, Any]) -> object:
        return self.skill_router.execute_skill(skill_name, dict(inputs))

    def format_result(self, skill_name: str, result: Mapping[str, Any]) -> str:
        return self.format_skill_result(skill_name, dict(result))


@dataclass(frozen=True, slots=True)
class TurnServiceBundle:
    """Request-scoped dependencies consumed by the canonical turn pipeline.

    ``RequestServiceBundle`` above describes the agent facade's reusable
    capabilities. This bundle describes stateful services for one request so
    API and UI hosts cannot silently assemble different memory, approval, or
    persistence paths.
    """

    profile_store: ProfileStorePort | None = None
    knowledge_store: KnowledgeStorePort | None = None
    artifact_coordinator: ArtifactCoordinatorPort | None = None
    workflow_coordinator: WorkflowCoordinatorPort | None = None
    workflow_formatter: WorkflowResultFormatter | None = None
    feedback_store: FeedbackStorePort | None = None
    strategy_store: StrategyStorePort | None = None
    episode_store: EpisodeStorePort | None = None
    reflection_scheduler: ReflectionSchedulerPort | None = None
    consolidation_scheduler: ConsolidationSchedulerPort | None = None
    meta_memory_store: MetaMemoryStorePort | None = None
    memory_manager: MemoryManagerPort | None = None
    tencentdb_memory: TencentMemoryPort | None = None
    permission_store: PermissionStorePort | None = None
    event_bus: EventBusPort | None = None
    session_store: SessionStorePort | None = None
    outbox_store: Any | None = None  # OutboxStore for async learning tail

    def get(self, name: str, fallback: object = None) -> object:
        """Read an optional request service without reaching into ``extra``."""

        return getattr(self, name, fallback)


__all__ = ["RequestServiceBundle", "TurnServiceBundle"]
