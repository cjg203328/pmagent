"""
Cost Control Skill - 成本管控

美术外包场景下的成本管控增强：
  1. 成本估算 (estimate) —— 人天费率**只从 `team_members.daily_cost` 读取**，
     做「人天 × 费率 + 管理费 + 税」推导。
  2. 预算跟踪 (budget) —— 项目维度：报价 vs 实际成本，给出剩余与偏差率。
  3. 超支告警 (overrun) —— 实际/预算达到阈值即告警。

费率来源与缺失行为遵循 [`docs/product/领域数据契约.md`](../../docs/product/领域数据契约.md)：
唯一权威来源是 `team_members.daily_cost`，技能层不得自行持有费率默认值；
取不到费率时返回 `needs_input` 并列出缺哪一项，**不产出报价**。
"""

from typing import Any, Dict, List, Optional

from .base_skill import BaseSkill
from .input_schemas import BUILTIN_SKILL_INPUT_SCHEMAS
from .rate_model import (
    MissingInput,
    needs_input,
    normalize_daily_cost,
    normalize_staff_level,
    resolve_member_rate,
)

_HOURS_PER_DAY = 8


def _optional_rate(value: Any) -> Optional[float]:
    """Return a usable rate, or ``None`` when it is absent or nonsensical.

    Management and tax rates are commercial parameters; inventing one here
    would silently change every quote, so absence stays absent.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not 0 <= parsed <= 1:
        return None
    return parsed


class CostControlSkill(BaseSkill):
    """成本管控：人天成本推导、预算跟踪、超支告警"""

    skill_name = "cost_control"
    description = "成本管控：人天成本推导、预算跟踪、超支告警"
    version = "1.0"
    requires_llm = False
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def __init__(self, context: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        cfg = (self.context.get("config", {}) if self.context else {}) or {}
        cost_cfg = cfg.get("cost_config", {}) if isinstance(cfg, dict) else {}
        # 管理费与税没有内置默认值：读不到就留 None，由 estimate 转为 needs_input。
        self.overhead_rate = _optional_rate(cost_cfg.get("overhead_rate"))
        self.tax_rate = _optional_rate(cost_cfg.get("tax_rate"))
        self.db = self.context.get("database") if self.context else None

    @staticmethod
    def _coerce_project_id(value: Any) -> tuple[int | None, str | None]:
        """Accept only a lossless positive integer project identifier.

        Model/tool inputs may use strings for identifiers, but silently
        truncating a float (``1.9 -> 1``) or accepting ``True`` can read the
        wrong project's budget.  Reject those values instead of coercing them.
        """
        if isinstance(value, bool):
            return None, "project_id 必须是正整数"
        if isinstance(value, int):
            return (value, None) if value >= 1 else (None, "project_id 必须是正整数")
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.isdigit():
                project_id = int(normalized)
                return (
                    (project_id, None)
                    if project_id >= 1
                    else (None, "project_id 必须是正整数")
                )
        return None, "project_id 必须是正整数"

    # ── 费率解析 ──
    def _members(self) -> List[Any]:
        """Read the rate table; an empty result means "not connected", not "free"."""
        if self.db is None:
            return []
        getter = getattr(self.db, "list_members", None)
        if not callable(getter):
            return []
        try:
            loaded = getter(is_active=None)
        except TypeError:
            try:
                loaded = getter()
            except Exception:  # noqa: BLE001 - 读取失败等同于拿不到数据
                return []
        except Exception:  # noqa: BLE001
            return []
        return list(loaded) if isinstance(loaded, (list, tuple)) else []

    def _resolve_member(
        self, member_name: Optional[str], staff_level: Optional[str]
    ) -> tuple[Optional[Any], List[MissingInput]]:
        """Pick the member whose rate applies, or explain why nobody qualifies.

        Rates live on people, not on levels, so a level is only usable when it
        maps to exactly one distinct rate. Two people at the same level with
        different rates is an ambiguity, not a coin flip.
        """
        members = self._members()
        if not members:
            return None, [
                MissingInput(
                    field="team_members",
                    reason=(
                        "读不到团队成员费率表，无法确定人天费率"
                        "（需要维护 team_members.daily_cost）"
                    ),
                )
            ]

        if member_name:
            wanted = str(member_name).strip()
            matched = [
                member
                for member in members
                if str(getattr(member, "name", "") or "").strip() == wanted
            ]
            if not matched:
                return None, [
                    MissingInput(
                        field="member",
                        reason=f"团队成员中找不到 {wanted}",
                        subject=wanted,
                    )
                ]
            if len(matched) > 1:
                return None, [
                    MissingInput(
                        field="member",
                        reason=f"存在 {len(matched)} 名同名成员 {wanted}，需指定具体人员",
                        subject=wanted,
                    )
                ]
            return matched[0], []

        level = normalize_staff_level(staff_level)
        if level is None:
            return None, [
                MissingInput(
                    field="staff_level",
                    reason=f"无法识别的技能档位 {staff_level!r}",
                )
            ]

        candidates = [
            member
            for member in members
            if normalize_staff_level(getattr(member, "skill_level", None)) == level
        ]
        if not candidates:
            return None, [
                MissingInput(
                    field="member",
                    reason=f"档位 {level} 下没有可用成员，请指定具体人员",
                )
            ]

        # 人天费率是金额，必须用金额校验器；用 0-1 比例校验会把 500 当成无效值，
        # 从而把「同档不同价」的歧义静默放过。
        distinct = {
            normalize_daily_cost(getattr(member, "daily_cost", None))
            for member in candidates
        }
        distinct.discard(None)
        if len(distinct) > 1:
            return None, [
                MissingInput(
                    field="member",
                    reason=(
                        f"档位 {level} 下有 {len(candidates)} 名成员且费率不同，"
                        "需指定具体人员"
                    ),
                )
            ]

        # 全部同价（或全部未标定）时取任一人即可；未标定由 resolve_member_rate 报出。
        return candidates[0], []

    # ── 成本估算 ──
    def estimate(
        self,
        hours: float,
        staff_level: str = "中级",
        quantity: int = 1,
        complexity: Optional[str] = None,
        member: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cost from the person's own rate; never from a built-in level table."""
        missing: List[MissingInput] = []

        if member is None:
            try:
                quantity = int(quantity)
            except (TypeError, ValueError):
                return {"success": False, "error": "quantity 必须是正整数"}
            if quantity < 1:
                return {"success": False, "error": "quantity 必须是正整数"}

        resolved_member, pick_missing = self._resolve_member(member, staff_level)
        missing.extend(pick_missing)

        rate = None
        if resolved_member is not None:
            rate = resolve_member_rate(resolved_member)
            missing.extend(rate.missing)

        if self.overhead_rate is None:
            missing.append(
                MissingInput(
                    field="overhead_rate",
                    reason="管理费率未配置（cost_config.overhead_rate）",
                )
            )
        if self.tax_rate is None:
            missing.append(
                MissingInput(
                    field="tax_rate",
                    reason="税率未配置（cost_config.tax_rate）",
                )
            )

        try:
            hours_value = float(hours)
        except (TypeError, ValueError):
            return {"success": False, "error": "hours 必须是数字"}
        if hours_value < 0:
            return {"success": False, "error": "hours 不能为负数"}

        if missing:
            payload = needs_input(missing)
            payload["hours"] = hours_value
            payload["quantity"] = int(quantity)
            return payload

        if rate is None or rate.daily_cost is None:
            # missing 为空却仍拿不到费率，说明解析器与校验逻辑不一致；宁可报错也不猜。
            return {
                "success": False,
                "error": "费率解析异常：未收集到缺失项但费率为空",
            }

        # 上面已确认 missing 为空，即两个费率都已配置；绑定为局部变量以固定类型。
        overhead_rate = self.overhead_rate
        tax_rate = self.tax_rate
        if overhead_rate is None or tax_rate is None:
            return {
                "success": False,
                "error": "费率解析异常：管理费率或税率为空",
            }

        daily = rate.daily_cost
        labor = (hours_value / _HOURS_PER_DAY) * daily * int(quantity)
        overhead = labor * overhead_rate
        taxable = labor + overhead
        tax = taxable * tax_rate
        total = taxable + tax
        member_name = str(getattr(resolved_member, "name", "") or "").strip()

        return {
            "success": True,
            "staff_level": normalize_staff_level(
                getattr(resolved_member, "skill_level", None)
            )
            or staff_level,
            "member": member_name,
            "daily_cost": daily,
            "cost_source": rate.cost_source,
            "cost_effective_at": rate.cost_effective_at,
            "hours": hours_value,
            "quantity": int(quantity),
            "labor_cost": round(labor, 2),
            "overhead_rate": self.overhead_rate,
            "overhead_cost": round(overhead, 2),
            "tax_rate": self.tax_rate,
            "tax_cost": round(tax, 2),
            "total_cost": round(total, 2),
            "message": (
                f"{member_name or staff_level}，{hours:g}工时×{int(quantity)}个："
                f"人工 {labor:.0f} + 管理 {overhead:.0f} + 税 {tax:.0f} = {total:.0f}"
                f"（费率来源 {rate.cost_source}）"
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
                f"（利用率 {(spent / budget * 100):.0f}%）"
                if budget
                else "预算为 0"
            ),
        }

    # ── 超支告警 ──
    def overrun(self, project_id: int, threshold: float = 0.9) -> Dict[str, Any]:
        b = self.budget(project_id)
        if not b.get("success"):
            return b
        util = b.get("utilization_rate")
        if util is None:
            return {
                **b,
                "threshold": threshold,
                "alert": False,
                "level": "ok",
                "message": "预算为 0，无法判断超支",
            }
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
                if alert
                else f"成本使用 {util * 100:.0f}%，处于安全区间。"
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
                member=inputs.get("member"),
            )
        if action in ("budget", "预算", "预算跟踪"):
            project_id, error = self._coerce_project_id(inputs.get("project_id"))
            if error or project_id is None:
                return {"success": False, "error": error or "project_id 必须是正整数"}
            return self.budget(project_id=project_id)
        if action in ("overrun", "超支", "告警", "超支告警"):
            project_id, error = self._coerce_project_id(inputs.get("project_id"))
            if error or project_id is None:
                return {"success": False, "error": error or "project_id 必须是正整数"}
            return self.overrun(
                project_id=project_id,
                threshold=float(inputs.get("threshold", 0.9)),
            )
        return {
            "success": False,
            "error": f"不支持的 action: {action}",
            "hint": "支持 action=estimate / budget / overrun",
        }
