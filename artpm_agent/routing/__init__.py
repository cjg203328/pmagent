"""Intent routing boundary, isolated from the agent monolith."""

from .service import IntentDecision, IntentRouter

__all__ = ["IntentDecision", "IntentRouter"]
