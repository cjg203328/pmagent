"""
测试对话页面模型选择器和友好错误提示。

运行方式：
    python -m pytest tests/test_chat_model_selector.py -v
"""

import os
import pytest
from unittest.mock import Mock, patch


class TestChatModelSelector:
    """测试对话页面模型选择器。"""

    def test_parse_cached_models(self):
        """测试模型列表解析。"""
        from artpm_agent.views.chat_model_selector import parse_cached_models

        # 正常 JSON 列表
        result = parse_cached_models('["gpt-4o", "gpt-4o-mini"]')
        assert result == ["gpt-4o", "gpt-4o-mini"]

        # 空字符串
        result = parse_cached_models("")
        assert result == []

        # 无效 JSON
        result = parse_cached_models("invalid json")
        assert result == []

    def test_get_available_models_from_env(self):
        """测试从环境变量读取可用模型。"""
        from artpm_agent.views.chat_model_selector import get_available_models

        with patch.dict(os.environ, {
            "LLM_AVAILABLE_MODELS": '["model-a", "model-b", "model-c"]'
        }):
            models = get_available_models()
            assert models == ["model-a", "model-b", "model-c"]

    def test_get_available_models_fallback_by_provider(self):
        """测试根据 provider 自动推断候选模型。"""
        from artpm_agent.views.chat_model_selector import get_available_models

        # OpenAI provider
        with patch.dict(os.environ, {
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": "gpt-4o-mini",
            "LLM_AVAILABLE_MODELS": ""
        }):
            models = get_available_models()
            assert "gpt-4o" in models
            assert "gpt-4o-mini" in models
            assert "gpt-3.5-turbo" in models

        # Anthropic provider
        with patch.dict(os.environ, {
            "LLM_PROVIDER": "anthropic",
            "LLM_MODEL": "claude-3-5-sonnet-20241022",
            "LLM_AVAILABLE_MODELS": ""
        }):
            models = get_available_models()
            assert any("claude" in m for m in models)

    def test_apply_model_override_to_agent(self):
        """测试模型覆盖应用到 Agent。"""
        from artpm_agent.views.chat_model_selector import apply_model_override_to_agent

        # 创建 mock agent（需要真实的字典，不能用 Mock）
        class MockGateway:
            def __init__(self):
                self._llm_config = {"model": "gpt-4o-mini"}
                self.last_response_model = "gpt-4o-mini"

        mock_gateway = MockGateway()
        mock_agent = Mock()
        mock_agent.model_gateway = mock_gateway

        # 覆盖模型
        apply_model_override_to_agent(mock_agent, "gpt-4o")

        assert mock_gateway._llm_config["model"] == "gpt-4o"
        assert mock_gateway.last_response_model == "gpt-4o"
        assert hasattr(mock_gateway, "_original_model")
        assert mock_gateway._original_model == "gpt-4o-mini"

        # 恢复默认
        apply_model_override_to_agent(mock_agent, None)

        assert mock_gateway._llm_config["model"] == "gpt-4o-mini"
        assert not hasattr(mock_gateway, "_original_model")

    def test_format_model_name(self):
        """测试模型名称格式化。"""
        from artpm_agent.views.chat_model_selector import _format_model_name

        assert "[OpenAI]" in _format_model_name("gpt-4o-mini")
        assert "[Anthropic]" in _format_model_name("claude-3-5-sonnet-20241022")
        assert "[Zhipu]" in _format_model_name("glm-5.2")
        assert "[DeepSeek]" in _format_model_name("deepseek-v4-flash")

        # 移除日期后缀
        formatted = _format_model_name("claude-3-5-sonnet-20241022")
        assert "20241022" not in formatted


class TestFriendlyErrorMessages:
    """测试友好错误提示。"""

    def test_authentication_error(self):
        """测试认证错误提示。"""
        from artpm_agent.ui_helpers import _chat_error_message

        error = Exception("401: Invalid API key")
        message = _chat_error_message(error, "gpt-4o-mini")

        assert "认证失败" in message
        assert "API Key" in message
        assert "解决方案" in message

    def test_timeout_error(self):
        """测试超时错误提示。"""
        from artpm_agent.ui_helpers import _chat_error_message

        error = Exception("Request timeout after 30s")
        message = _chat_error_message(error, "gpt-4o")

        assert "超时" in message or "响应超时" in message
        assert "重新生成" in message
        assert "解决方案" in message

    def test_rate_limit_error(self):
        """测试限流错误提示。"""
        from artpm_agent.ui_helpers import _chat_error_message

        error = Exception("429: Too many requests")
        message = _chat_error_message(error, "gpt-3.5-turbo")

        assert "服务繁忙" in message or "限流" in message or "限制" in message
        assert "解决方案" in message

    def test_connection_error(self):
        """测试连接错误提示。"""
        from artpm_agent.ui_helpers import _chat_error_message

        error = Exception("Connection refused")
        message = _chat_error_message(error, "gpt-4o-mini")

        assert "连接失败" in message
        assert "API Base URL" in message
        assert "解决方案" in message

    def test_model_not_found_error(self):
        """测试模型不存在错误提示。"""
        from artpm_agent.ui_helpers import _chat_error_message

        error = Exception("404: Model not found")
        message = _chat_error_message(error, "unknown-model")

        assert "不存在" in message or "不支持" in message
        assert "解决方案" in message

    def test_available_models_hint(self):
        """测试错误提示中包含可用模型建议。"""
        from artpm_agent.ui_helpers import _chat_error_message

        with patch.dict(os.environ, {
            "LLM_AVAILABLE_MODELS": '["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]'
        }):
            error = Exception("Service unavailable")
            message = _chat_error_message(error, "gpt-4o")

            # 应该包含候选模型提示
            assert ("gpt-4o" in message or "gpt-4o-mini" in message or
                    "你可以尝试切换到其他模型" in message)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
