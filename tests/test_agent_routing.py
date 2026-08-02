"""
测试 agent.py 对 7 大工作流技能的意图路由接线（不依赖完整 Agent 初始化）。

覆盖：
  1. INTENT_KEYWORDS / SKILL_ROUTE_SIGNALS / INTENT_EXAMPLES 均含 6 个新技能名。
  2. _detect_intent 关键词+高置信门禁路径能把示例 PM 短语路由到正确技能
     （embedding 层用空字典 stub，避免依赖 memory/LLM）。
  3. _extract_inputs 为 6 个技能产出正确输入字段。
  4. _format_skill_result 为 6 个技能产出可读 markdown（按结果 key 判别动作）。
"""
from types import SimpleNamespace

from artpm_agent.agent import ArtPMAgent

NEW_SKILLS = [
    "requirements_assessment",
    "cost_control",
    "quote_scheduling",
    "progress_management",
    "delivery",
    "retrospective",
]


def _make_agent():
    """绕过 __init__，只设置路由所需的最小属性。"""
    agent = object.__new__(ArtPMAgent)
    agent._intent_embeddings = {}  # 空 -> embedding 层直接返回 None
    agent.memory = SimpleNamespace(_get_embedding=lambda x: [0.0] * 256)
    agent.llm_client = None
    agent.config = {}
    return agent


def test_routing_tables_contain_new_skills():
    for name in NEW_SKILLS:
        assert name in ArtPMAgent.INTENT_KEYWORDS, f"{name} 缺失于 INTENT_KEYWORDS"
        assert name in ArtPMAgent.SKILL_ROUTE_SIGNALS, f"{name} 缺失于 SKILL_ROUTE_SIGNALS"
        assert name in ArtPMAgent.INTENT_EXAMPLES, f"{name} 缺失于 INTENT_EXAMPLES"


def test_detect_intent_routes_each_skill():
    agent = _make_agent()
    cases = {
        "requirements_assessment": "生成需求范围确认清单",
        "cost_control": "帮我做成本估算",
        "quote_scheduling": "排一下这个项目的时间线",
        "progress_management": "查看里程碑进度",
        "delivery": "生成产品交付清单",
        "retrospective": "做项目复盘总结",
    }
    for expected, text in cases.items():
        got = agent._detect_intent(text)
        assert got == expected, f"「{text}」应路由到 {expected}，实际 {got}"


def test_extract_inputs_requirements_assessment():
    agent = _make_agent()
    ctx = {"asset_name": "角色A", "project_name": "P1", "client": "C1"}
    inp = agent._extract_inputs("评估一下角色模型的复杂度", "requirements_assessment", ctx)
    assert inp["action"] == "assess"
    assert inp["asset_type"] == "角色"
    assert inp["asset_name"] == "角色A"


def test_extract_inputs_cost_control_estimate():
    agent = _make_agent()
    inp = agent._extract_inputs("估算中级原画8小时成本", "cost_control", {})
    assert inp["action"] == "estimate"
    assert inp["staff_level"] == "中级"
    assert inp["hours"] == 8


def test_extract_inputs_quote_scheduling_milestone():
    agent = _make_agent()
    inp = agent._extract_inputs("生成里程碑计划", "quote_scheduling", {})
    assert inp["action"] == "milestone"
    assert inp["complexity"] == "medium"


def test_extract_inputs_progress_management_blockers():
    agent = _make_agent()
    inp = agent._extract_inputs("排查阻塞卡点", "progress_management", {"project_id": 7})
    assert inp["action"] == "blockers"
    assert inp["project_id"] == 7


def test_extract_inputs_delivery_acceptance():
    agent = _make_agent()
    inp = agent._extract_inputs("出一张验收单", "delivery", {"project_id": 3})
    assert inp["action"] == "acceptance"
    assert inp["project_id"] == 3


def test_delivery_record_phrase_routes_and_extracts_identifiers():
    agent = _make_agent()
    prompt = "记录资产交付，项目7，交付单号D-7"

    assert agent._detect_intent(prompt) == "delivery"
    inputs = agent._extract_inputs(prompt, "delivery", {})

    assert inputs["action"] == "record"
    assert inputs["project_id"] == 7
    assert inputs["delivery_no"] == "D-7"


def test_extract_inputs_retrospective_lessons():
    agent = _make_agent()
    inp = agent._extract_inputs("把经验教训沉淀到知识库", "retrospective", {"project_id": 5})
    assert inp["action"] == "lessons"
    assert inp["project_id"] == 5


def test_format_requirements_assessment_assess():
    agent = _make_agent()
    out = agent._format_skill_result("requirements_assessment", {
        "success": True, "asset_type": "角色", "complexity": "complex",
        "score": 8, "message": "命中影视级",
    })
    assert "复杂度评估" in out and "complex" in out


def test_format_cost_control_estimate():
    agent = _make_agent()
    out = agent._format_skill_result("cost_control", {
        "success": True, "staff_level": "中级", "daily_cost": 500, "hours": 8,
        "quantity": 1, "labor_cost": 500, "overhead_rate": 0.15,
        "overhead_cost": 75, "tax_rate": 0.06, "tax_cost": 34.5, "total_cost": 609.5,
    })
    assert "成本估算" in out and "610" in out


def test_format_quote_scheduling_timeline():
    agent = _make_agent()
    out = agent._format_skill_result("quote_scheduling", {
        "success": True, "finish_date": "2026-08-01", "total_man_days": 5,
        "timeline": [{"asset_name": "角色A", "complexity": "medium",
                      "man_days": 3, "start": "2026-07-20", "end": "2026-07-23"}],
    })
    assert "排期时间线" in out and "角色A" in out


def test_format_progress_management_standup():
    agent = _make_agent()
    out = agent._format_skill_result("progress_management", {
        "success": True, "overall_progress": 0.5, "in_progress_count": 2,
        "blocker_count": 1, "check_interval_hours": 24,
        "blockers": [{"task_name": "任务1", "reasons": ["已逾期"]}],
    })
    assert "每日站会摘要" in out and "50%" in out


def test_format_delivery_manifest():
    agent = _make_agent()
    out = agent._format_skill_result("delivery", {
        "success": True, "summary": "项目共 2 个资产，1 个已就绪交付。",
        "items": [{"asset_name": "角色A", "asset_type": "角色", "status": "已完成",
                   "progress": 100, "latest_version": "v2"}],
    })
    assert "交付清单" in out and "角色A" in out


def test_format_retrospective_report():
    agent = _make_agent()
    out = agent._format_skill_result("retrospective", {
        "success": True, "project_name": "项目X", "client": "客户Y",
        "completed_tasks": 8, "total_tasks": 10, "on_time_rate": 0.8,
        "avg_quality_score": 4.2, "total_revisions": 3, "max_revisions": 2,
        "budget": 100000, "actual_cost": 95000, "cost_variance": 5000,
        "duration_days": 30,
    })
    assert "结项复盘报告" in out and "项目X" in out and "5000" in out
