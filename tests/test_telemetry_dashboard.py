"""Tests for the telemetry dashboard (aggregations + HTML render + CLI)."""

from artpm_agent.runtime.telemetry import AgentTelemetry
from artpm_agent.runtime.telemetry_dashboard import (
    collect_dashboard,
    main,
    render_html,
)


def _seed(tmp_path):
    db = str(tmp_path / "telemetry.db")
    tel = AgentTelemetry(db_path=db, enabled=True)
    tel.record(task_type="chat", model_used="gpt-4o", latency_ms=100,
               fallback=False, cache_hit=True, success=True)
    tel.record(task_type="chat", model_used="gpt-4o", latency_ms=200,
               fallback=False, cache_hit=False, success=True)
    tel.record(task_type="reasoning", model_used="o3", latency_ms=800,
               fallback=False, cache_hit=False, success=True)
    tel.record(task_type="reasoning", model_used="o3", latency_ms=900,
               fallback=True, cache_hit=False, success=True)
    tel.record(task_type="chat", model_used="gpt-4o", latency_ms=0,
               fallback=False, cache_hit=True, success=False)
    return tel, db


def test_by_task_type(tmp_path):
    tel, _ = _seed(tmp_path)
    by = tel.by_task_type(50)
    assert set(by.keys()) == {"chat", "reasoning"}
    assert by["chat"]["samples"] == 3
    assert by["reasoning"]["samples"] == 2
    assert by["chat"]["cache_hit_rate"] == 0.667  # 2 cache hits of 3 chat samples


def test_by_model(tmp_path):
    tel, _ = _seed(tmp_path)
    by = tel.by_model(50)
    assert "gpt-4o" in by and "o3" in by
    assert by["o3"]["samples"] == 2
    assert by["gpt-4o"]["samples"] == 3


def test_latency_trend_chronological(tmp_path):
    tel, _ = _seed(tmp_path)
    trend = tel.latency_trend(50)
    assert len(trend) == 5
    assert trend[0]["latency_ms"] == 100  # earliest sample first
    assert trend[-1]["success"] is False


def test_collect_dashboard_structure(tmp_path):
    tel, _ = _seed(tmp_path)
    d = collect_dashboard(tel, window=50)
    assert d["overall"]["samples"] == 5
    assert "chat" in d["by_task_type"]
    assert d["trend"]
    assert d["generated_at"]


def test_render_html_markers(tmp_path):
    tel, _ = _seed(tmp_path)
    html = render_html(collect_dashboard(tel, 50))
    assert "<!DOCTYPE html>" in html
    assert "遥测运营看板" in html
    assert "gpt-4o" in html  # appears in per-model table
    assert "<svg" in html.lower()  # latency sparkline


def test_render_html_empty(tmp_path):
    p = tmp_path / "empty.db"
    tel = AgentTelemetry(db_path=str(p), enabled=True)
    html = render_html(collect_dashboard(tel, 10))
    assert "暂无遥测数据" in html


def test_cli_main_writes_html(tmp_path):
    tel, db = _seed(tmp_path)
    out = tmp_path / "report.html"
    rc = main(["--db", db, "--out", str(out), "--window", "50"])
    assert rc == 0
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "遥测运营看板" in content
