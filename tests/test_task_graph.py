from __future__ import annotations

from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from artpm_agent.config import Config
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.workflows.coordinator import WorkflowCoordinator
from artpm_agent.workflows.store import WorkflowStore
from artpm_agent.workflows.task_graph import (
    LangGraphTaskOrchestrator,
    TaskGraphInputError,
    TaskSpec,
    make_sqlite_session_audit,
)


def test_task_graph_resolves_dependencies_and_parallel_batch():
    calls: list[tuple[str, str]] = []
    lock = threading.Lock()

    def runner(task, context):
        with lock:
            calls.append((task.id, threading.current_thread().name))
        time.sleep(0.01)
        if task.id in {"research", "budget"}:
            return {"value": 2 if task.id == "research" else 3}
        return {
            "total": context["results"]["research"]["value"]
            + context["results"]["budget"]["value"]
        }

    graph = LangGraphTaskOrchestrator(runner, max_parallelism=2, max_retries=0)
    state = graph.invoke(
        "prepare an estimate",
        [
            {"id": "research"},
            {"id": "budget"},
            {
                "id": "estimate",
                "depends_on": ["research", "budget"],
            },
        ],
        thread_id="graph-dependencies",
    )

    assert state["status"] == "succeeded"
    assert state["completed"] == ["research", "budget", "estimate"]
    assert state["results"]["estimate"] == {"total": 5}
    assert {task_id for task_id, _ in calls} == {"research", "budget", "estimate"}
    assert all(name.startswith("artpm-task") for _, name in calls[:2])


def test_task_graph_retries_a_failed_task_with_bounded_attempts():
    attempts = 0

    def runner(task, context):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary provider failure")
        return {"attempt": attempts}

    graph = LangGraphTaskOrchestrator(runner, max_retries=2)
    state = graph.invoke("retry", [{"id": "provider"}], thread_id="graph-retry")

    assert state["status"] == "succeeded"
    assert state["attempts"] == {"provider": 3}
    assert state["results"]["provider"] == {"attempt": 3}
    assert state["errors"] == {}
    assert sum(event["event"] == "task.failed" for event in state["trace"]) == 2


def test_task_graph_call_override_can_disable_retries():
    attempts = 0

    def runner(task, context):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("permanent failure")

    graph = LangGraphTaskOrchestrator(runner, max_retries=2)
    state = graph.invoke(
        "no retry",
        [{"id": "provider"}],
        max_retries=0,
        thread_id="graph-no-retry",
    )

    assert state["status"] == "failed"
    assert attempts == 1
    assert state["attempts"] == {"provider": 1}


def test_task_graph_pauses_and_resumes_approval_with_checkpoint():
    calls: list[tuple[str, bool]] = []

    def runner(task, context):
        calls.append((task.id, context["approved"]))
        return {"ok": True}

    graph = LangGraphTaskOrchestrator(runner, max_retries=0)
    waiting = graph.invoke(
        "publish report",
        [
            {"id": "draft"},
            {
                "id": "publish",
                "depends_on": ["draft"],
                "side_effect": True,
                "requires_approval": True,
            },
        ],
        thread_id="graph-approval",
    )

    assert waiting["status"] == "waiting_approval"
    assert waiting["pending_approval"] == ["publish"]
    assert calls == [("draft", False)]
    assert graph.snapshot("graph-approval")["status"] == "waiting_approval"

    completed = graph.resume("graph-approval", True)

    assert completed["status"] == "succeeded"
    assert completed["approved"] == ["publish"]
    assert calls == [("draft", False), ("publish", True)]


def test_task_graph_rejects_cycles_and_unsafe_side_effect_contracts():
    graph = LangGraphTaskOrchestrator(lambda task, context: {})

    with pytest.raises(TaskGraphInputError, match="cycle"):
        graph.invoke(
            "cycle",
            [
                {"id": "a", "depends_on": ["b"]},
                {"id": "b", "depends_on": ["a"]},
            ],
            thread_id="graph-cycle",
        )
    with pytest.raises(ValidationError):
        TaskSpec(id="write", side_effect=True)


def test_workflow_coordinator_exposes_safe_langgraph_skill_adapter(tmp_path: Path):
    db_path = tmp_path / "conversations.db"
    conversation = ConversationStore(db_path).create_conversation("graph")

    class Router:
        def execute_skill(self, skill_id, inputs):
            return {"success": True, "skill_id": skill_id, "inputs": inputs}

    class Agent:
        router = Router()

    coordinator = WorkflowCoordinator(WorkflowStore(db_path), Agent())
    graph = coordinator.create_task_orchestrator(max_retries=0)
    state = graph.invoke(
        "read project data",
        [
            {
                "id": "read",
                "skill_id": "progress_tracker",
                "capability": "projects.read",
                "input_data": {"project_id": "P1"},
            }
        ],
        conversation_id=conversation["id"],
        thread_id="coordinator-graph",
    )

    assert state["status"] == "succeeded"
    assert state["results"]["read"]["skill_id"] == "progress_tracker"
    entries = coordinator.create_task_orchestrator().session_store.list_entries(
        conversation["id"]
    )
    assert any(entry.entry_type == "task_graph.finished" for entry in entries)


