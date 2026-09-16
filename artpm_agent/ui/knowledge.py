"""Pure knowledge-intent and context projection helpers.

Database access stays in ``ui_helpers.build_knowledge_context``.  This module
only normalizes user text and projects already-fetched records into a bounded
prompt fragment, making the authority boundary explicit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


def extract_knowledge_rule(prompt: Any) -> str | None:
    """Return an explicit long-term rule request, never an inferred preference."""
    text = " ".join(str(prompt or "").strip().split())
    if not text or any(
        phrase in text for phrase in ("记住这份附件", "附件加入知识库", "附件加入资料库")
    ):
        return None
    explicit = re.search(
        r"^(?:请)?记住(?:这条)?(?:规则|偏好)?[：:，,\s]+(.{2,1000})$",
        text,
        re.IGNORECASE,
    )
    if explicit:
        return explicit.group(1).strip()
    add_rule = re.search(
        r"^(?:把|将)(.{2,1000}?)(?:作为|设为)?(?:规则|偏好)?"
        r"(?:加入|写入)(?:知识库|资料库)$",
        text,
        re.IGNORECASE,
    )
    if add_rule:
        return add_rule.group(1).strip(" ，,：:")
    if text.startswith(("以后", "今后")) and any(
        marker in text
        for marker in ("统一", "一律", "默认", "必须", "不要", "请", "按", "使用", "采用")
    ):
        return text
    return None


def is_knowledge_ingestion_request(prompt: Any) -> bool:
    text = str(prompt or "").casefold()
    return (
        any(target in text for target in ("知识库", "资料库"))
        and any(
            action in text for action in ("加入", "写入", "保存", "收录", "沉淀", "学习")
        )
        and any(subject in text for subject in ("附件", "文件", "这份", "这些"))
    )


def compose_knowledge_context(
    rules: Iterable[Mapping[str, Any]] | None,
    resources: Iterable[Mapping[str, Any]] | None,
    *,
    max_chars: int = 6000,
) -> str:
    """Project trusted knowledge records into a bounded model context."""
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 0:
        raise ValueError("max_chars must be a non-negative integer")
    lines: list[str] = []
    rule_rows = list(rules or ())
    resource_rows = list(resources or ())
    if rule_rows:
        lines.append("已采纳规则：")
        lines.extend(
            f"- {row.get('statement', '')}" for row in rule_rows if row.get("statement")
        )
    if resource_rows:
        lines.append("相关资料：")
        for resource in resource_rows:
            title = resource.get("title", "未命名资料")
            version = resource.get("current_version") or resource.get("version")
            excerpt = str(
                resource.get("text") or resource.get("searchable_text") or ""
            ).strip()
            lines.append(f"- {title}（v{version}）：{excerpt}")
    return "\n".join(lines)[:max_chars]


__all__ = [
    "compose_knowledge_context",
    "extract_knowledge_rule",
    "is_knowledge_ingestion_request",
]
