"""Component-rule view of the legacy CSS facade."""

from __future__ import annotations


def style_components_css() -> str:
    """Return the compatibility stylesheet for component renderers."""
    from artpm_agent.ui_style import STYLE_CSS

    return STYLE_CSS


__all__ = ["style_components_css"]
