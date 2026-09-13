from types import SimpleNamespace
from unittest.mock import Mock

from artpm_agent.views import settings


def test_persist_settings_writes_vision_pairing_env(tmp_path, monkeypatch):
    """persist_settings must persist the vision pairing config to .env."""
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_PROVIDER=custom\n", encoding="utf-8")

    monkeypatch.setattr(settings, "PROJECT_ENV_PATH", env_path)
    settings.persist_settings(
        {
            "provider": "custom",
            "model": "deepseek-v4-pro",
            "framework": "langchain",
            "mcp_enabled": False,
            "mcp_key": "",
            "mcp_url": "",
            "api_key": "sk-main",
            "api_base_url": "",
            "available_models": [],
            "models_synced_at": "",
            "vision_provider": "zhipu",
            "vision_model": "glm-4v-flash",
            "vision_api_key": "vision-key-123",
            "vision_api_base": "https://open.bigmodel.cn/api/paas/v4",
        },
        env_path=env_path,
    )

    text = env_path.read_text(encoding="utf-8")
    assert "LLM_VISION_PROVIDER=zhipu" in text
    assert "glm-4v-flash" in text and "LLM_VISION_MODEL" in text
    assert "vision-key-123" in text and "LLM_VISION_API_KEY" in text
    assert "open.bigmodel.cn/api/paas/v4" in text and "LLM_VISION_API_BASE" in text


