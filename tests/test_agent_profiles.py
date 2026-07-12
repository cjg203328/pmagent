from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys

import pytest
from pydantic import ValidationError


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"
sys.path.insert(0, str(APP_ROOT))

from memory.conversation_store import ConversationStore
from profiles import (
    AgentIdentity,
    AgentIdentityPatch,
    AgentProfilePatch,
    AgentProfileStore,
    ProfileConflictError,
    QuotePolicy,
    QuotePolicyPatch,
)


def make_stores(tmp_path):
    path = tmp_path / "conversations.db"
    conversations = ConversationStore(path)
    conversation = conversations.create_conversation("Profile 配置")
    profiles = AgentProfileStore(path)
    return path, conversations, conversation, profiles


def quote_patch(**values):
    return AgentProfilePatch(quote_policy=QuotePolicyPatch(**values))


def test_profile_models_are_strict_and_validate_risk_threshold_order():
    with pytest.raises(ValidationError):
        QuotePolicy(overhead_rate="0.2")
    with pytest.raises(ValidationError):
        QuotePolicy(high_risk_below=0.2, medium_risk_below=0.1)
    with pytest.raises(ValidationError):
        QuotePolicyPatch(tax_rate=0.06, secret="not allowed")
    with pytest.raises(ValidationError):
        AgentProfilePatch()


def test_default_profile_matches_existing_business_defaults_and_builds_context(
    tmp_path,
):
    _, _, _, store = make_stores(tmp_path)

    profile = store.get_effective_profile()

    assert profile.workspace_id == "local-default"
    assert profile.profile_id == "local-default"
    assert profile.revision == 1
    assert profile.quote_policy.overhead_rate == 0.15
    assert profile.quote_policy.tax_rate == 0.06
    assert profile.quote_policy.high_risk_below == 0.05
    assert profile.quote_policy.medium_risk_below == 0.15
    inputs = profile.quote_skill_inputs()
    assert inputs["overhead_rate"] == 0.15
    assert inputs["cost_config"]["risk_thresholds"] == {
        "high_below": 0.05,
        "medium_below": 0.15,
    }
    prompt = profile.system_prompt_fragment()
    assert "ArtPM Agent" in prompt
    assert "不改变工具权限" in prompt
    assert "管理费率：15.00%" in prompt


def test_store_can_seed_profile_from_existing_global_configuration(tmp_path):
    path = tmp_path / "conversations.db"
    ConversationStore(path)
    store = AgentProfileStore(
        path,
        default_identity=AgentIdentity(display_name="美术 PM 助手"),
        default_quote_policy=QuotePolicy(overhead_rate=0.2, tax_rate=0.03),
    )

    profile = store.get_effective_profile()

    assert profile.identity.display_name == "美术 PM 助手"
    assert profile.quote_policy.overhead_rate == 0.2
    assert profile.quote_policy.tax_rate == 0.03


def test_proposal_is_inert_until_confirmation_then_persists_new_revision(tmp_path):
    path, _, conversation, store = make_stores(tmp_path)
    before = store.get_effective_profile()
    patch = AgentProfilePatch(
        identity=AgentIdentityPatch(
            display_name="工作室 PM",
            response_style="concise",
        ),
        quote_policy=QuotePolicyPatch(
            overhead_rate=0.2,
            tax_rate=0.04,
            high_risk_below=0.08,
            medium_risk_below=0.18,
        ),
    )

    proposal = store.propose_change(
        conversation["id"],
        patch,
        turn_id="turn-profile-1",
        summary="调整工作室报价策略",
        idempotency_key="profile-change-1",
    )

    assert proposal.status == "pending"
    assert proposal.base_revision == 1
    assert store.get_effective_profile() == before

    confirmed = store.confirm_change(proposal.id, actor="本地用户")
    assert confirmed.revision == 2
    assert confirmed.identity.display_name == "工作室 PM"
    assert confirmed.identity.response_style == "concise"
    assert confirmed.quote_policy.overhead_rate == 0.2
    assert confirmed.quote_policy.medium_risk_below == 0.18
    assert AgentProfileStore(path).get_effective_profile() == confirmed
    assert store.get_proposal(proposal.id).status == "confirmed"
    assert store.get_proposal(proposal.id).applied_revision == 2


