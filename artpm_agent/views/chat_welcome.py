"""Welcome-page suggestions independent of Streamlit rendering."""

from __future__ import annotations

from typing import TypedDict


class WelcomeSuggestion(TypedDict, total=False):
    icon: str
    text: str
    prompt: str
    mode: str


WELCOME_SUGGESTIONS: tuple[WelcomeSuggestion, ...] = (
    {"icon": ":material/analytics:", "text": "分析利润率", "prompt": "帮我分析当前项目的利润率和成本结构"},
    {"icon": ":material/request_quote:", "text": "创建报价", "prompt": "帮我创建一个新的项目报价，包含客户、报价金额和工期"},
    {"icon": ":material/query_stats:", "text": "项目概览", "prompt": "查看所有项目的整体经营概览和统计数据"},
    {"icon": ":material/rule:", "text": "评估需求", "prompt": "我有一个新的产品需求，帮我评估技术可行性和成本"},
    {"icon": ":material/summarize:", "text": "生成周报", "prompt": "根据近期项目数据，生成一份本周工作总结报告"},
    {"icon": ":material/tune:", "text": "优化流程", "prompt": "根据现有工作流，给出优化项目管理的具体建议"},
    {"icon": ":material/edit_document:", "text": "智能编辑文件", "mode": "edit"},
)


def welcome_suggestions() -> tuple[WelcomeSuggestion, ...]:
    """Return an immutable copy so callers cannot mutate shared page state."""
    return tuple(dict(item) for item in WELCOME_SUGGESTIONS)  # type: ignore[return-value]


__all__ = ["WELCOME_SUGGESTIONS", "WelcomeSuggestion", "welcome_suggestions"]
