"""费率与成本领域模型：唯一权威来源、供应商分层、资产基线、改稿与账期。

本模块只定义**结构**与**缺失行为**，不推测数值。领域数据契约 §4 / §5 / §7
的业务数值待业务方确认，因此这里全部用 `None` 表示「未标定」，并且：
任何取不到数值的路径都返回 `needs_input`，不得回退到内置常量。

对应 [`docs/product/领域数据契约.md`](../../docs/product/领域数据契约.md)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

HOURS_PER_DAY = 8

VALID_COST_SOURCES = ("contract", "quote_history", "manual", "default")

SUPPLIER_TIERS = ("internal", "studio", "freelance", "overseas")

STAFF_LEVELS = ("初级", "中级", "中高级", "高级", "资深")

# `team_members.skill_level` 里既有中文档位也有英文档位，两者必须落到同一套
# 命名上，否则「按档位筛人」会因为写法不同而漏掉人。
LEVEL_ALIASES = {
    "junior": "初级",
    "初级": "初级",
    "intermediate": "中级",
    "中级": "中级",
    "middle": "中级",
    "senior": "高级",
    "中高级": "中高级",
    "高级": "高级",
    "资深": "资深",
}

ASSET_TYPES = (
    "角色-原画",
    "角色-3D建模",
    "场景-2D",
    "场景-3D",
    "UI",
    "特效",
    "动作-绑定",
    "图标-拆分件",
)


@dataclass(frozen=True)
class MissingInput:
    """缺数据的具体说明，对应 PRD §7.3 I5 的澄清卡片。"""

    field: str
    reason: str
    subject: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"field": self.field, "reason": self.reason}
        if self.subject:
            payload["subject"] = self.subject
        return payload


@dataclass
class RateResolution:
    """一次费率解析的结果：要么拿到可追溯的费率，要么明确缺什么。"""

    daily_cost: Optional[float] = None
    cost_source: Optional[str] = None
    cost_effective_at: Optional[str] = None
    missing: List[MissingInput] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.daily_cost is not None and not self.missing

    def to_dict(self) -> Dict[str, Any]:
        return {
            "daily_cost": self.daily_cost,
            "cost_source": self.cost_source,
            "cost_effective_at": self.cost_effective_at,
            "missing": [item.to_dict() for item in self.missing],
        }


def normalize_cost_source(raw: Any) -> Optional[str]:
    """Only the four documented sources are valid; anything else is ambiguous."""
    value = str(raw or "").strip().lower()
    return value if value in VALID_COST_SOURCES else None


def normalize_staff_level(raw: Any) -> Optional[str]:
    """Map a stored level onto the five documented names, or ``None``.

    ``team_members.skill_level`` holds English levels while the business speaks
    the five Chinese names; both spellings must land on one key or filtering by
    level silently misses people.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    return LEVEL_ALIASES.get(text) or LEVEL_ALIASES.get(text.lower())


def normalize_daily_cost(raw: Any) -> Optional[float]:
    """Return a usable per-day rate, or ``None`` when it is absent or invalid.

    A day rate is money, not a ratio, so it must not be validated against a
    0-1 range — that mistake turns 500 元/人天 into "no rate at all".
    """
    return _positive_or_none(raw)


def resolve_member_rate(member: Any) -> RateResolution:
    """Read the rate from ``team_members`` and never guess when it is absent.

    ``member`` may be an ORM object or a plain mapping; both are accepted so
    callers can pass either a query result or already-serialized data.
    """
    name = _read(member, "name")
    raw_cost = _read(member, "daily_cost")
    source = normalize_cost_source(_read(member, "cost_source"))

    missing: List[MissingInput] = []
    daily_cost: Optional[float] = None

    if raw_cost is None:
        missing.append(
            MissingInput(
                field="daily_cost",
                reason="该人员未标定人天费率，无法计价",
                subject=name,
            )
        )
    else:
        try:
            parsed = float(raw_cost)
        except (TypeError, ValueError):
            missing.append(
                MissingInput(
                    field="daily_cost",
                    reason="人天费率不是有效数值",
                    subject=name,
                )
            )
        else:
            if parsed <= 0:
                missing.append(
                    MissingInput(
                        field="daily_cost",
                        reason="人天费率必须为正数",
                        subject=name,
                    )
                )
            else:
                daily_cost = parsed

    if source is None and daily_cost is not None:
        missing.append(
            MissingInput(
                field="cost_source",
                reason=("费率来源未标记，报价单无法说明该费率来自合同还是估算"),
                subject=name,
            )
        )

    return RateResolution(
        daily_cost=daily_cost,
        cost_source=source,
        cost_effective_at=_stringify(_read(member, "cost_effective_at")),
        missing=missing,
    )