def test_persist_settings_unsets_empty_vision_pairing(tmp_path, monkeypatch):
    """Empty vision fields must remove stale LLM_VISION_* env entries."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=custom\n"
        "LLM_VISION_PROVIDER=zhipu\n"
        "LLM_VISION_MODEL=glm-4v-flash\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(settings, "PROJECT_ENV_PATH", env_path)
    settings.persist_settings(
        {
            "provider": "custom",
            "model": "deepseek-v4-pro",
            "mcp_enabled": False,
            "mcp_key": "",
            "mcp_url": "",
            "api_key": "sk-main",
            "api_base_url": "",
            "available_models": [],
            "models_synced_at": "",
            "vision_provider": "",
            "vision_model": "",
            "vision_api_key": "",
            "vision_api_base": "",
        },
        env_path=env_path,
    )

    text = env_path.read_text(encoding="utf-8")
    assert "LLM_VISION_PROVIDER" not in text
    assert "LLM_VISION_MODEL" not in text


def test_replace_session_agent_closes_previous_instance(monkeypatch):
    previous = SimpleNamespace(close=Mock())
    session_state = {"agent": previous, "db": object()}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=session_state))
    database = object()
    refreshed = SimpleNamespace(database=database)

    settings._replace_session_agent(refreshed)

    assert session_state == {"agent": refreshed, "db": database}
    previous.close.assert_called_once_with()


def test_replace_session_agent_reapplies_session_model_override(monkeypatch):
    previous = SimpleNamespace(close=Mock())
    session_state = {"agent": previous, "db": object(), "chat_active_model": "gpt-4o"}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=session_state))
    refreshed_gateway = SimpleNamespace(
        _llm_config={"model": "gpt-4o-mini"},
        last_response_model=None,
    )
    refreshed = SimpleNamespace(database=object(), model_gateway=refreshed_gateway)

    settings._replace_session_agent(refreshed)

    assert refreshed_gateway._llm_config["model"] == "gpt-4o"
    assert refreshed_gateway.last_response_model == "gpt-4o"
    previous.close.assert_called_once_with()


def test_persist_settings_falls_back_to_in_place_env_writer(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# keep comment\n"
        "LLM_PROVIDER=anthropic\n"
        "OPENAI_API_BASE=https://old.example/v1\n"
        "UNRELATED=value\n",
        encoding="utf-8",
    )

    def fail_dotenv(*_args, **_kwargs):
        raise PermissionError("replace denied")

    monkeypatch.setattr(settings, "_persist_env_with_dotenv", fail_dotenv)
    monkeypatch.setenv("OPENAI_API_BASE", "https://old.example/v1")

    settings.persist_settings(
        {
            "provider": "custom",
            "model": "deepseek-v4-flash",
            "mcp_enabled": False,
            "mcp_key": "",
            "mcp_url": "",
            "api_key": "sk-test",
            "api_base_url": "",
            "available_models": [],
            "models_synced_at": "",
        },
        env_path=env_path,
    )

    text = env_path.read_text(encoding="utf-8")
    assert "# keep comment" in text
    assert "UNRELATED=value" in text
    assert "LLM_PROVIDER=custom" in text
    assert "LLM_MODEL=deepseek-v4-flash" in text
    assert "OPENAI_API_KEY=sk-test" in text
    assert "OPENAI_API_BASE=" not in text


def test_persist_settings_writes_selected_model_framework(tmp_path):
    env_path = tmp_path / ".env"

    settings.persist_settings(
        {
            "provider": "custom",
            "model": "deepseek-v4-flash",
            "framework": "native",
            "mcp_enabled": False,
            "mcp_key": "",
            "mcp_url": "",
            "api_key": "sk-test",
            "api_base_url": "",
            "available_models": [],
            "models_synced_at": "",
        },
        env_path=env_path,
    )

    assert "LLM_FRAMEWORK=native" in env_path.read_text(encoding="utf-8")
    assert "MCP_TRANSPORT=stdio" in env_path.read_text(encoding="utf-8")


def test_data_root_reload_closes_old_agent_before_reinitializing(
    tmp_path,
    monkeypatch,
):
    from artpm_agent.core import mcp_client as mcp_module

    order = []
    previous = SimpleNamespace(close=lambda: order.append("agent-close"))
    session_state = {
        "agent": previous,
        "db": object(),
        "conversation_store": object(),
    }
    fake_st = SimpleNamespace(
        session_state=session_state,
        success=Mock(),
        toast=Mock(),
        rerun=Mock(),
        error=Mock(),
    )
    monkeypatch.setattr(settings, "st", fake_st)
    monkeypatch.setattr(settings, "resolve_data_root", lambda: tmp_path / "old")
    monkeypatch.setattr(settings, "save_data_root", Mock())
    monkeypatch.setattr(settings, "reset_config", Mock())
    monkeypatch.setattr(
        mcp_module,
        "reset_mcp_client",
        lambda: order.append("mcp-reset"),
    )

    def init():
        order.append("init")
        assert "agent" not in session_state
        session_state["agent"] = SimpleNamespace(database=object())

    monkeypatch.setattr(settings, "init_session", init)

    settings._apply_data_root(
        str(tmp_path / "new"),
        migrate=False,
        reset=False,
    )

    assert order == ["mcp-reset", "agent-close", "init"]
    fake_st.rerun.assert_called_once_with()


def test_data_root_save_failure_uses_callback_and_keeps_current_agent(
    tmp_path,
    monkeypatch,
):
    previous = SimpleNamespace(close=Mock())
    session_state = {"agent": previous, "db": object()}
    monkeypatch.setattr(
        settings,
        "st",
        SimpleNamespace(session_state=session_state),
    )
    monkeypatch.setattr(settings, "resolve_data_root", lambda: tmp_path / "old")
    monkeypatch.setattr(
        settings,
        "save_data_root",
        Mock(side_effect=PermissionError("write denied")),
    )
    reset_config = Mock()
    callback = Mock()
    monkeypatch.setattr(settings, "reset_config", reset_config)
    monkeypatch.setattr(settings, "render_error_callback", callback)

    settings._apply_data_root(
        str(tmp_path / "new"),
        migrate=False,
        reset=False,
    )

    callback.assert_called_once()
    reset_config.assert_not_called()
    previous.close.assert_not_called()
    assert session_state["agent"] is previous
