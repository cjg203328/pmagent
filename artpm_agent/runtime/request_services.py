"""Explicit request-service composition for agent and turn boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True, slots=True)
class RequestServiceBundle:
    """Request dependencies grouped by responsibility.

    The bundle is intentionally small and provider-neutral. The orchestrator
    remains the compatibility facade, while new hosts can inspect or replace
    one request concern without rebuilding the whole agent.
    """

    intent_router: Any
    skill_router: Any
    model_gateway: Any
    parse_attachments: Callable[[str, Mapping[str, Any]], Any]
    prepare_vision_attachments: Callable[[Any, bool], Any]
    format_skill_result: Callable[[str, dict[str, Any]], str]

    def detect_intent(self, user_input: str) -> Any:
        return self.intent_router.detect(user_input)

    def detect_intent_decision(self, user_input: str) -> Any:
        detector = getattr(self.intent_router, "detect_decision", None)
        if callable(detector):
            return detector(user_input)
        return self.detect_intent(user_input)

    def execute_skill(self, skill_name: str, inputs: Mapping[str, Any]) -> Any:
        return self.skill_router.execute_skill(skill_name, dict(inputs))

    def format_result(self, skill_name: str, result: dict[str, Any]) -> str:
        return self.format_skill_result(skill_name, result)


@dataclass(frozen=True, slots=True)
class TurnServiceBundle:
    """Request-scoped dependencies consumed by the canonical turn pipeline.

    ``RequestServiceBundle`` above describes the agent facade's reusable
    capabilities. This bundle describes stateful services for one request so
    API and UI hosts cannot silently assemble different memory, approval, or
    persistence paths.
    """

    profile_store: Any = None
    knowledge_store: Any = None
    artifact_coordinator: Any = None
    workflow_coordinator: Any = None
    workflow_formatter: Callable[[Any, Any], str] | None = None
    feedback_store: Any = None
    strategy_store: Any = None
    episode_store: Any = None
    reflection_scheduler: Any = None
    consolidation_scheduler: Any = None
    meta_memory_store: Any = None
    memory_manager: Any = None
    tencentdb_memory: Any = None
    permission_store: Any = None
    event_bus: Any = None
    session_store: Any = None

    def get(self, name: str, fallback: Any = None) -> Any:
        """Read an optional request service without reaching into ``extra``."""

        return getattr(self, name, fallback)


__all__ = ["RequestServiceBundle", "TurnServiceBundle"]
