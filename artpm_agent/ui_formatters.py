"""Compatibility facade for dependency-light UI formatters.

The implementations live in :mod:`artpm_agent.ui.formatters`; this module is
kept for plugins and the historical wildcard imports used by the Streamlit
entrypoint.
"""

from artpm_agent.ui.formatters import (
    artifact_subtitle,
    current_model_id,
    format_cn_date,
    format_money,
    format_size,
    history_limits,
    normalize_agent_response,
    response_model_id,
)

__all__ = [
    "artifact_subtitle",
    "current_model_id",
    "format_cn_date",
    "format_money",
    "format_size",
    "history_limits",
    "normalize_agent_response",
    "response_model_id",
]