def test_langgraph_runtime_configuration_is_env_overridable(monkeypatch):
    monkeypatch.setenv("AGENT_ORCHESTRATION_FRAMEWORK", "langgraph")
    monkeypatch.setenv("LANGGRAPH_ENABLED", "false")
    monkeypatch.setenv("LANGGRAPH_MAX_PARALLELISM", "3")
    monkeypatch.setenv("LANGGRAPH_MAX_RETRIES", "1")
    monkeypatch.setenv("MEMORY_CONTEXT_MAX_TOKENS", "640")

    config = Config()

    assert config.get("agent_runtime.orchestration_framework") == "langgraph"
    assert config.get("agent_runtime.langgraph_enabled") is False
    assert config.get("agent_runtime.langgraph_max_parallelism") == 3
    assert config.get("agent_runtime.langgraph_max_retries") == 1
    assert config.get("agent_runtime.memory_context_max_tokens") == 640


@pytest.mark.parametrize(
    ("runner", "options", "error_type", "message"),
    [
        (None, {}, TypeError, "task_runner must be callable"),
        (lambda _task, _context: {}, {"max_parallelism": 0}, ValueError, "max_parallelism"),
        (lambda _task, _context: {}, {"max_parallelism": True}, ValueError, "max_parallelism"),
        (lambda _task, _context: {}, {"max_retries": -1}, ValueError, "max_retries"),
        (lambda _task, _context: {}, {"max_retries": True}, ValueError, "max_retries"),
    ],
)
def test_task_graph_constructor_rejects_invalid_runtime_options(
    runner, options, error_type, message
):
    with pytest.raises(error_type, match=message):
        LangGraphTaskOrchestrator(runner, **options)


@pytest.mark.parametrize(
    ("tasks", "message"),
    [
        ([], "at least one task"),
        ([{"id": "same"}, {"id": "same"}], "duplicate task id"),
        ([{"id": "child", "depends_on": ["missing"]}], "unknown task"),
    ],
)
def test_task_graph_rejects_incomplete_task_sets(tasks, message):
    graph = LangGraphTaskOrchestrator(lambda _task, _context: {})

    with pytest.raises(TaskGraphInputError, match=message):
        graph.invoke("validate", tasks, thread_id=f"invalid-{message}")


@pytest.mark.parametrize(
    "bad_value",
    [object(), float("nan"), {1: "non-string key"}],
)
def test_task_graph_rejects_non_checkpointable_values(bad_value):
    graph = LangGraphTaskOrchestrator(lambda _task, _context: {})

    with pytest.raises((TaskGraphInputError, TypeError, ValidationError)):
        graph.invoke(
            "serialize",
            [{"id": "invalid", "input_data": {"value": bad_value}}],
            thread_id=f"invalid-json-{type(bad_value).__name__}",
        )


def test_task_graph_resolves_nested_checkpoint_references():
    contexts = {}

    def runner(task, context):
        contexts[task.id] = context
        if task.id == "source":
            return {"items": [{"amount": 7}]}
        return {"received": context["inputs"]}

    graph = LangGraphTaskOrchestrator(runner, max_parallelism=1, max_retries=0)
    state = graph.invoke(
        "prepare nested estimate",
        [
            {"id": "source"},
            {
                "id": "consumer",
                "depends_on": ["source"],
                "input_data": {
                    "amount": "$results.source.items.0.amount",
                    "goal": "$goal",
                    "conversation": "$conversation_id",
                    "list": ["$results.source.items.0.amount", "literal"],
                },
            },
        ],
        conversation_id="conversation-refs",
        thread_id="graph-refs",
    )

    assert state["status"] == "succeeded"
    assert contexts["consumer"]["inputs"] == {
        "amount": 7,
        "goal": "prepare nested estimate",
        "conversation": "conversation-refs",
        "list": [7, "literal"],
    }


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("$unknown.value", "unsupported task input reference"),
        ("$results.source.missing", "missing task input reference"),
        ("$results.source.items.2", "out of range"),
    ],
)
def test_task_graph_records_invalid_references_as_task_failures(reference, message):
    def runner(task, _context):
        if task.id == "source":
            return {"items": [1]}
        return {"ok": True}

    graph = LangGraphTaskOrchestrator(runner, max_retries=0)
    state = graph.invoke(
        "invalid reference",
        [
            {"id": "source"},
            {
                "id": "consumer",
                "depends_on": ["source"],
                "input_data": {"value": reference},
            },
        ],
        thread_id=f"invalid-ref-{message}",
    )

    assert state["status"] == "failed"
    assert message in state["errors"]["consumer"]
    assert state["attempts"]["consumer"] == 1


