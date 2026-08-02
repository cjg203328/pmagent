"""Contract tests for settings persistence helpers."""

import os

from dotenv import dotenv_values

from artpm_agent.views import settings


def _settings_payload(**overrides):
    payload = {
        "provider": "custom",
        "model": "test-model",
        "framework": "langchain",
        "mcp_enabled": True,
        "mcp_transport": "http",
        "mcp_key": "forge-test-key",
        "mcp_url": "https://skills.example.test",
        "api_key": "provider-test-key",
        "api_base_url": "https://provider.example.test/v1",
        "available_models": [],
        "models_synced_at": "",
    }
    payload.update(overrides)
    return payload


def test_dotenv_value_quotes_values_that_need_escaping():
    assert settings._dotenv_value("plain") == "plain"
    assert settings._dotenv_value("") == '""'
    assert settings._dotenv_value("two words") == '"two words"'
    assert settings._dotenv_value("value#comment") == '"value#comment"'


def test_persist_settings_writes_current_contract(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("# preserved\nUNRELATED=yes\n", encoding="utf-8")
    for key in (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_FRAMEWORK",
        "MCP_ENABLED",
        "MCP_TRANSPORT",
        "SKILLS_FORGE_KEY",
        "SKILLS_FORGE_URL",
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)

    settings.persist_settings(_settings_payload(), env_path=env_path)

    saved = dotenv_values(env_path)
    assert saved["UNRELATED"] == "yes"
    assert saved["LLM_PROVIDER"] == "custom"
    assert saved["LLM_MODEL"] == "test-model"
    assert saved["LLM_FRAMEWORK"] == "langchain"
    assert saved["MCP_ENABLED"] == "true"
    assert saved["MCP_TRANSPORT"] == "http"
    assert saved["OPENAI_API_BASE"] == "https://provider.example.test/v1"


def test_persist_settings_unsets_empty_provider_credentials(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=old-provider-key\n"
        "OPENAI_API_BASE=https://old.example.test/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "old-provider-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://old.example.test/v1")

    settings.persist_settings(
        _settings_payload(api_key="", api_base_url=""),
        env_path=env_path,
    )

    saved = dotenv_values(env_path)
    assert "OPENAI_API_KEY" not in saved
    assert "OPENAI_API_BASE" not in saved
    assert "OPENAI_API_KEY" not in os.environ
    assert "OPENAI_API_BASE" not in os.environ


def test_persist_settings_normalizes_unknown_mcp_transport(tmp_path):
    env_path = tmp_path / ".env"

    settings.persist_settings(
        _settings_payload(mcp_transport="invalid"),
        env_path=env_path,
    )

    assert dotenv_values(env_path)["MCP_TRANSPORT"] == "stdio"


def test_mcp_refresh_requires_enabled_credentials_and_http_url():
    assert not settings._mcp_refresh_ready(
        enabled=False,
        transport="stdio",
        api_key="sk-test",
        url="",
    )
    assert not settings._mcp_refresh_ready(
        enabled=True,
        transport="stdio",
        api_key=" ",
        url="",
    )
    assert settings._mcp_refresh_ready(
        enabled=True,
        transport="stdio",
        api_key="sk-test",
        url="",
    )
    assert not settings._mcp_refresh_ready(
        enabled=True,
        transport="http",
        api_key="sk-test",
        url="",
    )
    assert settings._mcp_refresh_ready(
        enabled=True,
        transport="http",
        api_key="sk-test",
        url="https://skills.example.test",
    )


def test_mcp_status_messages_are_actionable_without_internal_details():
    assert settings._mcp_status_message(enabled=False) == "未启用"
    assert "API Key" in settings._mcp_status_message(
        enabled=True,
        error_category="auth",
    )
    assert "服务端地址" in settings._mcp_status_message(
        enabled=True,
        error_category="parse",
    )
    generic = settings._mcp_status_message(
        enabled=True,
        error_category="internal-stack-trace",
    )
    assert generic == "连接检查失败，请检查配置后重试。"
    assert "internal-stack-trace" not in generic


def test_settings_page_copy_has_no_recommendation_or_feature_promotion():
    import inspect

    source = inspect.getsource(settings.settings_page)
    for phrase in (
        "（推荐）",
        "兼容回退",
        "低风险策略可自动应用",
        "云端开销",
    ):
        assert phrase not in source
