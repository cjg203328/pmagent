from types import SimpleNamespace
from unittest.mock import Mock

from artpm_agent.views import settings


def test_replace_session_agent_closes_previous_instance(monkeypatch):
    previous = SimpleNamespace(close=Mock())
    session_state = {"agent": previous, "db": object()}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=session_state))
    database = object()
    refreshed = SimpleNamespace(database=database)

    settings._replace_session_agent(refreshed)

    assert session_state == {"agent": refreshed, "db": database}
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
