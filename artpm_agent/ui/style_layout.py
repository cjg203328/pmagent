"""Layout-rule view of the legacy CSS facade."""

from __future__ import annotations


def style_layout_css() -> str:
    """Return the complete legacy stylesheet for layout-aware hosts.

    The extraction is intentionally lazy during the staged migration.  It
    keeps Streamlit's one-shot stylesheet injection stable while callers move
    individual selectors into this boundary.
    """
    from artpm_agent.ui_style import STYLE_CSS

    return STYLE_CSS


__all__ = ["style_layout_css"]
