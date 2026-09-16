"""Tests for the optimized ModelGateway: task routing, cache, telemetry."""
from concurrent.futures import ThreadPoolExecutor
import time

from artpm_agent.providers.gateway import ModelGateway
from artpm_agent.providers.response_cache import ResponseCache
from artpm_agent.runtime.performance import performance_snapshot, reset_performance_metrics


class FakeClient:
    def __init__(self, model):
        self.model = model
        self.calls = 0

    def chat(self, prompt, system_prompt=None, history=None):
        self.calls += 1
        return f"ok:{self.model}"

    def stream_chat(self, prompt, system_prompt=None, history=None):
        self.calls += 1
        yield f"ok:{self.model}"


class FakeTelemetry:
    def __init__(self):
        self.records = []

    def record(self, **kw):
        self.records.append(kw)


def _gw(primary, available, **kw):
    cfg = {"model": primary, "provider": "openai", "available_models": available}
    return ModelGateway(
        cfg,
        FakeClient(primary),
        client_factory=lambda c: FakeClient(c["model"]),
        **kw,
    )


def test_task_routing_selects_cheap_model():
    gw = _gw("gpt-4o", ["gpt-4o-mini", "gpt-4o"])
    out = gw.chat_with_failover("hi", "sys", [], task_type="routing")
    assert "gpt-4o-mini" in out


def test_default_no_task_uses_primary():
    gw = _gw("gpt-4o", ["gpt-4o-mini"])
    out = gw.chat_with_failover("hi", "sys", [])
    assert "gpt-4o" in out


def test_first_model_call_latency_is_recorded_once():
    reset_performance_metrics()
    gw = _gw("gpt-4o", [])

    gw.chat_with_failover("first", "sys", [])
    gw.chat_with_failover("second", "sys", [])

    metric = performance_snapshot()["first_model_call"]
    assert metric["count"] == 1
    assert metric["latency_ms"] >= 0
    assert metric["success"] is True


def test_response_cache_avoid_second_llm_call():
    cache = ResponseCache(enabled=True, ttl=100)
    gw = _gw("gpt-4o", ["gpt-4o-mini"], response_cache=cache)
    gw.chat_with_failover("same question", "sys", [])
    gw.chat_with_failover("same question", "sys", [])
    # second identical call served from cache -> primary client called once
    assert gw._primary_client.calls == 1


def test_stream_response_populates_cache_after_complete_answer():
    cache = ResponseCache(enabled=True, ttl=100)
    gw = _gw("gpt-4o", ["gpt-4o-mini"], response_cache=cache)
    first = "".join(gw.stream_with_failover("same question", "sys", []))
    second = "".join(gw.stream_with_failover("same question", "sys", []))
    assert second == first
    assert gw._primary_client.calls == 1
    assert cache.stats()["hits"] == 1


def test_cache_is_isolated_by_workspace_scope():
    cache = ResponseCache(enabled=True, ttl=100)
    gw = _gw("gpt-4o", [], response_cache=cache)
    gw.chat_with_failover("same", "sys", [], cache_scope="tenant:workspace-a")
    gw.chat_with_failover("same", "sys", [], cache_scope="tenant:workspace-b")
    gw.chat_with_failover("same", "sys", [], cache_scope="tenant:workspace-a")
    assert gw._primary_client.calls == 2


def test_partial_stream_is_never_cached():
    class BrokenStreamClient(FakeClient):
        def stream_chat(self, prompt, system_prompt=None, history=None):
            self.calls += 1
            yield "partial"
            raise RuntimeError("connection reset")

    cache = ResponseCache(enabled=True, ttl=100)
    client = BrokenStreamClient("gpt-4o")
    gw = ModelGateway(
        {"model": "gpt-4o", "provider": "openai", "available_models": []},
        client,
        response_cache=cache,
    )
    for _ in range(2):
        gw.mark_model_healthy("gpt-4o")
        try:
            list(gw.stream_with_failover("same", "sys", []))
        except RuntimeError:
            pass
    assert client.calls == 2
    assert cache.stats()["writes"] == 0


