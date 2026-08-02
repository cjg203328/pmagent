from pathlib import Path
from types import SimpleNamespace

import requests

from artpm_agent import health_check
from artpm_agent.ui_asset_recovery import inspect_frontend_bundle


def _static_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    js_dir = root / "static" / "js"
    js_dir.mkdir(parents=True)
    (root / "index.html").write_text(
        '<script type="module" src="./static/js/index.Main123.js"></script>',
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        """{
          "index.html": {"file": "static/js/index.Main123.js"},
          "ChatInput.tsx": {"file": "static/js/ChatInput.Widget123.js"},
          "Slider.tsx": {"file": "static/js/Slider.Widget123.js"},
          "DataFrame.tsx": {"file": "static/js/DataFrame.Widget123.js"}
        }""",
        encoding="utf-8",
    )
    (js_dir / "index.Main123.js").write_text("export {};", encoding="utf-8")
    (js_dir / "ChatInput.Widget123.js").write_text("export {};", encoding="utf-8")
    (js_dir / "Slider.Widget123.js").write_text("export {};", encoding="utf-8")
    (js_dir / "DataFrame.Widget123.js").write_text("export {};", encoding="utf-8")
    return root


def test_inspect_frontend_bundle_checks_manifest_assets(tmp_path):
    result = inspect_frontend_bundle(_static_bundle(tmp_path))

    assert result["status"] == "ok"
    assert result["javascript_assets"] == 4
    assert result["entrypoint"] == "index.Main123.js"


def test_inspect_frontend_bundle_reports_missing_chunk(tmp_path):
    root = _static_bundle(tmp_path)
    (root / "static" / "js" / "ChatInput.Widget123.js").unlink()

    result = inspect_frontend_bundle(root)

    assert result["status"] == "error"
    assert result["missing"] == ["static/js/ChatInput.Widget123.js"]


def test_health_rejects_html_fallback_for_missing_javascript(tmp_path, monkeypatch):
    root = _static_bundle(tmp_path)

    def get(url, *, timeout):
        if url.endswith("/_stcore/health"):
            return SimpleNamespace(
                status_code=200,
                headers={"Content-Type": "text/plain"},
                content=b"ok",
            )
        return SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=b"<!DOCTYPE html><html></html>",
        )

    monkeypatch.setattr(requests, "get", get)

    result = health_check.check_frontend_assets(
        static_root=root,
        base_url="http://127.0.0.1:8501",
    )

    assert result["status"] == "error"
    assert result["server"] == "asset_mismatch"
    assert result["invalid_assets"][0]["content_type"].startswith("text/html")


def test_health_marks_streamlit_as_unhealthy_when_server_is_unreachable(
    tmp_path, monkeypatch
):
    root = _static_bundle(tmp_path)

    def get(_url, *, timeout):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", get)

    result = health_check.check_frontend_assets(
        static_root=root,
        base_url="http://127.0.0.1:8501",
    )

    assert result["status"] == "error"
    assert result["server"] == "not_running"


def test_api_health_accepts_ok_gateway_response(monkeypatch):
    class Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}

        @staticmethod
        def json():
            return {"status": "ok", "version": "v1"}

    monkeypatch.setattr(requests, "get", lambda _url, *, timeout: Response())

    result = health_check.check_api_health(base_url="http://api.test")

    assert result["status"] == "ok"
    assert result["server"] == "ok"
    assert result["version"] == "v1"


def test_api_health_is_optional_when_default_gateway_is_not_running(monkeypatch):
    def get(_url, *, timeout):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", get)

    monkeypatch.delenv("ARTPM_API_BASE_URL", raising=False)
    monkeypatch.delenv("ARTPM_API_URL", raising=False)
    monkeypatch.delenv("ARTPM_API_REQUIRED", raising=False)
    result = health_check.check_api_health()

    assert result["status"] == "optional"
    assert result["server"] == "not_running"
