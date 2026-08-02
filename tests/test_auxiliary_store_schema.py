"""Regression tests for lightweight, isolated auxiliary SQLite stores."""

from contextlib import closing
import sqlite3

from artpm_agent.evolution.scheduler import ReflectionScheduler
from artpm_agent.evolution.strategy_store import StrategyStore
from artpm_agent.memory.episode_store import EpisodeStore
from artpm_agent.memory.feedback_store import FeedbackStore
from artpm_agent.memory.sqlite_manager import SQLiteManager


def _application_tables(path) -> set[str]:
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {str(row[0]) for row in rows if not str(row[0]).startswith("sqlite_")}


def test_primary_store_still_installs_business_schema(tmp_path):
    path = tmp_path / "primary.db"
    SQLiteManager(path)

    tables = _application_tables(path)
    assert {"projects", "documents", "staff", "task_assignments"} <= tables


def test_auxiliary_stores_only_install_their_owned_tables(tmp_path):
    stores = (
        (EpisodeStore, "episodes.db", {"episodes"}),
        (FeedbackStore, "feedback.db", {"feedback"}),
        (StrategyStore, "strategies.db", {"strategies"}),
        (ReflectionScheduler, "reflection.db", {"reflection_runs"}),
    )

    for store_type, filename, expected_tables in stores:
        path = tmp_path / filename
        store_type(str(path))
        assert _application_tables(path) == expected_tables


def test_auxiliary_store_removes_only_an_empty_known_primary_scaffold(tmp_path):
    path = tmp_path / "old-episodes.db"
    SQLiteManager(path)

    EpisodeStore(str(path))

    assert _application_tables(path) == {"episodes"}


def test_auxiliary_store_preserves_primary_scaffold_when_it_contains_data(tmp_path):
    path = tmp_path / "old-feedback.db"
    manager = SQLiteManager(path)
    manager.insert(
        "staff",
        {"id": "staff-1", "name": "Owner", "status": "active"},
    )

    FeedbackStore(str(path))

    tables = _application_tables(path)
    assert {"feedback", "staff", "projects", "documents"} <= tables
    assert manager.get_by_id("staff", "staff-1")["name"] == "Owner"


def test_auxiliary_store_preserves_scaffold_referenced_by_custom_table(tmp_path):
    path = tmp_path / "custom-reference.db"
    manager = SQLiteManager(path)
    with manager.get_connection() as connection:
        connection.execute(
            "CREATE TABLE custom_links ("
            "id INTEGER PRIMARY KEY, "
            "project_id TEXT REFERENCES projects(id))"
        )

    EpisodeStore(str(path))

    assert {"episodes", "custom_links", "projects"} <= _application_tables(path)
