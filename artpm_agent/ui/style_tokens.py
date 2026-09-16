"""Design-token view of the legacy CSS facade.

Token extraction is lazy so importing this module never initializes Streamlit.
The existing ``artpm_agent.ui_style.STYLE_CSS`` string remains the browser
contract until the remaining page-specific rules are migrated.
"""

from __future__ import annotations

import re


def style_tokens_css() -> str:
    from artpm_agent.ui_style import STYLE_CSS

    match = re.search(r":root\s*\{(?P<body>.*?)\n\s*\}", STYLE_CSS, re.DOTALL)
    return match.group("body").strip() if match else ""


def token_names() -> tuple[str, ...]:
    return tuple(sorted(set(re.findall(r"(--pm-[a-z0-9-]+)\s*:", style_tokens_css()))))


def build_tokens_css() -> str:
    return f":root {{\n{style_tokens_css()}\n}}"


__all__ = ["build_tokens_css", "style_tokens_css", "token_names"]
