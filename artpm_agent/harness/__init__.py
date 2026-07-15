"""
Application harness - Phase 3 of Pi architecture adoption.

This module orchestrates the turn lifecycle (Profile/Knowledge/Artifact/Skill/Model)
above the provider-neutral agent loop. It owns session assembly, resource loading,
and persistence coordination without embedding them in the UI or agent core.

Refs: PI_ARCHITECTURE_ADOPTION.md Phase 3
"""

from .turn_service import TurnContext, TurnResult, run_turn
from .profile_handler import try_profile_proposal
from .knowledge_handler import try_knowledge_ingestion, is_knowledge_ingestion_request
from .knowledge_rule_handler import try_knowledge_rule_proposal
from .artifact_handler import try_artifact_generation
from .workflow_handler import try_workflow_routing
from .skill_handler import try_skill_routing
from .model_handler import fallback_to_model
from .agent_session import AgentSession, ModelToolCallsDisabledError

__all__ = [
    "TurnContext",
    "TurnResult",
    "run_turn",
    "try_profile_proposal",
    "try_knowledge_ingestion",
    "is_knowledge_ingestion_request",
    "try_knowledge_rule_proposal",
    "try_artifact_generation",
    "try_workflow_routing",
    "try_skill_routing",
    "fallback_to_model",
    "AgentSession",
    "ModelToolCallsDisabledError",
]
