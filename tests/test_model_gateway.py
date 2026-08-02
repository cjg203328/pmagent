"""Contract tests for providers.gateway.ModelGateway.

These prove the model-failover boundary can be exercised with only a config dict
and a client factory — no Streamlit, database, or Skill wiring (PI_ARCHITECTURE_ADOPTION.md
Phase 2 completion criterion: "一个 Provider Adapter 的输入输出可在不初始化 Streamlit、
数据库或 Skill 的情况下测试").
"""

import pytest
from unittest.mock import Mock

from artpm_agent.providers.gateway import ModelGateway


def _gateway(
    primary="deepseek-v4-flash",
    provider="custom",
    available=None,
    primary_client=None,
    factory=None,
    failover_max_attempts=None,
):
    llm_config = {"model": primary, "provider": provider}
    if available is not None:
        llm_config["available_models"] = available
    if failover_max_attempts is not None:
        llm_config["failover_max_attempts"] = failover_max_attempts
    return ModelGateway(
        llm_config,
        primary_client,
        client_factory=factory or (lambda _cfg: Mock()),
    )


def test_primary_model_id_reads_config():
    assert _gateway(primary="abc").primary_model_id() == "abc"


def test_fallback_prefers_same_model_family():
    g = _gateway(
        primary="deepseek-v4-flash",
        available=["qwen3.5", "deepseek-v4-pro", "glm-5.2"],
    )
    # Same-family candidates sort first; ties broken alphabetically.
    assert g.fallback_model_ids("deepseek-v4-flash") == [
        "deepseek-v4-pro",
        "glm-5.2",
        "qwen3.5",
    ]


def test_fallback_disabled_for_non_allowlisted_provider():
    g = _gateway(primary="a", provider="unknown", available=["b", "c"])
    assert g.fallback_model_ids("a") == []


def test_fallback_skips_unavailable_models():
    g = _gateway(primary="a", available=["b", "c", "d"])
    g.mark_model_unavailable("b")
    g.mark_model_unavailable("c")
    assert g.fallback_model_ids("a") == ["d"]


def test_cooldown_marks_unavailable_then_healthy():
    g = _gateway(primary="a")
    assert g.is_model_available("a")
    g.mark_model_unavailable("a")
    assert not g.is_model_available("a")
    g.mark_model_healthy("a")
    assert g.is_model_available("a")


def test_chat_with_failover_retries_to_fallback():
    primary = Mock()
    primary.chat.side_effect = TimeoutError("timed out")
    fallback = Mock()
    fallback.chat.return_value = "备用回答"
    factory = Mock(side_effect=[fallback])
    g = _gateway(
        primary="deepseek-v4-flash",
        available=["deepseek-v4-pro"],
        primary_client=primary,
        factory=factory,
    )

    resp = g.chat_with_failover("hi", "sys", [])

    assert "deepseek-v4-pro" in resp
    assert resp.endswith("备用回答")
    assert g.last_response_model == "deepseek-v4-pro"
    assert g.last_model_fallback_from == "deepseek-v4-flash"
    assert primary.chat.call_count == 1
    assert fallback.chat.call_count == 1


def test_chat_with_failover_tries_every_available_candidate():
    primary = Mock()
    primary.chat.side_effect = TimeoutError("primary timeout")
    first_fallback = Mock()
    first_fallback.chat.side_effect = TimeoutError("fallback timeout")
    second_fallback = Mock()
    second_fallback.chat.return_value = "final answer"
    g = _gateway(
        primary="a",
        available=["b", "c"],
        primary_client=primary,
        factory=Mock(side_effect=[first_fallback, second_fallback]),
        failover_max_attempts=3,
    )

    response = g.chat_with_failover("hi", "sys", [])

    assert response.endswith("final answer")
    assert g.last_response_model == "c"


def test_chat_with_failover_caps_total_attempts_by_default():
    primary = Mock()
    primary.chat.side_effect = TimeoutError("primary timeout")
    first_fallback = Mock()
    first_fallback.chat.side_effect = TimeoutError("fallback timeout")
    unused_fallback = Mock()
    g = _gateway(
        primary="a",
        available=["b", "c", "d"],
        primary_client=primary,
        factory=Mock(side_effect=[first_fallback, unused_fallback]),
    )

    with pytest.raises(RuntimeError, match="模型请求失败"):
        g.chat_with_failover("hi", "sys", [])

    assert primary.chat.call_count == 1
    assert first_fallback.chat.call_count == 1
    assert unused_fallback.chat.call_count == 0


