"""Pure UI contracts and small rendering boundaries.

The Streamlit-facing compatibility modules remain at ``artpm_agent.ui_helpers``
and ``artpm_agent.ui_style``.  New code should import side-effect-free helpers
from this package so they can be tested without bootstrapping a page.
"""

from .approvals import (
    permission_impact,
    permission_parameter_summary,
    thaw_permission_json,
)
from .formatters import (
    artifact_subtitle,
    current_model_id,
    format_cn_date,
    format_money,
    format_size,
    history_limits,
    normalize_agent_response,
    response_model_id,
)
from .knowledge import (
    compose_knowledge_context,
    extract_knowledge_rule,
    is_knowledge_ingestion_request,
)
from .rendering import conversation_title_from_prompt, message_for_ui

__all__ = [
    "artifact_subtitle",
    "compose_knowledge_context",
    "conversation_title_from_prompt",
    "current_model_id",
    "extract_knowledge_rule",
    "format_cn_date",
    "format_money",
    "format_size",
    "history_limits",
    "is_knowledge_ingestion_request",
    "message_for_ui",
    "normalize_agent_response",
    "permission_impact",
    "permission_parameter_summary",
    "response_model_id",
    "thaw_permission_json",
]
