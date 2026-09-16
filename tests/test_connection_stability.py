import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from artpm_agent.agent import ArtPMAgent
from artpm_agent.core.mcp_client import MCPClient
from artpm_agent.core.mcp_client_stdio import StdioMCPClient
from artpm_agent.core.mcp_client_unified import UnifiedMCPClient
from artpm_agent.providers.gateway import ModelGateway
from artpm_agent.utils.streaming_progress import (
    TurnStage,
    progress_context,
    stream_with_progress,
)
from artpm_agent.utils.unlimited_ocr import UnlimitedOCRClient


def _stdio_client() -> StdioMCPClient:
    client = StdioMCPClient(api_key="sk_live_test", enabled=False)
    client.enabled = True
    return client


def test_stdio_ping_never_treats_stale_tool_cache_as_healthy(monkeypatch):
    client = _stdio_client()
    client._tools_cache = [{"name": "resolve_skill"}]

    def fail():
        raise ConnectionError("transport closed")

    monkeypatch.setattr(client, "_list_sync", fail)

    ok, message = client.ping()

    assert ok is False
    assert "transport closed" in message


def test_stdio_connect_returns_when_worker_has_already_failed(monkeypatch):
    client = _stdio_client()
    monkeypatch.setattr(
        "artpm_agent.core.mcp_client_stdio._CONNECT_TIMEOUT",
        10,
    )

    def fail_worker():
        client._connect_error = "npx failed"
        client._broken = True

    monkeypatch.setattr(client, "_background_main", fail_worker)
    started = time.monotonic()

    assert client._ensure_connected() is False
    assert time.monotonic() - started < 1.0
    client.close()


def test_stdio_background_worker_closes_its_event_loop(monkeypatch):
    client = _stdio_client()
    loops = []
    original_factory = asyncio.new_event_loop

    def tracked_loop():
        loop = original_factory()
        loops.append(loop)
        return loop

    async def finish_immediately():
        return None

    monkeypatch.setattr(asyncio, "new_event_loop", tracked_loop)
    monkeypatch.setattr(client, "_keep_alive", finish_immediately)
    try:
        client._background_main()
    finally:
        asyncio.set_event_loop(None)

    assert len(loops) == 1
    assert loops[0].is_closed()


def test_stdio_close_unregisters_atexit_callback(monkeypatch):
    from artpm_agent.core import mcp_client_stdio as module

    registered = []
    unregistered = []
    monkeypatch.setattr(module.atexit, "register", registered.append)
    monkeypatch.setattr(module.atexit, "unregister", unregistered.append)

    client = StdioMCPClient(api_key="sk_live_test", enabled=True)
    client.close()
    client.close()

    assert len(registered) == 1
    assert unregistered == registered


def test_stale_async_failure_reuses_newer_stdio_generation(monkeypatch):
    client = _stdio_client()
    client._generation = 1
    client._session = object()
    ensure_calls = []

    def ensure(force=False):
        ensure_calls.append(force)
        return True

    attempts = 0

    async def submit(coro, _timeout):
        nonlocal attempts
        coro.close()
        attempts += 1
        if attempts == 1:
            with client._lock:
                client._generation = 2
                client._session = newer_session
                client._broken = False
            raise RuntimeError("old transport closed")
        return {"success": True, "data": "ok"}

    newer_session = object()
    monkeypatch.setattr(client, "_ensure_connected", ensure)
    monkeypatch.setattr(client, "_submit_async", submit)

    result = asyncio.run(client._acall("resolve_skill", {}))

    assert result["success"] is True
    assert ensure_calls == [False, False]
    assert client._session is newer_session


def test_http_ping_recovers_after_transient_startup_failure(monkeypatch):
    monkeypatch.setenv("MCP_ENABLED", "true")
    monkeypatch.setenv("SKILLS_FORGE_KEY", "sk_live_test")
    monkeypatch.setenv("SKILLS_FORGE_URL", "https://skills.example.test/api")
    response = SimpleNamespace(
        status_code=200,
        headers={"content-type": "application/json"},
        raise_for_status=lambda: None,
        json=lambda: {"skills": [{"name": "resolve_skill"}]},
    )
    get = Mock(
        side_effect=[
            requests.ConnectionError("offline"),
            requests.ConnectionError("offline"),
            requests.ConnectionError("offline"),
            response,
        ]
    )
    monkeypatch.setattr("artpm_agent.core.mcp_client.requests.get", get)
    monkeypatch.setattr("artpm_agent.core.mcp_client.time.sleep", lambda _delay: None)

    client = MCPClient()
    assert client.enabled is False

    ok, message = client.ping()

    assert ok is True
    assert client.enabled is True
    assert "1 个技能" in message
    assert client.list_skills() == [{"name": "resolve_skill"}]


