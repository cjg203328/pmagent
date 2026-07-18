"""User-friendly error messages and suggestions.

Transforms technical errors into actionable guidance for users.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ErrorTemplate:
    """Template for user-facing error message."""
    user_facing: str
    suggested_actions: list[str]
    severity: str = "error"  # error, warning, info
    internal_note: str = ""


# Error templates catalog
ERROR_TEMPLATES: dict[str, ErrorTemplate] = {
    "model_unavailable": ErrorTemplate(
        user_facing="AI 服务暂时繁忙，请稍后重试",
        suggested_actions=[
            "等待 1-2 分钟后重试",
            "检查网络连接是否正常",
            "如持续出现，请联系管理员"
        ],
        severity="error",
        internal_note="Model connection failed or timed out"
    ),

    "rate_limit": ErrorTemplate(
        user_facing="请求过于频繁，请稍后再试",
        suggested_actions=[
            "等待 {cooldown_seconds} 秒后重试",
            "减少请求频率",
            "如需更高配额，请联系管理员升级套餐"
        ],
        severity="warning"
    ),

    "quota_exceeded": ErrorTemplate(
        user_facing="今日配额已用完",
        suggested_actions=[
            "等待次日配额重置（北京时间 00:00）",
            "升级到更高套餐以获得更多配额",
            "查看配额使用情况：设置 → 配额管理"
        ],
        severity="warning"
    ),

    "invalid_input": ErrorTemplate(
        user_facing="输入格式有误：{validation_error}",
        suggested_actions=[
            "检查输入格式是否符合要求",
            "参考示例：{example}",
            "如需帮助，输入「帮助」查看使用指南"
        ],
        severity="warning"
    ),

    "file_too_large": ErrorTemplate(
        user_facing="文件超出大小限制（{size_mb} MB > {limit_mb} MB）",
        suggested_actions=[
            "压缩文件后重试",
            "分割为多个小文件上传",
            "升级套餐以获得更大文件限制"
        ],
        severity="warning"
    ),

    "file_format_unsupported": ErrorTemplate(
        user_facing="不支持的文件格式：{format}",
        suggested_actions=[
            "支持的格式：{supported_formats}",
            "转换为支持的格式后重试",
            "如需支持新格式，请联系技术支持"
        ],
        severity="warning"
    ),

    "ocr_unavailable": ErrorTemplate(
        user_facing="OCR 服务暂时不可用，无法识别扫描件或图片中的文字",
        suggested_actions=[
            "检查 OCR 服务配置",
            "使用文本版 PDF 或 Word 文档",
            "手动输入关键信息"
        ],
        severity="warning"
    ),

    "database_error": ErrorTemplate(
        user_facing="数据读取失败，请稍后重试",
        suggested_actions=[
            "刷新页面重试",
            "检查数据库文件是否存在",
            "如持续出现，请联系管理员检查数据库完整性"
        ],
        severity="error",
        internal_note="Database query failed"
    ),

    "memory_retrieval_failed": ErrorTemplate(
        user_facing="记忆检索失败，本次对话将无法使用历史上下文",
        suggested_actions=[
            "本次对话仍可继续",
            "提供完整信息以获得更好的回答",
            "管理员：检查向量库状态"
        ],
        severity="warning"
    ),

    "skill_execution_failed": ErrorTemplate(
        user_facing="执行技能「{skill_name}」时出错：{error_detail}",
        suggested_actions=[
            "检查输入数据是否完整",
            "重新表述问题后重试",
            "尝试其他方式获取信息"
        ],
        severity="error"
    ),

    "network_error": ErrorTemplate(
        user_facing="网络连接失败",
        suggested_actions=[
            "检查网络连接",
            "检查防火墙设置",
            "如使用代理，确认代理配置正确"
        ],
        severity="error"
    ),

    "timeout": ErrorTemplate(
        user_facing="请求超时，可能是任务太复杂或网络较慢",
        suggested_actions=[
            "简化问题后重试",
            "检查网络速度",
            "增加超时时间：设置 → 高级 → 请求超时"
        ],
        severity="warning"
    ),

    "authentication_failed": ErrorTemplate(
        user_facing="API 密钥无效或已过期",
        suggested_actions=[
            "检查 API 密钥是否正确",
            "确认 API 密钥未过期",
            "在设置中重新配置 API 密钥"
        ],
        severity="error"
    ),

    "generic": ErrorTemplate(
        user_facing="处理请求时出现错误",
        suggested_actions=[
            "稍后重试",
            "如问题持续，请联系技术支持",
            "提供错误代码: {error_id}"
        ],
        severity="error"
    )
}


def classify_error(error: Exception) -> str:
    """Classify error type from exception.

    Args:
        error: Exception instance

    Returns:
        Error type key for templates
    """
    error_str = str(error).lower()
    error_type = type(error).__name__.lower()

    # Rate limiting
    if "rate limit" in error_str or "429" in error_str or "too many requests" in error_str:
        return "rate_limit"

    # Quota
    if "quota" in error_str or "limit exceeded" in error_str:
        return "quota_exceeded"

    # Timeout
    if "timeout" in error_str or "timed out" in error_str:
        return "timeout"

    # Network
    if any(term in error_str for term in ["connection", "network", "unreachable", "refused"]):
        return "network_error"

    # Authentication
    if any(term in error_str for term in ["auth", "401", "403", "unauthorized", "forbidden", "api key"]):
        return "authentication_failed"

    # Server errors
    if any(code in error_str for code in ["500", "502", "503", "504"]):
        return "model_unavailable"

    # File errors
    if "file too large" in error_str or "exceeds" in error_str:
        return "file_too_large"

    if "unsupported" in error_str and "format" in error_str:
        return "file_format_unsupported"

    # Database
    if "database" in error_str or "sqlite" in error_str or "sqlalchemy" in error_type:
        return "database_error"

    # Validation
    if "validation" in error_str or "invalid" in error_str:
        return "invalid_input"

    return "generic"


def format_error_for_user(
    error: Exception,
    *,
    error_type: str | None = None,
    context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Format error as user-friendly message with suggestions.

    Args:
        error: Exception instance
        error_type: Optional explicit error type (overrides classification)
        context: Optional context for template variables

    Returns:
        Dictionary with message, suggestions, severity
    """
    context = context or {}

    # Classify error if not provided
    if error_type is None:
        error_type = classify_error(error)

    # Get template
    template = ERROR_TEMPLATES.get(error_type, ERROR_TEMPLATES["generic"])

    # Format message with context
    try:
        user_message = template.user_facing.format(**context)
    except KeyError as e:
        logger.warning(f"Missing context variable in error template: {e}")
        user_message = template.user_facing

    # Format suggestions with context
    formatted_suggestions = []
    for suggestion in template.suggested_actions:
        try:
            formatted_suggestions.append(suggestion.format(**context))
        except KeyError:
            formatted_suggestions.append(suggestion)

    # Add error ID for support
    import uuid
    error_id = str(uuid.uuid4())[:8]

    result = {
        "message": user_message,
        "suggestions": formatted_suggestions,
        "severity": template.severity,
        "error_id": error_id,
        "error_type": error_type,
        "original_error": str(error),  # For logging
        "internal_note": template.internal_note
    }

    # Log error with context
    logger.error(
        f"Error [{error_id}] {error_type}: {str(error)}",
        extra={"error_context": context},
        exc_info=True
    )

    return result


