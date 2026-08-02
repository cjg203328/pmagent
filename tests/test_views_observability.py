"""Contract tests for the native Streamlit observability view."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from artpm_agent.runtime.telemetry import AgentTelemetry
from artpm_agent.runtime.telemetry_dashboard import collect_dashboard
from artpm_agent.views import observability


class _Column:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


@pytest.fixture
def fake_streamlit(monkeypatch):
    streamlit = SimpleNamespace(
        session_state={},
        columns=lambda spec: [_Column() for _ in range(len(spec) if isinstance(spec, list) else spec)],
        slider=Mock(return_value=500),
        markdown=Mock(),
        button=Mock(return_value=False),
        error=Mock(),
        caption=Mock(),
        dataframe=Mock(),
        plotly_chart=Mock(),
        success=Mock(),
        info=Mock(),
        column_config=SimpleNamespace(TextColumn=Mock()),
    )
    monkeypatch.setattr(observability, "st", streamlit)
    monkeypatch.setattr(observability, "render_page_header", Mock())
    monkeypatch.setattr(observability, "default_telemetry_db_path", lambda: "unused.db")
    return streamlit


def _dashboard_data():
    return {
        "token_summary": {
            "samples": 2,
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "cached_tokens": 10,
            "cost_usd": 0.001,
        },
        "connection_summary": {
            "samples": 2,
            "ok": 1,
            "failed": 1,
            "success_rate": 0.5,
            "avg_latency_ms": 120.0,
            "error_breakdown": {},
        },
        "by_model": {},
        "by_provider": {},
        "trend": [],
        "connection_trend": [],
        "endpoint_health": [],
        "evolution_summary": {"events": 0, "errors": 0},
        "evolution_events": [],
    }


def test_page_rejects_invalid_dashboard_payload(fake_streamlit, monkeypatch):
    monkeypatch.setattr(observability, "_load_dashboard", Mock(return_value=None))

    observability.observability_page()

    fake_streamlit.error.assert_called_once_with("遥测数据暂不可用，请稍后刷新。")


def test_page_renders_current_token_and_connection_contract(
    fake_streamlit,
    monkeypatch,
):
    metric_rail = Mock()
    health_strip = Mock()
    evolution_section = Mock()
    monkeypatch.setattr(
        observability,
        "_load_dashboard",
        Mock(return_value=_dashboard_data()),
    )
    monkeypatch.setattr(observability, "_metric_rail", metric_rail)
    monkeypatch.setattr(observability, "_health_strip", health_strip)
    monkeypatch.setattr(observability, "_evolution_section", evolution_section)

    observability.observability_page()

    health_strip.assert_called_once_with(True, False, None, 0)
    assert metric_rail.call_count == 2
    evolution_section.assert_called_once_with(
        {"events": 0, "errors": 0},
        [],
    )


def test_health_strip_distinguishes_unknown_and_failed_states(fake_streamlit):
    observability._health_strip(None, False, False, 2)

    html = fake_streamlit.markdown.call_args_list[-1].args[0]
    assert "Token 暂无数据" in html
    assert "连接 异常" in html
    assert html.count("2 异常") == 1
    assert 'obs-health-pill unknown' in html
    assert html.count('obs-health-pill bad') == 2


def test_collect_dashboard_returns_nested_observability_data(tmp_path):
    telemetry = AgentTelemetry(
        db_path=str(tmp_path / "telemetry.db"),
        enabled=True,
    )
    telemetry.record(
        task_type="chat",
        model_used="gpt-4o-mini",
        latency_ms=75,
        success=True,
        prompt_tokens=20,
        completion_tokens=5,
        total_tokens=25,
        provider="openai",
    )
    telemetry.record_connection(
        provider="openai",
        model="gpt-4o-mini",
        endpoint="https://api.openai.com/v1",
        attempt=0,
        ok=True,
        latency_ms=75,
        task_type="chat",
    )

    result = collect_dashboard(telemetry, window=100)

    assert result["token_summary"]["total_tokens"] == 25
    assert result["connection_summary"]["success_rate"] == 1.0
    assert result["window"] == 100


def test_collect_dashboard_handles_empty_database(tmp_path):
    telemetry = AgentTelemetry(
        db_path=str(tmp_path / "empty.db"),
        enabled=True,
    )

    result = collect_dashboard(telemetry, window=100)

    assert result["token_summary"]["samples"] == 0
    assert result["connection_summary"]["samples"] == 0
    assert result["evolution_summary"]["events"] == 0
