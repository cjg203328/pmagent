from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.runtime.counters import counter_snapshot, reset_counters
from artpm_agent.workflows.defaults import get_builtin_workflows
from artpm_agent.workflows.engine import WorkflowEngine
from artpm_agent.workflows.models import (
    AttachmentSelectionData,
    WorkflowDefinition,
    WorkflowSelectionContext,
    WorkflowStepDefinition,
    WorkflowTrigger,
)
from artpm_agent.workflows.selector import WorkflowSelector
from artpm_agent.workflows.store import WorkflowStore


class _SessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name, value):
        self[name] = value


def test_ui_workflow_coordinator_rebinds_when_tenant_changes(monkeypatch):
    from artpm_agent import ui_helpers as helpers
    from artpm_agent.tenancy import TenantContext

    class Store:
        pass

    class Router:
        def __init__(self, tenant_id=None):
            self.tenant_id = tenant_id

        def for_tenant(self, context):
            return Router(context.tenant_id)

    class Coordinator:
        def __init__(
            self,
            _store,
            agent,
            *,
            workspace_id,
            profile_id,
            tenant_id,
        ):
            self.agent = agent
            self.workspace_id = workspace_id
            self.profile_id = profile_id
            self.tenant_id = tenant_id

    store = Store()
    agent = SimpleNamespace(router=Router())
    state = _SessionState(
        agent=agent,
        tenant_context=TenantContext(
            tenant_id="tenant-a",
            workspace_id="shared-workspace",
            principal_id="alice",
        ),
    )
    monkeypatch.setattr(helpers, "st", SimpleNamespace(session_state=state))
    monkeypatch.setattr(helpers, "WORKFLOW_RUNTIME_AVAILABLE", True)
    monkeypatch.setattr(helpers, "get_workflow_store", lambda: store)
    monkeypatch.setattr(
        helpers,
        "get_current_profile",
        lambda: SimpleNamespace(profile_id="profile-a"),
    )
    monkeypatch.setattr(helpers, "WorkflowCoordinator", Coordinator)

    first = helpers.get_workflow_coordinator()
    state.tenant_context = TenantContext(
        tenant_id="tenant-b",
        workspace_id="shared-workspace",
        principal_id="bob",
    )
    second = helpers.get_workflow_coordinator()

    assert first is not second
    assert first.tenant_id == "tenant-a"
    assert first.agent.router.tenant_id == "tenant-a"
    assert second.tenant_id == "tenant-b"
    assert second.agent.router.tenant_id == "tenant-b"


def test_ui_workflow_coordinator_accepts_legacy_session_local_router(monkeypatch):
    from artpm_agent import ui_helpers as helpers
    from artpm_agent.tenancy import TenantContext

    class Coordinator:
        def __init__(
            self,
            _store,
            agent,
            *,
            workspace_id,
            profile_id,
            tenant_id,
        ):
            self.agent = agent
            self.workspace_id = workspace_id
            self.profile_id = profile_id
            self.tenant_id = tenant_id

    router = SimpleNamespace(execute_skill=lambda *_args, **_kwargs: None)
    agent = SimpleNamespace(router=router)
    state = _SessionState(
        agent=agent,
        tenant_context=TenantContext.local(),
    )
    monkeypatch.setattr(helpers, "st", SimpleNamespace(session_state=state))
    monkeypatch.setattr(helpers, "WORKFLOW_RUNTIME_AVAILABLE", True)
    monkeypatch.setattr(helpers, "get_workflow_store", object)
    monkeypatch.setattr(
        helpers,
        "get_current_profile",
        lambda: SimpleNamespace(profile_id="profile-a"),
    )
    monkeypatch.setattr(helpers, "WorkflowCoordinator", Coordinator)

    coordinator = helpers.get_workflow_coordinator()

    assert coordinator is not None
    assert coordinator.agent.base_agent is agent
    assert coordinator.agent.router is router


