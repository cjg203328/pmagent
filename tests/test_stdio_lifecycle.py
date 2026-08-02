"""Focused lifecycle tests for the persistent stdio MCP worker."""

import asyncio
import sys
import threading
from types import ModuleType, SimpleNamespace

from artpm_agent.core.mcp_client_stdio import StdioMCPClient


def _client() -> StdioMCPClient:
    client = StdioMCPClient(api_key="sk_test", enabled=False)
    client.enabled = True
    return client


def test_close_joins_without_holding_state_lock() -> None:
    client = _client()
    joined = threading.Event()

    class FakeThread:
        alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            assert not client._lock.locked()
            self.alive = False
            joined.set()

    worker = FakeThread()
    client._thread = worker  # type: ignore[assignment]
    client._ready.set()

    client.close()
    client.close()

    assert joined.is_set()
    assert client._thread is None
    assert not client._ready.is_set()


def test_close_allows_worker_cleanup_to_acquire_state_lock() -> None:
    client = _client()
    started = threading.Event()
    cleaned = threading.Event()

    def worker_main() -> None:
        started.set()
        client._shutdown.wait()
        with client._lock:
            cleaned.set()

    worker = threading.Thread(target=worker_main, daemon=True)
    client._thread = worker
    client._ready.set()
    worker.start()
    assert started.wait(timeout=1)

    client.close()

    assert cleaned.is_set()
    assert not worker.is_alive()


def test_force_reconnect_joins_old_worker_before_starting_new_one() -> None:
    client = _client()
    started = threading.Event()
    workers: list[threading.Thread] = []

    def worker_main() -> None:
        current = threading.current_thread()
        workers.append(current)
        with client._lock:
            client._ready.set()
        started.set()
        client._shutdown.wait()
        with client._lock:
            client._ready.clear()
            if client._thread is current:
                client._thread = None

    client._background_main = worker_main  # type: ignore[method-assign]

    assert client._ensure_connected()
    assert started.wait(timeout=1)
    first = client._thread
    assert first is not None

    assert client._ensure_connected(force=True)
    second = client._thread
    assert second is not None and second is not first
    assert not first.is_alive()

    client.close()


def test_shutdown_suppresses_expected_transport_teardown_warning(monkeypatch) -> None:
    client = _client()
    client._shutdown.set()

    class BrokenStdioContext:
        async def __aenter__(self):
            raise RuntimeError("transport closed during shutdown")

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    mcp_module = ModuleType("mcp")
    mcp_module.ClientSession = SimpleNamespace
    stdio_module = ModuleType("mcp.client.stdio")
    stdio_module.stdio_client = lambda params: BrokenStdioContext()
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_module)
    warning_calls = []
    monkeypatch.setattr(
        "artpm_agent.core.mcp_client_stdio.logger.warning",
        lambda *args, **kwargs: warning_calls.append((args, kwargs)),
    )

    asyncio.run(client._keep_alive())

    assert warning_calls == []
    assert client._connect_error is None
