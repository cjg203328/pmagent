from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from artpm_agent.api import ChatOutcome, GatewayServices, create_app
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.security.permission_store import PermissionStore
from artpm_agent.workflows.store import WorkflowStore
from artpm_agent.api.embed import EmbedError, _origin


def _services(tmp_path: Path) -> GatewayServices:
    db_path = tmp_path / "embed.sqlite"
    conversations = ConversationStore(db_path)

    def chat(command):
        return ChatOutcome(response=f"echo:{command.message}", handled_by="embed-test")

    return GatewayServices(
        conversations=conversations,
        permissions=PermissionStore(db_path),
        workflows=WorkflowStore(db_path),
        chat_handler=chat,
        capability_provider=lambda: [],
    )


def test_embed_exchange_session_and_chat_are_origin_bound(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ARTPM_EMBED_ENABLED", "1")
    monkeypatch.setenv("ARTPM_EMBED_SECRET", "test-signing-secret")
    monkeypatch.setenv("ARTPM_EMBED_CHANNELS", "")
    monkeypatch.setenv("ARTPM_EMBED_CHANNEL", "demo")
    monkeypatch.setenv("ARTPM_EMBED_PUBLISH_TOKEN", "publish-only")
    monkeypatch.setenv("ARTPM_EMBED_ALLOWED_ORIGINS", "https://example.com")

    with TestClient(create_app(_services(tmp_path))) as client:
        config = client.get(
            "/embed/demo/config",
            headers={"origin": "https://example.com"},
        )
        assert config.status_code == 200
        assert "frame-ancestors https://example.com" in config.headers["content-security-policy"]

        exchange = client.post(
            "/embed/demo/exchange",
            headers={"origin": "https://example.com"},
            json={"origin": "https://example.com", "publish_token": "publish-only"},
        )
        assert exchange.status_code == 200
        exchange_token = exchange.json()["exchange_token"]

        session = client.post(
            "/embed/demo/session",
            headers={"origin": "https://example.com"},
            json={"origin": "https://example.com", "exchange_token": exchange_token},
        )
        assert session.status_code == 201
        session_token = session.json()["session_token"]

        chat = client.post(
            "/embed/demo/chat",
            headers={"origin": "https://example.com"},
            json={
                "origin": "https://example.com",
                "session_token": session_token,
                "message": "hello",
            },
        )
        assert chat.status_code == 200
        assert chat.json()["response"] == "echo:hello"

        denied = client.post(
            "/embed/demo/chat",
            headers={"origin": "https://evil.example"},
            json={
                "origin": "https://evil.example",
                "session_token": session_token,
                "message": "hello",
            },
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "embed_origin_denied"


def test_embed_is_disabled_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ARTPM_EMBED_ENABLED", raising=False)
    monkeypatch.delenv("ARTPM_EMBED_SECRET", raising=False)
    monkeypatch.delenv("ARTPM_EMBED_CHANNELS", raising=False)
    with TestClient(create_app(_services(tmp_path))) as client:
        response = client.get(
            "/embed/demo/config",
            headers={"origin": "https://example.com"},
        )
    assert response.status_code == 404


def test_embed_origin_normalizes_default_ports_and_rejects_credentials():
    assert _origin("HTTPS://Example.com:443/") == "https://example.com"
    assert _origin("http://example.com:8080") == "http://example.com:8080"
    assert _origin("https://[::1]:443") == "https://[::1]"

    for value in (
        "https://user:secret@example.com",
        "https://example.com:not-a-port",
        "https://example.com:65536",
        "https://example.com////",
    ):
        try:
            _origin(value)
        except EmbedError as error:
            assert error.code == "invalid_embed_origin"
        else:  # pragma: no cover - assertion documents the rejection contract
            raise AssertionError(f"origin should be rejected: {value}")


def test_embed_route_accepts_equivalent_browser_origin_forms(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ARTPM_EMBED_ENABLED", "1")
    monkeypatch.setenv("ARTPM_EMBED_SECRET", "test-signing-secret")
    monkeypatch.setenv("ARTPM_EMBED_CHANNEL", "demo")
    monkeypatch.setenv("ARTPM_EMBED_PUBLISH_TOKEN", "publish-only")
    monkeypatch.setenv("ARTPM_EMBED_ALLOWED_ORIGINS", "https://example.com")

    with TestClient(create_app(_services(tmp_path))) as client:
        response = client.post(
            "/embed/demo/exchange",
            headers={"origin": "HTTPS://EXAMPLE.COM:443/"},
            json={
                "origin": "https://example.com/",
                "publish_token": "publish-only",
            },
        )

    assert response.status_code == 200