def test_ui_learning_services_are_lazy_and_session_cached(monkeypatch, tmp_path):
    from artpm_agent import ui_state
    from artpm_agent.harness import outcome_recorder
    from artpm_agent.memory import consolidation, episode_store

    state = _SessionState()
    monkeypatch.setitem(
        sys.modules,
        "streamlit",
        SimpleNamespace(session_state=state),
    )
    episode_calls = []
    consolidation_calls = []

    class EpisodeStore:
        def __init__(self, path):
            episode_calls.append(path)

    class ConsolidationScheduler:
        def __init__(self):
            consolidation_calls.append(True)

    episode_path = tmp_path / "episodes.sqlite"
    monkeypatch.setattr(outcome_recorder, "default_episode_db_path", lambda: episode_path)
    monkeypatch.setattr(episode_store, "EpisodeStore", EpisodeStore)
    monkeypatch.setattr(consolidation, "ConsolidationScheduler", ConsolidationScheduler)

    first_episode = ui_state.get_episode_store()
    second_episode = ui_state.get_episode_store()
    first_scheduler = ui_state.get_consolidation_scheduler()
    second_scheduler = ui_state.get_consolidation_scheduler()

    assert first_episode is second_episode
    assert first_scheduler is second_scheduler
    assert episode_calls == [episode_path]
    assert consolidation_calls == [True]


def test_ui_artifacts_follow_factory_tenant_workspace_and_profile_scope(monkeypatch):
    from artpm_agent import ui_state
    from artpm_agent.tenancy import TenantContext

    state = _SessionState(
        tenant_context=TenantContext(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_id="alice",
        ),
        profile_id="profile-a",
    )
    monkeypatch.setitem(
        sys.modules,
        "streamlit",
        SimpleNamespace(session_state=state),
    )
    calls = []

    class Factory:
        def __init__(self):
            self.generators = {}
            self.coordinators = {}

        @staticmethod
        def _key(tenant_context, profile_id):
            return (
                tenant_context.tenant_id,
                tenant_context.workspace_id,
                profile_id,
            )

        def artifact_generator(self, tenant_context, *, profile_id):
            key = self._key(tenant_context, profile_id)
            calls.append(("generator", key))
            return self.generators.setdefault(key, SimpleNamespace(scope=key))

        def artifact_coordinator(self, tenant_context, *, profile_id):
            key = self._key(tenant_context, profile_id)
            calls.append(("coordinator", key))
            return self.coordinators.setdefault(
                key,
                SimpleNamespace(
                    scope=key,
                    generator=self.generators.setdefault(
                        key,
                        SimpleNamespace(scope=key),
                    ),
                ),
            )

    factory = Factory()
    monkeypatch.setattr(ui_state, "_get_runtime_factory", lambda: factory)
    monkeypatch.setattr(ui_state, "ARTIFACT_RUNTIME_AVAILABLE", True)

    first_generator = ui_state.get_artifact_generator()
    first_coordinator = ui_state.get_artifact_coordinator()
    state.profile_id = "profile-b"
    second_generator = ui_state.get_artifact_generator()
    second_coordinator = ui_state.get_artifact_coordinator()

    assert first_coordinator.generator is first_generator
    assert second_coordinator.generator is second_generator
    assert first_generator is not second_generator
    assert first_coordinator is not second_coordinator
    assert calls == [
        ("generator", ("tenant-a", "workspace-a", "profile-a")),
        ("coordinator", ("tenant-a", "workspace-a", "profile-a")),
        ("generator", ("tenant-a", "workspace-a", "profile-b")),
        ("coordinator", ("tenant-a", "workspace-a", "profile-b")),
    ]
    assert state.artifact_generator is second_generator
    assert state.artifact_coordinator is second_coordinator


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"


