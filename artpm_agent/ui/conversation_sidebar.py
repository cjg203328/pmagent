"""Conversation sidebar contracts.

The Streamlit widget implementation remains in ``ui_helpers`` during the
incremental migration.  These pure projections are the stable boundary for
future sidebar implementations.
"""

from .rendering import conversation_title_from_prompt, message_for_ui

__all__ = ["conversation_title_from_prompt", "message_for_ui"]
