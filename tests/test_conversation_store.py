from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "artpm_agent"
sys.path.insert(0, str(APP_ROOT))

from memory.conversation_store import ConversationStore


def make_store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "conversations.db")


def insert_workspace(
    path: Path,
    workspace_id: str,
    *,
    profile_id: str = "profile-b",
    name: str = "工作区 B",
) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO workspaces(
                id, profile_id, name, settings_json, created_at, updated_at
            ) VALUES (?, ?, ?, '{}', ?, ?)
            """,
            (
                workspace_id,
                profile_id,
                name,
                "2026-07-12T00:00:00+00:00",
                "2026-07-12T00:00:00+00:00",
            ),
        )


def create_v1_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE chat_schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            INSERT INTO chat_schema_migrations(version, applied_at)
            VALUES (1, '2026-07-11T00:00:00+00:00');

            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'complete',
                model_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                    ON DELETE CASCADE,
                UNIQUE(conversation_id, turn_id, role)
            );
            INSERT INTO conversations(id, title, created_at, updated_at)
            VALUES (
                'legacy-conversation',
                '旧会话',
                '2026-07-11T00:00:00+00:00',
                '2026-07-11T00:00:00+00:00'
            );
            INSERT INTO messages(
                conversation_id, turn_id, role, content, status,
                metadata_json, created_at
            ) VALUES (
                'legacy-conversation', 'legacy-turn', 'user', '旧消息',
                'complete', '{}', '2026-07-11T00:00:00+00:00'
            );
            """
        )


def test_default_workspace_is_created_and_settings_persist(tmp_path):
    path = tmp_path / "conversations.db"
    store = ConversationStore(path)

    workspace = store.get_workspace()
    assert workspace["id"] == ConversationStore.DEFAULT_WORKSPACE_ID
    assert workspace["profile_id"] == ConversationStore.DEFAULT_PROFILE_ID
    assert workspace["name"] == ConversationStore.DEFAULT_WORKSPACE_NAME
    assert workspace["settings"] == {}
    assert [item["id"] for item in store.list_workspaces()] == [
        ConversationStore.DEFAULT_WORKSPACE_ID
    ]

    updated = store.update_workspace_settings(
        ConversationStore.DEFAULT_WORKSPACE_ID,
        {"workflow": {"context_mode": "recent"}},
    )
    assert updated["settings"] == {"workflow": {"context_mode": "recent"}}
    assert ConversationStore(path).get_workspace()["settings"] == updated["settings"]

    with pytest.raises(ValueError):
        store.update_workspace_settings(ConversationStore.DEFAULT_WORKSPACE_ID, [])
    with pytest.raises(KeyError):
        store.update_workspace_settings("missing", {})


def test_conversations_and_messages_are_scoped_to_workspace(tmp_path):
    path = tmp_path / "conversations.db"
    store = ConversationStore(path)
    insert_workspace(path, "workspace-b")

    default_conversation = store.create_conversation("默认会话")
    other_conversation = store.create_conversation(
        "另一个工作区",
        workspace_id="workspace-b",
    )
    other_message = store.add_message(
        other_conversation["id"],
        "user",
        "隔离消息",
        workspace_id="workspace-b",
    )

    assert default_conversation["workspace_id"] == "local-default"
    assert other_conversation["workspace_id"] == "workspace-b"
    assert [item["id"] for item in store.list_conversations()] == [
        default_conversation["id"]
    ]
    assert [
        item["id"]
        for item in store.list_conversations(workspace_id="workspace-b")
    ] == [other_conversation["id"]]
    assert store.get_conversation(other_conversation["id"]) is None
    assert store.get_conversation(
        other_conversation["id"], workspace_id="workspace-b"
    )["title"] == "另一个工作区"
    assert store.list_messages(other_conversation["id"]) == []
    assert store.list_messages(
        other_conversation["id"], workspace_id="workspace-b"
    ) == [other_message]
    assert store.build_context(other_conversation["id"]) == []
    assert store.build_context(
        other_conversation["id"], workspace_id="workspace-b"
    ) == [{"role": "user", "content": "隔离消息"}]

    with pytest.raises(KeyError):
        store.create_conversation(workspace_id="missing")
    with pytest.raises(KeyError):
        store.add_message(other_conversation["id"], "assistant", "越界")
    with pytest.raises(KeyError):
        store.clear_conversation(other_conversation["id"])
    assert store.delete_message(other_message["id"]) is False
    assert store.delete_conversation(other_conversation["id"]) is False
    assert store.delete_conversation(
        other_conversation["id"], workspace_id="workspace-b"
    ) is True