def test_workflow_engine_construction_is_counted(tmp_path):
    reset_counters()
    WorkflowEngine(
        WorkflowStore(tmp_path / "counted-engine.sqlite"),
        lambda _skill, _inputs: {"success": True},
    )

    assert counter_snapshot()["runtime.workflow_engine.constructed"] == 1
    reset_counters()


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


def retryable_step(**overrides):
    values = {
        "id": "retry",
        "skill_id": "first_skill",
        "capability": "test.read",
        "on_error": "retry",
        "retryable": True,
        "idempotent": True,
        "max_retries": 2,
        "retry_backoff": "none",
    }
    values.update(overrides)
    return WorkflowStepDefinition(**values)


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


def test_retry_contract_requires_explicit_bounded_idempotency():
    with pytest.raises(ValidationError, match="retryable, idempotent"):
        WorkflowStepDefinition(
            id="retry_without_contract",
            skill_id="first_skill",
            capability="test.read",
            on_error="retry",
            retryable=True,
            max_retries=1,
        )

    with pytest.raises(ValidationError, match="retryable and idempotent"):
        WorkflowStepDefinition(
            id="bounded_without_contract",
            skill_id="first_skill",
            capability="test.read",
            max_retries=1,
        )


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


def test_idempotent_side_effect_can_retry_normal_failure_with_fixed_key(tmp_path):
    _, _, conversation, store = make_runtime(tmp_path)
    definition = custom_workflow(
        WorkflowStepDefinition(
            id="dispatch",
            skill_id="reminder_bot",
            capability="reminders.dispatch",
            side_effect=True,
            approval="user",
            on_error="retry",
            retryable=True,
            idempotent=True,
            max_retries=1,
            retry_backoff="none",
        ),
        workflow_id="retry_side_effect",
    )
    store.put_definition(definition)
    calls = []

    def execute(_skill_id, inputs):
        calls.append(inputs)
        if len(calls) == 1:
            raise TimeoutError("dispatch timed out")
        return {"success": True}

    engine = WorkflowEngine(store, execute, capability_allowlist=ALLOWLIST)
    waiting = engine.start(
        definition,
        conversation["id"],
        idempotency_key="retry-side-effect",
    )
    assert waiting.run.status == "awaiting_approval"

    result = engine.decide_approval(
        waiting.run.id,
        0,
        decision="approved",
        actor="user",
        actor_level="user",
    )

    assert result.run.status == "succeeded"
    assert len(calls) == 2
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]
    assert any(
        event.event_type == "step.retry_scheduled"
        for event in store.list_events(result.run.id)
    )


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


def test_transient_failure_retries_only_with_an_explicit_idempotency_contract(tmp_path):
    _, _, conversation, store = make_runtime(tmp_path)
    definition = custom_workflow(retryable_step(), workflow_id="retry_once")
    store.put_definition(definition)
    calls = []

    def execute(_skill_id, _inputs):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("upstream timed out")
        return {"success": True, "value": "ok"}

    engine = WorkflowEngine(store, execute, capability_allowlist=ALLOWLIST)
    result = engine.start(
        definition,
        conversation["id"],
        idempotency_key="retry-once",
    )

    assert result.run.status == "succeeded"
    assert len(calls) == 2
    assert result.steps[0].attempt == 2
    assert any(
        event.event_type == "step.retry_scheduled"
        for event in store.list_events(result.run.id)
    )


def test_retry_limit_persists_terminal_failure(tmp_path):
    _, _, conversation, store = make_runtime(tmp_path)
    definition = custom_workflow(
        retryable_step(max_retries=1),
        workflow_id="retry-limit",
    )
    store.put_definition(definition)
    calls = []
    engine = WorkflowEngine(
        store,
        lambda _skill_id, _inputs: calls.append(1)
        or (_ for _ in ()).throw(ConnectionError("network unavailable")),
        capability_allowlist=ALLOWLIST,
    )

    result = engine.start(
        definition,
        conversation["id"],
        idempotency_key="retry-limit",
    )

    assert result.run.status == "failed"
    assert len(calls) == 2
    assert result.steps[0].attempt == 2
    assert result.steps[0].error_class == "network"


