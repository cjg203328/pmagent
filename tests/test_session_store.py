from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "artpm_agent"

from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.memory.session_store import SessionEntry, SessionStore
from artpm_agent.runtime.events import AgentEvent, AgentEventType, AgentMessage


def make_stores(tmp_path: Path) -> tuple[ConversationStore, SessionStore]:
    conversations = ConversationStore(tmp_path / "conversations.db")
    return conversations, SessionStore(conversations)


def insert_workspace(path: Path, workspace_id: str) -> None:
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            """
            INSERT INTO workspaces(
                id, profile_id, name, settings_json, created_at, updated_at
            ) VALUES (?, 'profile-b', 'Workspace B', '{}', ?, ?)
            """,
            (
                workspace_id,
                "2026-07-14T00:00:00+00:00",
                "2026-07-14T00:00:00+00:00",
            ),
        )


def test_append_allows_multiple_typed_entries_in_one_turn(tmp_path):
    conversations, store = make_stores(tmp_path)
    conversation = conversations.create_conversation("Session log")

    first = store.append(
        conversation["id"],
        "assistant_message",
        run_id="run-1",
        turn_id="turn-1",
        message={"role": "assistant", "content": "Working"},
    )
    second = store.append(
        conversation["id"],
        "assistant_message",
        run_id="run-1",
        turn_id="turn-1",
        message={"role": "assistant", "content": "Done"},
    )
    third = store.append(
        conversation["id"],
        "tool_call",
        run_id="run-1",
        turn_id="turn-1",
        tool_call={"id": "call-1", "name": "quote", "arguments": {"x": 1}},
    )
    fourth = store.append(
        conversation["id"],
        "tool_result",
        run_id="run-1",
        turn_id="turn-1",
        tool_result={"content": "ok", "is_error": False},
    )

    entries = store.list_entries(conversation["id"])
    assert all(isinstance(entry, SessionEntry) for entry in entries)
    assert [entry.sequence for entry in entries] == [1, 2, 3, 4]
    assert [entry.id for entry in entries] == sorted(entry.id for entry in entries)
    assert entries == [first, second, third, fourth]
    assert entries[1].message["content"] == "Done"
    assert entries[2].tool_call["arguments"] == {"x": 1}
    assert entries[3].tool_result == {"content": "ok", "is_error": False}
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")


def test_append_event_preserves_structured_runtime_fields(tmp_path):
    conversations, store = make_stores(tmp_path)
    conversation = conversations.create_conversation("Events")
    message = AgentMessage(
        role="assistant",
        content="partial",
        id="message-1",
        timestamp=12.5,
        status="pending",
        metadata={"model": "test"},
    )
    message_event = AgentEvent(
        type=AgentEventType.MESSAGE_UPDATE,
        run_id="run-event",
        turn_id="turn-event",
        timestamp=13.0,
        message=message,
        delta="ial",
        metadata={"chunk": 2},
    )
    tool_event = AgentEvent(
        type=AgentEventType.TOOL_EXECUTION_END,
        run_id="run-event",
        turn_id="turn-event",
        timestamp=14.0,
        tool_call_id="call-1",
        tool_name="lookup",
        tool_arguments={"query": "项目"},
        tool_result={"content": "结果", "details": {"count": 1}},
        is_error=False,
    )

    stored_message = store.append_event(conversation["id"], message_event)
    stored_tool = store.append_event(conversation["id"], tool_event)

    assert stored_message.entry_type == "message_update"
    assert stored_message.message == message.to_dict()
    assert stored_message.payload == {
        "event_timestamp": 13.0,
        "delta": "ial",
        "error": None,
        "is_error": False,
        "metadata": {"chunk": 2},
    }
    assert stored_tool.entry_type == "tool_execution_end"
    assert stored_tool.tool_call == {
        "id": "call-1",
        "name": "lookup",
        "arguments": {"query": "项目"},
    }
    assert stored_tool.tool_result == {
        "content": "结果",
        "details": {"count": 1},
    }


def test_append_event_preserves_empty_tool_result(tmp_path):
    conversations, store = make_stores(tmp_path)
    conversation = conversations.create_conversation("Empty tool result")
    event = AgentEvent(
        type=AgentEventType.TOOL_EXECUTION_END,
        run_id="run-empty",
        turn_id="turn-empty",
        timestamp=14.0,
        tool_call_id="call-empty",
        tool_name="lookup",
        tool_result={},
    )

    stored = store.append_event(conversation["id"], event)

    assert stored.tool_result == {}
    assert store.replay(conversation["id"])[0].tool_result == {}


