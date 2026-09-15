"""Embedding providers used by the local vector-search runtime."""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Any, Iterable, Mapping, Protocol


class EmbeddingProvider(Protocol):
    """Small provider contract shared by memory and knowledge indexes."""

    dimension: int
    fingerprint: str

    def embed(self, text: str) -> list[float]:
        """Return one normalized embedding vector."""


class DeterministicEmbeddingProvider:
    """Offline character n-gram embeddings with a stable dimensionality.

    This provider keeps vector retrieval operational without sending workspace
    knowledge to a remote embedding API. A future remote or local neural
    provider can implement the same contract and use a distinct fingerprint.
    """

    VERSION = "char-ngram-feature-hash-v1"

    def __init__(self, dimension: int = 1536):
        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise TypeError("dimension must be an integer")
        if dimension < 64:
            raise ValueError("dimension must be at least 64")
        self.dimension = dimension
        self.fingerprint = f"{self.VERSION}:{dimension}"

    def embed(self, text: str) -> list[float]:
        normalized = str(text or "").casefold().strip()
        if not normalized:
            return [0.0] * self.dimension

        vector = [0.0] * self.dimension
        for ngram in self._ngrams(normalized):
            digest = hashlib.md5(
                ngram.encode("utf-8"), usedforsecurity=False
            ).digest()
            position, sign_value = struct.unpack("<II", digest[:8])
            index = position % self.dimension
            vector[index] += 1.0 if sign_value & 1 else -1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    def embed_many(self, texts: Iterable[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    @staticmethod
    def _ngrams(text: str) -> Iterable[str]:
        emitted = False
        for size in (3, 4, 5):
            for index in range(max(0, len(text) - size + 1)):
                emitted = True
                yield text[index:index + size]
        if not emitted:
            # Short Chinese labels and IDs still need a non-zero vector.
            yield text


def create_embedding_provider(
    config: Mapping[str, Any] | None = None,
) -> EmbeddingProvider:
    """Build the configured embedding provider without network side effects."""
    values = dict(config or {})
    provider = str(
        values.get("embedding_provider", "local_feature_hash")
    ).strip().lower()
    if provider not in {
        "local_feature_hash",
        "feature_hash",
        "deterministic",
    }:
        raise ValueError(f"Unsupported embedding provider: {provider}")
    try:
        dimension = int(values.get("embedding_dimension", 1536))
    except (TypeError, ValueError) as error:
        raise ValueError("embedding_dimension must be an integer") from error
    return DeterministicEmbeddingProvider(dimension=dimension)


__all__ = [
    "DeterministicEmbeddingProvider",
    "EmbeddingProvider",
    "create_embedding_provider",
]
