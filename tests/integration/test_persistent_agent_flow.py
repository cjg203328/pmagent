"""Offline integration tests across the production orchestration boundaries."""

from __future__ import annotations

from artpm_agent.harness.turn_service import TurnContext, run_turn
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.memory.cross_session_memory import CrossSessionMemory
from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore
from artpm_agent.workflows.coordinator import WorkflowCoordinator
from artpm_agent.workflows.store import WorkflowStore


class _QuoteRouter:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def execute_skill(self, skill_id, inputs):
        self.calls.append((skill_id, dict(inputs)))
        if skill_id != "quote_calculator":
            raise AssertionError(f"unexpected skill: {skill_id}")
        return {
            "success": True,
            "quote_amount": inputs["quote_amount"],
            "cost": inputs["cost"],
            "net_profit": inputs["quote_amount"] - inputs["cost"],
            "profit_rate_percent": "40.0%",
        }


class _WorkflowAgent:
    def __init__(self):
        self.router = _QuoteRouter()

    def prepare_skill_inputs(self, _prompt, skill_id, _context):
        if skill_id != "quote_calculator":
            return {}
        return {
            "quote_amount": 100_000,
            "cost": 60_000,
            "cost_config": {"tax_rate": 0.06, "overhead_rate": 0.15},
            "overhead_rate": 0.15,
        }

    @staticmethod
    def format_skill_result(skill_id, result):
        assert skill_id == "quote_calculator"
        return f"利润率 {result['profit_rate_percent']}"


class _ThinModelAgent:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self.calls: list[tuple[str, dict]] = []

    def chat(self, prompt, context=None):
        self.calls.append((prompt, dict(context or {})))
        return f"{self.model_id}: ok"


def test_harness_executes_and_persists_selected_workflow(tmp_path):
    db_path = tmp_path / "conversation-and-workflow.db"
    conversation = ConversationStore(db_path).create_conversation("integration")
    store = WorkflowStore(db_path)
    agent = _WorkflowAgent()
    coordinator = WorkflowCoordinator(store, agent)
    context = TurnContext(
        turn_id="turn-integration-quote",
        conversation_id=conversation["id"],
        user_input="报价10万成本6万，评估利润",
        agent=agent,
    )

    result = run_turn(
        context,
        workflow_coordinator=coordinator,
        request_conversation_id=conversation["id"],
    )

    assert result.success is True
    assert result.handled_by == "workflow:quote_assessment"
    assert result.response == "利润率 40.0%"
    assert [skill for skill, _inputs in agent.router.calls] == ["quote_calculator"]

    reopened = WorkflowStore(db_path)
    persisted = reopened.get_run(result.metadata["workflow_run_id"])
    assert persisted.status == "succeeded"
    assert persisted.outputs["assessment"]["net_profit"] == 40_000


def test_global_memory_survives_model_replacement_and_stays_workspace_scoped(
    tmp_path,
):
    knowledge_path = tmp_path / "knowledge.db"
    first_store = WorkspaceKnowledgeStore(
        knowledge_path,
        enable_vector_search=False,
    )
    memory_id = CrossSessionMemory(first_store).save_memory(
        "Project Borealis budget ceiling is 800000 CNY.",
        "project_context",
        workspace_id="studio-a",
        source_conversation="source-conversation",
        source_type="explicit",
    )
    assert memory_id

    reopened_store = WorkspaceKnowledgeStore(
        knowledge_path,
        enable_vector_search=False,
    )
    first_model = _ThinModelAgent("model-a")
    first_context = TurnContext(
        turn_id="turn-model-a",
        conversation_id="new-conversation-a",
        user_input="What is the Borealis budget?",
        agent=first_model,
        extra={"workspace_id": "studio-a", "memory_inject_max_tokens": 300},
    )

    first_result = run_turn(first_context, knowledge_store=reopened_store)

    assert first_result.response == "model-a: ok"
    assert "Borealis budget ceiling" in first_context.knowledge_context

    replacement_model = _ThinModelAgent("model-b")
    replacement_context = TurnContext(
        turn_id="turn-model-b",
        conversation_id="new-conversation-b",
        user_input="Recall the Borealis budget ceiling",
        agent=replacement_model,
        extra={"workspace_id": "studio-a", "memory_inject_max_tokens": 300},
    )
    replacement_result = run_turn(
        replacement_context,
        knowledge_store=reopened_store,
    )

    assert replacement_result.response == "model-b: ok"
    assert "800000 CNY" in replacement_context.knowledge_context

    other_workspace_context = TurnContext(
        turn_id="turn-other-workspace",
        conversation_id="other-workspace-conversation",
        user_input="Recall the Borealis budget ceiling",
        agent=_ThinModelAgent("model-c"),
        extra={"workspace_id": "studio-b", "memory_inject_max_tokens": 300},
    )
    run_turn(other_workspace_context, knowledge_store=reopened_store)
    assert "800000 CNY" not in other_workspace_context.knowledge_context
