"""费率领域模型测试：唯一来源、分层系数、资产基线、改稿与账期的缺失行为。

核心断言只有一条：**取不到数值时必须说缺什么，不得回退到内置常量。**
"""

from __future__ import annotations

import pytest

from artpm_agent.skills.rate_model import (
    ASSET_TYPES,
    STAFF_LEVELS,
    SUPPLIER_TIERS,
    VALID_COST_SOURCES,
    asset_baseline,
    needs_input,
    normalize_cost_source,
    payment_terms_cost,
    resolve_member_rate,
    revision_cost,
    supplier_multiplier,
)


class FakeMember:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


# ── 费率解析 ──────────────────────────────────────────────────


def test_member_with_calibrated_rate_resolves():
    member = FakeMember(
        name="张三",
        daily_cost=800,
        cost_source="contract",
        cost_effective_at="2026-03-01",
    )
    result = resolve_member_rate(member)

    assert result.resolved is True
    assert result.daily_cost == 800.0
    assert result.cost_source == "contract"
    assert result.cost_effective_at == "2026-03-01"
    assert result.missing == []


def test_member_without_daily_cost_is_missing_not_guessed():
    result = resolve_member_rate(FakeMember(name="李四", daily_cost=None))

    assert result.resolved is False
    assert result.daily_cost is None
    assert [item.field for item in result.missing] == ["daily_cost"]
    assert result.missing[0].subject == "李四"


def test_member_without_cost_source_is_flagged_not_silently_accepted():
    """有费率但无来源，报价单无法说明来源，必须标记。"""
    result = resolve_member_rate(FakeMember(name="王五", daily_cost=600))

    assert result.daily_cost == 600.0
    assert result.resolved is False
    assert [item.field for item in result.missing] == ["cost_source"]


def test_member_accepts_plain_mapping():
    result = resolve_member_rate(
        {"name": "赵六", "daily_cost": 700, "cost_source": "manual"}
    )
    assert result.resolved is True
    assert result.daily_cost == 700.0


@pytest.mark.parametrize("bad", [0, -100, "abc"])
def test_invalid_daily_cost_is_rejected(bad):
    result = resolve_member_rate(
        FakeMember(name="钱七", daily_cost=bad, cost_source="manual")
    )

    assert result.resolved is False
    assert result.daily_cost is None
    assert result.missing[0].field == "daily_cost"


def test_normalize_cost_source_only_accepts_documented_values():
    assert normalize_cost_source("contract") == "contract"
    assert normalize_cost_source(" DEFAULT ") == "default"
    assert normalize_cost_source("guess") is None
    assert normalize_cost_source(None) is None
    assert set(VALID_COST_SOURCES) == {
        "contract",
        "quote_history",
        "manual",
        "default",
    }


# ── 供应商分层 ────────────────────────────────────────────────


def test_supplier_multiplier_requires_calibration():
    assert supplier_multiplier("studio", None) is None
    assert supplier_multiplier("studio", {}) is None
    assert supplier_multiplier("studio", {"studio": None}) is None
    assert supplier_multiplier("unknown-tier", {"unknown-tier": 2.0}) is None
    assert supplier_multiplier("studio", {"studio": 1.8}) == 1.8


def test_supplier_multiplier_rejects_non_positive():
    assert supplier_multiplier("freelance", {"freelance": 0}) is None
    assert supplier_multiplier("freelance", {"freelance": -1}) is None


def test_supplier_tiers_are_the_four_documented_layers():
    assert set(SUPPLIER_TIERS) == {"internal", "studio", "freelance", "overseas"}


# ── 资产基线 ──────────────────────────────────────────────────


def test_asset_baseline_requires_type_and_complexity():
    baselines = {"角色-3D建模": {"simple": 3, "medium": 6, "complex": 12}}

    assert asset_baseline("角色-3D建模", "complex", baselines) == 12
    assert asset_baseline("角色-3D建模", "COMPLEX", baselines) == 12
    assert asset_baseline("场景-3D", "complex", baselines) is None
    assert asset_baseline("角色-3D建模", "huge", baselines) is None
    assert asset_baseline("角色-3D建模", "complex", None) is None
    assert asset_baseline("", "complex", baselines) is None


def test_asset_types_cover_the_documented_categories():
    assert "UI" in ASSET_TYPES
    assert "特效" in ASSET_TYPES
    assert "动作-绑定" in ASSET_TYPES
    assert len(ASSET_TYPES) == 8


def test_staff_levels_keep_the_five_existing_names():
    assert set(STAFF_LEVELS) == {"初级", "中级", "中高级", "高级", "资深"}


# ── 改稿系数 ──────────────────────────────────────────────────


def test_revision_without_policy_is_missing():
    result = revision_cost(4, base_days=10, policy=None)

    assert result["resolved"] is False
    assert result["missing"][0]["field"] == "revision_policy"


def test_revision_inside_included_rounds_costs_nothing_extra():
    result = revision_cost(
        2,
        base_days=10,
        policy={"included_revisions": 3, "revision_cost_ratio": 0.15},
    )

    assert result["resolved"] is True
    assert result["extra_revisions"] == 0
    assert result["cost_days"] == 10


def test_revision_beyond_included_adds_ratio_per_round():
    result = revision_cost(
        5,
        base_days=10,
        policy={"included_revisions": 3, "revision_cost_ratio": 0.15},
    )

    assert result["extra_revisions"] == 2
    assert result["cost_days"] == pytest.approx(13.0)
    assert result["reworked"] is False


def test_revision_reaching_trigger_requotes_as_new_item():
    result = revision_cost(
        9,
        base_days=10,
        policy={
            "included_revisions": 3,
            "revision_cost_ratio": 0.15,
            "rework_trigger_ratio": 5,
        },
    )

    assert result["reworked"] is True
    assert result["cost_days"] == pytest.approx(10.0)
    assert "返工" in result["reason"]


# ── 账期 ──────────────────────────────────────────────────────


def test_payment_terms_without_rate_is_missing():
    result = payment_terms_cost(90, amount=100000)

    assert result["resolved"] is False
    assert result["missing"][0]["field"] == "payment_terms_days"


def test_payment_terms_cost_is_prorated_by_days():
    result = payment_terms_cost(365, amount=100000, annual_rate=0.06)

    assert result["resolved"] is True
    assert result["days"] == 365
    assert result["cost"] == pytest.approx(6000.0)


# ── needs_input 载荷 ──────────────────────────────────────────


def test_needs_input_lists_subjects_and_refuses_to_quote():
    payload = needs_input(resolve_member_rate(FakeMember(name="张三")).missing)

    assert payload["status"] == "needs_input"
    assert payload["success"] is False
    assert payload["needs_input"] is True
    assert payload["subjects"] == ["张三"]
    assert "不产出报价" in payload["message"]
    assert payload["missing"][0]["field"] == "daily_cost"
