"""Optimized settings callbacks.

Replace slow st.success() calls with fast st.toast() notifications.
"""
from artpm_agent.ui.ui_optimizations import (
    show_toast,
    optimized_callback,
    validate_api_key_sync,
)


# Patch for settings.py line 276
def notify_data_root_switched():
    """Fast notification for data root switch."""
    show_toast("数据存储目录已切换并重新加载", icon="✅")


# Patch for settings.py line 520
def notify_model_sync_success(count: int):
    """Fast notification for model sync."""
    show_toast(f"已同步 {count} 个模型", icon="🔄")


# Patch for settings.py lines 572, 584
def notify_mcp_connected(transport: str, skill_count: int, market_count: int = 0):
    """Fast notification for MCP connection."""
    transport_label = "stdio (npx)" if transport == "stdio" else "HTTP REST"

    if market_count > 0:
        msg = f"Skills Forge 已连接（{transport_label}），{skill_count} 个工具 + {market_count} 个市场技能"
    else:
        msg = f"Skills Forge 已连接（{transport_label}），{skill_count} 个技能可用"

    show_toast(msg, icon="🔗")


# Patch for settings.py line 703
def notify_workflow_saved():
    """Fast notification for workflow save."""
    show_toast("工作流设置已保存", icon="💾")


# Patch for API key validation (new feature)
@optimized_callback(
    success_message="API Key 验证成功",
    error_message="API Key 验证失败",
    debounce=True,
    cache_ttl=30
)
def validate_and_save_api_key(
    provider: str,
    api_key: str,
    api_base: str | None = None
) -> tuple[bool, str]:
    """Validate API key before saving.

    Args:
        provider: LLM provider
        api_key: API key to validate
        api_base: Optional API base URL

    Returns:
        (is_valid, message)
    """
    # Quick check
    if not api_key or api_key.startswith("sk-your-"):
        return False, "请输入有效的 API Key"

    # Async validation
    is_valid, message = validate_api_key_sync(provider, api_key, api_base)

    return is_valid, message


# Integration helper
def apply_settings_optimizations():
    """Apply all settings optimizations.

    Call this at the start of settings_page() to enable optimizations.
    """
    import streamlit as st

    # Enable toast notifications globally
    if "ui_optimizations_enabled" not in st.session_state:
        st.session_state.ui_optimizations_enabled = True

    # Set faster rerun delay
    if hasattr(st, "set_page_config"):
        try:
            st.set_page_config(
                page_title="设置",
                layout="wide",
                initial_sidebar_state="expanded"
            )
        except Exception:
            pass  # Already configured
