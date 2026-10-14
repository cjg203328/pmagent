"""Conversation compressor degradation contract.

The class docstring promises that an LLM failure falls back to truncation. These
tests pin that promise: without them the fallback can regress to "log an error
and return nothing", which silently leaves long conversations uncompressed in
offline deployments.
"""

from __future__ import annotations

from artpm_agent.memory.conversation_compressor import ConversationCompressor


def _dialog(turns: int = 4) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for index in range(turns):
        messages.append({"role": "user", "content": f"第 {index} 轮用户诉求"})
        messages.append({"role": "assistant", "content": f"第 {index} 轮助手回复"})
    return messages


def test_llm_exception_falls_back_to_recent_user_utterances():
    compressor = ConversationCompressor()

    def exploding_llm(_prompt: str) -> str:
        raise RuntimeError("provider unreachable")

    result = compressor.compress(_dialog(), exploding_llm)

    # The whole point: the window is still bounded when the model is gone.
    assert result.was_compressed is True
    assert result.degraded is True
    assert "provider unreachable" in (result.error or "")
    assert result.summary_text
    # Only user text is preserved; assistant prose is not carried over.
    assert "第 3 轮用户诉求" in result.summary_text
    assert "助手回复" not in result.summary_text


def test_empty_completion_is_treated_as_failure_not_a_summary():
    compressor = ConversationCompressor()

    result = compressor.compress(_dialog(), lambda _prompt: "   ")

    assert result.degraded is True
    assert result.was_compressed is True
    assert result.error == "LLM 返回空摘要"
    assert "第 3 轮用户诉求" in result.summary_text


def test_fallback_is_marked_so_it_is_distinguishable_from_a_real_summary():
    compressor = ConversationCompressor()
    good = compressor.compress(_dialog(), lambda _prompt: '{"summary": "正常摘要"}')
    bad = compressor.compress(_dialog(), lambda _prompt: (_ for _ in ()).throw(ValueError("x")))

    assert good.degraded is False
    assert bad.degraded is True
    assert good.summary_text != bad.summary_text


def test_fallback_keeps_only_the_most_recent_user_messages():
    compressor = ConversationCompressor(fallback_user_messages=2)
    messages = [
        {"role": "user", "content": "最早的需求"},
        {"role": "assistant", "content": "回复"},
        {"role": "user", "content": "中间的需求"},
        {"role": "assistant", "content": "回复"},
        {"role": "user", "content": "最新的需求"},
    ]

    result = compressor.compress(
        messages, lambda _prompt: (_ for _ in ()).throw(RuntimeError("down"))
    )

    assert "最新的需求" in result.summary_text
    assert "中间的需求" in result.summary_text
    assert "最早的需求" not in result.summary_text


def test_fallback_truncates_very_long_utterances():
    compressor = ConversationCompressor(fallback_message_chars=10)
    long_line = "很长的用户输入" * 50

    result = compressor.compress(
        [{"role": "user", "content": long_line}],
        lambda _prompt: (_ for _ in ()).throw(RuntimeError("down")),
    )

    assert "…" in result.summary_text
    # The retained line must be bounded, not the whole 350-character input.
    assert long_line not in result.summary_text


def test_fallback_without_any_user_message_reports_failure_without_fake_summary():
    compressor = ConversationCompressor()

    result = compressor.compress(
        [{"role": "assistant", "content": "只有助手消息"}],
        lambda _prompt: (_ for _ in ()).throw(RuntimeError("down")),
    )

    # Nothing trustworthy to keep, so an empty injection beats a wrong one.
    assert result.summary_text == ""
    assert result.was_compressed is False
    assert result.error


def test_successful_compression_is_unaffected_by_the_fallback():
    compressor = ConversationCompressor()

    result = compressor.compress(
        _dialog(), lambda _prompt: '{"summary": "真实摘要", "key_points": ["决策A"]}'
    )

    assert result.was_compressed is True
    assert result.degraded is False
    assert result.summary_text == "真实摘要"
    assert result.key_points == ["决策A"]


def test_degraded_summary_is_injected_into_the_compressed_context():
    """The fallback is only useful if build_compressed_context actually emits it."""
    compressor = ConversationCompressor()
    result = compressor.compress(
        _dialog(), lambda _prompt: (_ for _ in ()).throw(RuntimeError("down"))
    )

    context = compressor.build_compressed_context(
        [{"role": "user", "content": "最近一句"}], result
    )

    assert "[历史对话摘要]" in context
    assert "第 3 轮用户诉求" in context
    assert "最近一句" in context
