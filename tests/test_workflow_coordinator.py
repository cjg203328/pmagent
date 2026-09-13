from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"

from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.workflows.coordinator import WorkflowCoordinator, format_workflow_result
from artpm_agent.workflows.store import WorkflowStore


class RecordingRouter:
    def __init__(self):
        self.calls = []

    def execute_skill(self, skill_id, inputs):
        self.calls.append((skill_id, dict(inputs)))
        if skill_id == "quote_calculator":
            return {
                "success": True,
                "quote_amount": inputs["quote_amount"],
                "cost": inputs["cost"],
                "net_profit": inputs["quote_amount"] - inputs["cost"],
                "profit_rate_percent": "40.0%",
            }
        if skill_id == "reminder_bot":
            return {
                "success": True,
                "sent_status": "dry_run",
                "messages": [
                    {
                        "recipient": inputs["recipients"][0],
                        "subject": f"任务 {inputs['task_id']} 催办",
                    }
                ],
            }
        if skill_id == "reminder_dispatch":
            assert inputs["approved"] is True
            assert inputs["idempotency_key"]
            return {
                "success": True,
                "sent_status": "sent",
                "delivery_results": [{"recipient": "张三", "success": True}],
            }
        raise AssertionError(f"unexpected skill: {skill_id}")


class WorkflowAgent:
    def __init__(self, *, quote_amount=100_000, cost=60_000):
        self.router = RecordingRouter()
        self.quote_amount = quote_amount
        self.cost = cost

    def prepare_skill_inputs(self, prompt, skill_id, context):
        if skill_id == "quote_calculator":
            return {
                "quote_amount": self.quote_amount,
                "cost": self.cost,
                "cost_config": {"tax_rate": 0.06, "overhead_rate": 0.15},
                "overhead_rate": 0.15,
            }
        if skill_id == "reminder_bot":
            return {"recipients": ["张三"], "tone": "formal"}
        return {}

    def format_skill_result(self, skill_id, result):
        if skill_id == "quote_calculator":
            return f"利润率 {result['profit_rate_percent']}"
        if skill_id == "reminder_bot":
            return f"预览：{result['messages'][0]['subject']}"
        if skill_id == "reminder_dispatch":
            return "催办已发送"
        return str(result)


def make_coordinator(tmp_path, agent=None):
    db_path = tmp_path / "conversations.db"
    conversations = ConversationStore(db_path)
    conversation = conversations.create_conversation("工作流会话")
    workflow_store = WorkflowStore(db_path)
    agent = agent or WorkflowAgent()
    return conversation, workflow_store, agent, WorkflowCoordinator(workflow_store, agent)


def test_non_default_workspace_uses_its_own_builtin_catalog(tmp_path):
    db_path = tmp_path / "conversations.db"
    conversations = ConversationStore(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with conversations._connection(write=True) as connection:  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO workspaces(
                id, profile_id, name, tenant_id, settings_json, created_at, updated_at
            ) VALUES ('workspace-b', 'local-default', 'Workspace B', 'tenant-b', '{}', ?, ?)
            """,
            (now, now),
        )
    conversation = conversations.create_conversation(
        "非默认工作区", workspace_id="workspace-b"
    )
    store = WorkflowStore(db_path)
    agent = WorkflowAgent()
    coordinator = WorkflowCoordinator(
        store,
        agent,
        workspace_id="workspace-b",
        profile_id="local-default",
    )

    outcome = coordinator.process(
        "报价10万成本6万，评估利润",
        conversation_id=conversation["id"],
        turn_id="turn-workspace-b",
        agent_context={"conversation_history": []},
    )

    assert outcome.matched
    assert outcome.execution.run.workspace_id == "workspace-b"
    assert store.list_definitions(workspace_id="local-default")
    assert store.list_definitions(workspace_id="workspace-b")


def test_quote_workflow_executes_once_and_persists_the_run(tmp_path):
    conversation, store, agent, coordinator = make_coordinator(tmp_path)

    outcome = coordinator.process(
        "报价10万成本6万，评估利润",
        conversation_id=conversation["id"],
        turn_id="turn-quote",
        agent_context={"conversation_history": []},
    )

    assert outcome.matched
    assert outcome.execution.run.status == "succeeded"
    assert [call[0] for call in agent.router.calls] == ["quote_calculator"]
    assert format_workflow_result(agent, outcome.execution) == "利润率 40.0%"
    persisted = store.list_runs(conversation["id"])
    assert [run.id for run in persisted] == [outcome.execution.run.id]

    repeated = coordinator.process(
        "报价10万成本6万，评估利润",
        conversation_id=conversation["id"],
        turn_id="turn-quote",
        agent_context={"conversation_history": []},
    )
    assert repeated.execution.run.id == outcome.execution.run.id
    assert len(agent.router.calls) == 1


def test_incomplete_workflow_falls_back_without_creating_a_run(tmp_path):
    agent = WorkflowAgent(quote_amount=0, cost=0)
    conversation, store, agent, coordinator = make_coordinator(tmp_path, agent)

    outcome = coordinator.process(
        "报价应该怎么评估",
        conversation_id=conversation["id"],
        turn_id="turn-incomplete",
        agent_context={},
    )

    assert not outcome.matched
    assert outcome.fallback_reason == "missing_required_context"
    assert store.list_runs(conversation["id"]) == []
    assert agent.router.calls == []


def test_reminder_preview_survives_reload_and_dispatches_once_after_approval(tmp_path):
    conversation, store, agent, coordinator = make_coordinator(tmp_path)

    outcome = coordinator.process(
        "请催办任务 T1",
        conversation_id=conversation["id"],
        turn_id="turn-reminder",
        agent_context={},
    )

    assert outcome.execution.run.status == "awaiting_approval"
    assert [call[0] for call in agent.router.calls] == ["reminder_bot"]
    assert "预览" in format_workflow_result(agent, outcome.execution)

    restored = WorkflowCoordinator(store, agent)
    waiting = restored.engine.result(outcome.execution.run.id)
    assert waiting.run.status == "awaiting_approval"
    assert waiting.approval.status == "pending"

    completed = restored.engine.decide_approval(
        waiting.run.id,
        waiting.run.current_step,
        decision="approved",
        actor="local-default",
        actor_level="user",
    )
    assert completed.run.status == "succeeded"
    assert [call[0] for call in agent.router.calls] == [
        "reminder_bot",
        "reminder_dispatch",
    ]
    replay = restored.engine.decide_approval(
        waiting.run.id,
        waiting.run.current_step,
        decision="approved",
        actor="local-default",
        actor_level="user",
    )
    assert replay.run.status == "succeeded"
    assert len(agent.router.calls) == 2
