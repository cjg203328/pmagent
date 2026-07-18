"""进化闭环事件采集的契约测试（隔离至 tmp_path）。"""
import sys
from pathlib import Path

import pytest

# 确保项目根目录在 Python 路径中
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from artpm_agent.runtime.telemetry import AgentTelemetry  # noqa: E402
from artpm_agent.runtime.telemetry_dashboard import (  # noqa: E402
    collect_dashboard,
    render_html,
)


@pytest.fixture()
def telemetry(tmp_path):
    db = str(tmp_path / "telemetry.db")
    tel = AgentTelemetry(db_path=db, enabled=True)
    yield tel
    try:
        Path(db).unlink()
    except OSError:
        pass


def test_record_and_recent_roundtrip(telemetry):
    telemetry.record_event(stage="auto_reflect", level="warning", message="boom")
    telemetry.record_event(stage="outcome_record", level="info", message="ok")
    events = telemetry.recent_events(limit=10)
    assert len(events) == 2
    # 最近优先（DESC）
    assert events[0]["stage"] == "outcome_record"
    assert events[0]["level"] == "info"


def test_recent_events_filters_by_level_and_stage(telemetry):
    telemetry.record_event(stage="auto_reflect", level="warning", message="a")
    telemetry.record_event(stage="auto_reflect", level="error", message="b")
    telemetry.record_event(stage="outcome_record", level="info", message="c")
    warns = telemetry.recent_events(limit=10, level="warning")
    assert len(warns) == 1 and warns[0]["message"] == "a"
    reflects = telemetry.recent_events(limit=10, stage="auto_reflect")
    assert len(reflects) == 2


def test_evolution_summary_aggregates(telemetry):
    telemetry.record_event(stage="auto_reflect", level="warning", message="x")
    telemetry.record_event(stage="auto_consolidate", level="warning", message="y")
    telemetry.record_event(stage="outcome_record", level="info", message="z")
    summary = telemetry.evolution_summary(window=50)
    assert summary["events"] == 3
    assert summary["errors"] == 2
    assert summary["by_stage"].get("auto_reflect") == 1
    assert summary["by_stage"].get("auto_consolidate") == 1
    assert summary["last_run"] is not None
    assert summary["last_error"] is not None
    assert summary["last_error"]["stage"] == "auto_consolidate"


def test_evolution_summary_empty(telemetry):
    summary = telemetry.evolution_summary(window=50)
    assert summary["events"] == 0
    assert summary["errors"] == 0
    assert summary["last_run"] is None


def test_record_event_disabled_is_noop():
    tel = AgentTelemetry(db_path=":memory:", enabled=False)
    tel.record_event(stage="auto_reflect", level="warning", message="x")
    assert tel.recent_events(limit=10) == []


def test_collect_dashboard_includes_evolution(telemetry):
    telemetry.record_event(stage="auto_reflect", level="warning", message="boom")
    dash = collect_dashboard(telemetry, window=50)
    assert "evolution_summary" in dash
    assert "evolution_events" in dash
    assert dash["evolution_summary"]["events"] == 1
    assert len(dash["evolution_events"]) == 1


def test_render_html_includes_evolution_panel(telemetry):
    telemetry.record_event(stage="auto_reflect", level="warning", message="boom")
    telemetry.record_event(stage="outcome_record", level="info", message="ok")
    dash = collect_dashboard(telemetry, window=50)
    html = render_html(dash)
    assert "进化闭环" in html
    assert "最近事件" in html
    assert "自动复盘" in html  # 阶段中文标签


def test_render_html_no_evolution_panel_when_empty(telemetry):
    dash = collect_dashboard(telemetry, window=50)
    html = render_html(dash)
    assert "进化闭环" not in html


def test_only_info_heartbeat_sets_last_run_not_last_error(telemetry):
    # 成功心跳（loop_run / info）应让「最后运行」有值，且不应被误判为异常
    telemetry.record_event(stage="loop_run", level="info", message="回合闭环完成")
    summary = telemetry.evolution_summary(window=50)
    assert summary["events"] == 1
    assert summary["errors"] == 0
    assert summary["last_run"] is not None
    assert summary["last_error"] is None


def test_loop_run_label_present_in_both_maps():
    from artpm_agent.runtime.telemetry_dashboard import (
        _EVOLUTION_STAGE_LABELS as dash_labels,
    )
    from artpm_agent.views.observability import (
        _EVOLUTION_STAGE_LABELS as page_labels,
    )

    assert dash_labels.get("loop_run") == "闭环运行"
    assert page_labels.get("loop_run") == "闭环运行"