def test_http_backend_accepts_unified_force_argument(monkeypatch):
    client = object.__new__(MCPClient)
    client.enabled = True
    client._configured = True
    client.available_skills = [{"name": "resolve_skill"}]
    client.base_url = "https://skills.example.test/api"
    client.api_key = "sk_live_test"
    client.last_error = None
    client.last_error_category = None
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"success": True, "data": "resolved"},
    )
    post = Mock(return_value=response)
    monkeypatch.setattr("artpm_agent.core.mcp_client.requests.post", post)

    result = asyncio.run(client.call_skill("resolve_skill", {"query": "x"}, force=True))

    assert result == {"success": True, "data": "resolved"}
    post.assert_called_once()


@pytest.mark.asyncio
async def test_http_skill_execution_offloads_blocking_request(monkeypatch):
    client = object.__new__(MCPClient)
    client.enabled = True
    client._configured = True
    client.available_skills = [{"name": "resolve_skill"}]
    client.base_url = "https://skills.example.test/api"
    client.api_key = "sk_live_test"
    client.last_error = None
    client.last_error_category = None
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"success": True, "data": "resolved"},
    )
    post = Mock(return_value=response)
    offloaded = []

    async def run_in_worker(function, *args, **kwargs):
        offloaded.append(function)
        return function(*args, **kwargs)

    monkeypatch.setattr("artpm_agent.core.mcp_client.requests.post", post)
    monkeypatch.setattr(asyncio, "to_thread", run_in_worker)

    result = await client.call_skill("resolve_skill", {"query": "x"})

    assert result == {"success": True, "data": "resolved"}
    assert post in offloaded


def test_unified_ping_rejects_backend_that_claims_ping_but_is_disabled(
    monkeypatch,
):
    monkeypatch.setenv("MCP_ENABLED", "true")
    client = UnifiedMCPClient()
    client._remote = SimpleNamespace(
        ping=lambda: (True, "looks healthy"),
        is_enabled=lambda: False,
    )

    ok, _message = client.ping()

    assert ok is False


def test_unified_ping_normalizes_backend_exception(monkeypatch):
    monkeypatch.setenv("MCP_ENABLED", "true")
    client = UnifiedMCPClient()
    client._remote = SimpleNamespace(
        ping=Mock(side_effect=RuntimeError("pipe closed")),
        is_enabled=lambda: True,
    )

    ok, message = client.ping()

    assert ok is False
    assert "pipe closed" in message


def test_unified_remote_call_recovers_disabled_http_backend(monkeypatch):
    monkeypatch.setenv("MCP_ENABLED", "true")

    class RecoveringRemote:
        available = False

        def is_enabled(self):
            return self.available

        def ping(self):
            self.available = True
            return True, "recovered"

        async def call_skill(self, name, params, force=False):
            return {"success": True, "skill": name, "params": params}

    client = UnifiedMCPClient()
    client._enhanced = SimpleNamespace(
        list_tools=lambda: [],
        is_enabled=lambda: True,
    )
    client._remote = RecoveringRemote()

    result = asyncio.run(client.call_skill("resolve_skill", {"query": "x"}))

    assert result["success"] is True
    assert result["skill"] == "resolve_skill"


def test_unified_sync_tool_bridge_works_inside_running_event_loop(
    monkeypatch,
):
    monkeypatch.setenv("MCP_ENABLED", "false")

    class Local:
        def is_enabled(self):
            return True

        def list_tools(self):
            return [{"name": "read_file"}]

        async def call_tool(self, name, params):
            return {"success": True, "tool": name, "params": params}

    client = UnifiedMCPClient()
    client._enhanced = Local()

    async def invoke():
        return client.call_tool("read_file", {"file_path": "README.md"})

    result = asyncio.run(invoke())

    assert result["success"] is True
    assert result["tool"] == "read_file"


def test_switching_unified_workspace_closes_old_transport(monkeypatch, tmp_path):
    from artpm_agent.core import mcp_client_unified as module

    old = UnifiedMCPClient(str(tmp_path / "old"))
    old.close = Mock()
    monkeypatch.setattr(module, "_unified_mcp_client", old)

    new = module.get_unified_mcp_client(str(tmp_path / "new"))

    assert new is not old
    old.close.assert_called_once_with()
    module.reset_unified_mcp_client()


def test_agent_close_releases_database_mcp_and_model_gateway():
    agent = ArtPMAgent.__new__(ArtPMAgent)
    agent.database = Mock()
    agent.mcp_client = Mock()
    agent.model_gateway = Mock()

    agent.close()

    agent.database.close.assert_called_once_with()
    agent.mcp_client.close.assert_called_once_with()
    agent.model_gateway.close.assert_called_once_with()


