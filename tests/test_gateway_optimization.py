"""Tests for the optimized ModelGateway: task routing, cache, telemetry."""
from artpm_agent.providers.gateway import ModelGateway
from artpm_agent.providers.response_cache import ResponseCache


class FakeClient:
    def __init__(self, model):
        self.model = model
        self.calls = 0

    def chat(self, prompt, system_prompt=None, history=None):
        self.calls += 1
        return f"ok:{self.model}"

    def stream_chat(self, prompt, system_prompt=None, history=None):
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


def test_response_cache_avoid_second_llm_call():
    cache = ResponseCache(enabled=True, ttl=100)
    gw = _gw("gpt-4o", ["gpt-4o-mini"], response_cache=cache)
    gw.chat_with_failover("same question", "sys", [])
    gw.chat_with_failover("same question", "sys", [])
    # second identical call served from cache -> primary client called once
    assert gw._primary_client.calls == 1


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
