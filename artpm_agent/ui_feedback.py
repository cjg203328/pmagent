"""Consistent, actionable feedback surfaces for the Streamlit UI."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
import logging
import os
import re
from typing import Any
from uuid import uuid4

import streamlit as st

from artpm_agent.presentation.error_messages import (
    extract_context_from_error,
    format_error_for_user,
)

logger = logging.getLogger(__name__)


def _feedback_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def probe_backend_health(*, base_url: str | None = None, timeout: float = 0.6) -> dict[str, Any]:
    """Probe the FastAPI gateway for a lightweight UI link indicator."""
    configured_base = base_url or os.getenv("ARTPM_API_BASE_URL") or os.getenv("ARTPM_API_URL")
    required = bool(configured_base) or os.getenv("ARTPM_API_REQUIRED", "").casefold() in {
        "1",
        "true",
        "yes",
    }
    if base_url is None:
        base_url = configured_base
    if not base_url:
        port = os.getenv("ARTPM_API_PORT", "8765").strip() or "8765"
        base_url = f"http://127.0.0.1:{port}"
    base_url = str(base_url).rstrip("/")
    try:
        import requests

        response = requests.get(f"{base_url}/health", timeout=timeout)
    except Exception as error:  # noqa: BLE001 - UI status must never crash the app
        return {
            "status": "error" if required else "optional",
            "server": "not_running",
            "url": base_url,
            "detail": str(error),
        }
    try:
        payload = response.json()
    except ValueError:
        return {
            "status": "error",
            "server": "invalid_response",
            "url": base_url,
        }
    gateway_status = payload.get("status") if isinstance(payload, Mapping) else None
    if response.status_code != 200 or gateway_status not in {"ok", "degraded"}:
        return {
            "status": "error",
            "server": "unhealthy",
            "url": base_url,
            "gateway_status": gateway_status,
        }
    return {
        "status": "ok" if gateway_status == "ok" else "degraded",
        "server": "ok",
        "url": base_url,
        "gateway_status": gateway_status,
        "version": payload.get("version", ""),
    }


def build_error_info(
    error: Exception,
    *,
    error_type: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a serializable user-facing error payload without internals."""
    merged_context = extract_context_from_error(error)
    if context:
        merged_context.update(dict(context))
    info = format_error_for_user(
        error,
        error_type=error_type,
        context=merged_context,
    )
    # ``original_error`` is useful to logs but must never be rendered by UI.
    info.pop("original_error", None)
    info.pop("internal_note", None)
    return info


def build_attachment_error_info(
    error: Exception,
    *,
    max_files: int = 3,
    max_file_size_mb: int = 50,
    max_total_size_mb: int = 100,
) -> dict[str, Any]:
    """Map attachment validation/storage failures to bounded, friendly copy."""
    error_id = _feedback_id("upload")
    detail = str(error)
    normalized = detail.casefold()
    logger.warning("Attachment rejected [%s]: %s", error_id, detail)

    if "unsupported attachment extension" in normalized:
        match = re.search(r"extension:\s*\.([a-z0-9]+)", normalized)
        extension = f".{match.group(1)}" if match else "该格式"
        return {
            "message": f"暂不支持 {extension} 文件。",
            "suggestions": [
                "请转换为 PDF、Word、Excel、图片或纯文本后重新选择",
                "确认文件扩展名与实际内容一致",
            ],
            "severity": "warning",
            "error_id": error_id,
        }
    if "at most" in normalized and "files" in normalized:
        return {
            "message": f"一次最多添加 {max_files} 个附件。",
            "suggestions": ["移除部分文件后重新发送", "将文件合并后再上传"],
            "severity": "warning",
            "error_id": error_id,
        }
    if "batch exceeds" in normalized:
        return {
            "message": f"本次附件总大小不能超过 {max_total_size_mb} MB。",
            "suggestions": ["移除部分文件后重试", "压缩文件或分批发送"],
            "severity": "warning",
            "error_id": error_id,
        }
    if "exceeds" in normalized:
        return {
            "message": f"单个附件不能超过 {max_file_size_mb} MB。",
            "suggestions": ["压缩文件后重试", "拆分文件后分批发送"],
            "severity": "warning",
            "error_id": error_id,
        }
    if "empty" in normalized:
        return {
            "message": "不能上传空文件。",
            "suggestions": ["确认文件已正确保存后重新选择"],
            "severity": "warning",
            "error_id": error_id,
        }
    if isinstance(error, FileNotFoundError) or "does not exist" in normalized:
        return {
            "message": "附件已失效或被移动，请重新上传。",
            "suggestions": ["移除失效附件", "通过左侧加号重新选择文件"],
            "severity": "warning",
            "error_id": error_id,
        }
    if isinstance(error, OSError):
        return {
            "message": "暂时无法保存附件，请稍后重试。",
            "suggestions": ["检查工作区存储是否可用", "重新选择文件后再发送"],
            "severity": "error",
            "error_id": error_id,
        }
    return {
        "message": "无法读取所选附件，请重新选择。",
        "suggestions": ["确认文件未损坏", "重新选择文件后再发送"],
        "severity": "warning",
        "error_id": error_id,
    }


