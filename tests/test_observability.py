"""Observability tests: token consumption + connection/link recording.

Covers ``runtime.pricing``, ``runtime.telemetry`` aggregations, the dashboard
data layer, and the ``ModelGateway`` wiring that records token/cost + per-attempt
connection events on success, cache-hit, and failure paths.

All tests are non-destructive and run without Streamlit, the business database,
or any network call.
"""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from artpm_agent.providers.gateway import ModelGateway
from artpm_agent.runtime import pricing
from artpm_agent.runtime.telemetry import AgentTelemetry
from artpm_agent.runtime.telemetry_dashboard import collect_dashboard, render_html


# ───────────────────────────────── pricing ─────────────────────────────────


def test_pricing_known_model_returns_expected_cost():
    # gpt-4o: $2.5/1M in, $10/1M out -> 1000*2.5e-6 + 500*10e-6 = 0.0075
    assert round(pricing.estimate_cost("gpt-4o", 1000, 500), 4) == 0.0075


def test_pricing_unknown_model_uses_tier_fallback_and_never_raises():
    # unknown model -> general-tier fallback (non-negative, no exception)
    assert pricing.estimate_cost("totally-unknown-model-xyz", 1000, 500) >= 0


def test_pricing_never_raises_on_garbage_input():
    assert pricing.estimate_cost(None, -5, "abc") >= 0
    assert pricing.estimate_cost("", 0, 0) >= 0


def test_pricing_register_and_match():
    pricing.register_price("my-custom-model", 1.0, 2.0)
    # 1000 in * 1e-6 + 1000 out * 2e-6 = 0.001 + 0.002 = 0.003
    assert pricing.estimate_cost("my-custom-model", 1000, 1000) == 0.003


def test_pricing_cache_hit_cost_is_zero_via_gateway_helper():
    g = ModelGateway({"model": "gpt-4o", "provider": "openai"}, Mock())
    usage = g._estimate_usage(
        system_prompt="s", prompt="p", history=[], output_text="o",
        model="gpt-4o", client=None, cache_hit=True,
    )
    assert usage["cost_usd"] == 0.0
    assert usage["cached_tokens"] == usage["completion_tokens"]


# ─────────────────────────── telemetry storage ────────────────────────────


def test_telemetry_token_summary_aggregates(tmp_path):
    tel = AgentTelemetry(db_path=str(tmp_path / "telemetry.db"), enabled=True)
    tel.record(
        task_type="chat", model_used="gpt-4o", latency_ms=100.0, success=True,
        prompt_tokens=100, completion_tokens=20, total_tokens=120, cost_usd=0.001,
        provider="openai", endpoint="https://api.openai.com/v1",
    )
    tel.record(
        task_type="chat", model_used="gpt-4o", latency_ms=110.0, success=True,
        prompt_tokens=200, completion_tokens=40, total_tokens=240, cost_usd=0.002,
        provider="openai", endpoint="https://api.openai.com/v1",
    )
    s = tel.token_summary(200)
    assert s["samples"] == 2
    assert s["prompt_tokens"] == 300
    assert s["completion_tokens"] == 60
    assert s["total_tokens"] == 360
    assert s["cost_usd"] == 0.003


def test_telemetry_connection_summary_and_health(tmp_path):
    tel = AgentTelemetry(db_path=str(tmp_path / "telemetry.db"), enabled=True)
    tel.record_connection(
        provider="openai", model="gpt-4o", endpoint="https://api.openai.com/v1",
        attempt=0, ok=True, latency_ms=120.0, task_type="chat",
    )
    tel.record_connection(
        provider="openai", model="gpt-4o", endpoint="https://api.openai.com/v1",
        attempt=0, ok=False, error_type="timeout", latency_ms=3000.0, task_type="chat",
    )
    cs = tel.connection_summary(200)
    assert cs["samples"] == 2
    assert cs["ok"] == 1
    assert cs["success_rate"] == 0.5
    assert cs["error_breakdown"] == {"timeout": 1}

    health = tel.endpoint_health(200)
    assert len(health) == 1
    assert health[0]["success_rate"] == 0.5
    assert health[0]["last_error"] == "timeout"


def test_telemetry_by_provider_groups_tokens_and_conn(tmp_path):
    tel = AgentTelemetry(db_path=str(tmp_path / "telemetry.db"), enabled=True)
    tel.record(
        task_type="chat", model_used="gpt-4o", latency_ms=100.0, success=True,
        total_tokens=120, cost_usd=0.001, provider="openai",
        endpoint="https://api.openai.com/v1",
    )
    tel.record_connection(
        provider="openai", model="gpt-4o", endpoint="https://api.openai.com/v1",
        attempt=0, ok=True, latency_ms=120.0, task_type="chat",
    )
    bp = tel.by_provider(200)
    assert "openai" in bp
    assert bp["openai"]["total_tokens"] == 120
    assert bp["openai"]["attempts"] == 1
    assert bp["openai"]["conn_success_rate"] == 1.0


