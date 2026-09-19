from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from artpm_agent.providers.gateway import ModelGateway
from artpm_agent.providers.gateway_cache import BoundedClientCache


class _ClosableClient:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def test_bounded_client_cache_expires_and_closes_idle_entries() -> None:
    now = [10.0]
    client = _ClosableClient()
    cache = BoundedClientCache(
        max_entries=2,
        ttl_seconds=5,
        close_client=lambda value: value.close(),
        clock=lambda: now[0],
    )
    cache["model-a"] = client

    now[0] = 16.0

    assert cache.prune() == 1
    assert len(cache) == 0
    assert client.close_count == 1


def test_bounded_client_cache_keeps_hard_limit_while_all_entries_are_leased() -> None:
    first = _ClosableClient()
    second = _ClosableClient()
    cache = BoundedClientCache(
        max_entries=1,
        ttl_seconds=60,
        close_client=lambda value: value.close(),
    )

    with cache.lease("model-a", lambda: first) as leased_first:
        with cache.lease("model-b", lambda: second) as leased_second:
            assert leased_first is first
            assert leased_second is second
            assert len(cache) == 1
            assert first.close_count == 0
        assert second.close_count == 1

    cache.close_all()
    assert first.close_count == 1


def test_client_cache_serializes_concurrent_construction() -> None:
    entered = Event()
    release = Event()
    created: list[_ClosableClient] = []
    cache = BoundedClientCache(
        max_entries=2,
        ttl_seconds=60,
        close_client=lambda value: value.close(),
    )

    def factory() -> _ClosableClient:
        entered.set()
        assert release.wait(2)
        client = _ClosableClient()
        created.append(client)
        return client

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(cache.get_or_create, "shared-model", factory)
            for _ in range(8)
        ]
        assert entered.wait(2)
        release.set()
        clients = [future.result(timeout=2) for future in futures]

    assert len(created) == 1
    assert all(client is created[0] for client in clients)
    cache.close_all()


def test_gateway_response_metadata_is_request_local() -> None:
    gateway = ModelGateway(
        {"model": "primary", "provider": "custom"},
        _ClosableClient(),
        client_factory=lambda _config: _ClosableClient(),
        response_cache=False,
    )
    release = Event()
    ready = Event()
    ready_count = 0
    guard = Lock()

    def record(model_id: str) -> tuple[str | None, str | None]:
        nonlocal ready_count
        gateway.record_success(model_id, "primary")
        with guard:
            ready_count += 1
            if ready_count == 2:
                ready.set()
        assert release.wait(2)
        metadata = gateway.response_metadata
        return metadata.model_id, metadata.fallback_from

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(record, "fallback-a")
        second = executor.submit(record, "fallback-b")
        assert ready.wait(2)
        release.set()
        assert first.result(timeout=2) == ("fallback-a", "primary")
        assert second.result(timeout=2) == ("fallback-b", "primary")

    assert gateway.last_response_model == "primary"
    assert gateway.last_model_fallback_from is None


def test_gateway_enforces_model_request_concurrency_limit() -> None:
    active = 0
    max_active = 0
    calls = 0
    guard = Lock()
    two_entered = Event()
    release = Event()

    class Client(_ClosableClient):
        def chat(self, *_args, **_kwargs) -> str:
            nonlocal active, max_active, calls
            with guard:
                active += 1
                calls += 1
                max_active = max(max_active, active)
                if active == 2:
                    two_entered.set()
            assert release.wait(2)
            with guard:
                active -= 1
            return "ok"

    gateway = ModelGateway(
        {
            "model": "primary",
            "provider": "custom",
            "max_concurrent_requests": 2,
            "request_queue_timeout_seconds": 2,
        },
        Client(),
        client_factory=lambda _config: Client(),
        response_cache=False,
    )
    gateway._response_cache = None

    def invoke(index: int) -> str:
        return gateway.chat_with_failover(
            f"prompt-{index}",
            "system",
            [],
            cache_scope=f"scope-{index}",
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(invoke, index) for index in range(3)]
        assert two_entered.wait(2)
        with guard:
            assert calls == 2
        release.set()
        assert [future.result(timeout=2) for future in futures] == ["ok"] * 3

    assert max_active == 2
    gateway.close()
