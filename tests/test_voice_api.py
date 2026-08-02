from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from artpm_agent.api import ChatOutcome, GatewayServices, create_app
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.security.permission_store import PermissionStore
from artpm_agent.voice import VoiceUnavailableError
from artpm_agent.workflows.store import WorkflowStore


def _headers(**changes):
    headers = {
        "x-workspace-id": "local-default",
        "x-tenant-id": "local",
        "x-actor-id": "alice",
        "x-actor-role": "user",
        "x-actor-kind": "human",
    }
    headers.update(changes)
    return headers


class FakeBroker:
    def __init__(self):
        self.calls = []

    def status(self):
        return {
            "enabled": True,
            "recording_ready": True,
            "realtime_ready": True,
        }

    def create(self, *, principal, conversation_id):
        self.calls.append((principal, conversation_id))
        return {
            "server_url": "wss://voice.example.test",
            "participant_token": "opaque-token",
            "room_name": "artpm-session",
            "session_id": "session-a",
            "expires_at": "2030-01-01T00:00:00Z",
            "agent_name": "artpm-voice",
            "features": {"approval_in_ui": True},
        }


@pytest.fixture()
def voice_gateway(tmp_path):
    path = tmp_path / "voice.sqlite"
    conversations = ConversationStore(path)
    permissions = PermissionStore(path)
    workflows = WorkflowStore(path)
    services = GatewayServices(
        conversations=conversations,
        permissions=permissions,
        workflows=workflows,
        chat_handler=lambda _command: ChatOutcome(response="ok"),
        capability_provider=lambda: [],
    )
    broker = FakeBroker()
    client = TestClient(create_app(services, voice_broker=broker))
    conversation = conversations.create_conversation(
        "Voice test",
        workspace_id="local-default",
    )
    return client, broker, conversation


def test_voice_status_uses_gateway_identity_and_has_no_credentials(voice_gateway):
    client, _, _ = voice_gateway

    missing = client.get("/v1/voice/status")
    response = client.get("/v1/voice/status", headers=_headers())

    assert missing.status_code == 401
    assert response.status_code == 200
    assert response.json()["workspace_id"] == "local-default"
    assert response.json()["realtime_ready"] is True
    assert "token" not in response.text.casefold()
    assert "secret" not in response.text.casefold()


def test_voice_session_is_bound_to_existing_conversation(voice_gateway):
    client, broker, conversation = voice_gateway

    response = client.post(
        "/v1/voice/sessions",
        headers=_headers(),
        json={"conversation_id": conversation["id"]},
    )

    assert response.status_code == 201
    assert response.json()["participant_token"] == "opaque-token"
    assert broker.calls[0][0].actor_id == "alice"
    assert broker.calls[0][1] == conversation["id"]


def test_voice_session_cannot_cross_workspace_or_use_service_actor(voice_gateway):
    client, broker, conversation = voice_gateway

    unknown = client.post(
        "/v1/voice/sessions",
        headers=_headers(**{"x-workspace-id": "other-workspace"}),
        json={"conversation_id": conversation["id"]},
    )
    service = client.post(
        "/v1/voice/sessions",
        headers=_headers(**{"x-actor-kind": "service"}),
        json={"conversation_id": conversation["id"]},
    )

    assert unknown.status_code in {404, 503}
    assert service.status_code == 403
    assert broker.calls == []


def test_voice_unavailable_is_a_structured_callback(voice_gateway):
    client, broker, conversation = voice_gateway
    broker.create = lambda **_kwargs: (_ for _ in ()).throw(
        VoiceUnavailableError("voice channel is not enabled")
    )

    response = client.post(
        "/v1/voice/sessions",
        headers=_headers(),
        json={"conversation_id": conversation["id"]},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_unavailable"
    assert response.json()["error"]["request_id"]
