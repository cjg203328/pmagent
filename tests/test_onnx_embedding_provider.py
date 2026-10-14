"""ONNX embedding provider contract.

The provider runs a real ``onnxruntime`` session, which would make these tests
depend on a model file and on onnxruntime being installed. Instead a fake session
is injected: the provider's own logic (input filtering, pooling, dimension checks,
normalization) is what needs pinning, and that is pure NumPy.

The real onnxruntime path was verified separately against a generated ONNX graph
— see the verification note in
``docs/roadmaps/memory-retrieval-optimization-plan.md``.
"""

from __future__ import annotations

import numpy as np
import pytest

from artpm_agent.memory.embeddings import (
    DeterministicEmbeddingProvider,
    OnnxEmbeddingProvider,
    create_embedding_provider,
)


class _Input:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSession:
    """Stands in for an onnxruntime InferenceSession.

    Returns ``output`` verbatim and records the feed it received, so tests can
    assert both the result handling and which inputs were forwarded.
    """

    def __init__(self, output, input_names=("input_ids",)) -> None:
        self._output = output
        self._input_names = input_names
        self.seen_feed: dict | None = None

    def get_inputs(self):
        return [_Input(name) for name in self._input_names]

    def run(self, _outputs, feed):
        self.seen_feed = dict(feed)
        return [np.asarray(self._output, dtype=np.float32)]


def _tokenizer(texts):
    """Map each character to a token id, padded to a rectangle."""
    rows = [[(ord(ch) % 50) + 1 for ch in str(text)] or [1] for text in texts]
    width = max(len(row) for row in rows)
    return {"input_ids": np.asarray([r + [0] * (width - len(r)) for r in rows])}


def _provider(output, *, dimension, input_names=("input_ids",), model="tiny"):
    return OnnxEmbeddingProvider(
        f"{model}.onnx",
        dimension=dimension,
        tokenizer=_tokenizer,
        model_name=model,
        session=_FakeSession(output, input_names),
    )


def test_vectors_are_normalized_to_unit_length():
    provider = _provider([[3.0, 4.0]], dimension=2)

    vector = provider.embed("ab")

    assert vector == pytest.approx([0.6, 0.8])


def test_identical_text_produces_identical_vectors():
    provider = _provider([[1.0, 2.0, 3.0]], dimension=3)

    assert provider.embed("项目") == provider.embed("项目")


def test_distinct_text_produces_distinct_vectors():
    class _PerCallSession(_FakeSession):
        def __init__(self):
            super().__init__([[1.0, 0.0]])
            self.calls = 0

        def run(self, _outputs, feed):
            self.calls += 1
            return [np.asarray([[1.0, float(self.calls)]], dtype=np.float32)]

    provider = OnnxEmbeddingProvider(
        "m.onnx", dimension=2, tokenizer=_tokenizer, session=_PerCallSession()
    )

    assert provider.embed("a") != provider.embed("b")


def test_batch_input_returns_one_vector_per_text():
    provider = _provider([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]], dimension=2)

    vectors = provider.embed_many(["a", "bb", "ccc"])

    assert len(vectors) == 3
    assert all(len(v) == 2 for v in vectors)


def test_empty_input_short_circuits_without_calling_the_model():
    session = _FakeSession([[1.0, 0.0]])
    provider = OnnxEmbeddingProvider(
        "m.onnx", dimension=2, tokenizer=_tokenizer, session=session
    )

    assert provider.embed_many([]) == []
    assert session.seen_feed is None


def test_token_level_output_is_mean_pooled():
    # Shape (batch=1, tokens=3, dim=2) must collapse to (1, 2).
    token_level = np.asarray([[[1.0, 1.0], [3.0, 3.0], [5.0, 5.0]]], dtype=np.float32)
    provider = _provider(token_level, dimension=2)

    vector = provider.embed("abc")

    # Mean of the token rows is [3, 3], normalized to ~[0.707, 0.707].
    assert vector == pytest.approx([0.7071, 0.7071], abs=1e-4)


def test_only_accepted_input_names_are_forwarded():
    session = _FakeSession([[1.0, 0.0]], input_names=("input_ids",))
    provider = OnnxEmbeddingProvider(
        "m.onnx", dimension=2, tokenizer=_tokenizer, session=session
    )

    provider.embed("abc")

    # attention_mask is produced by the tokenizer only when requested; a model
    # that does not declare it must not receive it.
    assert set(session.seen_feed or {}) <= {"input_ids"}


def test_dimension_mismatch_is_reported_clearly():
    provider = _provider([[1.0, 2.0, 3.0]], dimension=8)

    with pytest.raises(ValueError, match="expected 8"):
        provider.embed("abc")


def test_row_count_mismatch_is_reported_clearly():
    provider = _provider([[1.0, 0.0]], dimension=2)

    with pytest.raises(ValueError, match="rows for"):
        provider.embed_many(["a", "b"])


def test_fingerprint_includes_the_model_label_and_dimension():
    provider = _provider([[1.0, 0.0]], dimension=2, model="bge-small-zh")

    assert provider.fingerprint == "onnx-sentence-encoder-v1:bge-small-zh:2"


def test_zero_vector_does_not_produce_nan():
    provider = _provider([[0.0, 0.0]], dimension=2)

    vector = provider.embed("a")

    assert not any(np.isnan(v) for v in vector)


def test_missing_model_file_is_reported_with_the_setting_name():
    def tokenizer(_texts):
        return {"input_ids": [[1]]}

    with pytest.raises(FileNotFoundError, match="embedding_model_path"):
        OnnxEmbeddingProvider(
            "definitely-missing.onnx", dimension=2, tokenizer=tokenizer
        )


def test_factory_falls_back_when_the_model_file_is_not_a_valid_onnx_graph(tmp_path):
    """A wrong or corrupt model file must degrade visibly, not crash a turn."""
    model = tmp_path / "not-a-model.onnx"
    model.write_bytes(b"this is not an ONNX protobuf")

    provider = create_embedding_provider(
        {
            "embedding_provider": "onnx",
            "embedding_model_path": str(model),
            "embedding_dimension": 64,
        },
        tokenizer=_tokenizer,
    )

    assert isinstance(provider, DeterministicEmbeddingProvider)
    assert provider.fallback_reason
    assert "onnx provider unavailable" in provider.fallback_reason


def test_default_provider_remains_the_offline_one():
    """Selecting a neural provider must stay opt-in."""
    provider = create_embedding_provider({"embedding_dimension": 64})

    assert isinstance(provider, DeterministicEmbeddingProvider)
