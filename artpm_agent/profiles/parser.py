"""Deterministic Chinese parsing and preview for Agent Profile changes."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from .models import (
    AgentIdentityPatch,
    AgentProfile,
    AgentProfilePatch,
    QuotePolicyPatch,
    apply_profile_patch,
)


class ProfileChangeParseError(ValueError):
    """Raised when an explicit change request would create an invalid profile."""


_CHANGE_VERBS = (
    "调整",
    "修改",
    "设置",
    "设定",
    "改成",
    "改为",
    "设为",
    "更新",
    "变更",
    "调到",
    "改到",
    "定为",
    "切换",
    "更名",
    "以后叫",
    "今后叫",
)
_FUTURE_ACTION_MARKERS = ("以后回答", "以后回复", "今后回答", "今后回复")
_CHANGE_VERB_PATTERN = "|".join(map(re.escape, _CHANGE_VERBS))
_HYPOTHETICAL_PREFIXES = ("如果", "假如", "假设", "要是", "倘若")
_REQUEST_MARKERS = ("请", "帮我", "我要", "我想", "需要", "现在", "直接")
_DISCUSSION_MARKERS = (
    "什么意思",
    "为什么",
    "会怎样",
    "会如何",
    "有什么影响",
    "如何修改",
    "怎么修改",
    "如何设置",
    "怎么设置",
    "是否要",
    "要不要",
)
_VALUE_END = r"(?=$|[，,。；;！？!?\n]|\s+(?:并且|同时|以及|然后))"


def _has_explicit_change_action(text: str) -> bool:
    if not any(verb in text for verb in (*_CHANGE_VERBS, *_FUTURE_ACTION_MARKERS)):
        return False
    stripped = text.strip()
    if stripped.startswith(_HYPOTHETICAL_PREFIXES) and not any(
        marker in stripped for marker in _REQUEST_MARKERS
    ):
        return False
    if any(marker in stripped for marker in _DISCUSSION_MARKERS):
        return False
    return True


def _field_assignment_pattern(
    label_pattern: str, value_pattern: str
) -> re.Pattern[str]:
    return re.compile(
        rf"(?:"
        rf"(?:把|将)?\s*(?:{label_pattern})\s*(?:{_CHANGE_VERB_PATTERN})\s*"
        rf"(?:为|到|成)?\s*"
        rf"|(?:{_CHANGE_VERB_PATTERN})\s*(?:默认)?\s*(?:{label_pattern})\s*(?:为|到|成)?\s*"
        rf")({value_pattern})",
        re.IGNORECASE,
    )


_CHINESE_NUMBER = r"[零〇一二两三四五六七八九十百点]+"
_NUMBER = rf"(?:百分之\s*)?(?:-?\d+(?:\.\d+)?|{_CHINESE_NUMBER})\s*%?"
_RATE_PATTERNS = {
    "overhead_rate": _field_assignment_pattern(
        r"管理费率|管理费比例|管理费",
        _NUMBER,
    ),
    "tax_rate": _field_assignment_pattern(r"税率|税费率|税费比例", _NUMBER),
    "high_risk_below": _field_assignment_pattern(
        r"高风险阈值|高风险线|高风险利润率",
        _NUMBER,
    ),
    "medium_risk_below": _field_assignment_pattern(
        r"中风险阈值|中风险线|中风险利润率",
        _NUMBER,
    ),
}

_TEXT_VALUE = rf"[^，,。；;！？!?\n]{{1,200}}?{_VALUE_END}"
_TEXT_PATTERNS = {
    "display_name": _field_assignment_pattern(
        r"Agent\s*名称|agent\s*名称|智能体名称|助手名称|你的名字|名字",
        _TEXT_VALUE,
    ),
    "role": _field_assignment_pattern(
        r"Agent\s*角色|agent\s*角色|智能体角色|助手角色|角色",
        _TEXT_VALUE,
    ),
    "domain": _field_assignment_pattern(
        r"Agent\s*领域|agent\s*领域|专业领域|业务领域|服务领域|领域",
        _TEXT_VALUE,
    ),
}

_CURRENCY_PATTERN = _field_assignment_pattern(
    r"默认币种|结算币种|报价币种|币种",
    rf"[^，,。；;！？!?\n]{{1,20}}?{_VALUE_END}",
)
_STYLE_PATTERN = _field_assignment_pattern(
    r"回答风格|回复风格|响应风格|回答方式",
    rf"[^，,。；;！？!?\n]{{1,20}}?{_VALUE_END}",
)
_FUTURE_NAME_PATTERN = re.compile(
    rf"(?:以后叫|今后叫)\s*([^，,。；;！？!?\n]{{1,200}}?){_VALUE_END}"
)
_FUTURE_STYLE_PATTERN = re.compile(
    r"(?:以后|今后)\s*(?:回答|回复)\s*(?:要|尽量|更)?\s*"
    rf"([^，,。；;！？!?\n]{{1,20}}?){_VALUE_END}"
)

_CURRENCY_ALIASES = {
    "人民币": "CNY",
    "cny": "CNY",
    "美元": "USD",
    "美金": "USD",
    "usd": "USD",
    "欧元": "EUR",
    "eur": "EUR",
    "日元": "JPY",
    "日币": "JPY",
    "jpy": "JPY",
    "港币": "HKD",
    "hkd": "HKD",
}
_STYLE_ALIASES = {
    "简洁": "concise",
    "精简": "concise",
    "简短": "concise",
    "短一点": "concise",
    "平衡": "balanced",
    "适中": "balanced",
    "默认": "balanced",
    "正常": "balanced",
    "详细": "detailed",
    "详尽": "detailed",
    "展开": "detailed",
    "详细一点": "detailed",
}


def _clean_text_value(value: str) -> str:
    value = value.strip().strip("\"'“”‘’")
    for suffix in ("即可", "就行", "就好", "吧"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip()
    return value


def _parse_rate(value: str) -> float:
    normalized = value.strip().replace(" ", "")
    is_percent = normalized.startswith("百分之") or normalized.endswith("%")
    normalized = normalized.removeprefix("百分之").removesuffix("%")
    try:
        number = float(normalized)
    except ValueError as error:
        try:
            number = _parse_chinese_number(normalized)
        except ValueError:
            raise ProfileChangeParseError(f"无法识别比例：{value}") from error
    if is_percent or abs(number) > 1:
        number /= 100
    return number


def _parse_chinese_number(value: str) -> float:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }

    def parse_integer(part: str) -> int:
        if not part:
            return 0
        if all(char in digits for char in part):
            return int("".join(str(digits[char]) for char in part))
        total = 0
        pending = 0
        for char in part:
            if char in digits:
                pending = digits[char]
            elif char == "十":
                total += (pending or 1) * 10
                pending = 0
            elif char == "百":
                total += (pending or 1) * 100
                pending = 0
            else:
                raise ValueError("unsupported Chinese number")
        return total + pending

    if value.count("点") > 1:
        raise ValueError("invalid Chinese decimal")
    integer, separator, fraction = value.partition("点")
    result = float(parse_integer(integer))
    if separator:
        if not fraction or not all(char in digits for char in fraction):
            raise ValueError("invalid Chinese decimal")
        result += int("".join(str(digits[char]) for char in fraction)) / (
            10 ** len(fraction)
        )
    return result


def _parse_currency(value: str) -> str:
    normalized = _clean_text_value(value).casefold()
    if normalized in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[normalized]
    if re.fullmatch(r"[a-z]{3}", normalized):
        return normalized.upper()
    raise ProfileChangeParseError(f"不支持的币种：{value.strip()}")


def _parse_style(value: str) -> str:
    normalized = _clean_text_value(value)
    for alias, style in _STYLE_ALIASES.items():
        if alias in normalized:
            return style
    raise ProfileChangeParseError(f"不支持的回答风格：{value.strip()}")


def parse_profile_change(
    text: str,
    current_profile: AgentProfile,
) -> AgentProfilePatch | None:
    """Parse an explicit Chinese change request into a validated profile patch."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(current_profile, AgentProfile):
        raise TypeError("current_profile must be an AgentProfile")
    message = " ".join(text.strip().split())
    if not message or not _has_explicit_change_action(message):
        return None

    quote_changes: dict[str, Any] = {}
    for field, pattern in _RATE_PATTERNS.items():
        match = pattern.search(message)
        if match:
            quote_changes[field] = _parse_rate(match.group(1))

    currency_match = _CURRENCY_PATTERN.search(message)
    if currency_match:
        quote_changes["currency"] = _parse_currency(currency_match.group(1))

    identity_changes: dict[str, Any] = {}
    for field, pattern in _TEXT_PATTERNS.items():
        match = pattern.search(message)
        if match:
            value = _clean_text_value(match.group(1))
            if value:
                identity_changes[field] = value

    future_name = _FUTURE_NAME_PATTERN.search(message)
    if future_name and "display_name" not in identity_changes:
        identity_changes["display_name"] = _clean_text_value(future_name.group(1))

    style_match = _STYLE_PATTERN.search(message) or _FUTURE_STYLE_PATTERN.search(
        message
    )
    if style_match:
        identity_changes["response_style"] = _parse_style(style_match.group(1))

    if not quote_changes and not identity_changes:
        return None

    try:
        patch = AgentProfilePatch(
            identity=(
                AgentIdentityPatch.model_validate(identity_changes)
                if identity_changes
                else None
            ),
            quote_policy=(
                QuotePolicyPatch.model_validate(quote_changes)
                if quote_changes
                else None
            ),
        )
        resolved = apply_profile_patch(
            current_profile,
            patch,
            revision=current_profile.revision + 1,
            updated_at=current_profile.updated_at,
        )
    except (ValidationError, ValueError) as error:
        raise ProfileChangeParseError(f"配置变更无效：{error}") from error

    if (
        resolved.identity == current_profile.identity
        and resolved.quote_policy == current_profile.quote_policy
    ):
        return None
    return patch


