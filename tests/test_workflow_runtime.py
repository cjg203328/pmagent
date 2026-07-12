from __future__ import annotations

from pathlib import Path
import sys

import pytest
from pydantic import ValidationError


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"
sys.path.insert(0, str(APP_ROOT))

from memory.conversation_store import ConversationStore
from workflows.defaults import get_builtin_workflows
from workflows.engine import WorkflowEngine
from workflows.models import (
    AttachmentSelectionData,
    WorkflowDefinition,
    WorkflowSelectionContext,
    WorkflowStepDefinition,
    WorkflowTrigger,
)
from workflows.selector import WorkflowSelector
from workflows.store import WorkflowStore


ALLOWLIST = {
    "quote_calculator": frozenset({"quote.calculate"}),
    "progress_tracker": frozenset({"projects.read"}),
    "reminder_bot": frozenset({"reminders.dispatch"}),
    "first_skill": frozenset({"test.read"}),
    "second_skill": frozenset({"test.read"}),
}


def make_runtime(tmp_path):
    path = tmp_path / "conversations.db"
    conversations = ConversationStore(path)
    conversation = conversations.create_conversation("工作流测试")
    return path, conversations, conversation, WorkflowStore(path)


def custom_workflow(*steps, workflow_id="custom_flow", version=1, trigger=None):
    return WorkflowDefinition(
        id=workflow_id,
        version=version,
        name="自定义工作流",
        description="用于验证声明式线性执行。",
        source="custom",
        read_only=False,
        trigger=trigger or WorkflowTrigger(always=True),
        steps=tuple(steps),
    )


def test_models_are_strict_forbid_extra_and_limit_steps():
    step = WorkflowStepDefinition(
        id="step",
        skill_id="first_skill",
        capability="test.read",
    )
    with pytest.raises(ValidationError):
        WorkflowDefinition(
            id="bad",
            version="1",
            name="错误",
            description="版本不能从字符串转换。",
            source="custom",
            read_only=False,
            steps=(step,),
        )
    with pytest.raises(ValidationError):
        WorkflowStepDefinition(
            id="bad",
            skill_id="first_skill",
            capability="test.read",
            unknown=True,
        )
    with pytest.raises(ValidationError):
        custom_workflow(*([step] * 9))


def test_builtin_workflows_are_read_only_and_versioned():
    workflows = get_builtin_workflows()

    assert {workflow.id for workflow in workflows} == {
        "quote_assessment",
        "progress_check",
        "reminder_dispatch",
    }
    assert all(workflow.source == "builtin" for workflow in workflows)
    assert all(workflow.read_only and workflow.version >= 1 for workflow in workflows)
    with pytest.raises(ValidationError):
        workflows[0].name = "不能修改"


def test_selector_prefers_explicit_id_and_never_invents_a_definition():
    definitions = get_builtin_workflows()
    selector = WorkflowSelector(ALLOWLIST)
    context = WorkflowSelectionContext(
        prompt="请检查进度，但我明确选择报价评估",
        conversation_id="conversation-1",
        explicit_workflow_id="quote_assessment",
        context_data={"project_id": "P1"},
    )

    decision = selector.select(context, definitions)

    assert decision.explicit is True
    assert decision.definition.id == "quote_assessment"
    unknown = selector.select(
        context.model_copy(update={"explicit_workflow_id": "model_generated"}),
        definitions,
    )
    assert unknown.definition is None


def test_selector_applies_hard_context_rules_and_deterministic_scoring():
    selector = WorkflowSelector(ALLOWLIST)
    definitions = get_builtin_workflows()

    no_project = selector.select(
        WorkflowSelectionContext(
            prompt="检查项目进度",
            conversation_id="conversation-1",
        ),
        definitions,
    )
    assert no_project.definition is None

    progress = selector.select(
        WorkflowSelectionContext(
            prompt="检查项目进度和交付节点",
            conversation_id="conversation-1",
            context_data={"project_id": "P1"},
        ),
        definitions,
    )
    assert progress.definition.id == "progress_check"

    quote = selector.select(
        WorkflowSelectionContext(
            prompt="分析这份报价和利润",
            conversation_id="conversation-1",
            attachments=(AttachmentSelectionData(extension="xlsx"),),
        ),
        definitions,
    )
    assert quote.definition.id == "quote_assessment"


def test_selector_and_engine_reject_non_allowlisted_capabilities(tmp_path):
    _, _, conversation, store = make_runtime(tmp_path)
    unsafe = custom_workflow(
        WorkflowStepDefinition(
            id="shell",
            skill_id="execute_command",
            capability="commands.execute",
        ),
        workflow_id="unsafe",
    )
    store.put_definition(unsafe)
    selector = WorkflowSelector(ALLOWLIST)
    context = WorkflowSelectionContext(
        prompt="执行",
        conversation_id=conversation["id"],
        explicit_workflow_id="unsafe",
    )

    assert selector.select(context, [unsafe]).definition is None
    calls = []
    engine = WorkflowEngine(
        store,
        lambda skill, inputs: calls.append((skill, inputs)) or {"success": True},
        capability_allowlist=ALLOWLIST,
    )
    with pytest.raises(PermissionError):
        engine.start(unsafe, conversation["id"])
    assert calls == []


