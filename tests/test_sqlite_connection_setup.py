"""A failed SQLite setup must not leave a live handle behind."""

import pytest

from artpm_agent.memory import conversation_store as conversation_store_module
from artpm_agent.memory import session_store as session_store_module
from artpm_agent.memory import sqlite_manager as sqlite_manager_module
from artpm_agent.memory import workspace_knowledge_store as knowledge_store_module
from artpm_agent.profiles import store as profile_store_module
from artpm_agent.workflows import store as workflow_store_module


class _FailingSetupConnection:
    def __init__(self):
        self.closed = False
        self.row_factory = None

    def execute(self, *_args, **_kwargs):
        raise RuntimeError("PRAGMA setup failed")

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("module", "store_type"),
    [
        (conversation_store_module, conversation_store_module.ConversationStore),
        (session_store_module, session_store_module.SessionStore),
        (profile_store_module, profile_store_module.AgentProfileStore),
        (workflow_store_module, workflow_store_module.WorkflowStore),
        (knowledge_store_module, knowledge_store_module.WorkspaceKnowledgeStore),
    ],
)
def test_store_connect_closes_when_pragma_setup_fails(
    monkeypatch, tmp_path, module, store_type
):
    connection = _FailingSetupConnection()
    monkeypatch.setattr(module.sqlite3, "connect", lambda *_args, **_kwargs: connection)
    store = store_type.__new__(store_type)
    store.db_path = str(tmp_path / "store.db")

    with pytest.raises(RuntimeError, match="PRAGMA setup failed"):
        store._connect()

    assert connection.closed is True


def test_sqlite_manager_context_closes_when_initialization_fails(monkeypatch, tmp_path):
    connection = _FailingSetupConnection()
    monkeypatch.setattr(
        sqlite_manager_module.sqlite3,
        "connect",
        lambda *_args, **_kwargs: connection,
    )
    manager = sqlite_manager_module.SQLiteManager.__new__(
        sqlite_manager_module.SQLiteManager
    )
    manager.db_path = str(tmp_path / "manager.db")

    with pytest.raises(RuntimeError, match="PRAGMA setup failed"):
        with manager.get_connection():
            raise AssertionError("the context body must not run")

    assert connection.closed is True
