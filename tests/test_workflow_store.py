from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"

from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.workflows.models import (
    WorkflowDefinition,
    WorkflowOverride,
    WorkflowStepDefinition,
    WorkflowTrigger,
)
from artpm_agent.workflows.store import WorkflowConflictError, WorkflowStore


def make_stores(tmp_path):
    path = tmp_path / "conversations.db"
    conversations = ConversationStore(path)
    conversation = conversations.create_conversation("持久运行")
    workflows = WorkflowStore(path)
    return path, conversations, conversation, workflows


def custom_definition(version=1, priority=0):
    return WorkflowDefinition(
        id="custom_review",
        version=version,
        name="自定义评审",
        description="验证自定义定义的不可变版本。",
        source="custom",
        read_only=False,
        priority=priority,
        trigger=WorkflowTrigger(always=True),
        steps=(
            WorkflowStepDefinition(
                id="review",
                skill_id="quote_calculator",
                capability="quote.calculate",
                input_map={"amount": "$input.amount"},
            ),
        ),
    )


def test_schema_is_idempotent_in_conversation_db_and_installs_builtins(tmp_path):
    path, _, _, store = make_stores(tmp_path)
    reopened = WorkflowStore(path)

    with closing(sqlite3.connect(path)) as conn, conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        versions = conn.execute(
            "SELECT version FROM workflow_schema_migrations"
        ).fetchall()
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert {
        "workflow_definitions",
        "workflow_overrides",
        "workflow_runs",
        "workflow_steps",
        "workflow_approvals",
        "workflow_events",
    }.issubset(tables)
    assert versions == [(1,), (2,), (3,)]
    assert journal_mode.lower() == "wal"
    assert {item.id for item in reopened.list_definitions()} == {
        "quote_assessment",
        "progress_check",
        "reminder_dispatch",
    }
    assert store.count_rows("workflow_definitions") == 3


def test_definition_versions_are_immutable_and_latest_is_resolved(tmp_path):
    _, _, _, store = make_stores(tmp_path)
    first = custom_definition(version=1, priority=1)
    second = custom_definition(version=2, priority=2)

    store.put_definition(first)
    store.put_definition(first)
    store.put_definition(second)

    assert store.get_definition("custom_review").version == 2
    assert [
        item.version
        for item in store.list_definitions(latest_only=False)
        if item.id == "custom_review"
    ] == [2, 1]
    changed_same_version = first.model_copy(update={"name": "偷偷覆盖"})
    with pytest.raises(ValueError, match="immutable"):
        store.put_definition(changed_same_version)


def test_override_changes_resolution_without_mutating_definition_snapshot(tmp_path):
    _, _, conversation, store = make_stores(tmp_path)
    base = store.get_definition("quote_assessment")
    store.set_override(
        WorkflowOverride(
            workflow_id=base.id,
            workflow_version=base.version,
            enabled=False,
            priority=99,
        )
    )

    resolved = store.get_definition("quote_assessment")
    assert resolved.enabled is False
    assert resolved.priority == 99
    with closing(sqlite3.connect(store.db_path)) as conn, conn:
        raw = conn.execute(
            "SELECT definition_json FROM workflow_definitions WHERE workflow_id = ?",
            (base.id,),
        ).fetchone()[0]
    assert '"enabled":true' in raw
    assert '"priority":30' in raw

    run = store.create_run(
        resolved,
        conversation["id"],
        input_data={"quote_amount": 10, "cost": 5},
        idempotency_key="snapshot-override",
    )
    assert run.definition_snapshot.enabled is False
    assert run.definition_snapshot.priority == 99


def test_clear_override_restores_definition_defaults_in_scoped_workspace(tmp_path):
    _, _, _, store = make_stores(tmp_path)
    definition = store.get_definition("quote_assessment")
    store.set_override(
        WorkflowOverride(
            workflow_id=definition.id,
            workflow_version=definition.version,
            enabled=False,
            priority=99,
        )
    )

    assert store.get_definition(definition.id).enabled is False
    assert store.clear_override(
        definition.id,
        version=definition.version,
        workspace_id="local-default",
        profile_id="local-default",
    ) is True
    restored = store.get_definition(definition.id)
    assert restored.enabled is True
    assert restored.priority == definition.priority


def test_run_snapshot_and_idempotency_key_are_persisted(tmp_path):
    _, _, conversation, store = make_stores(tmp_path)
    definition = store.get_definition("quote_assessment")

    first = store.create_run(
        definition,
        conversation["id"],
        input_data={"quote_amount": 100, "cost": 60},
        context_data={"project_id": "P1"},
        idempotency_key="turn-1",
    )
    repeated = store.create_run(
        definition,
        conversation["id"],
        input_data={"quote_amount": 100, "cost": 60},
        context_data={"project_id": "P1"},
        idempotency_key="turn-1",
    )

    assert repeated.id == first.id
    assert first.definition_snapshot == definition
    assert len(store.list_steps(first.id)) == len(definition.steps)
    with pytest.raises(ValueError, match="different inputs"):
        store.create_run(
            definition,
            conversation["id"],
            input_data={"quote_amount": 999, "cost": 60},
            context_data={"project_id": "P1"},
            idempotency_key="turn-1",
        )