def test_entries_are_isolated_by_workspace_and_conversation(tmp_path):
    conversations, store = make_stores(tmp_path)
    insert_workspace(Path(conversations.db_path), "workspace-b")
    first_conversation = conversations.create_conversation("First")
    second_conversation = conversations.create_conversation("Second")
    other_workspace_conversation = conversations.create_conversation(
        "Other workspace",
        workspace_id="workspace-b",
    )

    first = store.append(
        first_conversation["id"],
        "event",
        run_id="run-1",
        turn_id="turn-1",
        payload={"owner": "first"},
    )
    second = store.append(
        second_conversation["id"],
        "event",
        run_id="run-2",
        turn_id="turn-2",
        payload={"owner": "second"},
    )
    other = store.append(
        other_workspace_conversation["id"],
        "event",
        run_id="run-3",
        turn_id="turn-3",
        workspace_id="workspace-b",
        payload={"owner": "workspace-b"},
    )

    assert store.list_entries(first_conversation["id"]) == [first]
    assert store.list_entries(second_conversation["id"]) == [second]
    assert store.list_entries(other_workspace_conversation["id"]) == []
    assert store.list_entries(
        other_workspace_conversation["id"], workspace_id="workspace-b"
    ) == [other]
    assert first.sequence == second.sequence == other.sequence == 1

    with pytest.raises(KeyError):
        store.append(
            other_workspace_conversation["id"],
            "event",
            run_id="run-4",
            turn_id="turn-4",
        )


def test_clear_conversation_entries_is_scoped_and_validates_conversation(tmp_path):
    conversations, store = make_stores(tmp_path)
    first_conversation = conversations.create_conversation("First")
    second_conversation = conversations.create_conversation("Second")
    store.append(
        first_conversation["id"],
        "event",
        run_id="run-first",
        turn_id="turn-first",
    )
    store.append(
        second_conversation["id"],
        "event",
        run_id="run-second",
        turn_id="turn-second",
    )

    assert store.clear_conversation_entries(first_conversation["id"]) == 1
    assert store.replay(first_conversation["id"]) == []
    assert len(store.replay(second_conversation["id"])) == 1
    with pytest.raises(KeyError, match="Unknown conversation"):
        store.clear_conversation_entries("missing")


def test_concurrent_appends_allocate_gapless_sequences(tmp_path):
    conversations, store = make_stores(tmp_path)
    conversation = conversations.create_conversation("Concurrent")

    def append_entry(index: int) -> SessionEntry:
        return store.append(
            conversation["id"],
            "event",
            run_id="run-concurrent",
            turn_id="turn-concurrent",
            payload={"index": index},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        appended = list(executor.map(append_entry, range(48)))

    replayed = store.replay(conversation["id"])
    assert len(appended) == len(replayed) == 48
    assert [entry.sequence for entry in replayed] == list(range(1, 49))
    assert [entry.id for entry in replayed] == sorted(entry.id for entry in replayed)
    assert {entry.payload["index"] for entry in replayed} == set(range(48))


def test_json_round_trip_cursor_and_reopen_replay(tmp_path):
    path = tmp_path / "conversations.db"
    conversations = ConversationStore(path)
    conversation = conversations.create_conversation("Reopen")
    store = SessionStore(conversations)
    first = store.append(
        conversation["id"],
        "message",
        run_id="run-json",
        turn_id="turn-json",
        message={
            "role": "assistant",
            "content": "报价完成",
            "parts": ["文本", {"amount": 12.5}],
        },
        payload={"ok": True, "empty": None},
    )
    second = store.append(
        conversation["id"],
        "event",
        run_id="run-json",
        turn_id="turn-json",
        payload={"step": 2},
    )

    reopened = SessionStore(path)
    replayed = reopened.replay(conversation["id"])
    assert replayed == [first, second]
    assert reopened.replay(conversation["id"], after_sequence=first.sequence) == [
        second
    ]
    assert reopened.list_entries(conversation["id"], limit=1) == [first]
    assert replayed[0].message["content"] == "报价完成"
    assert replayed[0].to_dict()["payload"] == {"ok": True, "empty": None}
    assert datetime.fromisoformat(replayed[0].created_at).tzinfo is not None


def test_session_schema_migration_is_idempotent(tmp_path):
    path = tmp_path / "conversations.db"
    ConversationStore(path)
    SessionStore(path)
    SessionStore(path)

    with closing(sqlite3.connect(path)) as conn:
        versions = conn.execute(
            "SELECT version FROM session_schema_migrations ORDER BY version"
        ).fetchall()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(session_entries)")}
        foreign_keys = conn.execute(
            "PRAGMA foreign_key_list(session_entries)"
        ).fetchall()

    assert versions == [(1,)]
    assert {
        "id",
        "sequence",
        "workspace_id",
        "conversation_id",
        "entry_type",
        "run_id",
        "turn_id",
        "message_json",
        "tool_call_json",
        "tool_result_json",
        "payload_json",
        "created_at",
    } <= columns
    assert {row[3] for row in foreign_keys} == {"conversation_id", "workspace_id"}


def test_invalid_identifiers_cursors_and_json_are_rejected(tmp_path):
    conversations, store = make_stores(tmp_path)
    conversation = conversations.create_conversation("Validation")

    with pytest.raises(ValueError):
        store.append(conversation["id"], " ", run_id="run", turn_id="turn")
    with pytest.raises(KeyError):
        store.append("missing", "event", run_id="run", turn_id="turn")
    with pytest.raises(ValueError, match="payload must contain only JSON values"):
        store.append(
            conversation["id"],
            "event",
            run_id="run",
            turn_id="turn",
            payload={"unsupported": object()},
        )
    with pytest.raises(ValueError):
        store.list_entries(conversation["id"], after_sequence=-1)
    with pytest.raises(ValueError):
        store.list_entries(conversation["id"], limit=-1)