def supplier_multiplier(
    tier: Any, coefficients: Optional[Mapping[str, Any]]
) -> Optional[float]:
    """Return the supplier tier coefficient, or ``None`` when uncalibrated."""
    key = str(tier or "").strip().lower()
    if key not in SUPPLIER_TIERS or not isinstance(coefficients, Mapping):
        return None
    raw = coefficients.get(key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def asset_baseline(
    asset_type: Any,
    complexity: Any,
    baselines: Optional[Mapping[str, Any]],
) -> Optional[float]:
    """Return ``asset_type × complexity`` days, or ``None`` when uncalibrated."""
    if not isinstance(baselines, Mapping):
        return None
    type_key = str(asset_type or "").strip()
    complexity_key = str(complexity or "").strip().lower()
    if not type_key or complexity_key not in ("simple", "medium", "complex"):
        return None
    row = baselines.get(type_key)
    if not isinstance(row, Mapping):
        return None
    raw = row.get(complexity_key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def revision_cost(
    revision_count: Any,
    base_days: float,
    policy: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Apply the revision policy; uncalibrated policy yields ``needs_input``.

    The formula is deliberately explicit rather than clever: included rounds
    are free, each extra round adds ``revision_cost_ratio`` of the base effort,
    and reaching ``rework_trigger_ratio`` rounds means re-quoting the whole
    asset as a new item.
    """
    if not isinstance(policy, Mapping):
        return {
            "resolved": False,
            "missing": [
                MissingInput(
                    field="revision_policy",
                    reason="改稿系数未标定，无法计算返工成本",
                ).to_dict()
            ],
        }

    included = _positive_or_none(policy.get("included_revisions"))
    ratio = _positive_or_none(policy.get("revision_cost_ratio"))
    trigger = _positive_or_none(policy.get("rework_trigger_ratio"))

    if included is None or ratio is None:
        return {
            "resolved": False,
            "missing": [
                MissingInput(
                    field="revision_policy",
                    reason="改稿系数未标定，无法计算返工成本",
                ).to_dict()
            ],
        }

    try:
        count = int(revision_count)
    except (TypeError, ValueError):
        count = 0
    count = max(count, 0)
    extra = max(count - int(included), 0)

    if trigger is not None and extra >= trigger:
        return {
            "resolved": True,
            "reworked": True,
            "extra_revisions": extra,
            "cost_days": round(base_days, 2),
            "reason": (f"超改稿 {extra} 轮，达到整件返工阈值 {trigger:g}，按新件计价"),
        }

    cost_days = base_days * (1 + extra * ratio)
    return {
        "resolved": True,
        "reworked": False,
        "extra_revisions": extra,
        "cost_days": round(cost_days, 2),
        "reason": f"超改稿 {extra} 轮，每轮 +{ratio * 100:g}% 工时",
    }


def payment_terms_cost(
    payment_terms_days: Any,
    amount: float,
    annual_rate: Any = None,
) -> Dict[str, Any]:
    """Cash-flow cost of the payment term; without a rate it stays uncalibrated."""
    rate = _positive_or_none(annual_rate)
    if rate is None:
        return {
            "resolved": False,
            "missing": [
                MissingInput(
                    field="payment_terms_days",
                    reason="账期资金成本年化利率未标定，无法计算现金流成本",
                ).to_dict()
            ],
        }
    try:
        days = int(payment_terms_days)
    except (TypeError, ValueError):
        days = 0
    days = max(days, 0)
    cost = float(amount) * rate * days / 365
    return {
        "resolved": True,
        "days": days,
        "annual_rate": rate,
        "cost": round(cost, 2),
    }


def needs_input(missing: Iterable[Any]) -> Dict[str, Any]:
    """Build the non-error clarification payload used by every caller."""
    items = [
        item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in missing
    ]
    names = [item.get("subject") for item in items if item.get("subject")]

    def _describe(item: Mapping[str, Any]) -> str:
        subject = item.get("subject")
        reason = item.get("reason", "")
        return f"{subject}：{reason}" if subject else str(reason)

    detail = "；".join(_describe(item) for item in items)
    return {
        "status": "needs_input",
        "success": False,
        "missing": items,
        "needs_input": True,
        "message": (
            (f"缺少 {len(items)} 项费率数据：" if items else "缺少费率数据：")
            + detail
            + "。补齐前不产出报价。"
        ),
        "subjects": names,
    }


def _read(source: Any, key: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _stringify(value: Any) -> Optional[str]:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return str(isoformat())
        except (TypeError, ValueError):
            return None
    text = str(value).strip()
    return text or None


def _positive_or_none(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