def test_oversized_runtime_payload_is_rejected_before_persistence(tmp_path):
    _, _, conversation, store = make_stores(tmp_path)

    with pytest.raises(ValueError, match="payload exceeds"):
        store.create_run(
            store.get_definition("quote_assessment"),
            conversation["id"],
            input_data={"raw": "x" * (WorkflowStore.MAX_JSON_BYTES + 1)},
            idempotency_key="oversized",
        )
    assert store.count_rows("workflow_runs") == 0


def test_run_transition_uses_compare_and_swap(tmp_path):
    _, _, conversation, store = make_stores(tmp_path)
    definition = store.get_definition("quote_assessment")
    run = store.create_run(
        definition,
        conversation["id"],
        idempotency_key="cas-1",
    )

    running = store.transition_run(
        run.id,
        expected_status="pending",
        expected_version=0,
        new_status="running",
    )

    assert running.status == "running"
    assert running.state_version == 1
    with pytest.raises(WorkflowConflictError):
        store.transition_run(
            run.id,
            expected_status="pending",
            expected_version=0,
            new_status="running",
        )


def test_concurrent_run_creation_uses_one_idempotency_boundary(tmp_path):
    _, _, conversation, store = make_stores(tmp_path)
    definition = store.get_definition("quote_assessment")

    def create_once(_):
        return store.create_run(
            definition,
            conversation["id"],
            input_data={"quote_amount": 10, "cost": 5},
            idempotency_key="concurrent-turn",
        ).id

    with ThreadPoolExecutor(max_workers=8) as executor:
        run_ids = list(executor.map(create_once, range(16)))

    assert len(set(run_ids)) == 1
    assert store.count_rows("workflow_runs") == 1
    assert store.count_rows("workflow_steps") == 1


def test_unknown_conversation_is_rejected(tmp_path):
    _, _, _, store = make_stores(tmp_path)

    with pytest.raises(KeyError, match="unknown conversation"):
        store.create_run(
            store.get_definition("quote_assessment"),
            "missing-conversation",
            idempotency_key="missing-conversation",
        )


def test_run_cannot_bind_a_conversation_from_another_workspace(tmp_path):
    path, conversations, _, store = make_stores(tmp_path)
    now = "2026-07-12T00:00:00+00:00"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            """
            INSERT INTO workspaces(
                id, profile_id, name, settings_json, created_at, updated_at
            ) VALUES (?, 'local-default', ?, '{}', ?, ?)
            """,
            ("workspace-b", "Workspace B", now, now),
        )
    other = conversations.create_conversation(
        "其它工作区",
        workspace_id="workspace-b",
    )

    with pytest.raises(KeyError, match="unknown conversation"):
        store.create_run(
            store.get_definition("quote_assessment"),
            other["id"],
            idempotency_key="cross-workspace",
        )


def test_list_and_clear_conversation_runs_are_scoped(tmp_path):
    _, _, conversation, store = make_stores(tmp_path)
    definition = store.get_definition("quote_assessment")
    first = store.create_run(
        definition,
        conversation["id"],
        idempotency_key="list-1",
    )
    second = store.create_run(
        definition,
        conversation["id"],
        idempotency_key="list-2",
    )

    assert {run.id for run in store.list_runs(conversation["id"])} == {
        first.id,
        second.id,
    }
    assert store.clear_conversation_runs(conversation["id"]) == 2
    assert store.list_runs(conversation["id"]) == []
    assert store.count_rows("workflow_steps") == 0
    assert store.count_rows("workflow_events") == 0


def test_deleting_conversation_cascades_run_steps_approvals_and_events(tmp_path):
    _, conversations, conversation, store = make_stores(tmp_path)
    definition = store.get_definition("reminder_dispatch")
    run = store.create_run(
        definition,
        conversation["id"],
        input_data={"recipients": ["张三"], "tone": "formal"},
        context_data={"task_id": "T1"},
        idempotency_key="cascade-run",
    )
    store.request_approval(run.id, 0, "user")
    assert store.count_rows("workflow_runs") == 1
    assert store.count_rows("workflow_approvals") == 1
    assert store.count_rows("workflow_events") >= 2

    conversations.delete_conversation(conversation["id"])

    assert store.count_rows("workflow_runs") == 0
    assert store.count_rows("workflow_steps") == 0
    assert store.count_rows("workflow_approvals") == 0
    assert store.count_rows("workflow_events") == 0
    assert store.count_rows("workflow_definitions") == 3
