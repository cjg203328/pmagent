import pytest


@pytest.fixture(autouse=True)
def disable_external_llm_for_tests(monkeypatch, tmp_path):
    """Keep the test suite deterministic and prevent accidental API spend."""
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    monkeypatch.setenv(
        "CONVERSATION_DB_PATH",
        str(tmp_path / "conversations.db"),
    )
