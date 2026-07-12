import sys
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from dotenv import dotenv_values
from streamlit.testing.v1 import AppTest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "artpm_agent"
sys.path.insert(0, str(APP_ROOT))

from utils.llm_client import OpenAIClient
from utils.model_catalog import (
    ModelCatalogError,
    fetch_openai_compatible_models,
    normalize_openai_base_url,
    parse_cached_models,
    serialize_cached_models,
)


def test_openai_compatible_model_sync_uses_configured_endpoint():
    sdk_client = Mock()
    sdk_client.models.list.return_value = SimpleNamespace(data=[
        SimpleNamespace(id="zeta-chat"),
        {"id": "alpha-chat"},
        SimpleNamespace(id="zeta-chat"),
        SimpleNamespace(id=""),
    ])

    with patch("openai.OpenAI", return_value=sdk_client) as openai_class:
        models = fetch_openai_compatible_models(
            "sk-valid-test-key",
            "https://provider.example/v1/",
            timeout=7,
        )

    assert models == ["alpha-chat", "zeta-chat"]
    openai_class.assert_called_once_with(
        api_key="sk-valid-test-key",
        base_url="https://provider.example/v1",
        timeout=7,
        max_retries=1,
    )
    sdk_client.models.list.assert_called_once_with()


def test_model_sync_rejects_unsafe_or_incomplete_base_urls():
    with pytest.raises(ModelCatalogError, match="完整"):
        normalize_openai_base_url("provider.example/v1")
    with pytest.raises(ModelCatalogError, match="用户名或密码"):
        normalize_openai_base_url("https://user:pass@provider.example/v1")
    with pytest.raises(ModelCatalogError, match="查询参数"):
        normalize_openai_base_url("https://provider.example/v1?token=secret")


def test_model_cache_round_trip_is_deduplicated_and_sorted():
    serialized = serialize_cached_models(["z-model", "a-model", "z-model"])
    assert serialized == '["a-model","z-model"]'
    assert parse_cached_models(serialized) == ["a-model", "z-model"]
    assert parse_cached_models("z-model, a-model") == ["a-model", "z-model"]


def test_persist_settings_saves_default_model_and_catalog(tmp_path, monkeypatch):
    import app as streamlit_app

    env_path = tmp_path / ".env"
    env_path.write_text(
        "# preserved\nKEEP_ME=yes\n"
        "UNLIMITED_OCR_ENABLED=true\n"
        "UNLIMITED_OCR_BASE_URL=http://ocr-sidecar.internal:10000\n",
        encoding="utf-8",
    )
    managed_keys = [
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_AVAILABLE_MODELS",
        "LLM_MODELS_SYNCED_AT",
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "MCP_ENABLED",
        "SKILLS_FORGE_KEY",
        "SKILLS_FORGE_URL",
        "OVERHEAD_RATE",
        "TAX_RATE",
    ]
    for key in managed_keys:
        monkeypatch.delenv(key, raising=False)

    streamlit_app.persist_settings({
        "provider": "custom",
        "model": "selected-chat-model",
        "api_key": "sk-valid-test-key",
        "api_base_url": "https://provider.example/v1",
        "available_models": ["selected-chat-model", "other-model"],
        "models_synced_at": "2026-07-11T12:00:00+08:00",
        "mcp_enabled": False,
        "mcp_key": "",
        "mcp_url": "",
        "overhead_rate": 0.15,
        "tax_rate": 0.06,
    }, env_path=env_path)

    saved = dotenv_values(env_path)
    assert saved["KEEP_ME"] == "yes"
    assert saved["LLM_PROVIDER"] == "custom"
    assert saved["LLM_MODEL"] == "selected-chat-model"
    assert parse_cached_models(saved["LLM_AVAILABLE_MODELS"]) == [
        "other-model",
        "selected-chat-model",
    ]
    assert saved["LLM_MODELS_SYNCED_AT"] == "2026-07-11T12:00:00+08:00"
    assert saved["OPENAI_API_BASE"] == "https://provider.example/v1"
    assert saved["UNLIMITED_OCR_ENABLED"] == "true"
    assert saved["UNLIMITED_OCR_BASE_URL"] == "http://ocr-sidecar.internal:10000"


def test_openai_chat_sends_the_selected_model():
    client = OpenAIClient({
        "openai_api_key": "sk-valid-test-key",
        "model": "selected-chat-model",
        "retry_max_attempts": 1,
    })
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
    )
    client.client = sdk_client

    assert client.chat("hello", system_prompt="system") == "ok"
    kwargs = sdk_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "selected-chat-model"
    assert kwargs["messages"][-1] == {"role": "user", "content": "hello"}


def test_openai_chat_sends_system_history_then_current_prompt():
    client = OpenAIClient({
        "openai_api_key": "sk-valid-test-key",
        "model": "selected-chat-model",
        "retry_max_attempts": 1,
    })
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="next answer"))]
    )
    client.client = sdk_client
    history = [
        {"role": "user", "content": "previous question", "status": "complete"},
        {"role": "assistant", "content": "previous answer", "status": "complete"},
        {"role": "assistant", "content": "failed answer", "status": "error"},
        {"role": "system", "content": "untrusted system message"},
        {"role": "user", "content": "current question", "status": "complete"},
    ]

    assert client.chat(
        "current question",
        system_prompt="trusted system prompt",
        history=history,
    ) == "next answer"

    messages = sdk_client.chat.completions.create.call_args.kwargs["messages"]
    assert messages == [
        {"role": "system", "content": "trusted system prompt"},
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": "previous answer"},
        {"role": "user", "content": "current question"},
    ]


def test_openai_client_uses_one_bounded_retry_layer():
    with patch("openai.OpenAI") as openai_class:
        OpenAIClient({
            "openai_api_key": "sk-valid-test-key",
            "openai_api_base": "https://provider.example/v1",
            "model": "selected-chat-model",
            "request_timeout_seconds": 30,
        })

    openai_class.assert_called_once_with(
        api_key="sk-valid-test-key",
        base_url="https://provider.example/v1",
        timeout=30.0,
        max_retries=0,
    )


def test_settings_page_exposes_model_dropdown_and_manual_id():
    app = AppTest.from_file("artpm_agent/app.py").run(timeout=30)
    app.button(key="nav_settings").click().run(timeout=30)

    assert not app.exception
    model_choice = app.selectbox(key="model_choice_custom")
    assert "手动输入模型 ID" in model_choice.options

    model_choice.select("手动输入模型 ID").run(timeout=30)
    assert not app.exception
    manual_model = app.text_input(key="manual_model_custom")
    manual_model.set_value("manual-chat-model").run(timeout=30)
    assert app.session_state["manual_model_custom"] == "manual-chat-model"


def test_app_import_survives_stale_utils_package_cache():
    script = """
import sys
sys.path.insert(0, 'artpm_agent')
import utils
for name in (
    'ModelCatalogError',
    'fetch_openai_compatible_models',
    'parse_cached_models',
    'serialize_cached_models',
):
    if hasattr(utils, name):
        delattr(utils, name)
import app
assert app.AVAILABLE
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_model_catalog_import_failure_does_not_disable_core_app():
    script = """
import builtins
import sys
sys.path.insert(0, 'artpm_agent')
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'utils.model_catalog':
        raise ImportError('simulated model catalog failure')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import app
assert app.AVAILABLE
assert not app.MODEL_CATALOG_AVAILABLE
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
