"""DeepSeek provider adapter tests (deepseek-chat / deepseek-reasoner).

Covers the DeepSeek-specific wire rules borrowed from deepseek-harness
``packages/llm/llm-deepseek``: reasoning_content captured separately,
reasoning_effort serialization, and content never being null.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from artpm_agent.providers import ModelGateway
from artpm_agent.utils.llm_client import (
    DeepSeekClient,
    create_llm_client,
)


def _build_client(**overrides):
    config = {
        "provider": "deepseek",
        "model": "deepseek-reasoner",
        "temperature": 0.4,
        "max_tokens": 512,
        "retry_max_attempts": 1,
        "deepseek_api_key": "sk-test-deepseek-key-1234",
        **overrides,
    }
    client = create_llm_client(config)
    assert isinstance(client, DeepSeekClient)
    return client


def test_factory_returns_deepseek_client_and_default_base_url():
    client = _build_client()
    assert client.reasoning_effort is None
    assert client.last_reasoning_content is None
    assert str(client.client.base_url) == "https://api.deepseek.com"


def test_chat_captures_reasoning_content_and_returns_text():
    client = _build_client()
    message = SimpleNamespace(content="final answer", reasoning_content="thinking…")
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    client.client.chat.completions.create = Mock(return_value=response)

    result = client.chat("question", system_prompt="sys")

    assert result == "final answer"
    assert client.last_reasoning_content == "thinking…"


def test_chat_never_returns_none_on_reasoning_only_turn():
    # Pure reasoning reply: content is None on the wire, must come back as "".
    client = _build_client()
    message = SimpleNamespace(content=None, reasoning_content="only thinking")
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    client.client.chat.completions.create = Mock(return_value=response)

    assert client.chat("question") == ""
    assert client.last_reasoning_content == "only thinking"


def test_chat_without_reasoning_leaves_reasoning_none():
    client = _build_client()
    message = SimpleNamespace(content="plain", reasoning_content=None)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    client.client.chat.completions.create = Mock(return_value=response)

    assert client.chat("question") == "plain"
    assert client.last_reasoning_content is None


def test_reasoning_effort_off_serializes_thinking_disabled():
    client = _build_client(reasoning_effort="off")
    message = SimpleNamespace(content="ok", reasoning_content=None)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    create = Mock(return_value=response)
    client.client.chat.completions.create = create

    client.chat("question")

    kwargs = create.call_args.kwargs
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in kwargs


@pytest.mark.parametrize("effort", ["high", "max"])
def test_reasoning_effort_high_and_max_pass_through(effort):
    client = _build_client(reasoning_effort=effort)
    message = SimpleNamespace(content="ok", reasoning_content=None)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    create = Mock(return_value=response)
    client.client.chat.completions.create = create

    client.chat("question")

    kwargs = create.call_args.kwargs
    assert kwargs["extra_body"] == {"reasoning_effort": effort}


def test_reasoning_effort_unset_leaves_body_unchanged():
    client = _build_client()
    message = SimpleNamespace(content="ok", reasoning_content=None)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    create = Mock(return_value=response)
    client.client.chat.completions.create = create

    client.chat("question")

    kwargs = create.call_args.kwargs
    assert "extra_body" not in kwargs


def test_unsupported_reasoning_effort_rejected():
    with pytest.raises(ValueError, match="DEEPSEEK_REASONING_EFFORT"):
        _build_client(reasoning_effort="ultra")


def test_stream_chat_accumulates_reasoning_and_yields_text():
    client = _build_client()
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content=None, reasoning_content="step one"))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content="text ", reasoning_content=None))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content="delta", reasoning_content="step two"))]),
        SimpleNamespace(choices=[]),
    ]
    stream = iter(chunks)
    client.client.chat.completions.create = Mock(return_value=stream)

    text = list(client.stream_chat("question"))

    assert text == ["text ", "delta"]
    assert client.last_reasoning_content == "step onestep two"


def test_chat_with_images_raises_clear_capability_error():
    client = _build_client()
    with pytest.raises(NotImplementedError, match="不支持图片"):
        client.chat_with_images("describe", ["a.png"])


def test_deepseek_is_failover_provider():
    # The gateway must treat DeepSeek as a first-class failover provider.
    assert "deepseek" in ModelGateway.MODEL_FAILOVER_PROVIDERS


def test_missing_deepseek_key_raises():
    from artpm_agent.utils.llm_client import is_valid_api_key

    assert not is_valid_api_key("")
    with pytest.raises(ValueError):
        create_llm_client(
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "deepseek_api_key": "",
            }
        )


def test_env_mapping_loads_deepseek_settings(monkeypatch):
    """DEEPSEEK_* env vars must flow into the llm config the gateway reads."""
    from artpm_agent.config import Config

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-env-key-123")
    monkeypatch.setenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "high")

    config = Config()

    llm = config.config["llm"]
    assert llm["provider"] == "deepseek"
    assert llm["deepseek_api_key"] == "sk-deepseek-env-key-123"
    assert llm["deepseek_api_base"] == "https://api.deepseek.com/v1"
    assert llm["reasoning_effort"] == "high"


def test_env_mapping_loads_vision_pairing_settings(monkeypatch):
    """LLM_VISION_* env vars must flow into the llm config so the gateway
    builds the dedicated vision client from the same source of truth."""
    from artpm_agent.config import Config

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("LLM_VISION_PROVIDER", "zhipu")
    monkeypatch.setenv("LLM_VISION_MODEL", "glm-4v-flash")
    monkeypatch.setenv("LLM_VISION_API_KEY", "vision-env-key-123")
    monkeypatch.setenv("LLM_VISION_API_BASE", "https://open.bigmodel.cn/api/paas/v4")

    config = Config()

    llm = config.config["llm"]
    assert llm["vision_provider"] == "zhipu"
    assert llm["vision_model"] == "glm-4v-flash"
    assert llm["vision_api_key"] == "vision-env-key-123"
    assert llm["vision_api_base"] == "https://open.bigmodel.cn/api/paas/v4"
