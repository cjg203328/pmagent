"""Embedding provider selection contract.

Two things must stay true:

1. The default stays offline and dependency-free. A fresh install with no API
   key and no model files must still get working vector recall.
2. A requested neural provider that cannot be built degrades *visibly*. The
   previous code documented "set llm_client and the client will be used" while
   never reading that parameter, so a misconfiguration looked identical to a
   working setup.
"""

from __future__ import annotations

import pytest

from artpm_agent.memory.embeddings import (
    DeterministicEmbeddingProvider,
    RemoteEmbeddingProvider,
    create_embedding_provider,
)


class _BatchClient:
    """Minimal client exposing the batch embedding contract."""

    def __init__(self, dimension: int = 4) -> None:
        self.dimension = dimension

    def embed_texts(self, texts):
        return [[1.0] + [0.0] * (self.dimension - 1) for _ in texts]


class _SingleClient:
    """Client exposing only the single-text method."""

    def embed(self, text: str):
        return [0.5, 0.5]


def test_default_provider_is_offline_and_dependency_free():
    provider = create_embedding_provider()

    assert isinstance(provider, DeterministicEmbeddingProvider)
    assert provider.dimension == 1536
    assert provider.fallback_reason is None


def test_default_provider_is_stable_for_the_same_text():
    provider = create_embedding_provider()

    assert provider.embed("项目进度") == provider.embed("项目进度")
    assert provider.embed("项目进度") != provider.embed("交付清单")


def test_offline_provider_is_lexical_not_semantic():
    """Document the known limitation rather than pretend it is semantic.

    If this ever starts passing as "similar", the offline provider has been
    replaced and the surrounding documentation needs updating too.
    """
    provider = create_embedding_provider()
    left = provider.embed("成本")
    right = provider.embed("开销")

    dot = sum(a * b for a, b in zip(left, right))
    assert dot == pytest.approx(0.0, abs=1e-6)


def test_remote_provider_is_built_when_a_client_is_supplied():
    client = _BatchClient(dimension=4)

    provider = create_embedding_provider(
        {"embedding_provider": "remote", "embedding_dimension": 4},
        llm_client=client,
    )

    assert isinstance(provider, RemoteEmbeddingProvider)
    assert provider.dimension == 4
    assert provider.embed("任意文本") == [1.0, 0.0, 0.0, 0.0]


def test_remote_provider_accepts_a_single_text_client():
    provider = create_embedding_provider(
        {"embedding_provider": "remote", "embedding_dimension": 2},
        llm_client=_SingleClient(),
    )

    assert provider.embed("x") == [0.5, 0.5]


def test_remote_request_without_a_client_falls_back_and_says_why():
    provider = create_embedding_provider({"embedding_provider": "remote"})

    # Degrades to offline rather than raising: a turn must not fail because an
    # optional neural provider is misconfigured.
    assert isinstance(provider, DeterministicEmbeddingProvider)
    assert provider.fallback_reason
    assert "no LLM client" in provider.fallback_reason


def test_onnx_request_without_a_model_path_falls_back_and_says_why():
    provider = create_embedding_provider({"embedding_provider": "onnx"})

    assert isinstance(provider, DeterministicEmbeddingProvider)
    assert provider.fallback_reason
    assert "embedding_model_path" in provider.fallback_reason


def test_onnx_request_without_a_tokenizer_falls_back_and_says_why():
    provider = create_embedding_provider(
        {"embedding_provider": "onnx", "embedding_model_path": "missing.onnx"}
    )

    assert isinstance(provider, DeterministicEmbeddingProvider)
    assert provider.fallback_reason


def test_onnx_request_with_a_missing_model_file_falls_back():
    def tokenizer(_texts):
        return {"input_ids": [[1, 2, 3]]}

    provider = create_embedding_provider(
        {"embedding_provider": "onnx", "embedding_model_path": "does-not-exist.onnx"},
        tokenizer=tokenizer,
    )

    assert isinstance(provider, DeterministicEmbeddingProvider)
    assert provider.fallback_reason
    assert "unavailable" in provider.fallback_reason


def test_unknown_provider_is_rejected_loudly():
    """A typo must not silently become the offline provider."""
    with pytest.raises(ValueError, match="Unsupported embedding provider"):
        create_embedding_provider({"embedding_provider": "bogus"})


def test_dimension_is_configurable_and_validated():
    provider = create_embedding_provider({"embedding_dimension": 256})
    assert provider.dimension == 256

    with pytest.raises(ValueError, match="must be at least 64"):
        create_embedding_provider({"embedding_dimension": 8})

    with pytest.raises(ValueError, match="must be an integer"):
        create_embedding_provider({"embedding_dimension": "wide"})


def test_fingerprint_differs_between_providers_so_indexes_cannot_mix():
    # 64 is the offline provider's minimum, so both providers share a dimension.
    offline = create_embedding_provider({"embedding_dimension": 64})
    remote = create_embedding_provider(
        {"embedding_provider": "remote", "embedding_dimension": 64},
        llm_client=_BatchClient(dimension=64),
    )

    # Same dimension, different vector space: the manifest must be able to tell
    # them apart or the index would be queried with incomparable vectors.
    assert offline.dimension == remote.dimension
    assert offline.fingerprint != remote.fingerprint


def test_remote_provider_rejects_a_dimension_mismatch():
    provider = create_embedding_provider(
        {"embedding_provider": "remote", "embedding_dimension": 4},
        llm_client=_BatchClient(dimension=4),
    )

    class _WrongClient:
        def embed_texts(self, texts):
            return [[1.0, 2.0] for _ in texts]

    mismatched = RemoteEmbeddingProvider(_WrongClient(), dimension=4)
    with pytest.raises(ValueError, match="expected 4"):
        mismatched.embed("x")

    # The correctly configured provider keeps working.
    assert len(provider.embed("x")) == 4


def test_remote_fingerprint_does_not_reuse_the_local_provider_label():
    """The default config's `embedding_model` names the *local* provider.

    Reusing it for a remote provider would produce a fingerprint like
    "remote-embedding-v1:char-ngram-feature-hash-v1:4", which misdescribes the
    vector space the index actually holds.
    """
    provider = create_embedding_provider(
        {
            "embedding_provider": "remote",
            "embedding_dimension": 4,
            "embedding_model": "char-ngram-feature-hash-v1",
        },
        llm_client=_BatchClient(dimension=4),
    )

    assert "char-ngram" not in provider.fingerprint
    assert provider.fingerprint == "remote-embedding-v1:_BatchClient:4"


def test_remote_fingerprint_uses_the_explicit_model_name_when_given():
    provider = create_embedding_provider(
        {
            "embedding_provider": "remote",
            "embedding_dimension": 4,
            "embedding_model_name": "text-embedding-3-small",
        },
        llm_client=_BatchClient(dimension=4),
    )

    assert provider.fingerprint == "remote-embedding-v1:text-embedding-3-small:4"
