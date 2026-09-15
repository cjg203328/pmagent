import sqlite3

import pytest

from artpm_agent.evolution import meta_memory as meta_memory_module
from artpm_agent.evolution.meta_memory import KnowledgeGap, MetaMemoryStore
from artpm_agent.memory import consolidation as consolidation_module
from artpm_agent.memory.consolidation import ConsolidationReport, ConsolidationScheduler
from artpm_agent.memory import sqlite_manager as sqlite_manager_module
from artpm_agent.memory.sqlite_manager import SQLiteManager
from artpm_agent.runtime import telemetry as telemetry_module
from artpm_agent.runtime.telemetry import AgentTelemetry


class _TrackingConnection(sqlite3.Connection):
    was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


@pytest.fixture()
def tracked_connections(monkeypatch):
    real_connect = sqlite3.connect
    connections = []

    def tracked_connect(*args, **kwargs):
        kwargs["factory"] = _TrackingConnection
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    def install(module):
        monkeypatch.setattr(module.sqlite3, "connect", tracked_connect)
        return connections

    return install


def test_meta_memory_store_closes_every_connection(tmp_path, tracked_connections):
    connections = tracked_connections(meta_memory_module)
    store = MetaMemoryStore(tmp_path / "meta.db")
    store.record_gaps(
        [KnowledgeGap("topic", "unknown", "ask_user", "detail", 0.0)]
    )
    store.top_gaps()

    assert connections
    assert all(connection.was_closed for connection in connections)


def test_consolidation_scheduler_closes_every_connection(
    tmp_path, tracked_connections
):
    connections = tracked_connections(consolidation_module)
    scheduler = ConsolidationScheduler(tmp_path / "consolidation.db")
    scheduler.should_run(workspace_id="workspace")
    scheduler.mark_run("workspace", ConsolidationReport(resources_scanned=1))
    scheduler.last_run("workspace")

    assert connections
    assert all(connection.was_closed for connection in connections)


def test_agent_telemetry_closes_every_connection(tmp_path, tracked_connections):
    connections = tracked_connections(telemetry_module)
    telemetry = AgentTelemetry(str(tmp_path / "telemetry.db"), enabled=True)
    telemetry.record(task_type="chat", model_used="model", success=True)
    telemetry.record_connection(provider="provider", model="model")
    telemetry.record_event(stage="test")
    telemetry.recent()
    telemetry.recent_connections()
    telemetry.recent_events()

    assert connections
    assert all(connection.was_closed for connection in connections)


def test_sqlite_manager_closes_connection_when_operation_fails(
    tmp_path, tracked_connections
):
    connections = tracked_connections(sqlite_manager_module)
    manager = SQLiteManager(tmp_path / "auxiliary.db", initialize_schema=False)

    with pytest.raises(RuntimeError, match="boom"):
        with manager.get_connection() as connection:
            connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
            raise RuntimeError("boom")

    assert connections
    assert all(connection.was_closed for connection in connections)


def test_sqlite_manager_get_by_ids_batches_and_preserves_order(tmp_path):
    manager = SQLiteManager(tmp_path / "documents.db")
    manager.insert("documents", {"id": "doc-a", "raw_text": "A"})
    manager.insert("documents", {"id": "doc-b", "raw_text": "B"})
    manager.insert("documents", {"id": "doc-c", "raw_text": "C"})

    rows = manager.get_by_ids("documents", ["doc-c", "missing", "doc-a", "doc-c"])

    assert [row["id"] for row in rows] == ["doc-c", "doc-a"]
    assert manager.get_by_ids("documents", []) == []
