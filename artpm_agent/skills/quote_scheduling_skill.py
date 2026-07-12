"""
Quote & Scheduling Skill - 报价排期

美术外包场景下的报价排期增强：
  1. 人天估算 (estimate_man_days) —— 复杂度→基准工时（替换此前写死的 8h），
     再乘数量与历史系数，输出人天。
  2. 排期时间线 (build_schedule) —— 依据各资产人天与起始日期，按工作日排出
     每个资产的起止日期（支持团队并行度）。
  3. 里程碑计划 (milestone_plan) —— 把时间线聚合成可读里程碑。

日期统一用 ISO 字符串输入输出，便于测试与展示。
"""
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from .base_skill import BaseSkill


# 复杂度 → 基准工时（小时）。替换此前写死的 8h 假设。
BASE_HOURS = {"simple": 8, "medium": 24, "complex": 60}
_HOURS_PER_DAY = 8


def _to_date(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _add_workdays(start: datetime, workdays: float) -> datetime:
    """从 start 向后推进 workdays 个工作日（跳过周末）。"""
    if workdays <= 0:
        return start
    full = int(workdays)
    rem = workdays - full
    cur = start
    added = 0
    while added < full:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    if rem > 0:
        # 不足一天按当天内完成，不跨日
        pass
    return cur


class QuoteSchedulingSkill(BaseSkill):
    """报价排期：人天估算引擎 + 排期时间线 + 里程碑计划"""

    skill_name = "quote_scheduling"
    description = "报价排期：人天估算引擎、排期时间线、里程碑计划"
    version = "1.0"
    requires_llm = False

    # ── 人天估算 ──
    def estimate_man_days(self, complexity: str, asset_type: Optional[str] = None,
                          quantity: int = 1, history_factor: float = 1.0) -> Dict[str, Any]:
        base = BASE_HOURS.get((complexity or "").lower())
        if base is None:
            return {"success": False, "error": f"未知复杂度: {complexity}",
                    "available": list(BASE_HOURS)}
        hours = base * int(quantity) * float(history_factor)
        man_days = hours / _HOURS_PER_DAY
        return {
            "success": True,
            "complexity": complexity,
            "asset_type": asset_type,
            "quantity": int(quantity),
            "history_factor": float(history_factor),
            "base_hours": base,
            "total_hours": hours,
            "man_days": round(man_days, 2),
            "message": (
                f"{complexity} 资产，{quantity}个，历史系数 {history_factor}："
                f"约 {hours:.0f} 工时 ≈ {man_days:.1f} 人天"
            ),
        }

    # ── 排期时间线 ──
    def build_schedule(self, assets: List[Dict[str, Any]], start_date: str,
                       team_size: int = 1, parallel: int = 1) -> Dict[str, Any]:
        start = _to_date(start_date)
        if start is None:
            return {"success": False, "error": "start_date 必须是 ISO 日期"}
        if not assets:
            return {"success": False, "error": "assets 不能为空"}

        timeline = []
        # 按资产顺序累计；parallel 控制同时进行的资产数（简化：顺序累加人天）
        cursor = start
        total_md = 0.0
        for a in assets:
            est = self.estimate_man_days(
                complexity=a.get("complexity", "medium"),
                asset_type=a.get("asset_type"),
                quantity=a.get("quantity", 1),
                history_factor=a.get("history_factor", 1.0),
            )
            if not est["success"]:
                return est
            md = est["man_days"] / max(int(parallel), 1)
            begin = cursor
            end = _add_workdays(begin, md)
            timeline.append({
                "asset_name": a.get("asset_name", "未命名"),
                "asset_type": a.get("asset_type"),
                "complexity": a.get("complexity", "medium"),
                "man_days": est["man_days"],
                "start": begin.date().isoformat(),
                "end": end.date().isoformat(),
            })
            cursor = end
            total_md += est["man_days"]

        return {
            "success": True,
            "start_date": start.date().isoformat(),
            "team_size": team_size,
            "parallel": parallel,
            "total_man_days": round(total_md, 2),
            "timeline": timeline,
            "finish_date": cursor.date().isoformat(),
            "summary": (
                f"共 {len(timeline)} 个资产，约 {total_md:.1f} 人天"
                f"，预计 {cursor.date().isoformat()} 完成。"
            ),
        }

    # ── 里程碑计划 ──
    def milestone_plan(self, assets: List[Dict[str, Any]], start_date: str,
                       team_size: int = 1) -> Dict[str, Any]:
        sched = self.build_schedule(assets, start_date, team_size=team_size)
        if not sched["success"]:
            return sched
        timeline = sched["timeline"]
        milestones = [
            {"phase": "启动", "date": sched["start_date"], "note": "需求确认与立项"},
        ]
        # 每完成 1/3、2/3 设检查点
        n = len(timeline)
        for idx, item in enumerate(timeline):
            label = "资产交付" if idx == n - 1 else f"阶段{idx + 1}交付"
            milestones.append({
                "phase": label,
                "date": item["end"],
                "note": f"{item['asset_name']}（{item['complexity']}）",
            })
        milestones.append({
            "phase": "验收",
            "date": sched["finish_date"],
            "note": "整体交付与验收",
        })
        return {
            "success": True,
            "milestones": milestones,
            "finish_date": sched["finish_date"],
            "summary": f"共 {len(milestones)} 个里程碑，预计 {sched['finish_date']} 验收。",
        }

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        action = str(inputs.get("action") or "estimate").strip().lower()
        if action in ("estimate", "人天", "估算", "人天估算"):
            return self.estimate_man_days(
                complexity=inputs.get("complexity", "medium"),
                asset_type=inputs.get("asset_type"),
                quantity=inputs.get("quantity", 1),
                history_factor=inputs.get("history_factor", 1.0),
            )
        if action in ("schedule", "排期", "时间线", "build"):
            return self.build_schedule(
                assets=inputs.get("assets", []),
                start_date=inputs.get("start_date"),
                team_size=inputs.get("team_size", 1),
                parallel=inputs.get("parallel", 1),
            )
        if action in ("milestone", "里程碑", "milestone_plan"):
            return self.milestone_plan(
                assets=inputs.get("assets", []),
                start_date=inputs.get("start_date"),
                team_size=inputs.get("team_size", 1),
            )
        return {"success": False, "error": f"不支持的 action: {action}",
                "hint": "支持 action=estimate / schedule / milestone"}
