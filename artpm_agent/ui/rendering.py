"""Pure message and artifact projections shared by UI renderers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .formatters import artifact_subtitle as _artifact_subtitle
from .formatters import normalize_agent_response


def conversation_title_from_prompt(prompt: Any, max_length: int = 28) -> str:
    title = " ".join(str(prompt).split())
    if len(title) <= max_length:
        return title or "新对话"
    return f"{title[:max_length].rstrip()}…"


def message_for_ui(message: Mapping[str, Any]) -> dict[str, Any]:
    """Keep storage metadata nested while exposing legacy retry fields."""
    projected = dict(message)
    metadata = projected.get("metadata") or {}
    if isinstance(metadata, Mapping) and metadata.get("retry_prompt"):
        projected["retry_prompt"] = metadata["retry_prompt"]
    return projected


def artifact_subtitle(artifact: dict[str, Any]) -> str:
    return _artifact_subtitle(artifact)


__all__ = [
    "artifact_subtitle",
    "conversation_title_from_prompt",
    "message_for_ui",
    "normalize_agent_response",
]
