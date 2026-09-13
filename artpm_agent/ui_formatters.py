"""Pure formatting helpers shared by Streamlit views."""

from __future__ import annotations

from typing import Any


def format_cn_date(value: Any, include_time: bool = False) -> str:
    if not value:
        return "未知"
    if include_time:
        return f"{value.year}年{value.month}月{value.day}日 {value.hour:02d}:{value.minute:02d}"
    return f"{value.month}月{value.day}日"


def format_money(value: Any, compact: bool = False) -> str:
    amount = float(value or 0)
    if compact and abs(amount) >= 10000:
        return f"¥{amount / 10000:,.1f}万"
    return f"¥{amount:,.0f}"


def normalize_agent_response(response: Any) -> str:
    if not isinstance(response, str) or not response.strip():
        raise ValueError("模型服务未返回有效回答")
    return response.strip()


def history_limits(agent: Any) -> tuple[int, int]:
    config = getattr(agent, "config", None)
    if config is None or not hasattr(config, "get"):
        return 12, 8000
    return (
        int(config.get("llm.history_max_messages", 12)),
        int(config.get("llm.history_max_chars", 8000)),
    )


def current_model_id(agent: Any) -> str | None:
    config = getattr(agent, "config", None)
    if config is None or not hasattr(config, "get"):
        return None
    model_id = config.get("llm.model")
    return str(model_id).strip() if model_id else None


def response_model_id(agent: Any) -> str | None:
    model_id = getattr(agent, "last_response_model", None)
    if isinstance(model_id, str) and model_id.strip():
        return model_id.strip()
    return current_model_id(agent)


def format_size(num_bytes: Any) -> str:
    if not isinstance(num_bytes, int) or num_bytes <= 0:
        return ""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def artifact_subtitle(artifact: dict[str, Any]) -> str:
    fmt = str(artifact.get("format") or "").lower()
    parts: list[str] = []
    if fmt == "xlsx":
        rows = artifact.get("rows")
        columns = artifact.get("columns")
        if rows is not None and columns is not None:
            parts.append(f"{rows} 行 · {columns} 列")
        elif rows is not None:
            parts.append(f"{rows} 行")
    elif fmt == "docx" and artifact.get("paragraphs") is not None:
        parts.append(f"{artifact['paragraphs']} 段")
    size = format_size(artifact.get("size"))
    if size:
        parts.append(size)
    if not parts and fmt:
        parts.append(fmt.upper())
    return " · ".join(parts)


__all__ = [
    "artifact_subtitle", "current_model_id", "format_cn_date", "format_money",
    "format_size", "history_limits", "normalize_agent_response", "response_model_id",
]
