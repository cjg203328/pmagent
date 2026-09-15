"""成本管控技能单元测试：成本估算 / 预算跟踪 / 超支告警（fake DB，无需真实库）"""
from artpm_agent.skills.cost_control_skill import CostControlSkill
from artpm_agent.presentation import format_skill_result


class FakeProject:
    def __init__(self, pid, name, quote, cost=None, assets=None):
        self.id = pid
        self.project_name = name
        self.quote_amount = quote
        self.cost = cost
        self._assets = assets or []

    def get_project_assets(self):
        return self._assets


class FakeDB:
    def __init__(self, projects=None):
        self._projects = {p.id: p for p in (projects or [])}

    def get_project(self, pid):
        return self._projects.get(pid)

    def get_project_assets(self, pid):
        p = self._projects.get(pid)
        return p.get_project_assets() if p else []


def _skill(config=None, db=None):
    return CostControlSkill(context={"config": config or {}, "database": db})


def test_estimate_uses_daily_cost():
    cfg = {"cost_config": {"staff_levels": {"中级": {"daily_cost": 500}},
                           "overhead_rate": 0.15, "tax_rate": 0.06}}
    r = _skill(cfg).estimate(hours=16, staff_level="中级", quantity=1)
    assert r["success"]
    # 16h = 2 天 × 500 = 1000 人工；+15%管理=150；+6%税=69；合计 1219
    assert r["labor_cost"] == 1000.0
    assert r["total_cost"] == 1219.0


def test_estimate_fallback_when_no_config():
    r = _skill(None).estimate(hours=8, staff_level="高级")
    assert r["success"]
    assert r["daily_cost"] == 1000.0


def test_estimate_unknown_level():
    r = _skill(None).estimate(hours=8, staff_level="学徒")
    assert not r["success"]


def test_budget_uses_project_cost():
    db = FakeDB([FakeProject(1, "P1", 50000, cost=30000)])
    r = _skill(db=db).budget(1)
    assert r["success"]
    assert r["budget"] == 50000
    assert r["spent"] == 30000
    assert r["remaining"] == 20000


def test_budget_falls_back_to_assets():
    class A:
        total_price = 12000
    db = FakeDB([FakeProject(2, "P2", 40000, cost=None, assets=[A(), A()])])
    r = _skill(db=db).budget(2)
    assert r["spent"] == 24000


def test_zero_budget_results_are_renderable():
    """A zero budget has no meaningful utilization percentage, but is valid."""
    db = FakeDB([FakeProject(5, "P5", 0, cost=0)])

    budget_rendered = format_skill_result(
        "cost_control", _skill(db=db).budget(5)
    )
    assert "（利用率 不可用）" in budget_rendered
    assert "None" not in budget_rendered

    overrun_rendered = format_skill_result(
        "cost_control", _skill(db=db).overrun(5)
    )
    assert "• 利用率: 不可用" in overrun_rendered
    assert "None" not in overrun_rendered


def test_zero_budget_overrun_preserves_custom_threshold():
    db = FakeDB([FakeProject(6, "P6", 0, cost=0)])

    rendered = format_skill_result(
        "cost_control", _skill(db=db).overrun(6, threshold=0.75)
    )

    assert "（阈值 75%）" in rendered


def test_overrun_alert_triggers():
    db = FakeDB([FakeProject(3, "P3", 10000, cost=9500)])
    r = _skill(db=db).overrun(3, threshold=0.9)
    assert r["alert"] is True
    assert r["level"] == "warning"


def test_overrun_critical():
    db = FakeDB([FakeProject(4, "P4", 10000, cost=11000)])
    r = _skill(db=db).overrun(4)
    assert r["level"] == "critical"


def test_execute_dispatch():
    r = _skill(None).execute({"action": "estimate", "hours": 8})
    assert r["success"]
    assert not _skill(None).execute({"action": "x"})["success"]


def test_execute_rejects_lossy_project_id_coercion():
    class RecordingDB(FakeDB):
        def __init__(self):
            super().__init__([FakeProject(1, "P1", 100)])
            self.requested_ids = []

        def get_project(self, pid):
            self.requested_ids.append(pid)
            return super().get_project(pid)

    db = RecordingDB()
    skill = _skill(db=db)

    fractional = skill.execute({"action": "budget", "project_id": 1.9})
    boolean = skill.execute({"action": "overrun", "project_id": True})

    assert fractional == {"success": False, "error": "project_id 必须是正整数"}
    assert boolean == {"success": False, "error": "project_id 必须是正整数"}
    assert db.requested_ids == []


def test_execute_accepts_numeric_project_id_string_without_truncation():
    db = FakeDB([FakeProject(7, "P7", 100)])
    result = _skill(db=db).execute({"action": "budget", "project_id": "7"})

    assert result["success"] is True
    assert result["project_id"] == 7