def test_fallback_cache_keeps_actual_model_and_visible_notice():
    class FailingClient(FakeClient):
        def chat(self, prompt, system_prompt=None, history=None):
            self.calls += 1
            raise RuntimeError("connection timeout")

    primary = FailingClient("primary")
    fallback = FakeClient("fallback")
    cache = ResponseCache(enabled=True, ttl=100)
    gw = ModelGateway(
        {
            "model": "primary",
            "provider": "openai",
            "available_models": ["fallback"],
            "failover_max_attempts": 2,
        },
        primary,
        client_factory=lambda _config: fallback,
        response_cache=cache,
    )

    first = gw.chat_with_failover("same", "sys", [])
    second = gw.chat_with_failover("same", "sys", [])
    assert second == first
    assert "fallback" in second
    assert primary.calls == 1
    assert fallback.calls == 1

    recovered = FakeClient("primary")
    gw._primary_client = recovered
    gw.mark_model_healthy("primary")
    assert gw.chat_with_failover("same", "sys", []) == "ok:primary"
    assert recovered.calls == 1


def test_environment_false_overrides_enabled_config(monkeypatch):
    monkeypatch.setenv("ARTPM_RESPONSE_CACHE", "false")
    gw = ModelGateway(
        {"model": "gpt-4o", "response_cache_enabled": True},
        FakeClient("gpt-4o"),
    )
    assert gw.response_cache_stats()["enabled"] is False


def test_invalid_cache_limits_fall_back_without_disabling(monkeypatch):
    monkeypatch.delenv("ARTPM_RESPONSE_CACHE", raising=False)
    monkeypatch.setenv("ARTPM_RESPONSE_CACHE_TTL", "invalid")
    gw = ModelGateway(
        {"model": "gpt-4o", "response_cache_enabled": True},
        FakeClient("gpt-4o"),
    )
    stats = gw.response_cache_stats()
    assert stats["enabled"] is True
    assert stats["ttl"] == 1800


def test_concurrent_identical_requests_use_single_flight():
    class SlowClient(FakeClient):
        def chat(self, prompt, system_prompt=None, history=None):
            self.calls += 1
            time.sleep(0.05)
            return "shared answer"

    client = SlowClient("gpt-4o")
    cache = ResponseCache(enabled=True, ttl=100)
    gw = ModelGateway(
        {"model": "gpt-4o", "provider": "openai", "available_models": []},
        client,
        response_cache=cache,
        telemetry=FakeTelemetry(),
    )

    with ThreadPoolExecutor(max_workers=6) as executor:
        answers = list(
            executor.map(
                lambda _index: gw.chat_with_failover(
                    "same", "sys", [], cache_scope="tenant:workspace"
                ),
                range(6),
            )
        )

    assert answers == ["shared answer"] * 6
    assert client.calls == 1
    assert cache.stats()["hits"] == 5


def test_telemetry_recorded_on_success():
    tel = FakeTelemetry()
    gw = _gw("gpt-4o", ["gpt-4o-mini"], telemetry=tel)
    gw.chat_with_failover("hi", "sys", [])
    assert tel.records
    assert tel.records[0]["success"] is True
    assert tel.records[0]["model_used"]


def test_telemetry_recorded_on_failure():
    tel = FakeTelemetry()

    class FailingClient:
        def chat(self, *a, **k):
            raise RuntimeError("down")

        def stream_chat(self, *a, **k):
            yield ""

    cfg = {"model": "bad", "provider": "openai", "available_models": []}
    gw = ModelGateway(
        cfg, FailingClient(), client_factory=lambda c: FailingClient(), telemetry=tel
    )
    try:
        gw.chat_with_failover("hi", "sys", [])
    except RuntimeError:
        pass
    assert tel.records
    assert tel.records[-1]["success"] is False
    assert tel.records[-1]["error"]


def test_gateway_without_response_cache_does_not_crash(monkeypatch):
    """Regression: cache=None must not leave `cached` undefined (P0 fix)."""
    # Force the degraded path where no response cache object exists at all.
    monkeypatch.setattr(ModelGateway, "_build_response_cache", lambda self: None)
    gw = _gw("gpt-4o", ["gpt-4o-mini"])
    assert gw._response_cache is None
    out = gw.chat_with_failover("same question", "sys", [])
    assert "gpt-4o" in out
    # Streaming path must also be safe without a cache.
    chunks = list(gw.stream_with_failover("hi", "sys", []))
    assert "".join(chunks)
