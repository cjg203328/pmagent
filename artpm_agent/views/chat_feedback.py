"""Feedback-specific pure contracts for the chat page."""

from collections.abc import Mapping
from typing import Any

from .chat_state import feedback_was_saved, preceding_user_prompt


def feedback_key(message: Mapping[str, Any], index: int) -> str:
    return str(message.get("id", index))


__all__ = ["feedback_key", "feedback_was_saved", "preceding_user_prompt"]