def test_runtime_model_override_builds_client_for_selected_model():
    original = Mock()
    selected = Mock()
    selected.chat.return_value = "selected answer"
    factory = Mock(return_value=selected)
    g = _gateway(
        primary="a",
        available=["a", "b"],
        primary_client=original,
        factory=factory,
    )

    g._llm_config["model"] = "b"

    assert g.chat_with_failover("hi", "sys", []) == "selected answer"
    original.chat.assert_not_called()
    selected.chat.assert_called_once()
    assert factory.call_args.args[0]["model"] == "b"


def test_fallback_client_uses_shorter_request_timeout():
    primary = Mock()
    primary.chat.side_effect = TimeoutError("primary timeout")
    fallback = Mock()
    fallback.chat.return_value = "fallback answer"
    factory = Mock(return_value=fallback)
    g = ModelGateway(
        {
            "model": "a",
            "provider": "custom",
            "available_models": ["b"],
            "request_timeout_seconds": 12,
            "failover_request_timeout_seconds": 8,
        },
        primary,
        client_factory=factory,
    )

    assert g.chat_with_failover("hi", "sys", []).endswith("fallback answer")
    assert factory.call_args.args[0]["request_timeout_seconds"] == 8


def test_model_not_found_404_can_fail_over_to_configured_model():
    class ModelNotFoundError(RuntimeError):
        status_code = 404

    primary = Mock()
    primary.chat.side_effect = ModelNotFoundError("model not found")
    fallback = Mock()
    fallback.chat.return_value = "available model"
    g = _gateway(
        primary="retired-model",
        available=["available-model"],
        primary_client=primary,
        factory=Mock(return_value=fallback),
    )

    assert g.chat_with_failover("hi", "sys", []).endswith("available model")


def test_chat_with_failover_non_retryable_raises():
    primary = Mock()
    primary.chat.side_effect = RuntimeError("Invalid API key")
    g = _gateway(
        primary="deepseek-v4-flash",
        available=["deepseek-v4-pro"],
        primary_client=primary,
    )

    with pytest.raises(RuntimeError):
        g.chat_with_failover("hi", "sys", [])


def test_chat_with_failover_busy_when_all_circuits_open():
    g = _gateway(primary="a", available=["b", "c"])
    for model_id in ["a", "b", "c"]:
        g.mark_model_unavailable(model_id)

    with pytest.raises(RuntimeError, match="服务繁忙"):
        g.chat_with_failover("hi", "sys", [])


def test_stream_with_failover_before_first_chunk():
    primary = Mock()
    primary.stream_chat.side_effect = TimeoutError("timed out")
    fallback = Mock()
    fallback.stream_chat.return_value = iter(["备用", "回答"])
    factory = Mock(side_effect=[fallback])
    g = _gateway(
        primary="deepseek-v4-flash",
        available=["deepseek-v4-pro"],
        primary_client=primary,
        factory=factory,
    )

    chunks = list(g.stream_with_failover("hi", "sys", []))

    assert chunks[0].startswith("已切换备用模型：`deepseek-v4-pro`")
    assert "".join(chunks).endswith("备用回答")
    assert g.last_response_model == "deepseek-v4-pro"


def test_stream_with_failover_no_failover_after_partial_output():
    def partial(*_args, **_kwargs):
        yield "前半句"
        raise TimeoutError("timed out")

    primary = Mock()
    primary.stream_chat.side_effect = partial
    fallback = Mock()
    fallback.stream_chat.return_value = iter(["不应输出"])
    g = _gateway(
        primary="deepseek-v4-flash",
        available=["deepseek-v4-pro"],
        primary_client=primary,
        factory=Mock(side_effect=[fallback]),
    )

    stream = g.stream_with_failover("hi", "sys", [])
    assert next(stream) == "前半句"
    with pytest.raises(RuntimeError, match="模型请求失败"):
        next(stream)
    assert fallback.stream_chat.call_count == 0


def test_vision_fallback_prefers_likely_vision_model():
    g = _gateway(primary="deepseek-v4-flash", available=["qwen3.5", "glm-4v"])
    assert g.vision_fallback_model_ids("deepseek-v4-flash") == ["glm-4v"]


def test_vision_fallback_uses_explicit_config():
    g = ModelGateway({"model": "a", "vision_model": "custom-vision"}, None)
    assert g.vision_fallback_model_ids("a") == ["custom-vision"]