def test_v1_data_migrates_to_default_workspace_with_foreign_keys(tmp_path):
    path = tmp_path / "conversations.db"
    create_v1_database(path)

    store = ConversationStore(path)

    conversation = store.get_conversation("legacy-conversation")
    assert conversation["workspace_id"] == ConversationStore.DEFAULT_WORKSPACE_ID
    assert [item["content"] for item in store.list_messages(conversation["id"])] == [
        "旧消息"
    ]
    appended = store.add_message(conversation["id"], "assistant", "迁移后消息")
    assert appended["id"] > 1

    with sqlite3.connect(path) as conn:
        columns = {
            row[1]: row for row in conn.execute("PRAGMA table_info(conversations)")
        }
        conversation_foreign_keys = conn.execute(
            "PRAGMA foreign_key_list(conversations)"
        ).fetchall()
        message_foreign_keys = conn.execute(
            "PRAGMA foreign_key_list(messages)"
        ).fetchall()
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()

    assert columns["workspace_id"][3] == 1
    assert columns["workspace_id"][4] == "'local-default'"
    assert any(
        row[2] == "workspaces" and row[3] == "workspace_id"
        for row in conversation_foreign_keys
    )
    assert any(
        row[2] == "conversations"
        and row[3] == "conversation_id"
        and row[6].upper() == "CASCADE"
        for row in message_foreign_keys
    )
    assert violations == []

    assert store.delete_conversation(conversation["id"]) is True
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_conversation_lifecycle_and_clear(tmp_path):
    store = make_store(tmp_path)
    first = store.create_conversation(" 第一条对话 ")
    second = store.create_conversation()

    assert first["title"] == "第一条对话"
    assert store.get_conversation(first["id"])["id"] == first["id"]
    assert {item["id"] for item in store.list_conversations()} == {
        first["id"],
        second["id"],
    }

    renamed = store.rename_conversation(first["id"], "项目报价")
    assert renamed["title"] == "项目报价"

    store.add_message(first["id"], "user", "请计算报价")
    store.add_message(first["id"], "assistant", "可以")
    assert store.clear_conversation(first["id"]) == 2
    assert store.list_messages(first["id"]) == []
    assert store.get_conversation(first["id"]) is not None

    assert store.delete_conversation(first["id"]) is True
    assert store.delete_conversation(first["id"]) is False
    assert store.get_conversation(first["id"]) is None


def test_messages_persist_with_order_metadata_and_before_cursor(tmp_path):
    path = tmp_path / "conversations.db"
    store = ConversationStore(path)
    conversation = store.create_conversation("持久化")
    turn_id = "turn-1"
    first = store.add_message(
        conversation["id"],
        "user",
        "第一问",
        turn_id=turn_id,
        metadata={"project_id": 7},
    )
    second = store.add_message(
        conversation["id"],
        "assistant",
        "第一答",
        turn_id=turn_id,
        model_id="model-a",
    )
    third = store.add_message(conversation["id"], "user", "第二问")

    reopened = ConversationStore(path)
    messages = reopened.list_messages(conversation["id"])
    assert [item["content"] for item in messages] == ["第一问", "第一答", "第二问"]
    assert messages[0]["metadata"] == {"project_id": 7}
    assert messages[1]["model_id"] == "model-a"
    assert [item["id"] for item in reopened.list_messages(
        conversation["id"], before_message_id=third["id"]
    )] == [first["id"], second["id"]]
    assert reopened.list_messages(conversation["id"], limit=2) == messages[-2:]

    assert reopened.delete_message(second["id"]) is True
    assert reopened.delete_message(second["id"]) is False
    assert [item["content"] for item in reopened.list_messages(conversation["id"])] == [
        "第一问",
        "第二问",
    ]


