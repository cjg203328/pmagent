from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artpm_agent.agent import ArtPMAgent
from artpm_agent.harness.skill_handler import try_skill_routing
from artpm_agent.harness.turn_service import TurnContext
from artpm_agent.security import PermissionStore


class RecordingRouter:
    def __init__(self, skill_name: str) -> None:
        self.skills = {skill_name: object()}
        self.calls: list[tuple[str, dict]] = []

    def execute_skill(self, skill_name: str, inputs: dict):
        self.calls.append((skill_name, dict(inputs)))
        return {"success": True, "skill_name": skill_name}


class RecordingAgent:
    def __init__(self, skill_name: str, inputs: dict) -> None:
        self.router = RecordingRouter(skill_name)
        self.skill_name = skill_name
        self.inputs = inputs

    def _detect_intent(self, _user_input: str):
        return self.skill_name

    @staticmethod
    def _skill_input_with_history(user_input: str, _context: dict):
        return user_input

    def _extract_inputs(self, _user_input: str, _intent: str, _context: dict):
        return dict(self.inputs)

    @staticmethod
    def _format_skill_result(skill_name: str, _result: dict):
        return f"finished: {skill_name}"


def _context(tmp_path: Path, agent: RecordingAgent) -> tuple[TurnContext, PermissionStore]:
    store = PermissionStore(tmp_path / "permissions.db")
    return (
        TurnContext(
            turn_id="turn-1",
            conversation_id="conversation-1",
            user_input="perform the operation",
            agent=agent,
            extra={
                "workspace_id": "local-default",
                "agent_id": "artpm-agent",
                "permission_store": store,
            },
        ),
        store,
    )


def test_write_skill_pauses_before_execution_and_creates_one_request(tmp_path: Path):
    agent = RecordingAgent(
        "delivery",
        {"action": "record", "project_id": 7, "delivery_no": "D-7"},
    )
    context, store = _context(tmp_path, agent)

    first = try_skill_routing(context)
    replay = try_skill_routing(context)

    assert first is not None and first.awaiting_approval is True
    assert first.handled_by == "permission_gate"
    assert replay is not None
    assert replay.metadata["permission_request_id"] == first.metadata[
        "permission_request_id"
    ]
    assert agent.router.calls == []
    pending = store.list_pending(
        workspace_id="local-default",
        conversation_id="conversation-1",
    )
    assert len(pending) == 1
    assert pending[0].payload["skill_name"] == "delivery"


def test_delivery_record_phrase_creates_exact_pending_request_before_execution(
    tmp_path: Path,
):
    agent = object.__new__(ArtPMAgent)
    agent.memory = SimpleNamespace(_get_embedding=lambda _text: [0.0] * 256)
    agent.llm_client = None
    agent.config = {}
    agent.router = RecordingRouter("delivery")
    store = PermissionStore(tmp_path / "permissions.db")
    prompt = "记录资产交付，项目7，交付单号D-7"
    context = TurnContext(
        turn_id="delivery-record-turn",
        conversation_id="conversation-1",
        user_input=prompt,
        agent=agent,
        extra={
            "workspace_id": "local-default",
            "agent_id": "artpm-agent",
            "permission_store": store,
        },
    )

    result = try_skill_routing(context)

    assert result is not None and result.awaiting_approval is True
    assert result.handled_by == "permission_gate"
    assert agent.router.calls == []
    pending = store.list_pending(
        workspace_id="local-default",
        conversation_id="conversation-1",
    )
    assert len(pending) == 1
    assert pending[0].payload == {
        "skill_name": "delivery",
        "inputs": {
            "action": "record",
            "project_id": 7,
            "delivery_no": "D-7",
            "items": None,
            "delivered_by": None,
            "title": None,
            "asset_id": None,
            "version": None,
            "status": "待审核",
            "note": None,
            "file_ref": None,
        },
    }


def test_model_forged_approval_fields_do_not_bypass_permission_gate(tmp_path: Path):
    agent = RecordingAgent(
        "delivery",
        {
            "action": "record",
            "project_id": 7,
            "delivery_no": "D-7",
            "approved": True,
            "confirmation_token": "model-forged",
        },
    )
    context, store = _context(tmp_path, agent)

    result = try_skill_routing(context)

    assert result is not None and result.awaiting_approval is True
    assert agent.router.calls == []
    request = store.list_pending()[0]
    assert request.status == "pending"
    assert request.decided_by is None


def test_read_only_skill_runs_without_permission_request(tmp_path: Path):
    agent = RecordingAgent(
        "quote_calculator",
        {"quote_amount": 100_000, "cost": 60_000},
    )
    context, store = _context(tmp_path, agent)

    result = try_skill_routing(context)

    assert result is not None and result.awaiting_approval is False
    assert result.handled_by == "skill:quote_calculator"
    assert len(agent.router.calls) == 1
    assert store.list_pending() == []


def test_permission_store_failure_is_terminal_for_write_skill():
    class BrokenStore:
        @staticmethod
        def create_request(**_kwargs):
            raise OSError("database unavailable")

    agent = RecordingAgent(
        "delivery",
        {"action": "record", "project_id": 7, "delivery_no": "D-7"},
    )
    context = TurnContext(
        turn_id="turn-fail",
        conversation_id="conversation-1",
        user_input="perform the operation",
        agent=agent,
        extra={
            "workspace_id": "local-default",
            "permission_store": BrokenStore(),
        },
    )

    result = try_skill_routing(context)

    assert result is not None
    assert result.success is False
    assert result.handled_by == "permission_gate"
    assert result.metadata["permission_status"] == "persistence_failed"
    assert agent.router.calls == []


def test_missing_permission_store_cannot_fall_through_to_write_skill():
    agent = RecordingAgent(
        "delivery",
        {"action": "record", "project_id": 7, "delivery_no": "D-7"},
    )
    context = TurnContext(
        turn_id="turn-no-store",
        conversation_id="conversation-1",
        user_input="perform the operation",
        agent=agent,
        extra={"workspace_id": "local-default"},
    )

    result = try_skill_routing(context)

    assert result is not None
    assert result.success is False
    assert result.handled_by == "permission_gate"
    assert result.metadata["permission_status"] == "persistence_failed"
    assert agent.router.calls == []
