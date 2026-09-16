"""Staged CSS aggregation API.

``ui_style.STYLE_CSS`` is kept as the single Streamlit injection value.  This
module gives new callers explicit token/layout/component access and provides a
single place to switch from the compatibility source to physical fragments.
"""

from __future__ import annotations

from .style_components import style_components_css
from .style_layout import style_layout_css
from .style_tokens import build_tokens_css, token_names


def build_style_css() -> str:
    # All fragments currently originate from the compatibility facade.  Keeping
    # one output string avoids duplicate <style> tags and Streamlit cache churn.
    from artpm_agent.ui_style import STYLE_CSS

    return STYLE_CSS


STYLE_CSS = build_style_css()

__all__ = [
    "STYLE_CSS",
    "build_style_css",
    "build_tokens_css",
    "style_components_css",
    "style_layout_css",
    "token_names",
]
