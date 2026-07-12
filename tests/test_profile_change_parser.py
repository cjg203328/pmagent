from __future__ import annotations

from pathlib import Path
import sys

import pytest


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"
sys.path.insert(0, str(APP_ROOT))

from profiles import (
    AgentProfile,
    ProfileChangeParseError,
    format_profile_changes,
    parse_profile_change,
)


@pytest.fixture
def profile() -> AgentProfile:
    return AgentProfile(updated_at="2026-07-12T00:00:00+00:00")


@pytest.mark.parametrize(
    "message",
    [
        "管理费率是多少？",
        "税率为什么是 6%？",
        "高风险阈值怎么计算？",
        "你的名字是什么？",
        "回答风格是什么？",
        "如果税率改成 5%，利润会是多少？",
        "“把税率改成 5%”是什么意思？",
        "如何修改管理费率？",
        "为什么把税率改成 5%？",
        "税率改成 5% 会怎样？",
        "把管理费率调到 20% 有什么影响？",
        "是否要把税率改成 5%？",
    ],
)
def test_ordinary_questions_do_not_create_profile_patch(
    profile: AgentProfile,
    message: str,
):
    assert parse_profile_change(message, profile) is None


def test_parses_multiple_quote_rates_and_percent_notation(profile: AgentProfile):
    patch = parse_profile_change(
        "请把管理费率调整为 20%，税率设置为百分之五",
        profile,
    )

    assert patch is not None
    assert patch.quote_policy is not None
    assert patch.quote_policy.overhead_rate == pytest.approx(0.2)
    assert patch.quote_policy.tax_rate == pytest.approx(0.05)


def test_validates_final_risk_threshold_order(profile: AgentProfile):
    patch = parse_profile_change(
        "高风险阈值改为 8%，中风险阈值改为 18%",
        profile,
    )

    assert patch is not None
    assert patch.quote_policy is not None
    assert patch.quote_policy.high_risk_below == pytest.approx(0.08)
    assert patch.quote_policy.medium_risk_below == pytest.approx(0.18)

    with pytest.raises(ProfileChangeParseError, match="配置变更无效"):
        parse_profile_change("把高风险阈值调到 20%", profile)

    combined = parse_profile_change(
        "把高风险阈值调到 20%，同时把中风险阈值调到 30%",
        profile,
    )
    assert combined is not None
    assert combined.quote_policy is not None
    assert combined.quote_policy.high_risk_below == pytest.approx(0.2)
    assert combined.quote_policy.medium_risk_below == pytest.approx(0.3)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("把报价币种切换为美元", "USD"),
        ("默认币种改为 EUR", "EUR"),
        ("将结算币种设为港币", "HKD"),
    ],
)
def test_parses_supported_currency_aliases(
    profile: AgentProfile,
    message: str,
    expected: str,
):
    patch = parse_profile_change(message, profile)

    assert patch is not None
    assert patch.quote_policy is not None
    assert patch.quote_policy.currency == expected


def test_parses_agent_identity_fields(profile: AgentProfile):
    patch = parse_profile_change(
        "把 Agent 名称改为小艺，Agent 角色改为美术制片助手，专业领域改为游戏原画外包",
        profile,
    )

    assert patch is not None
    assert patch.identity is not None
    assert patch.identity.display_name == "小艺"
    assert patch.identity.role == "美术制片助手"
    assert patch.identity.domain == "游戏原画外包"


def test_parses_conversational_name_and_response_style(profile: AgentProfile):
    name_patch = parse_profile_change("以后叫小艺", profile)
    style_patch = parse_profile_change("今后回答详细一点", profile)

    assert name_patch is not None
    assert name_patch.identity is not None
    assert name_patch.identity.display_name == "小艺"
    assert style_patch is not None
    assert style_patch.identity is not None
    assert style_patch.identity.response_style == "detailed"


def test_explicit_question_request_is_still_a_change(profile: AgentProfile):
    patch = parse_profile_change("可以把税率改为 5% 吗？", profile)

    assert patch is not None
    assert patch.quote_policy is not None
    assert patch.quote_policy.tax_rate == pytest.approx(0.05)


def test_no_op_returns_none(profile: AgentProfile):
    assert parse_profile_change("把税率改为 6%", profile) is None
    assert parse_profile_change("回答风格设为平衡", profile) is None


def test_invalid_explicit_values_raise_parse_error(profile: AgentProfile):
    with pytest.raises(ProfileChangeParseError, match="不支持的币种"):
        parse_profile_change("把币种改为比特币", profile)

    with pytest.raises(ProfileChangeParseError, match="配置变更无效"):
        parse_profile_change("税率改为 120%", profile)

    with pytest.raises(ProfileChangeParseError, match="不支持的回答风格"):
        parse_profile_change("回答风格改为随意", profile)


def test_format_profile_changes_reports_exact_preview(profile: AgentProfile):
    patch = parse_profile_change(
        "Agent 名称改为小艺，回答风格改为简洁，管理费率改为 20%，币种改为美元",
        profile,
    )
    assert patch is not None

    preview = format_profile_changes(profile, patch)

    assert "Agent 名称：ArtPM Agent -> 小艺" in preview
    assert "回答风格：平衡 -> 简洁" in preview
    assert "管理费率：15.00% -> 20.00%" in preview
    assert "币种：CNY -> USD" in preview
    assert "revision 1" in preview
    assert "revision 2" in preview
    assert "当前尚未生效" in preview
