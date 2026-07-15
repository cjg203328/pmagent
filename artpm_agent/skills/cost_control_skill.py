"""
Cost Control Skill - 成本管控

美术外包场景下的成本管控增强：
  1. 成本估算 (estimate) —— 接入 config.cost_config.staff_levels.daily_cost，
     做「工时 × 人天费率 +  overhead + 税」推导（替换此前未使用的配置项）。
  2. 预算跟踪 (budget) —— 项目维度：报价 vs 实际成本，给出剩余与偏差率。
  3. 超支告警 (overrun) —— 实际/预算达到阈值即告警。

配置读取内置默认值，config.cost_config 可覆盖（不强制修改配置文件）。
"""
from typing import Dict, Any, Optional

from .base_skill import BaseSkill
from .input_schemas import BUILTIN_SKILL_INPUT_SCHEMAS


# 内置默认费率（与 default_config.json 对齐，缺失时回退）
DEFAULT_STAFF_LEVELS = {
    "初级": {"daily_cost": 300},
    "中级": {"daily_cost": 500},
    "中高级": {"daily_cost": 700},
    "高级": {"daily_cost": 1000},
    "资深": {"daily_cost": 1500},
}
DEFAULT_OVERHEAD_RATE = 0.15
DEFAULT_TAX_RATE = 0.06
_HOURS_PER_DAY = 8


class CostControlSkill(BaseSkill):
    """成本管控：人天成本推导、预算跟踪、超支告警"""

    skill_name = "cost_control"
    description = "成本管控：人天成本推导、预算跟踪、超支告警"
    version = "1.0"
    requires_llm = False
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def __init__(self, context: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        cfg = (self.config or {}).get("cost_config", {}) if isinstance(self.config, dict) else {}
        levels = cfg.get("staff_levels") or DEFAULT_STAFF_LEVELS
        self.staff_levels = {k: float(v.get("daily_cost", 0)) for k, v in levels.items()}
        self.overhead_rate = float(cfg.get("overhead_rate", DEFAULT_OVERHEAD_RATE))
        self.tax_rate = float(cfg.get("tax_rate", DEFAULT_TAX_RATE))
        self.db = self.context.get("database") if self.context else None

    # ── 成本估算 ──
    def estimate(self, hours: float, staff_level: str = "中级", quantity: int = 1,
                 complexity: Optional[str] = None) -> Dict[str, Any]:
        daily = self.staff_levels.get(staff_level)
        if daily is None:
            return {"success": False, "error": f"未知人员级别: {staff_level}",
                    "available": list(self.staff_levels)}
        labor = (float(hours) / _HOURS_PER_DAY) * daily * int(quantity)
        overhead = labor * self.overhead_rate
        taxable = labor + overhead
        tax = taxable * self.tax_rate
        total = taxable + tax
        return {
            "success": True,
            "staff_level": staff_level,
            "daily_cost": daily,
            "hours": float(hours),
            "quantity": int(quantity),
            "labor_cost": round(labor, 2),
            "overhead_rate": self.overhead_rate,
            "overhead_cost": round(overhead, 2),
            "tax_rate": self.tax_rate,
            "tax_cost": round(tax, 2),
            "total_cost": round(total, 2),
            "message": (
                f"{staff_level} 级别，{hours}工时×{quantity}个："
                f"人工 {labor:.0f} + 管理 {overhead:.0f} + 税 {tax:.0f} = {total:.0f}"
            ),
        }

    # ── 预算跟踪 ──
    def budget(self, project_id: int) -> Dict[str, Any]:
        if self.db is None or not hasattr(self.db, "get_project"):
            return {"success": False, "error": "数据库不可用"}
        project = self.db.get_project(project_id)
        if project is None:
            return {"success": False, "error": f"项目不存在: {project_id}"}
        budget = float(getattr(project, "quote_amount", 0) or 0)
        spent = float(getattr(project, "cost", 0) or 0)
        if spent == 0 and hasattr(self.db, "get_project_assets"):
            try:
                assets = self.db.get_project_assets(project_id)
                spent = sum(float(getattr(a, "total_price", 0) or 0) for a in assets)
            except Exception:
                spent = 0
        remaining = budget - spent
        variance = (remaining / budget) if budget else 0.0
        return {
            "success": True,
            "project_id": project_id,
            "project_name": getattr(project, "project_name", None),
            "budget": round(budget, 2),
            "spent": round(spent, 2),
            "remaining": round(remaining, 2),
            "variance_rate": round(variance, 4),
            "utilization_rate": round((spent / budget), 4) if budget else None,
            "summary": (
                f"预算 {budget:.0f}，已用 {spent:.0f}，剩余 {remaining:.0f}"
                f"（利用率 {(spent / budget * 100):.0f}%）" if budget else "预算为 0"
            ),
        }

    # ── 超支告警 ──
    def overrun(self, project_id: int, threshold: float = 0.9) -> Dict[str, Any]:
        b = self.budget(project_id)
        if not b.get("success"):
            return b
        util = b.get("utilization_rate")
        if util is None:
            return {**b, "alert": False, "message": "预算为 0，无法判断超支"}
        alert = util >= threshold
        level = "critical" if util >= 1.0 else ("warning" if alert else "ok")
        return {
            **b,
            "threshold": threshold,
            "alert": alert,
            "level": level,
            "message": (
                f"⚠️ 成本已用 {util * 100:.0f}%（阈值 {threshold * 100:.0f}%），"
                f"{'已超支！' if util >= 1 else '接近上限，请关注。'}"
                if alert else f"成本使用 {util * 100:.0f}%，处于安全区间。"
            ),
        }

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        action = str(inputs.get("action") or "estimate").strip().lower()
        if action in ("estimate", "估算", "成本估算", "报价成本"):
            return self.estimate(
                hours=inputs.get("hours", 0),
                staff_level=inputs.get("staff_level", "中级"),
                quantity=inputs.get("quantity", 1),
                complexity=inputs.get("complexity"),
            )
        if action in ("budget", "预算", "预算跟踪"):
            return self.budget(project_id=inputs.get("project_id"))
        if action in ("overrun", "超支", "告警", "超支告警"):
            return self.overrun(
                project_id=inputs.get("project_id"),
                threshold=float(inputs.get("threshold", 0.9)),
            )
        return {"success": False, "error": f"不支持的 action: {action}",
                "hint": "支持 action=estimate / budget / overrun"}