def test_context_is_scoped_filtered_and_bounded(tmp_path):
    store = make_store(tmp_path)
    conversation = store.create_conversation("上下文")
    other = store.create_conversation("其他")
    first = store.add_message(conversation["id"], "user", "第一问")
    store.add_message(conversation["id"], "assistant", "第一答")
    store.add_message(conversation["id"], "assistant", "失败内容", status="error")
    store.add_message(conversation["id"], "assistant", "生成中", status="pending")
    current = store.add_message(conversation["id"], "user", "当前问题")
    store.add_message(other["id"], "user", "不能串到其他会话")

    assert store.build_context(
        conversation["id"], before_message_id=current["id"]
    ) == [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
    ]
    assert store.build_context(conversation["id"], max_messages=1) == [
        {"role": "user", "content": "当前问题"}
    ]
    assert store.build_context(
        conversation["id"], max_messages=10, max_chars=2
    ) == [{"role": "user", "content": "当前"}]
    assert store.build_context(other["id"])[0]["content"] == "不能串到其他会话"
    assert first["id"] < current["id"]


def test_delete_conversation_cascades_messages(tmp_path):
    path = tmp_path / "conversations.db"
    store = ConversationStore(path)
    conversation = store.create_conversation()
    store.add_message(conversation["id"], "user", "需要删除")

    assert store.delete_conversation(conversation["id"]) is True
    with sqlite3.connect(path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
            (conversation["id"],),
        ).fetchone()[0]
    assert count == 0


def test_schema_migration_is_idempotent_and_wal_is_enabled(tmp_path):
    path = tmp_path / "conversations.db"
    ConversationStore(path)
    ConversationStore(path)

    with sqlite3.connect(path) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        versions = conn.execute(
            "SELECT version FROM chat_schema_migrations ORDER BY version"
        ).fetchall()
    assert journal_mode.lower() == "wal"
    assert versions == [(1,), (2,)]


def test_concurrent_message_writes_do_not_lose_data(tmp_path):
    store = make_store(tmp_path)
    conversation = store.create_conversation("并发")

    def write_message(index: int):
        return store.add_message(
            conversation["id"],
            "user",
            f"消息 {index}",
            turn_id=f"turn-{index}",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        messages = list(executor.map(write_message, range(32)))

    stored = store.list_messages(conversation["id"])
    assert len(messages) == 32
    assert len(stored) == 32
    assert {item["content"] for item in stored} == {f"消息 {i}" for i in range(32)}


def test_invalid_values_and_unknown_conversations_are_rejected(tmp_path):
    store = make_store(tmp_path)
    conversation = store.create_conversation()

    with pytest.raises(ValueError):
        store.create_conversation(" ")
    with pytest.raises(ValueError):
        store.rename_conversation(conversation["id"], "x" * 81)
    with pytest.raises(ValueError):
        store.add_message(conversation["id"], "invalid", "内容")
    with pytest.raises(ValueError):
        store.add_message(conversation["id"], "user", " ")
    with pytest.raises(ValueError):
        store.add_message(conversation["id"], "user", "内容", status="unknown")
    with pytest.raises(KeyError):
        store.add_message("missing", "user", "内容")
    with pytest.raises(KeyError):
        store.clear_messages("missing")


def test_turn_role_pair_is_idempotency_boundary(tmp_path):
    store = make_store(tmp_path)
    conversation = store.create_conversation()
    store.add_message(conversation["id"], "user", "问题", turn_id="turn-1")
    store.add_message(conversation["id"], "assistant", "回答", turn_id="turn-1")

    with pytest.raises(sqlite3.IntegrityError):
        store.add_message(conversation["id"], "user", "重复问题", turn_id="turn-1")
