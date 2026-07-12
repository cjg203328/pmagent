"""报价排期技能单元测试：人天估算 / 排期时间线 / 里程碑（无需真实库）"""
from skills.quote_scheduling_skill import QuoteSchedulingSkill


def _skill():
    return QuoteSchedulingSkill(context={})


def test_estimate_simple():
    r = _skill().estimate_man_days("simple", quantity=1)
    assert r["success"]
    assert r["total_hours"] == 8
    assert r["man_days"] == 1.0


def test_estimate_complex_quantity():
    r = _skill().estimate_man_days("complex", quantity=2, history_factor=1.0)
    assert r["total_hours"] == 120
    assert r["man_days"] == 15.0


def test_estimate_unknown():
    assert not _skill().estimate_man_days("外星").get("success")


def test_build_schedule_dates():
    assets = [
        {"asset_name": "主角", "complexity": "complex", "quantity": 1},
        {"asset_name": "场景", "complexity": "medium", "quantity": 1},
    ]
    r = _skill().build_schedule(assets, start_date="2026-08-01")
    assert r["success"]
    assert len(r["timeline"]) == 2
    assert r["timeline"][0]["start"] == "2026-08-01"
    # 复杂(60h=7.5md)+中等(24h=3md) ≈ 10.5 人天，结束日期应晚于开始
    assert r["finish_date"] > "2026-08-01"
    # 周末跳过：起止应为工作日
    assert r["timeline"][0]["start"] == "2026-08-01"  # 周六? 8-01-2026 是周六


def test_build_schedule_empty():
    assert not _skill().build_schedule([], "2026-08-01").get("success")


def test_milestone_plan():
    assets = [
        {"asset_name": "A", "complexity": "simple"},
        {"asset_name": "B", "complexity": "medium"},
    ]
    r = _skill().milestone_plan(assets, "2026-08-03")
    assert r["success"]
    phases = [m["phase"] for m in r["milestones"]]
    assert "启动" in phases and "验收" in phases


def test_execute_dispatch():
    r = _skill().execute({"action": "estimate", "complexity": "medium"})
    assert r["success"]
    assert not _skill().execute({"action": "x"})["success"]
