"""DATA_ROOT migration contract (P0).

Every standalone state database must resolve under the active DATA_ROOT unless a
dedicated environment variable overrides it. This locks the v0.2 migration so a
future change cannot silently reintroduce a hard-coded ``data/*.db`` path.
"""
from pathlib import Path

import pytest

from artpm_agent.config import resolve_data_root, resolve_state_path


STATE_FILES = [
    ("telemetry.db", "ARTPM_TELEMETRY_DB", "artpm_agent.runtime.telemetry", "default_telemetry_db_path"),
    ("feedback.db", "ARTPM_FEEDBACK_DB", "artpm_agent.memory.feedback_store", "default_feedback_db_path"),
    ("strategies.db", "ARTPM_STRATEGY_DB", "artpm_agent.evolution.strategy_store", "default_strategies_db_path"),
    ("reflection.db", "ARTPM_REFLECTION_DB", "artpm_agent.evolution.scheduler", "default_reflection_db_path"),
    ("meta_memory.db", "ARTPM_META_MEMORY_DB", "artpm_agent.evolution.meta_memory", "MetaMemoryStore"),
    ("consolidation.db", "ARTPM_CONSOLIDATION_DB", "artpm_agent.memory.consolidation", "ConsolidationScheduler"),
]


@pytest.mark.parametrize("filename,env_var,module,callable_name", STATE_FILES)
def test_state_file_resolves_under_data_root(monkeypatch, tmp_path, filename, env_var, module, callable_name):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    # ensure a previous dedicated override cannot leak in
    monkeypatch.delenv(env_var, raising=False)

    mod = __import__(module, fromlist=[callable_name])
    if callable_name == "MetaMemoryStore":
        path = str(mod.MetaMemoryStore().db_path)
    elif callable_name == "ConsolidationScheduler":
        path = str(mod.ConsolidationScheduler().db_path)
    else:
        path = getattr(mod, callable_name)()

    assert Path(path).resolve().parent == tmp_path.resolve()
    assert Path(path).name == filename


@pytest.mark.parametrize("filename,env_var,module,callable_name", STATE_FILES)
def test_dedicated_env_override_takes_precedence(monkeypatch, tmp_path, filename, env_var, module, callable_name):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "ignored"))
    override = tmp_path / "explicit" / filename
    monkeypatch.setenv(env_var, str(override))

    mod = __import__(module, fromlist=[callable_name])
    if callable_name == "MetaMemoryStore":
        path = str(mod.MetaMemoryStore().db_path)
    elif callable_name == "ConsolidationScheduler":
        path = str(mod.ConsolidationScheduler().db_path)
    else:
        path = getattr(mod, callable_name)()

    assert Path(path).resolve() == override.resolve()


def test_resolve_state_path_helper(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert resolve_state_path("custom.db").resolve() == (tmp_path / "custom.db").resolve()
    override = tmp_path / "other.db"
    monkeypatch.setenv("CUSTOM_VAR", str(override))
    assert resolve_state_path("custom.db", "CUSTOM_VAR").resolve() == override.resolve()


def test_data_root_is_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert resolve_data_root().is_absolute()