def test_task_max_attempts_overrides_graph_retry_budget():
    attempts = 0

    def runner(_task, _context):
        nonlocal attempts
        attempts += 1
        return {"success": False}

    graph = LangGraphTaskOrchestrator(runner, max_retries=5)
    state = graph.invoke(
        "bounded task",
        [{"id": "provider", "max_attempts": 1}],
        thread_id="task-specific-attempt-limit",
    )

    assert state["status"] == "failed"
    assert state["errors"] == {"provider": "task reported failure"}
    assert attempts == 1


def test_task_graph_rejection_finishes_without_running_side_effect():
    calls = []
    graph = LangGraphTaskOrchestrator(
        lambda task, _context: calls.append(task.id) or {"ok": True},
        max_retries=0,
    )
    waiting = graph.invoke(
        "publish",
        [{"id": "publish", "side_effect": True, "requires_approval": True}],
        thread_id="graph-reject",
    )

    assert waiting["status"] == "waiting_approval"
    rejected = graph.resume("graph-reject", False)

    assert rejected["status"] == "failed"
    assert rejected["errors"] == {"publish": "task approval rejected"}
    assert calls == []


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        ({"approved": ["other"], "rejected": ["publish"]}, "unknown pending"),
        ({"approved": ["publish"], "rejected": ["publish"]}, "both decisions"),
        ({"approved": [], "rejected": []}, "decide every pending"),
        ({"approved": 1, "rejected": ["publish"]}, "must be a task id list"),
        ("yes", "must be a boolean or mapping"),
    ],
)
def test_task_graph_rejects_ambiguous_approval_decisions(decision, message):
    with pytest.raises(TaskGraphInputError, match=message):
        LangGraphTaskOrchestrator._parse_approval(decision, ["publish"])


def test_task_graph_public_ids_and_sqlite_audit_factory(tmp_path):
    graph = LangGraphTaskOrchestrator(lambda _task, context: context["run_id"])
    state = graph.invoke(
        "ids",
        [TaskSpec(id="read")],
        run_id="run-explicit",
        conversation_id="conversation-explicit",
    )

    assert state["run_id"] == "run-explicit"
    assert state["turn_id"] == "run-explicit"
    assert state["results"] == {"read": "run-explicit"}
    audit = make_sqlite_session_audit(tmp_path / "audit.db")
    assert Path(audit.db_path) == tmp_path / "audit.db"


def test_coordinator_configuration_and_default_runner_safety(tmp_path):
    db_path = tmp_path / "conversations.db"
    store = WorkflowStore(db_path)

    class Router:
        def __init__(self):
            self.result = {"success": True}

        def execute_skill(self, _skill_id, _inputs):
            return self.result

    router = Router()
    agent = SimpleNamespace(
        router=router,
        config={
            "agent_runtime.orchestration_framework": "langgraph",
            "agent_runtime.langgraph_enabled": True,
            "agent_runtime.langgraph_max_parallelism": 2,
            "agent_runtime.langgraph_max_retries": 0,
        },
    )
    coordinator = WorkflowCoordinator(store, agent)
    graph = coordinator.create_collaboration_graph(session_store=False)

    assert graph.max_parallelism == 2
    assert graph.max_retries == 0
    with pytest.raises(ValueError, match="requires task.skill_id"):
        coordinator._run_read_only_graph_skill(TaskSpec(id="missing"), {"inputs": {}})
    with pytest.raises(PermissionError, match="outside the server allowlist"):
        coordinator._run_read_only_graph_skill(
            TaskSpec(id="unsafe", skill_id="progress_tracker", capability="files.delete"),
            {"inputs": {}},
        )
    with pytest.raises(PermissionError, match="WorkflowEngine approval"):
        coordinator._run_read_only_graph_skill(
            TaskSpec(
                id="dispatch",
                skill_id="reminder_dispatch",
                capability="reminders.dispatch",
                side_effect=True,
                requires_approval=True,
            ),
            {"inputs": {}},
        )

    router.result = {"success": False, "error": "backend rejected"}
    with pytest.raises(RuntimeError, match="backend rejected"):
        coordinator._run_read_only_graph_skill(
            TaskSpec(
                id="read",
                skill_id="progress_tracker",
                capability="projects.read",
            ),
            {"inputs": {}},
        )

    agent.config["agent_runtime.langgraph_enabled"] = False
    with pytest.raises(RuntimeError, match="orchestration is disabled"):
        coordinator.create_task_orchestrator(session_store=False)


def test_workflow_coordinator_requires_a_concrete_router_interface(tmp_path):
    agent_without_execute_skill = SimpleNamespace(router=SimpleNamespace())

    with pytest.raises(TypeError, match="router.execute_skill"):
        WorkflowCoordinator(
            WorkflowStore(tmp_path / "conversations.db"),
            agent_without_execute_skill,
        )