def test_proposal_and_confirmation_are_idempotent(tmp_path):
    _, _, conversation, store = make_stores(tmp_path)
    patch = quote_patch(tax_rate=0.05)

    first = store.propose_change(
        conversation["id"],
        patch,
        idempotency_key="same-proposal",
    )
    repeated = store.propose_change(
        conversation["id"],
        patch,
        idempotency_key="same-proposal",
    )
    assert repeated.id == first.id

    applied = store.confirm_change(first.id, actor="用户")
    applied_again = store.confirm_change(first.id, actor="用户")
    assert applied_again == applied
    with sqlite3.connect(store.db_path) as conn:
        revisions = conn.execute(
            "SELECT revision FROM agent_profiles ORDER BY revision"
        ).fetchall()
    assert revisions == [(1,), (2,)]


def test_stale_proposal_is_marked_conflict_instead_of_overwriting(tmp_path):
    _, _, conversation, store = make_stores(tmp_path)
    first = store.propose_change(
        conversation["id"],
        quote_patch(overhead_rate=0.2),
        idempotency_key="first-change",
    )
    stale = store.propose_change(
        conversation["id"],
        quote_patch(overhead_rate=0.3),
        idempotency_key="stale-change",
    )
    store.confirm_change(first.id, actor="用户")

    with pytest.raises(ProfileConflictError, match="profile changed"):
        store.confirm_change(stale.id, actor="用户")

    assert store.get_proposal(stale.id).status == "conflict"
    assert store.get_effective_profile().quote_policy.overhead_rate == 0.2


def test_rejected_proposal_never_changes_profile(tmp_path):
    _, _, conversation, store = make_stores(tmp_path)
    before = store.get_effective_profile()
    proposal = store.propose_change(
        conversation["id"],
        quote_patch(tax_rate=0.12),
        idempotency_key="reject-change",
    )

    rejected = store.reject_change(proposal.id, actor="用户")

    assert rejected.status == "rejected"
    assert store.reject_change(proposal.id, actor="用户").status == "rejected"
    assert store.get_effective_profile() == before
    with pytest.raises(ProfileConflictError):
        store.confirm_change(proposal.id, actor="用户")


def test_profile_proposal_is_scoped_to_the_conversation_workspace(tmp_path):
    path, conversations, default_conversation, store = make_stores(tmp_path)
    now = "2026-07-12T00:00:00+00:00"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO workspaces(
                id, profile_id, name, settings_json, created_at, updated_at
            ) VALUES ('workspace-b', 'profile-b', 'Workspace B', '{}', ?, ?)
            """,
            (now, now),
        )
    other_conversation = conversations.create_conversation(
        "Workspace B conversation",
        workspace_id="workspace-b",
    )

    with pytest.raises(KeyError, match="does not belong"):
        store.propose_change(
            default_conversation["id"],
            quote_patch(tax_rate=0.02),
            workspace_id="workspace-b",
        )

    proposal = store.propose_change(
        other_conversation["id"],
        quote_patch(tax_rate=0.02),
        workspace_id="workspace-b",
    )
    assert proposal.profile_id == "profile-b"
    updated = store.confirm_change(proposal.id, actor="Workspace B 用户")
    assert updated.workspace_id == "workspace-b"
    assert store.get_effective_profile().quote_policy.tax_rate == 0.06
    assert store.get_effective_profile("workspace-b").quote_policy.tax_rate == 0.02


def test_concurrent_confirmation_creates_only_one_revision(tmp_path):
    _, _, conversation, store = make_stores(tmp_path)
    proposal = store.propose_change(
        conversation["id"],
        quote_patch(overhead_rate=0.21),
        idempotency_key="concurrent-confirm",
    )

    with ThreadPoolExecutor(max_workers=4) as executor:
        profiles = list(
            executor.map(
                lambda _: store.confirm_change(proposal.id, actor="用户"),
                range(8),
            )
        )

    assert {profile.revision for profile in profiles} == {2}
    with sqlite3.connect(store.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM agent_profiles").fetchone()[0]
    assert count == 2


def test_profile_audit_survives_conversation_deletion(tmp_path):
    _, conversations, conversation, store = make_stores(tmp_path)
    proposal = store.propose_change(
        conversation["id"],
        quote_patch(overhead_rate=0.22),
        idempotency_key="durable-audit",
    )
    store.confirm_change(proposal.id, actor="用户")

    conversations.delete_conversation(conversation["id"])

    persisted = store.get_proposal(proposal.id)
    assert persisted.status == "confirmed"
    assert persisted.conversation_id is None
    assert store.get_effective_profile().revision == 2
    assert [event["event_type"] for event in store.list_events()] == [
        "profile.created",
        "proposal.created",
        "proposal.confirmed",
    ]


def test_profile_store_requires_workspace_schema(tmp_path):
    with pytest.raises(RuntimeError, match="ConversationStore"):
        AgentProfileStore(tmp_path / "empty.db")
