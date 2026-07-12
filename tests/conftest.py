import os

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


def pytest_collection_modifyitems(config, items):
    """Skip integration tests by default.

    Integration tests need a live MCP server / network / API keys and would
    hang or fail in CI. Enable them with ART_ENABLE_INTEGRATION=1.
    """
    if os.getenv("ART_ENABLE_INTEGRATION") == "1":
        return
    for item in items:
        if "mcp" in item.nodeid.lower():
            item.add_marker(
                pytest.mark.skip(
                    reason="integration test skipped (set ART_ENABLE_INTEGRATION=1 to run)"
                )
            )
