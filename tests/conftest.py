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
        if item.get_closest_marker("integration") is not None:
            item.add_marker(
                pytest.mark.skip(
                    reason="integration test skipped (set ART_ENABLE_INTEGRATION=1 to run)"
                )
            )


@pytest.fixture(autouse=True)
def db_resource_guard():
    """Per-test SQLite Engine 回收守卫（技术债 #7）。

    释放本测试执行期间由 DatabaseManager 新建的 Engine，
    避免游离连接被 GC 时抛出 "unclosed database" ResourceWarning。
    测试开始前已存在的 Engine（如 session 级 fixture）保持不动。
    """
    from artpm_agent.database.models import _ACTIVE_ENGINES

    baseline = set(_ACTIVE_ENGINES)
    try:
        yield
    finally:
        for _engine in list(_ACTIVE_ENGINES - baseline):
            try:
                _engine.dispose()
            except Exception:  # noqa: BLE001
                pass
            finally:
                _ACTIVE_ENGINES.discard(_engine)
