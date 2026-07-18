"""
对话页面的模型选择器组件。

允许用户在对话界面临时切换模型，而不需要去设置页面修改配置。
"""

import os
import streamlit as st
from typing import List, Optional, Tuple, Any

from artpm_agent.utils import get_logger

logger = get_logger(__name__)


def parse_cached_models(env_value: str) -> List[str]:
    """解析 LLM_AVAILABLE_MODELS 环境变量。"""
    if not env_value or not env_value.strip():
        return []
    try:
        import json
        models = json.loads(env_value)
        if isinstance(models, list):
            return [str(m).strip() for m in models if m]
        return []
    except Exception:
        return []


def get_available_models() -> List[str]:
    """获取当前配置的可用模型列表。"""
    # 从环境变量读取
    env_models = parse_cached_models(os.getenv("LLM_AVAILABLE_MODELS", ""))

    # 如果环境变量为空，尝试从当前模型推断
    if not env_models:
        current_model = os.getenv("LLM_MODEL", "").strip()
        provider = os.getenv("LLM_PROVIDER", "").strip().lower()

        # 根据 provider 提供默认候选
        if provider == "openai":
            env_models = ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]
        elif provider == "anthropic":
            env_models = [
                "claude-3-5-sonnet-20241022",
                "claude-3-haiku-20240307",
            ]
        elif provider == "zhipu":
            env_models = ["glm-5.2", "glm-4"]

        # 确保当前模型在列表中
        if current_model and current_model not in env_models:
            env_models.insert(0, current_model)

    return env_models


def get_current_model() -> str:
    """获取当前使用的模型 ID。"""
    # 优先从 session_state 读取（用户临时切换）
    if "chat_active_model" in st.session_state:
        return st.session_state.chat_active_model

    # 否则从环境变量读取（设置页配置）
    return os.getenv("LLM_MODEL", "").strip()


def set_active_model(model_id: str) -> None:
    """设置当前对话使用的模型（会话级别，不持久化）。"""
    st.session_state.chat_active_model = model_id
    logger.info(f"用户临时切换模型: {model_id}")


def render_model_selector() -> Optional[str]:
    """渲染模型选择器组件。

    Returns:
        用户选择的模型 ID，如果未切换则返回 None
    """
    available_models = get_available_models()
    current_model = get_current_model()

    if not available_models:
        # 没有可用模型列表，显示当前模型但不允许切换
        if current_model:
            st.caption(f"🤖 当前模型: {current_model}")
        return None

    # 确保当前模型在列表中
    if current_model and current_model not in available_models:
        available_models.insert(0, current_model)

    # 找到当前模型的索引
    try:
        current_index = available_models.index(current_model) if current_model else 0
    except ValueError:
        current_index = 0

    # 渲染选择器
    selected_model = st.selectbox(
        "模型",
        options=available_models,
        index=current_index,
        key="chat_model_selector",
        help="临时切换模型（不会保存到设置）",
        label_visibility="collapsed",
        format_func=lambda m: f"🤖 {_format_model_name(m)}",
    )

    # 检测是否切换了模型
    if selected_model and selected_model != current_model:
        set_active_model(selected_model)
        return selected_model

    return None


def _format_model_name(model_id: str) -> str:
    """格式化模型名称，让它更易读。"""
    # 提取模型名称的核心部分
    name = model_id

    # 移除版本日期后缀（如 -20241022）
    import re
    name = re.sub(r"-\d{8}$", "", name)

    # 添加友好的 provider 标识
    if "gpt" in name.lower():
        provider_tag = "[OpenAI]"
    elif "claude" in name.lower():
        provider_tag = "[Anthropic]"
    elif "glm" in name.lower():
        provider_tag = "[Zhipu]"
    elif "deepseek" in name.lower():
        provider_tag = "[DeepSeek]"
    elif "qwen" in name.lower():
        provider_tag = "[Qwen]"
    elif "gemini" in name.lower() or "gemma" in name.lower():
        provider_tag = "[Google]"
    elif "kimi" in name.lower():
        provider_tag = "[Moonshot]"
    else:
        provider_tag = ""

    return f"{name} {provider_tag}".strip()


def apply_model_override_to_agent(agent: Optional[Any], model_id: Optional[str]) -> None:
    """将用户选择的模型应用到 Agent 实例（临时覆盖）。

    Args:
        agent: Agent 实例
        model_id: 用户选择的模型 ID，None 表示使用默认模型
    """
    if agent is None:
        return

    # 如果有 ModelGateway，临时覆盖其主模型 ID
    model_gateway = getattr(agent, "model_gateway", None)
    if model_gateway is not None and hasattr(model_gateway, "_llm_config"):
        if model_id:
            # 保存原始配置（如果还没保存）
            if not hasattr(model_gateway, "_original_model"):
                model_gateway._original_model = model_gateway._llm_config.get("model")

            # 临时覆盖
            model_gateway._llm_config["model"] = model_id
            model_gateway.last_response_model = model_id
            logger.info(f"Agent 模型临时覆盖: {model_id}")
        else:
            # 恢复原始配置
            if hasattr(model_gateway, "_original_model"):
                model_gateway._llm_config["model"] = model_gateway._original_model
                model_gateway.last_response_model = model_gateway._original_model
                delattr(model_gateway, "_original_model")
                logger.info("Agent 模型恢复为默认配置")


def get_agent_current_model(agent: Optional[Any]) -> str:
    """获取 Agent 当前使用的模型 ID。"""
    if agent is None:
        return ""

    model_gateway = getattr(agent, "model_gateway", None)
    if model_gateway is not None:
        return model_gateway.primary_model_id() or ""

    # Fallback: 从配置读取
    return os.getenv("LLM_MODEL", "").strip()
