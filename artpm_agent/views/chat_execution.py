"""Canonical Harness execution boundary for the Streamlit chat host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from artpm_agent.internal.chat_harness_integration import execute_turn_with_harness


@dataclass(frozen=True, slots=True)
class HarnessTurnServices:
    profile_store: Any
    knowledge_store: Any
    artifact_coordinator: Any
    knowledge_rule_extractor: Any
    workflow_coordinator: Any
    workflow_formatter: Any
    session_store: Any
    event_bus: Any
    episode_store: Any = None
    consolidation_scheduler: Any = None


def execute_chat_turn(
    agent: Any,
    prompt: str,
    turn_id: str,
    conversation_id: str,
    agent_context: dict[str, Any],
    attachments: list[dict[str, Any]],
    file_paths: list[str],
    services: HarnessTurnServices,
):
    """Execute exactly one turn through the canonical Harness adapter."""
    return execute_turn_with_harness(
        agent,
        prompt,
        turn_id,
        conversation_id,
        agent_context,
        attachments,
        file_paths,
        services.profile_store,
        services.knowledge_store,
        services.artifact_coordinator,
        knowledge_rule_extractor=services.knowledge_rule_extractor,
        workflow_coordinator=services.workflow_coordinator,
        workflow_formatter=services.workflow_formatter,
        session_store=services.session_store,
        event_bus=services.event_bus,
        episode_store=services.episode_store,
        consolidation_scheduler=services.consolidation_scheduler,
    )


__all__ = ["HarnessTurnServices", "execute_chat_turn"]
