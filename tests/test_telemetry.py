"""Tests for the per-turn telemetry store."""
from artpm_agent.runtime.telemetry import AgentTelemetry


def test_record_and_summarize(tmp_path):
    t = AgentTelemetry(str(tmp_path / "tel.db"), enabled=True)
    t.record(task_type="chat", model_used="m1", latency_ms=100, success=True)
    t.record(task_type="chat", model_used="m2", latency_ms=200, fallback=True, success=True)
    t.record(task_type="chat", model_used="m1", latency_ms=50, cache_hit=True, success=True)
    t.record(task_type="chat", model_used="m3", latency_ms=500, success=False, error="boom")

    s = t.summarize()
    assert s["samples"] == 4
    assert s["cache_hit_rate"] == 0.25
    assert s["fallback_rate"] == 0.25
    assert s["failure_rate"] == 0.25
    assert s["avg_latency_ms"] == 212.5


def test_disabled_telemetry_noop(tmp_path):
    t = AgentTelemetry(str(tmp_path / "tel.db"), enabled=False)
    t.record(task_type="chat", model_used="m1", latency_ms=100, success=True)
    assert t.recent() == []
    assert t.summarize() == {"samples": 0}


def test_recent_returns_rows(tmp_path):
    t = AgentTelemetry(str(tmp_path / "tel.db"), enabled=True)
    t.record(task_type="routing", model_used="mini", latency_ms=10, success=True)
    rows = t.recent(limit=5)
    assert len(rows) == 1
    assert rows[0]["task_type"] == "routing"
    assert rows[0]["model_used"] == "mini"