def extract_context_from_error(error: Exception) -> dict[str, Any]:
    """Extract context variables from error message.

    Args:
        error: Exception instance

    Returns:
        Dictionary of context variables
    """
    context: dict[str, Any] = {}
    error_str = str(error)

    # Extract cooldown seconds
    cooldown_match = re.search(r"(\d+)\s*seconds?", error_str, re.IGNORECASE)
    if cooldown_match:
        context["cooldown_seconds"] = cooldown_match.group(1)

    # Extract file size
    size_match = re.search(r"(\d+(?:\.\d+)?)\s*MB", error_str, re.IGNORECASE)
    if size_match:
        context["size_mb"] = size_match.group(1)

    # Extract limit
    limit_match = re.search(r"limit[:\s]*(\d+(?:\.\d+)?)\s*MB", error_str, re.IGNORECASE)
    if limit_match:
        context["limit_mb"] = limit_match.group(1)

    return context


def render_error_markdown(error_info: dict[str, Any]) -> str:
    """Render error info as Markdown for UI display.

    Args:
        error_info: Output from format_error_for_user()

    Returns:
        Markdown string
    """
    severity_emoji = {
        "error": "❌",
        "warning": "⚠️",
        "info": "ℹ️"
    }

    emoji = severity_emoji.get(error_info["severity"], "⚠️")
    lines = [
        f"{emoji} **{error_info['message']}**",
        "",
        "**建议操作**:",
    ]

    for i, suggestion in enumerate(error_info["suggestions"], 1):
        lines.append(f"{i}. {suggestion}")

    lines.extend([
        "",
        f"_错误代码: `{error_info['error_id']}`_"
    ])

    return "\n".join(lines)
