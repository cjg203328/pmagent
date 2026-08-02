"""Regression coverage for per-conversation compression cursors."""

from artpm_agent.harness.memory_retrieval import _compression_injector
from artpm_agent.memory.conversation_compressor import ConversationCompressor


class _KnowledgeStore:
    def __init__(self) -> None:
        self.ingestions = []

    def ingest_resource(self, **kwargs):
        self.ingestions.append(kwargs)
        return {"id": str(len(self.ingestions))}


def _messages(count: int = 11):
    return [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"m{index}"}
        for index in range(count)
    ]


def _summary(_prompt: str) -> str:
    return (
        '{"summary":"done","key_points":[],"user_preferences":[],'
        '"entities":[]}'
    )


def test_reused_injector_does_not_recompress_the_same_message_window() -> None:
    store = _KnowledgeStore()
    first = _compression_injector(store, runtime=None)
    second = _compression_injector(store, runtime=None)
    first.compressor = ConversationCompressor(
        max_messages=10,
        max_tokens=100_000,
        min_compress_interval_min=30,
        min_new_messages=2,
    )
    calls = []

    def summarize(prompt: str) -> str:
        calls.append(prompt)
        return _summary(prompt)

    assert first is second
    assert first.compression_block(_messages(), "conversation-1", summarize)
    assert second.compression_block(_messages(), "conversation-1", summarize) == ""
    assert len(calls) == 1


def test_compression_cursor_is_isolated_by_workspace() -> None:
    store = _KnowledgeStore()
    injector = _compression_injector(store, runtime=None)
    injector.compressor = ConversationCompressor(
        max_messages=10,
        max_tokens=100_000,
        min_compress_interval_min=30,
        min_new_messages=2,
    )
    calls = []

    def summarize(prompt: str) -> str:
        calls.append(prompt)
        return _summary(prompt)

    injector.compression_block(
        _messages(), "shared-conversation", summarize, workspace_id="workspace-a"
    )
    injector.compression_block(
        _messages(), "shared-conversation", summarize, workspace_id="workspace-b"
    )

    assert len(calls) == 2
