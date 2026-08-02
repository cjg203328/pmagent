"""Tests for the current public metrics collector API."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import artpm_agent.utils.metrics as metrics_module
from artpm_agent.utils.metrics import MetricEvent, MetricsCollector, metrics


def _jsonl_rows(output_dir: Path) -> list[dict]:
    log_files = list(output_dir.glob("metrics_*.jsonl"))
    assert len(log_files) == 1
    return [
        json.loads(line)
        for line in log_files[0].read_text(encoding="utf-8").splitlines()
    ]


def test_metric_event_exposes_recorded_fields() -> None:
    timestamp = datetime(2026, 7, 22, tzinfo=timezone.utc)
    event = MetricEvent(
        timestamp=timestamp,
        metric_type="api_call",
        name="model_request",
        duration_ms=12.5,
        status="success",
        metadata={"model": "test-model"},
    )

    assert event.timestamp is timestamp
    assert event.metric_type == "api_call"
    assert event.name == "model_request"
    assert event.duration_ms == 12.5
    assert event.status == "success"
    assert event.metadata == {"model": "test-model"}


def test_collector_initializes_output_directory(tmp_path: Path) -> None:
    output_dir = tmp_path / "nested" / "metrics"
    collector = MetricsCollector(output_dir=str(output_dir))

    assert collector.output_dir == output_dir
    assert output_dir.is_dir()
    assert collector.events == []
    assert collector.get_summary() == {}


def test_track_success_records_memory_and_jsonl(monkeypatch, tmp_path: Path) -> None:
    elapsed = iter([100.0, 100.025])
    monkeypatch.setattr(metrics_module.time, "perf_counter", lambda: next(elapsed))
    collector = MetricsCollector(output_dir=str(tmp_path / "metrics"))

    with collector.track(
        "skill_execution",
        "quote_calculator",
        request_id="request-1",
    ):
        pass

    assert len(collector.events) == 1
    event = collector.events[0]
    assert event.metric_type == "skill_execution"
    assert event.name == "quote_calculator"
    assert event.duration_ms == pytest.approx(25.0)
    assert event.status == "success"
    assert event.metadata == {"request_id": "request-1"}

    rows = _jsonl_rows(collector.output_dir)
    assert rows == [
        {
            "timestamp": event.timestamp.isoformat(),
            "type": "skill_execution",
            "name": "quote_calculator",
            "duration_ms": pytest.approx(25.0),
            "status": "success",
            "metadata": {"request_id": "request-1"},
        }
    ]


def test_track_error_reraises_original_and_records_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    elapsed = iter([50.0, 50.01])
    monkeypatch.setattr(metrics_module.time, "perf_counter", lambda: next(elapsed))
    collector = MetricsCollector(output_dir=str(tmp_path / "metrics"))
    original = ValueError("invalid amount")

    with pytest.raises(ValueError) as exc_info:
        with collector.track("api_call", "create_quote", request_id="request-2"):
            raise original

    assert exc_info.value is original
    event = collector.events[0]
    assert event.status == "error"
    assert event.duration_ms == pytest.approx(10.0)
    assert event.metadata == {
        "request_id": "request-2",
        "error": "invalid amount",
    }
    assert _jsonl_rows(collector.output_dir)[0]["status"] == "error"


def test_get_summary_aggregates_counts_durations_and_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    elapsed = iter([0.0, 0.01, 1.0, 1.03, 2.0, 2.02])
    monkeypatch.setattr(metrics_module.time, "perf_counter", lambda: next(elapsed))
    collector = MetricsCollector(output_dir=str(tmp_path / "metrics"))

    with collector.track("skill", "quote"):
        pass
    with pytest.raises(RuntimeError):
        with collector.track("skill", "quote"):
            raise RuntimeError("failed")
    with collector.track("api", "health"):
        pass

    summary = collector.get_summary()

    assert summary["skill.quote"]["count"] == 2
    assert summary["skill.quote"]["errors"] == 1
    assert summary["skill.quote"]["total_ms"] == pytest.approx(40.0)
    assert summary["skill.quote"]["avg_ms"] == pytest.approx(20.0)
    assert summary["api.health"]["count"] == 1
    assert summary["api.health"]["errors"] == 0
    assert summary["api.health"]["avg_ms"] == pytest.approx(20.0)


def test_jsonl_escapes_untrusted_metadata_and_name(tmp_path: Path) -> None:
    collector = MetricsCollector(output_dir=str(tmp_path / "metrics"))
    untrusted_name = "../../outside\nnext-line"
    untrusted_value = 'quote "alpha"\nsecond line'

    with collector.track("api_call", untrusted_name, user_input=untrusted_value):
        pass

    rows = _jsonl_rows(collector.output_dir)
    assert rows[0]["name"] == untrusted_name
    assert rows[0]["metadata"]["user_input"] == untrusted_value
    assert not (tmp_path / "outside").exists()
    assert list(tmp_path.glob("metrics_*.jsonl")) == []


def test_nested_tracks_record_both_events(tmp_path: Path) -> None:
    collector = MetricsCollector(output_dir=str(tmp_path / "metrics"))

    with collector.track("workflow", "outer"):
        with collector.track("workflow", "inner"):
            pass

    assert [event.name for event in collector.events] == ["inner", "outer"]
    assert len(_jsonl_rows(collector.output_dir)) == 2


def test_global_metrics_is_a_collector_without_mutating_it() -> None:
    assert isinstance(metrics, MetricsCollector)
