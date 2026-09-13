import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_state_and_disable_llm(monkeypatch, tmp_path):
    """Per-test isolation for the full v0.2 state surface.

    Every persistent state database is redirected under ``tmp_path`` so tests
    never touch the real project ``./data`` directory or each other:

    * ``DATA_ROOT`` governs the default resolve_state_path() layout.
    * The dedicated ``ARTPM_*_DB`` env vars override individual state files
      (feedback / strategy / meta-memory / reflection / episode) for advanced
      deployments and tests.
    * The classic DB_PATH / MEMORY_DB_PATH / CONVERSATION_DB_PATH /
      VECTOR_DB_PATH keys are rebound too.

    In addition, every module-level singleton (Config, FeedbackStore,
    StrategyStore, MetaMemory, MetaMemoryStore, ReflectionScheduler,
    EpisodeStore, outcome recorder) is reset so a fresh tmp_path-backed
    instance is built on next access.
    """
    # ── LLM / API keys: keep tests deterministic, no accidental spend ──
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    # Unit tests must never start the external stdio MCP process. The explicit
    # integration run keeps the caller's MCP settings so the live contract can
    # exercise the configured transport instead of being silently disabled by
    # this fixture.
    if os.getenv("ART_ENABLE_INTEGRATION") != "1":
        monkeypatch.setenv("MCP_ENABLED", "false")
        monkeypatch.setenv("SKILLS_FORGE_KEY", "")

    # ── Single data root: everything else defaults underneath it ──
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))

    # ── Classic database paths ──
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setenv("CONVERSATION_DB_PATH", str(tmp_path / "conversations.db"))
    monkeypatch.setenv("VECTOR_DB_PATH", str(tmp_path / "vectors.db"))

    # ── Dedicated per-state-file overrides (resolve_state_path env_var) ──
    monkeypatch.setenv("ARTPM_FEEDBACK_DB", str(tmp_path / "feedback.db"))
    monkeypatch.setenv("ARTPM_STRATEGY_DB", str(tmp_path / "strategies.db"))
    monkeypatch.setenv("ARTPM_META_MEMORY_DB", str(tmp_path / "meta_memory.db"))
    monkeypatch.setenv("ARTPM_CONSOLIDATION_DB", str(tmp_path / "consolidation.db"))
    monkeypatch.setenv("ARTPM_REFLECTION_DB", str(tmp_path / "reflection.db"))
    monkeypatch.setenv("ARTPM_EPISODE_DB", str(tmp_path / "episode.db"))

    # Reset every module-level singleton so the next access rebuilds against
    # the freshly redirected tmp_path paths (not a stale project path).
    _reset_singletons()


def _reset_singletons() -> None:
    """Drop cached singletons without importing heavy optional dependencies."""
    try:
        from artpm_agent import config as _config

        _config.reset_config()
    except Exception:  # noqa: BLE001
        pass

    try:
        from artpm_agent.memory import feedback_store as _fb

        _fb._DEFAULT_STORE = None
    except Exception:  # noqa: BLE001
        pass

    try:
        from artpm_agent.evolution import strategy_store as _st

        _st._DEFAULT_STORE = None
    except Exception:  # noqa: BLE001
        pass

    try:
        from artpm_agent.evolution import meta_memory as _mm

        _mm._DEFAULT_META_MEMORY = None
        _mm._DEFAULT_META_STORE = None
    except Exception:  # noqa: BLE001
        pass

    try:
        from artpm_agent.evolution import scheduler as _sched

        _sched._DEFAULT_SCHEDULER = None
    except Exception:  # noqa: BLE001
        pass

    try:
        from artpm_agent.harness import outcome_recorder as _out

        _out._DEFAULT_STORE = None
    except Exception:  # noqa: BLE001
        pass

    try:
        from artpm_agent.memory import episode_store as _ep

        if hasattr(_ep, "_DEFAULT_STORE"):
            _ep._DEFAULT_STORE = None
    except Exception:  # noqa: BLE001
        pass


def pytest_collection_modifyitems(config, items):
    """Apply suite boundaries and skip external tests by default.

    Integration tests need a live MCP server / network / API keys and would
    hang or fail in CI. Enable them with ART_ENABLE_INTEGRATION=1.
    """
    slow_modules = {
        "test_model_sync.py",
        "test_permission_mode_streamlit.py",
        "test_permission_streamlit.py",
        "test_streamlit_app.py",
        "test_workflow_designer_streamlit.py",
    }
    if os.getenv("ART_ENABLE_INTEGRATION") == "1":
        integration_enabled = True
    else:
        integration_enabled = False
    for item in items:
        path = Path(str(item.fspath))
        if "integration" in path.parts:
            item.add_marker(pytest.mark.integration)
        if path.name in slow_modules:
            item.add_marker(pytest.mark.slow)
        if (
            not integration_enabled
            and item.get_closest_marker("integration") is not None
        ):
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
