from pathlib import Path
from unittest.mock import Mock

from artpm_agent.ui_asset_recovery import (
    build_recovery_script,
    find_frontend_entrypoint,
    install_frontend_recovery_guard,
)


def test_find_frontend_entrypoint_from_streamlit_index(tmp_path: Path):
    index = tmp_path / "index.html"
    index.write_text(
        '<script type="module" src="./static/js/index.Abc_123.js"></script>',
        encoding="utf-8",
    )

    assert find_frontend_entrypoint(index) == "index.Abc_123.js"


def test_find_frontend_entrypoint_returns_empty_for_missing_or_invalid_file(tmp_path):
    assert find_frontend_entrypoint(tmp_path / "missing.html") == ""
    invalid = tmp_path / "invalid.html"
    invalid.write_text("<html></html>", encoding="utf-8")
    assert find_frontend_entrypoint(invalid) == ""


def test_recovery_script_is_idempotent_and_rate_limited():
    script = build_recovery_script("index.Abc_123.js")

    assert "index.Abc_123.js" in script
    assert "__artpmFrontendRecoveryGuard" in script
    assert "unhandledrejection" in script
    assert "Failed to fetch dynamically imported module" in script
    assert "30000" in script
    assert "_artpm_frontend_recovery" in script
    assert "artpmFrontendGuard" in script


def test_install_uses_top_level_streamlit_html(monkeypatch):
    import streamlit as st

    render = Mock()
    monkeypatch.setattr(st, "html", render)

    install_frontend_recovery_guard()

    render.assert_called_once()
    assert render.call_args.kwargs == {
        "width": "content",
        "unsafe_allow_javascript": True,
    }


def test_install_falls_back_for_legacy_streamlit(monkeypatch):
    import streamlit as st
    import streamlit.components.v1 as components

    monkeypatch.setattr(st, "html", Mock(side_effect=TypeError("unsupported")))
    render = Mock()
    monkeypatch.setattr(components, "html", render)

    install_frontend_recovery_guard()

    render.assert_called_once()
    assert render.call_args.kwargs == {"height": 0, "width": 0}