def test_backoff_pauses_before_retry(tmp_path):
    _, _, conversation, store = make_runtime(tmp_path)
    definition = custom_workflow(
        retryable_step(retry_backoff="fixed", retry_backoff_seconds=60),
        workflow_id="retry-delay",
    )
    store.put_definition(definition)
    calls = []
    engine = WorkflowEngine(
        store,
        lambda _skill_id, _inputs: calls.append(1)
        or (_ for _ in ()).throw(TimeoutError("timeout")),
        capability_allowlist=ALLOWLIST,
    )

    result = engine.start(
        definition,
        conversation["id"],
        idempotency_key="retry-delay",
    )

    assert result.run.status == "pending"
    assert result.steps[0].next_retry_at is not None
    assert len(calls) == 1
    assert engine.resume(result.run.id).run.status == "pending"
    assert len(calls) == 1


def test_stale_safe_step_is_recovered_but_stale_side_effect_is_not_replayed(tmp_path):
    _, _, conversation, store = make_runtime(tmp_path)
    safe = custom_workflow(
        retryable_step(),
        workflow_id="stale-safe",
    )
    store.put_definition(safe)
    safe_run = store.create_run(safe, conversation["id"], idempotency_key="stale-safe")
    store.claim_step(safe_run.id, 0, {}, tenant_id="local")
    safe_calls = []
    safe_engine = WorkflowEngine(
        store,
        lambda _skill_id, _inputs: safe_calls.append(1)
        or {"success": True},
        capability_allowlist=ALLOWLIST,
        stale_after_seconds=0,
    )

    safe_result = safe_engine.resume(safe_run.id)

    assert safe_result.run.status == "succeeded"
    assert safe_calls == [1]
    assert any(
        event.event_type == "step.recovered"
        for event in store.list_events(safe_run.id)
    )

    side_effect = custom_workflow(
        WorkflowStepDefinition(
            id="send",
            skill_id="reminder_bot",
            capability="test.read",
            side_effect=True,
            compensation_skill_id="undo_send",
        ),
        workflow_id="stale-side-effect",
    )
    store.put_definition(side_effect)
    side_run = store.create_run(
        side_effect,
        conversation["id"],
        idempotency_key="stale-side-effect",
    )
    store.claim_step(side_run.id, 0, {}, tenant_id="local")
    side_calls = []
    side_engine = WorkflowEngine(
        store,
        lambda _skill_id, _inputs: side_calls.append(1) or {"success": True},
        capability_allowlist={"reminder_bot": {"test.read"}},
        stale_after_seconds=0,
    )

    side_result = side_engine.resume(side_run.id)

    assert side_result.run.status == "failed"
    assert side_calls == []
    assert any(
        event.event_type == "compensation.required"
        for event in store.list_events(side_run.id)
    )


def test_stale_policy_side_effect_is_not_replayed_when_definition_omits_flag(tmp_path):
    _, _, conversation, store = make_runtime(tmp_path)
    definition = custom_workflow(
        WorkflowStepDefinition(
            id="dispatch",
            skill_id="reminder_bot",
            capability="reminders.dispatch",
            idempotent=True,
            on_error="retry",
            retryable=True,
            max_retries=2,
            compensation_skill_id="undo_send",
        ),
        workflow_id="stale-policy-side-effect",
    )
    store.put_definition(definition)
    run = store.create_run(
        definition,
        conversation["id"],
        idempotency_key="stale-policy-side-effect",
    )
    store.claim_step(run.id, 0, {}, tenant_id="local")

    recovered = store.recover_stale_run(
        run.id,
        stale_after_seconds=0,
        tenant_id="local",
    )

    assert recovered.status == "failed"
    assert any(
        event.event_type == "compensation.required"
        for event in store.list_events(run.id)
    )