def _coerce_error_info(error_info: Mapping[str, Any] | Exception) -> dict[str, Any]:
    if isinstance(error_info, Mapping):
        info = dict(error_info)
    else:
        info = build_error_info(error_info)
    info.setdefault("message", "请求处理失败，请稍后重试。")
    info.setdefault("suggestions", ["稍后重试；如果问题持续，请检查连接和配置。"])
    info.setdefault("severity", "error")
    info.setdefault("error_id", "unknown")
    return info


def render_error_callback(
    error_info: Mapping[str, Any] | Exception,
    *,
    key: str,
    retry: bool = False,
    dismissible: bool = True,
    retry_label: str = "重试",
    dismiss_label: str = "关闭",
) -> str | None:
    """Render a compact error callback window and return the selected action."""
    info = _coerce_error_info(error_info)
    dismissed_key = f"{key}_dismissed"
    fingerprint_key = f"{key}_fingerprint"
    fingerprint = f"{info.get('error_id')}:{info.get('message')}"
    if st.session_state.get(fingerprint_key) != fingerprint:
        st.session_state[fingerprint_key] = fingerprint
        st.session_state.pop(dismissed_key, None)
    if st.session_state.get(dismissed_key):
        return None

    with st.container(key=key, border=True):
        st.markdown(
            '<span class="pm-error-callback-anchor" aria-hidden="true"></span>',
            unsafe_allow_html=True,
        )
        severity = str(info.get("severity", "error")).casefold()
        if severity not in {"error", "warning", "info", "success"}:
            severity = "error"
        message = escape(
            str(info.get("message", "请求处理失败，请稍后重试。")),
            quote=True,
        )
        st.markdown(
            (
                f'<div class="pm-error-summary pm-error-summary--{severity}" '
                'role="alert" aria-live="polite">'
                '<span class="pm-error-summary-icon" aria-hidden="true">!</span>'
                f'<span class="pm-error-summary-copy">{message}</span>'
                "</div>"
            ),
            unsafe_allow_html=True,
        )

        suggestions = [
            str(item).strip()
            for item in info.get("suggestions", [])
            if str(item).strip()
        ][:3]
        if suggestions:
            with st.expander("查看处理建议", expanded=False):
                for suggestion in suggestions:
                    st.markdown(f"- {suggestion}")
        st.caption(f"错误编号：{info.get('error_id', 'unknown')}")

        actions = []
        if retry:
            actions.append("retry")
        if dismissible:
            actions.append("dismiss")
        if not actions:
            return None

        columns = st.columns(len(actions), gap="small")
        for column, action in zip(columns, actions):
            with column:
                if action == "retry" and st.button(
                    retry_label,
                    key=f"{key}_retry",
                    type="primary",
                    icon=":material/refresh:",
                    width="content",
                ):
                    return "retry"
                if action == "dismiss" and st.button(
                    dismiss_label,
                    key=f"{key}_dismiss",
                    icon=":material/close:",
                    width="content",
                ):
                    st.session_state[dismissed_key] = True
                    return "dismiss"
    return None


def render_action_callback(
    feedback: Mapping[str, Any],
    *,
    key: str,
    dismissible: bool = True,
) -> str | None:
    """Render a durable success/info callback after a user-owned decision."""
    info = dict(feedback)
    title = str(info.get("title") or "操作已处理").strip()
    message = str(info.get("message") or "状态已更新。").strip()
    severity = str(info.get("severity") or "info").casefold()
    reference = str(info.get("reference") or "").strip()
    dismissed_key = f"{key}_dismissed"
    if st.session_state.get(dismissed_key):
        return None

    with st.container(key=key, border=True):
        st.markdown(
            '<span class="pm-action-callback-anchor" aria-hidden="true"></span>',
            unsafe_allow_html=True,
        )
        st.markdown(f"**{title}**")
        renderer = getattr(st, severity, st.info)
        renderer(message)
        if reference:
            st.caption(f"参考编号：{reference}")
        if dismissible and st.button(
            "知道了",
            key=f"{key}_dismiss",
            icon=":material/close:",
            width="content",
        ):
            st.session_state[dismissed_key] = True
            return "dismiss"
    return None


def render_backend_link_status(status: Mapping[str, Any], *, key: str = "backend_link_status") -> None:
    """Show a non-blocking local/API link status in the sidebar."""
    state = str(status.get("status", "optional")).casefold()
    if state == "ok":
        st.caption("● API 服务可达 · UI 本地直连")
    elif state == "degraded":
        st.warning("API 网关部分组件不可用，当前功能仍可继续。")
    elif state == "error":
        info = {
            "message": "API 网关暂时不可用，当前使用本地运行链路。",
            "suggestions": [
                "确认 API 服务运行在配置的地址",
                "检查 API 网关日志后再重试",
            ],
            "severity": "warning",
            "error_id": "api-link",
        }
        render_error_callback(info, key=key, retry=False, dismissible=False)
    else:
        st.caption("○ 本地直连模式 · API 网关未连接")