def test_engine_executes_linear_steps_and_resolves_prior_outputs(tmp_path):
    _, _, conversation, store = make_runtime(tmp_path)
    definition = custom_workflow(
        WorkflowStepDefinition(
            id="first",
            skill_id="first_skill",
            capability="test.read",
            input_map={"value": "$input.value"},
        ),
        WorkflowStepDefinition(
            id="second",
            skill_id="second_skill",
            capability="test.read",
            input_map={"value": "$steps.first.value"},
            output_key="final",
        ),
    )
    store.put_definition(definition)
    calls = []

    def execute(skill_id, inputs):
        calls.append((skill_id, inputs))
        if skill_id == "first_skill":
            return {"success": True, "value": inputs["value"] + 1}
        return {"success": True, "value": inputs["value"] * 2}

    engine = WorkflowEngine(store, execute, capability_allowlist=ALLOWLIST)
    result = engine.start(
        definition,
        conversation["id"],
        input_data={"value": 3},
        idempotency_key="linear-1",
    )

    assert result.run.status == "succeeded"
    assert result.run.outputs["first"]["value"] == 4
    assert result.run.outputs["final"]["value"] == 8
    assert [step.status for step in result.steps] == ["succeeded", "succeeded"]
    assert calls == [
        ("first_skill", {"value": 3}),
        ("second_skill", {"value": 4}),
    ]

    repeated = engine.start(
        definition,
        conversation["id"],
        input_data={"value": 3},
        idempotency_key="linear-1",
    )
    assert repeated.run.id == result.run.id
    assert len(calls) == 2


def test_side_effect_waits_for_server_approval_floor_before_calling(tmp_path):
    _, _, conversation, store = make_runtime(tmp_path)
    definition = custom_workflow(
        WorkflowStepDefinition(
            id="dispatch",
            skill_id="reminder_bot",
            capability="reminders.dispatch",
            input_map={"task_id": "$context.task_id"},
            side_effect=True,
            approval="none",
        ),
        workflow_id="admin_dispatch",
    )
    store.put_definition(definition)
    calls = []
    engine = WorkflowEngine(
        store,
        lambda skill, inputs: calls.append((skill, inputs)) or {"success": True},
        capability_allowlist=ALLOWLIST,
        approval_floor="admin",
    )

    waiting = engine.start(
        definition,
        conversation["id"],
        context_data={"task_id": "T1"},
        idempotency_key="approval-1",
    )

    assert waiting.run.status == "awaiting_approval"
    assert waiting.approval.requirement == "admin"
    assert calls == []
    with pytest.raises(PermissionError):
        engine.decide_approval(
            waiting.run.id,
            0,
            decision="approved",
            actor="普通用户",
            actor_level="user",
        )
    assert calls == []

    completed = engine.decide_approval(
        waiting.run.id,
        0,
        decision="approved",
        actor="管理员",
        actor_level="admin",
    )
    assert completed.run.status == "succeeded"
    assert len(calls) == 1
    skill_id, inputs = calls[0]
    assert skill_id == "reminder_bot"
    assert inputs["task_id"] == "T1"
    assert inputs["approved"] is True
    assert inputs["idempotency_key"].startswith(f"{waiting.run.id}:dispatch:")


def test_server_side_effect_policy_cannot_be_disabled_by_definition(tmp_path):
    _, _, conversation, store = make_runtime(tmp_path)
    definition = custom_workflow(
        WorkflowStepDefinition(
            id="hidden_effect",
            skill_id="reminder_bot",
            capability="reminders.dispatch",
            input_map={"approved": False, "idempotency_key": "user-controlled"},
            side_effect=False,
            approval="none",
        ),
        workflow_id="hidden_effect",
    )
    store.put_definition(definition)
    calls = []
    engine = WorkflowEngine(
        store,
        lambda skill, inputs: calls.append((skill, inputs)) or {"success": True},
        capability_allowlist=ALLOWLIST,
        approval_floor="none",
    )

    waiting = engine.start(
        definition,
        conversation["id"],
        idempotency_key="server-side-effect",
    )

    assert waiting.run.status == "awaiting_approval"
    assert waiting.approval.requirement == "user"
    assert calls == []
    completed = engine.decide_approval(
        waiting.run.id,
        0,
        decision="approved",
        actor="用户",
        actor_level="user",
    )
    assert completed.run.status == "succeeded"
    assert calls[0][1]["approved"] is True
    assert calls[0][1]["idempotency_key"] != "user-controlled"


def test_missing_input_and_skill_failure_are_persisted_without_retry(tmp_path):
    _, _, conversation, store = make_runtime(tmp_path)
    definition = custom_workflow(
        WorkflowStepDefinition(
            id="first",
            skill_id="first_skill",
            capability="test.read",
            input_map={"required": "$input.missing"},
        )
    )
    store.put_definition(definition)
    calls = []
    engine = WorkflowEngine(
        store,
        lambda skill, inputs: calls.append((skill, inputs)) or {"success": True},
        capability_allowlist=ALLOWLIST,
    )

    failed = engine.start(
        definition,
        conversation["id"],
        idempotency_key="missing-input",
    )

    assert failed.run.status == "failed"
    assert "missing workflow input reference" in failed.run.error
    assert calls == []
    assert engine.resume(failed.run.id).run.status == "failed"
    assert calls == []
