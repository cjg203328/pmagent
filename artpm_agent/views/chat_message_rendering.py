"""Stored-message rendering boundary for the Streamlit chat host."""

from __future__ import annotations

from typing import Any


def render_completed_message(
    st: Any,
    *,
    role: str,
    content: str,
    message: dict[str, Any],
    index: int,
    compact_assistant_copy: Any,
    render_attachments: Any,
    render_artifacts: Any,
    render_voice_reply: Any,
    render_feedback: Any,
) -> None:
    """Render one non-error message without owning page state transitions."""
    metadata = message.get("metadata") or {}
    display_content = (
        compact_assistant_copy(content) if role == "assistant" else content
    )
    st.markdown(display_content)
    if role == "user":
        render_attachments(metadata.get("attachments", []))
        return
    render_artifacts(metadata, message.get("id", index))
    render_voice_reply(message, index)
    render_feedback(message, index)


__all__ = ["render_completed_message"]
