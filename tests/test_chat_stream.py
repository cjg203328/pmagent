from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from starlette.testclient import TestClient

from artpm_agent.api import ChatOutcome, GatewayServices, create_app
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.security.permission_store import PermissionStore
from artpm_agent.runtime.event_bus import EventBus
from artpm_agent.runtime.events import AgentEvent, AgentEventType
from artpm_agent.workflows.store import WorkflowStore


def _headers(workspace: str = "workspace-a") -> dict[str, str]:
    return {
        "x-workspace-id": workspace,
        "x-actor-id": "alice",
        "x-tenant-id": "tenant-a",
    }


def _workspace(store: ConversationStore, workspace_id: str = "workspace-a") -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with store._connection(write=True) as connection:  # noqa: SLF001 - fixture setup
        connection.execute(
            """
            INSERT INTO workspaces(id, profile_id, name, settings_json, created_at, updated_at)
            VALUES (?, 'local-default', ?, '{}', ?, ?)
            """,
            (workspace_id, workspace_id, now, now),
        )


def _services(tmp_path: Path, *, chat_handler, stream_handler=None, event_bus=None):
    db_path = tmp_path / "stream.sqlite"
    conversations = ConversationStore(db_path)
    _workspace(conversations)
    return GatewayServices(
        conversations=conversations,
        permissions=PermissionStore(db_path),
        workflows=WorkflowStore(db_path),
        chat_handler=chat_handler,
        chat_stream_handler=stream_handler,
        capability_provider=lambda: [],
        event_bus=event_bus,
    )


def _frames(response):
    frames = []
    for raw in response.text.strip().split("\n\n"):
        fields = {}
        data_lines = []
        for line in raw.splitlines():
            if line.startswith("data: "):
                data_lines.append(line[6:])
            elif ":" in line:
                key, value = line.split(":", 1)
                fields[key] = value.strip()
        if data_lines:
            fields["data"] = json.loads("\n".join(data_lines))
            frames.append(fields)
    return frames


def test_stream_handler_emits_lifecycle_snapshot_and_resumable_ids(tmp_path: Path):
    def chat(_command):
        return ChatOutcome(response="unused")

    def stream(command):
        return [
            {
                "type": "retrieval_progress",
                "metadata": {"hits": 2},
            },
            {
                "type": "message_delta",
                "delta": "hello",
            },
            ChatOutcome(response="hello", handled_by="stream-test"),
        ]

    services = _services(tmp_path, chat_handler=chat, stream_handler=stream)
    response = TestClient(create_app(services)).post(
        "/v1/chat/stream",
        headers=_headers(),
        json={"message": "hi", "run_id": "run-1", "turn_id": "turn-1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(response)
    types = [frame["data"]["type"] for frame in frames]
    assert types == [
        "turn_start",
        "retrieval_progress",
        "message_delta",
        "snapshot",
        "turn_end",
    ]
    assert all(frame["data"]["run_id"] == "run-1" for frame in frames)
    assert all(frame["data"]["turn_id"] == "turn-1" for frame in frames)
    assert all(frame["id"].startswith("run-1:turn-1:") for frame in frames)
    assert frames[-2]["data"]["response"] == "hello"


def test_default_stream_bridges_event_bus_and_redacts_failures(tmp_path: Path):
    bus = EventBus()

    def chat(command):
        bus.publish(
            AgentEvent(
                AgentEventType.TOOL_EXECUTION_START,
                command.run_id or command.turn_id,
                command.turn_id,
                tool_name="safe_tool",
            )
        )
        raise RuntimeError("provider secret and local path")

    services = _services(tmp_path, chat_handler=chat, event_bus=bus)
    response = TestClient(create_app(services)).post(
        "/v1/chat/stream",
        headers=_headers(),
        json={"message": "fail", "run_id": "run-2", "turn_id": "turn-2"},
    )

    assert response.status_code == 200
    frames = _frames(response)
    types = [frame["data"]["type"] for frame in frames]
    assert types == ["turn_start", "tool_execution_start", "error", "snapshot", "turn_end"]
    assert "provider secret" not in response.text
    assert "local path" not in response.text
    assert frames[2]["data"]["error"] == "chat_failed"
    assert frames[-1]["data"]["is_error"] is True


def test_default_stream_emits_one_delta_for_legacy_final_response(tmp_path: Path):
    services = _services(
        tmp_path,
        chat_handler=lambda _command: ChatOutcome(response="legacy response"),
    )
    response = TestClient(create_app(services)).post(
        "/v1/chat/stream",
        headers=_headers(),
        json={"message": "hello"},
    )

    frames = _frames(response)
    assert [frame["data"]["type"] for frame in frames] == [
        "turn_start",
        "message_update",
        "snapshot",
        "turn_end",
    ]
    assert frames[1]["data"]["delta"] == "legacy response"


def test_stream_error_event_drops_provider_metadata(tmp_path: Path):
    def stream(_command):
        return [
            AgentEvent(
                AgentEventType.RUNTIME_ERROR,
                "run-error",
                "turn-error",
                error="provider stack",
                is_error=True,
                metadata={"traceback": "secret stack"},
            )
        ]

    services = _services(
        tmp_path,
        chat_handler=lambda _command: ChatOutcome(response="unused"),
        stream_handler=stream,
    )
    response = TestClient(create_app(services)).post(
        "/v1/chat/stream",
        headers=_headers(),
        json={"message": "hello"},
    )

    assert "provider stack" not in response.text
    assert "secret stack" not in response.text
