"""Tests for the vision pairing ("eyes model") in ModelGateway.

A dedicated vision model (e.g. GLM-4V-Flash) handles image requests
independently of the text primary model, so a non-vision primary (DeepSeek)
can still understand images.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from artpm_agent.providers.gateway import ModelGateway


def _vision_client(answer="图片分析结果"):
    client = Mock()
    client.chat_with_images.return_value = answer
    client.stream_chat_with_images = Mock(return_value=iter(["流式", "结果"]))
    return client


def _gateway(**overrides):
    llm_config = {
        "model": "deepseek-v4-pro",
        "provider": "custom",
        "openai_api_key": "sk-main-key",
        "vision_provider": "zhipu",
        "vision_model": "glm-4v-flash",
        "zhipu_api_key": "zhipu-key",
        "max_tokens": 2000,
        **overrides,
    }
    primary_client = overrides.pop("primary_client", Mock())
    factory = overrides.pop("_factory", None)
    return ModelGateway(
        llm_config,
        primary_client,
        client_factory=factory or (lambda cfg: _vision_client()),
    )


def test_has_vision_pairing_when_configured():
    g = _gateway()
    assert g.has_vision_pairing is True
    assert g._vision_model == "glm-4v-flash"
    assert g._vision_provider == "zhipu"


def test_no_vision_pairing_without_vision_model():
    g = _gateway(vision_model="")
    assert g.has_vision_pairing is False


def test_image_request_uses_dedicated_vision_client():
    captured = {}

    def factory(cfg):
        captured["cfg"] = cfg
        return _vision_client()

    g = _gateway(_factory=factory)

    result = g.chat_with_failover(
        "图里有什么？", "sys", [], image_paths=["C:/x.png"]
    )

    assert result == "图片分析结果"
    vision_client = g.vision_client()
    assert vision_client.chat_with_images.called
    # The vision client config points at the vision provider/model with the
    # provider-specific key, never the text primary.
    cfg = captured["cfg"]
    assert cfg["provider"] == "zhipu"
    assert cfg["model"] == "glm-4v-flash"
    assert cfg["zhipu_api_key"] == "zhipu-key"
    assert cfg["max_tokens"] <= 1024  # GLM-4V-Flash output cap


def test_text_request_never_touches_vision_client():
    primary = Mock()
    primary.chat.return_value = "文本回复"
    g = _gateway(primary_client=primary)
    g.chat_with_failover("普通文本问题", "sys", [], image_paths=None)
    assert g.vision_client() is None or not g.vision_client().chat_with_images.called


def test_vision_client_built_lazily_once():
    calls = []

    def factory(cfg):
        calls.append(1)
        return _vision_client()

    g = _gateway(_factory=factory)

    assert calls == []
    g.vision_client()
    g.vision_client()
    assert len(calls) == 1


def test_vision_client_failure_raises_clear_error():
    def factory(cfg):
        raise RuntimeError("no vision")

    g = _gateway(_factory=factory)
    with pytest.raises(RuntimeError, match="视觉模型未配置或不可用"):
        g.chat_with_failover("x", "sys", [], image_paths=["C:/x.png"])


def test_vision_config_falls_back_to_provider_defaults():
    def factory(cfg):
        return _vision_client()

    g = ModelGateway(
        {
            "model": "deepseek-v4-pro",
            "provider": "custom",
            "vision_model": "glm-4v-flash",
            "vision_provider": "zhipu",
            # no zhipu_api_key: vision_api_key wins
            "vision_api_key": "vision-special-key",
        },
        Mock(),
        client_factory=factory,
    )
    cfg = g._vision_client_config()
    assert cfg["zhipu_api_key"] == "vision-special-key"
    assert cfg["zhipu_api_base"] == "https://open.bigmodel.cn/api/paas/v4"


def test_image_stream_uses_vision_client_and_yields_chunks():
    g = _gateway()
    chunks = list(
        g.stream_with_failover(
            "图里有什么？", "sys", [], image_paths=["C:/x.png"]
        )
    )
    assert chunks == ["流式", "结果"]


def test_image_request_cache_hit_skips_client():
    calls = {"n": 0}

    def factory(cfg):
        calls["n"] += 1
        return _vision_client()

    g = _gateway(_factory=factory)
    g.chat_with_failover("图里有什么？", "sys", [], image_paths=["C:/x.png"])
    cached = g.chat_with_failover("图里有什么？", "sys", [], image_paths=["C:/x.png"])
    assert cached == "图片分析结果"
    assert calls["n"] == 1  # vision client built once; second call served from cache
