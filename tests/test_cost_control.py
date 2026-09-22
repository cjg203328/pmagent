"""成本管控技能单元测试：成本估算 / 预算跟踪 / 超支告警（fake DB，无需真实库）

费率语义遵循 [`docs/product/领域数据契约.md`](../docs/product/领域数据契约.md)：
人天费率只来自 `team_members.daily_cost`，取不到就 `needs_input`，
**不再**从配置档位表或内置常量回退。
"""

from artpm_agent.skills.cost_control_skill import CostControlSkill
from artpm_agent.presentation import format_skill_result

RATES = {"overhead_rate": 0.15, "tax_rate": 0.06}


class FakeMember:
    def __init__(self, name, daily_cost=None, skill_level=None, cost_source=None):
        self.name = name
        self.daily_cost = daily_cost
        self.skill_level = skill_level
        self.cost_source = cost_source
        self.cost_effective_at = None


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
    def __init__(self, projects=None, members=None):
        self._projects = {p.id: p for p in (projects or [])}
        self._members = list(members or [])

    def get_project(self, pid):
        return self._projects.get(pid)

    def get_project_assets(self, pid):
        p = self._projects.get(pid)
        return p.get_project_assets() if p else []

    def list_members(self, is_active=None):
        return list(self._members)


def _skill(config=None, db=None):
    return CostControlSkill(context={"config": config or {}, "database": db})


def _config(**overrides):
    return {"cost_config": {**RATES, **overrides}}


def _member_db():
    return FakeDB(
        members=[
            FakeMember(
                "张三", daily_cost=500, skill_level="中级", cost_source="contract"
            )
        ]
    )


# ── 费率来源：team_members.daily_cost ──────────────────────────


def test_estimate_uses_member_daily_cost():
    r = _skill(_config(), _member_db()).estimate(
        hours=16, staff_level="中级", quantity=1
    )

    assert r["success"] is True
    # 16h = 2 天 × 500 = 1000 人工；+15%管理=150；+6%税=69；合计 1219
    assert r["labor_cost"] == 1000.0
    assert r["total_cost"] == 1219.0
    assert r["daily_cost"] == 500.0
    assert r["cost_source"] == "contract"
    assert r["member"] == "张三"


def test_estimate_can_target_member_by_name():
    db = FakeDB(
        members=[
            FakeMember(
                "张三", daily_cost=500, skill_level="中级", cost_source="contract"
            ),
            FakeMember(
                "李四", daily_cost=900, skill_level="中级", cost_source="manual"
            ),
        ]
    )
    r = _skill(_config(), db).estimate(hours=8, member="李四")

    assert r["success"] is True
    assert r["daily_cost"] == 900.0


def test_estimate_refuses_when_no_member_table():
    """读不到费率表时不得回退内置常量，必须要求补数据。"""
    r = _skill(_config(), None).estimate(hours=8, staff_level="高级")

    assert r["success"] is False
    assert r["status"] == "needs_input"
    assert "team_members" in r["missing"][0]["field"]


def test_estimate_refuses_when_member_has_no_rate():
    db = FakeDB(members=[FakeMember("王五", daily_cost=None, skill_level="中级")])
    r = _skill(_config(), db).estimate(hours=8, staff_level="中级")

    assert r["success"] is False
    assert r["status"] == "needs_input"
    assert r["missing"][0]["field"] == "daily_cost"
    assert r["subjects"] == ["王五"]


def test_estimate_refuses_when_rate_has_no_source():
    """有数字没来源，报价单说不清出处，同样拦下。"""
    db = FakeDB(members=[FakeMember("王五", daily_cost=600, skill_level="中级")])
    r = _skill(_config(), db).estimate(hours=8, staff_level="中级")

    assert r["success"] is False
    assert r["missing"][0]["field"] == "cost_source"


def test_estimate_refuses_ambiguous_level_rates():
    """同档位多人不同价是歧义，不能随便挑一个。"""
    db = FakeDB(
        members=[
            FakeMember(
                "张三", daily_cost=500, skill_level="中级", cost_source="contract"
            ),
            FakeMember(
                "李四", daily_cost=900, skill_level="中级", cost_source="contract"
            ),
        ]
    )
    r = _skill(_config(), db).estimate(hours=8, staff_level="中级")

    assert r["success"] is False
    assert "费率不同" in r["missing"][0]["reason"]


def test_estimate_allows_same_level_same_rate():
    db = FakeDB(
        members=[
            FakeMember(
                "张三", daily_cost=500, skill_level="中级", cost_source="contract"
            ),
            FakeMember(
                "李四", daily_cost=500, skill_level="中级", cost_source="contract"
            ),
        ]
    )
    r = _skill(_config(), db).estimate(hours=8, staff_level="中级")

    assert r["success"] is True
    assert r["daily_cost"] == 500.0


def test_estimate_accepts_english_skill_level():
    db = FakeDB(
        members=[
            FakeMember(
                "张三", daily_cost=500, skill_level="senior", cost_source="contract"
            )
        ]
    )
    r = _skill(_config(), db).estimate(hours=8, staff_level="高级")

    assert r["success"] is True


def test_estimate_unknown_level_without_any_member_of_that_level():
    db = _member_db()
    r = _skill(_config(), db).estimate(hours=8, staff_level="学徒")

    assert r["success"] is False
    assert r["status"] == "needs_input"


def test_estimate_refuses_when_overhead_rate_missing():
    db = FakeDB(
        members=[
            FakeMember(
                "张三", daily_cost=500, skill_level="中级", cost_source="contract"
            )
        ]
    )
    r = _skill({"cost_config": {"tax_rate": 0.06}}, db).estimate(
        hours=8, staff_level="中级"
    )

    assert r["success"] is False
    assert [item["field"] for item in r["missing"]] == ["overhead_rate"]


def test_estimate_rejects_negative_hours():
    r = _skill(_config(), _member_db()).estimate(hours=-1, staff_level="中级")

    assert r["success"] is False
    assert "hours" in r["error"]


def test_estimate_rejects_non_positive_quantity():
    r = _skill(_config(), _member_db()).estimate(
        hours=8, staff_level="中级", quantity=0
    )

    assert r["success"] is False
    assert "quantity" in r["error"]


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

    budget_rendered = format_skill_result("cost_control", _skill(db=db).budget(5))
    assert "（利用率 不可用）" in budget_rendered
    assert "None" not in budget_rendered

    overrun_rendered = format_skill_result("cost_control", _skill(db=db).overrun(5))
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
    r = _skill(_config(), _member_db()).execute({"action": "estimate", "hours": 8})
    assert r["success"]
    assert not _skill(None).execute({"action": "x"})["success"]


def test_execute_estimate_without_rate_table_asks_for_input():
    r = _skill(_config(), None).execute({"action": "estimate", "hours": 8})

    assert r["success"] is False
    assert r["status"] == "needs_input"


def test_execute_forwards_member_selection():
    db = FakeDB(
        members=[
            FakeMember(
                "张三", daily_cost=500, skill_level="中级", cost_source="contract"
            ),
            FakeMember(
                "李四", daily_cost=900, skill_level="中级", cost_source="manual"
            ),
        ]
    )
    r = _skill(_config(), db).execute(
        {"action": "estimate", "hours": 8, "member": "李四"}
    )

    assert r["success"] is True
    assert r["daily_cost"] == 900.0


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