def test_mcp_preview_clears_removed_credentials(monkeypatch):
    from artpm_agent.core import mcp_client as mcp_module
    from artpm_agent.views import settings

    monkeypatch.setenv("SKILLS_FORGE_KEY", "stale-key")
    monkeypatch.setenv("SKILLS_FORGE_URL", "https://stale.example.test")
    monkeypatch.setattr(
        settings,
        "st",
        SimpleNamespace(
            session_state={
                "Skills Forge API Key": "",
                "Skills Forge URL": "",
            }
        ),
    )
    monkeypatch.setattr(settings, "AVAILABLE", False)
    reset = Mock()
    monkeypatch.setattr(mcp_module, "reset_mcp_client", reset)

    settings._apply_mcp_env_preview(False, "stdio")

    assert "SKILLS_FORGE_KEY" not in settings.os.environ
    assert "SKILLS_FORGE_URL" not in settings.os.environ
    assert settings.os.environ["MCP_TRANSPORT"] == "stdio"
    reset.assert_called_once_with()


def test_progress_callback_failure_does_not_abort_stream():
    class Agent:
        def stream_chat(self, _user_input, _context):
            yield from (str(index) for index in range(11))

    def broken_callback(_event):
        raise RuntimeError("window was replaced")

    chunks = list(
        stream_with_progress(
            Agent(),
            "hello",
            {},
            emit_progress=broken_callback,
        )
    )

    assert chunks == [str(index) for index in range(11)]


def test_progress_context_emits_error_instead_of_false_completion():
    events = []

    with pytest.raises(RuntimeError, match="callback window failed"):
        with progress_context(
            TurnStage.SKILL_EXECUTION,
            emit_progress=events.append,
        ):
            raise RuntimeError("callback window failed")

    assert [event.stage for event in events] == [
        TurnStage.SKILL_EXECUTION,
        TurnStage.ERROR,
    ]


def test_ocr_stream_survives_detached_delta_callback():
    client = UnlimitedOCRClient({"enabled": False, "max_output": 100})
    response = [
        b'data: {"choices":[{"delta":{"content":"one"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"two"}}]}\n',
        b"data: [DONE]\n",
    ]
    callback = Mock(side_effect=RuntimeError("window detached"))

    text, truncated = client._parse_stream(response, callback)

    assert text == "onetwo"
    assert truncated is False
    callback.assert_called_once_with("one")


def test_streamlit_render_failure_closes_provider_stream(monkeypatch):
    from artpm_agent import ui_helpers

    closed = []

    def source():
        try:
            yield "first"
            yield "second"
        finally:
            closed.append(True)

    agent = SimpleNamespace(stream_chat=lambda _prompt, context: source())

    def fail_render(chunks):
        assert next(chunks) == "first"
        raise RuntimeError("render window detached")

    monkeypatch.setattr(
        ui_helpers,
        "st",
        SimpleNamespace(write_stream=fail_render),
    )

    with pytest.raises(RuntimeError, match="render window detached"):
        ui_helpers.stream_agent_response(agent, "hello", {})

    assert closed == [True]


def test_model_gateway_closes_stream_when_consumer_disconnects():
    closed = []

    class Client:
        def stream_chat(self, *_args, **_kwargs):
            try:
                yield "first"
                yield "second"
            finally:
                closed.append(True)

    cache = SimpleNamespace(get=lambda *_args: None, put=lambda *_args: None)
    gateway = ModelGateway(
        {"model": "primary", "provider": "custom"},
        Client(),
        response_cache=cache,
    )
    stream = gateway.stream_with_failover("hello", "system", [])

    assert next(stream) == "first"
    stream.close()

    assert closed == [True]


def test_model_gateway_closes_distinct_cached_clients_once():
    first = SimpleNamespace(close=Mock())
    second = SimpleNamespace(close=Mock())
    gateway = ModelGateway(
        {"model": "primary", "provider": "custom"},
        first,
        response_cache=SimpleNamespace(),
    )
    gateway._clients["same"] = first
    gateway._clients["fallback"] = second

    gateway.close()
    gateway.close()

    first.close.assert_called_once_with()
    second.close.assert_called_once_with()


def test_agent_close_releases_later_resources_after_one_failure():
    agent = object.__new__(ArtPMAgent)
    agent.database = SimpleNamespace(
        close=Mock(side_effect=RuntimeError("database close failed"))
    )
    agent.mcp_client = SimpleNamespace(close=Mock())
    agent.model_gateway = SimpleNamespace(close=Mock())

    agent.close()

    agent.database.close.assert_called_once_with()
    agent.mcp_client.close.assert_called_once_with()
    agent.model_gateway.close.assert_called_once_with()
