from pathlib import Path
import sys

import pytest


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"
sys.path.insert(0, str(APP_ROOT))

from memory.conversation_store import ConversationStore
from skills.skill_router import CAPABILITY_REGISTRY
from workflows.engine import WorkflowEngine
from workflows.models import (
    WorkflowDefinition,
    WorkflowStepDefinition,
    WorkflowTrigger,
)
from workflows.risk_policy import (
    DEFAULT_RISK_POLICY,
    CapabilityRiskPolicy,
    audit_skill_capability_registry,
)
from workflows.store import WorkflowStore


@pytest.mark.parametrize(
    ("capability", "operation"),
    [
        ("documents.read", "read"),
        ("quote.calculate", "analyze"),
        ("tasks.plan", "generate"),
        ("artifacts.generate", "generate"),
        ("custom.search", "read"),
        ("custom.summarize", "analyze"),
        ("custom.preview", "generate"),
    ],
)
def test_read_analysis_and_generation_are_low_risk(capability, operation):
    rule = DEFAULT_RISK_POLICY.resolve(capability)

    assert rule.operation == operation
    assert rule.risk == "low"
    assert rule.read_only is True
    assert rule.allowed is True
    assert rule.minimum_approval == "none"
    assert rule.confirmation_scope == "none"


@pytest.mark.parametrize(
    ("capability", "operation"),
    [
        ("files.overwrite", "overwrite"),
        ("files.delete", "delete"),
        ("reminders.dispatch", "external_send"),
        ("data.bulk_change", "bulk_change"),
        ("custom.write", "update"),
        ("custom.import", "bulk_change"),
    ],
)
def test_persistent_or_external_changes_require_conversation_confirmation(
    capability,
    operation,
):
    rule = DEFAULT_RISK_POLICY.resolve(capability)

    assert rule.operation == operation
    assert rule.risk == "high"
    assert rule.read_only is False
    assert rule.allowed is True
    assert rule.minimum_approval == "user"
    assert rule.confirmation_scope == "conversation"


@pytest.mark.parametrize("capability", ["files.create", "data.create"])
def test_creating_persistent_data_is_not_treated_as_in_memory_generation(capability):
    rule = DEFAULT_RISK_POLICY.resolve(capability)

    assert rule.operation == "create"
    assert rule.risk == "medium"
    assert rule.read_only is False
    assert rule.minimum_approval == "user"
    assert rule.confirmation_scope == "conversation"


@pytest.mark.parametrize("capability", ["reminders.dispatch", "data.bulk_change"])
def test_external_and_bulk_changes_require_idempotency(capability):
    assert DEFAULT_RISK_POLICY.resolve(capability).requires_idempotency is True


def test_unknown_and_command_capabilities_fail_closed():
    command = DEFAULT_RISK_POLICY.resolve("commands.execute")
    unknown = DEFAULT_RISK_POLICY.resolve("vendor.magic")

    assert command.allowed is False
    assert command.risk == "critical"
    assert command.minimum_approval == "admin"
    assert unknown.allowed is False
    assert unknown.risk == "untrusted"
    assert unknown.minimum_approval == "admin"


def test_effective_approval_cannot_be_downgraded_by_definition():
    policy = CapabilityRiskPolicy()

    assert policy.effective_approval(
        "files.delete",
        declared_side_effect=False,
        declared_approval="none",
        approval_floor="none",
    ) == "user"
    assert policy.effective_approval(
        "documents.read",
        declared_side_effect=True,
        declared_approval="none",
        approval_floor="none",
    ) == "user"
    assert policy.effective_approval(
        "reminders.dispatch",
        declared_approval="none",
        approval_floor="admin",
    ) == "admin"


def make_definition(capability: str, skill_id: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=f"risk_{skill_id}",
        version=1,
        name="风险策略测试",
        description="验证服务端风险策略不能被定义降级。",
        source="custom",
        read_only=False,
        trigger=WorkflowTrigger(always=True),
        steps=(
            WorkflowStepDefinition(
                id="sensitive_step",
                skill_id=skill_id,
                capability=capability,
                input_map={"approved": False},
                side_effect=False,
                approval="none",
            ),
        ),
    )


def make_engine(tmp_path, definition, execute):
    path = tmp_path / "conversations.db"
    conversations = ConversationStore(path)
    conversation = conversations.create_conversation("风险测试")
    store = WorkflowStore(path)
    store.put_definition(definition)
    engine = WorkflowEngine(
        store,
        execute,
        capability_allowlist={
            definition.steps[0].skill_id: frozenset(
                {definition.steps[0].capability}
            )
        },
        approval_floor="none",
    )
    return conversation, engine


def test_engine_requires_persisted_confirmation_for_hidden_file_overwrite(tmp_path):
    definition = make_definition("files.overwrite", "file_writer")
    calls = []
    conversation, engine = make_engine(
        tmp_path,
        definition,
        lambda skill, inputs: calls.append((skill, inputs)) or {"success": True},
    )

    waiting = engine.start(
        definition,
        conversation["id"],
        idempotency_key="overwrite-once",
    )

    assert waiting.run.conversation_id == conversation["id"]
    assert waiting.run.status == "awaiting_approval"
    assert waiting.approval.requirement == "user"
    assert calls == []

    completed = engine.decide_approval(
        waiting.run.id,
        0,
        decision="approved",
        actor="当前用户",
        actor_level="user",
    )

    assert completed.run.status == "succeeded"
    assert len(calls) == 1
    assert calls[0][1]["approved"] is True
    assert calls[0][1]["idempotency_key"].startswith(waiting.run.id)


@pytest.mark.parametrize("capability", ["commands.execute", "vendor.magic"])
def test_engine_blocks_forbidden_capability_even_when_allowlisted(
    tmp_path,
    capability,
):
    definition = make_definition(capability, "unsafe_tool")
    calls = []
    conversation, engine = make_engine(
        tmp_path,
        definition,
        lambda skill, inputs: calls.append((skill, inputs)) or {"success": True},
    )

    with pytest.raises(PermissionError):
        engine.start(definition, conversation["id"])

    assert calls == []


def test_current_skill_metadata_matches_the_unified_policy():
    assert audit_skill_capability_registry(CAPABILITY_REGISTRY) == ()


def test_registry_audit_reports_approval_and_read_only_downgrades():
    metadata = {
        skill_id: dict(values) for skill_id, values in CAPABILITY_REGISTRY.items()
    }
    metadata["reminder_dispatch"]["requires_approval"] = False
    metadata["reminder_dispatch"]["read_only"] = True

    findings = audit_skill_capability_registry(metadata)
    codes = {finding.code for finding in findings}

    assert "approval_downgrade" in codes
    assert "read_only_mismatch" in codes