def format_profile_changes(
    before: AgentProfile,
    patch: AgentProfilePatch,
) -> str:
    """Format an exact before/after preview; no value is persisted here."""
    try:
        after = apply_profile_patch(
            before,
            patch,
            revision=before.revision + 1,
            updated_at=before.updated_at,
        )
    except (ValidationError, ValueError) as error:
        raise ProfileChangeParseError(f"配置变更无效：{error}") from error

    changes: list[str] = []
    identity_labels = {
        "display_name": "Agent 名称",
        "role": "Agent 角色",
        "domain": "专业领域",
        "response_style": "回答风格",
        "language": "默认语言",
        "guidance": "业务指引",
    }
    style_labels = {
        "concise": "简洁",
        "balanced": "平衡",
        "detailed": "详细",
    }
    for field, label in identity_labels.items():
        old = getattr(before.identity, field)
        new = getattr(after.identity, field)
        if old == new:
            continue
        if field == "response_style":
            old = style_labels[old]
            new = style_labels[new]
        changes.append(f"- {label}：{old} -> {new}")

    quote_labels = {
        "overhead_rate": "管理费率",
        "tax_rate": "税率",
        "high_risk_below": "高风险阈值",
        "medium_risk_below": "中风险阈值",
    }
    for field, label in quote_labels.items():
        old = getattr(before.quote_policy, field)
        new = getattr(after.quote_policy, field)
        if old != new:
            changes.append(f"- {label}：{old:.2%} -> {new:.2%}")
    if before.quote_policy.currency != after.quote_policy.currency:
        changes.append(
            f"- 币种：{before.quote_policy.currency} -> {after.quote_policy.currency}"
        )

    if not changes:
        return "配置与当前 Workspace Agent Profile 相同，无需变更。"
    return "\n".join(
        [
            "建议更新 Workspace Agent Profile：",
            *changes,
            f"确认后将从 revision {before.revision} 更新为 revision {after.revision}；当前尚未生效。",
        ]
    )
