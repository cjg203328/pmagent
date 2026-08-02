from __future__ import annotations

from pathlib import Path

import pytest

from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.workflows.designer import (
    WorkflowDraftConflictError,
    capability_allowlist_from_skill_metadata,
    list_capability_options,
    parse_input_map,
    save_workflow_draft,
)
from artpm_agent.workflows.store import WorkflowStore


def _store(tmp_path: Path) -> WorkflowStore:
    path = tmp_path / "conversations.db"
    ConversationStore(path)
    return WorkflowStore(path)


def _rows(skill_id: str = "quote_calculator") -> list[dict]:
    return [
        {
            "id": "assess",
            "skill_id": skill_id,
            "input_map": '{"quote_amount":"$input.quote_amount"}',
        }
    ]


def test_designer_saves_immutable_versions_and_skips_identical_draft(tmp_path: Path):
    store = _store(tmp_path)
    first = save_workflow_draft(
        store,
        workflow_id="visual_quote_review",
        name="Visual quote review",
        description="Review a quote from the visual editor.",
        rows=_rows(),
        trigger_keywords="quote, review, quote",
        priority=20,
    )
    repeated = save_workflow_draft(
        store,
        workflow_id="visual_quote_review",
        name="Visual quote review",
        description="Review a quote from the visual editor.",
        rows=_rows(),
        trigger_keywords=("quote", "review"),
        priority=20,
        expected_base_version=first.version,
    )
    changed = save_workflow_draft(
        store,
        workflow_id="visual_quote_review",
        name="Visual quote review",
        description="Review a quote from the visual editor with a new priority.",
        rows=_rows(),
        trigger_keywords="quote,review",
        priority=30,
        expected_base_version=first.version,
    )

    assert first.version == 1
    assert repeated.version == 1
    assert changed.version == 2
    assert changed.steps[0].capability == "quote.calculate"
    assert changed.steps[0].approval == "none"
    assert changed.steps[0].side_effect is False
    assert changed.read_only is True


def test_designer_derives_side_effect_approval_and_rejects_downgrade(tmp_path: Path):
    store = _store(tmp_path)
    definition = save_workflow_draft(
        store,
        workflow_id="visual_reminder",
        name="Visual reminder",
        description="Dispatch a reminder after confirmation.",
        rows=[
            {
                "id": "dispatch",
                "skill_id": "reminder_dispatch",
                "capability": "reminders.dispatch",
                "side_effect": False,
                "approval": "none",
                "input_map": {},
            }
        ],
        trigger_keywords="send reminder",
    )

    assert definition.steps[0].side_effect is True
    assert definition.steps[0].approval == "user"
    assert definition.read_only is False


def test_designer_blocks_unknown_pairs_builtins_and_stale_edits(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="unavailable skill"):
        save_workflow_draft(
            store,
            workflow_id="bad_skill",
            name="Bad skill",
            description="Must not save an arbitrary plugin call.",
            rows=_rows("shell_exec"),
            trigger_always=True,
        )
    with pytest.raises(ValueError, match="built-in workflows cannot be edited"):
        save_workflow_draft(
            store,
            workflow_id="quote_assessment",
            name="Replace builtin",
            description="This must fail.",
            rows=_rows(),
            trigger_always=True,
        )

    first = save_workflow_draft(
        store,
        workflow_id="stale_visual",
        name="Stale visual",
        description="Initial version.",
        rows=_rows(),
        trigger_always=True,
    )
    save_workflow_draft(
        store,
        workflow_id="stale_visual",
        name="Stale visual",
        description="Second version.",
        rows=_rows(),
        trigger_always=True,
        expected_base_version=first.version,
    )
    with pytest.raises(WorkflowDraftConflictError, match="changed"):
        save_workflow_draft(
            store,
            workflow_id="stale_visual",
            name="Stale visual",
            description="Stale editor version.",
            rows=_rows(),
            trigger_always=True,
            expected_base_version=first.version,
        )


def test_designer_scopes_versions_to_workspace_and_validates_input_json(tmp_path: Path):
    store = _store(tmp_path)
    first = save_workflow_draft(
        store,
        workflow_id="tenant_flow",
        name="Workspace A flow",
        description="Only belongs to workspace A.",
        rows=_rows(),
        trigger_keywords="workspace a",
        workspace_id="workspace-a",
    )
    second = save_workflow_draft(
        store,
        workflow_id="tenant_flow",
        name="Workspace B flow",
        description="Only belongs to workspace B.",
        rows=_rows(),
        trigger_keywords="workspace b",
        workspace_id="workspace-b",
    )

    assert first.version == second.version == 1
    assert store.get_definition("tenant_flow", workspace_id="workspace-a").name == (
        "Workspace A flow"
    )
    assert store.get_definition("tenant_flow", workspace_id="workspace-b").name == (
        "Workspace B flow"
    )
    with pytest.raises(ValueError, match="valid JSON"):
        parse_input_map("{bad json")


def test_capability_options_expose_only_allowed_policy_pairs():
    options = list_capability_options()

    assert options
    assert all(item.capability != "commands.execute" for item in options)
    dispatch = next(item for item in options if item.skill_id == "reminder_dispatch")
    assert dispatch.side_effect is True
    assert dispatch.approval == "user"


def test_plugin_workflow_capabilities_are_intersected_with_server_policy():
    allowlist = capability_allowlist_from_skill_metadata(
        [
            {
                "name": "external_reader",
                "is_plugin_skill": True,
                "capabilities": ["external.read", "commands.execute"],
                "read_only": True,
                "requires_approval": False,
            },
            {
                "name": "external_writer",
                "is_plugin_skill": True,
                "capabilities": ["external.update"],
                "read_only": False,
                "requires_approval": False,
            },
        ]
    )

    assert allowlist["external_reader"] == frozenset({"external.read"})
    assert "external_writer" not in allowlist