# ─────────────────────────── dashboard data layer ─────────────────────────


def test_dashboard_includes_observability_panels(tmp_path):
    tel = AgentTelemetry(db_path=str(tmp_path / "telemetry.db"), enabled=True)
    for i in range(4):
        tel.record(
            task_type="chat", model_used="gpt-4o", latency_ms=100.0 + i, success=True,
            prompt_tokens=100, completion_tokens=20, total_tokens=120, cost_usd=0.0006,
            provider="openai", endpoint="https://api.openai.com/v1", attempt=0,
        )
        tel.record_connection(
            provider="openai", model="gpt-4o", endpoint="https://api.openai.com/v1",
            attempt=0, ok=True, latency_ms=100.0 + i, task_type="chat",
        )
    tel.record_connection(
        provider="openai", model="gpt-4o", endpoint="https://api.openai.com/v1",
        attempt=0, ok=False, error_type="rate_limit", latency_ms=50.0, task_type="chat",
    )

    dash = collect_dashboard(tel, window=500)
    html = render_html(dash)
    assert "Token 消耗" in html
    assert "连接 / 链接情况" in html
    assert "端点健康" in html
    assert "错误类型分布" in html
    assert "按 Provider" in html


# ─────────────────────────── gateway wiring ───────────────────────────────


class FakeTelemetry:
    def __init__(self):
        self.turns = []
        self.conns = []

    def record(self, **kw):
        self.turns.append(kw)

    def record_connection(self, **kw):
        self.conns.append(kw)


def _gateway_with_telemetry(primary="gpt-4o", provider="openai",
                            primary_client=None, telemetry=None):
    llm_config = {
        "model": primary,
        "provider": provider,
        "openai_api_base": "https://api.openai.com/v1",
    }
    return ModelGateway(llm_config, primary_client, telemetry=telemetry)


def test_gateway_success_records_tokens_and_connection():
    client = Mock()
    client.chat.return_value = "你好，这是模型回复。"
    client.last_usage = {"prompt_tokens": 120, "completion_tokens": 30}
    tel = FakeTelemetry()
    g = _gateway_with_telemetry(primary_client=client, telemetry=tel)

    resp = g.chat_with_failover("hi", "sys", [])

    assert "你好" in resp
    assert len(tel.turns) == 1
    assert len(tel.conns) == 1
    turn = tel.turns[0]
    assert turn["prompt_tokens"] == 120
    assert turn["completion_tokens"] == 30
    assert turn["cost_usd"] > 0
    assert turn["provider"] == "openai"
    assert turn["endpoint"] == "https://api.openai.com/v1"
    assert turn["success"] is True
    conn = tel.conns[0]
    assert conn["ok"] is True
    assert conn["latency_ms"] >= 0


def test_gateway_cache_hit_records_zero_cost():
    client = Mock()
    client.chat.return_value = "real answer"
    tel = FakeTelemetry()
    g = _gateway_with_telemetry(primary_client=client, telemetry=tel)

    class Cache:
        def get(self, *a):
            return "cached answer"

        def put(self, *a):
            pass

    g._response_cache = Cache()

    resp = g.chat_with_failover("hi", "sys", [])
    assert resp == "cached answer"
    assert len(tel.turns) == 1
    assert tel.turns[0]["cache_hit"] is True
    assert tel.turns[0]["cost_usd"] == 0.0
    assert len(tel.conns) == 1
    assert tel.conns[0]["ok"] is True


def test_gateway_failure_records_error_and_connection_failure():
    client = Mock()
    client.chat.side_effect = RuntimeError("connection reset")
    tel = FakeTelemetry()
    g = _gateway_with_telemetry(primary_client=client, telemetry=tel)

    with pytest.raises(RuntimeError):
        g.chat_with_failover("hi", "sys", [])

    assert len(tel.turns) == 1
    turn = tel.turns[0]
    assert turn["success"] is False
    assert turn["error_type"] == "timeout"  # "connection reset" -> timeout bucket
    assert turn["error"] is not None
    assert len(tel.conns) == 1
    assert tel.conns[0]["ok"] is False
    assert tel.conns[0]["error_type"] == "timeout"


def test_gateway_streaming_success_records_connection_and_tokens():
    client = Mock()
    client.stream_chat.return_value = iter(["你好", "世界"])
    client.last_usage = {"prompt_tokens": 50, "completion_tokens": 10}
    tel = FakeTelemetry()
    g = _gateway_with_telemetry(primary_client=client, telemetry=tel)

    chunks = list(g.stream_with_failover("hi", "sys", []))
    assert "".join(chunks) == "你好世界"

    assert len(tel.turns) == 1
    assert len(tel.conns) == 1
    assert tel.conns[0]["ok"] is True
    # streaming reads client.last_usage (real usage path)
    assert tel.turns[0]["prompt_tokens"] == 50
    assert tel.turns[0]["completion_tokens"] == 10
