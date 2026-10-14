"""Embedding providers used by the local vector-search runtime.

Three providers share one contract so the vector index, the knowledge store and
the intent router can swap them without knowing which is active:

``local_feature_hash``
    Offline character n-gram feature hashing. Deterministic, dependency-free and
    the default. It has no synonym understanding — "成本" and "开销" share no
    n-gram — but it never blocks on network or model files.

``onnx``
    A local neural model executed through ``onnxruntime``. Understands synonyms
    and needs no network. Requires the operator to supply model weights and a
    tokenizer; neither ships with this repository.

``remote``
    An embedding API reached through an LLM client. Understands synonyms but
    needs network and credentials.

Every provider declares its own ``fingerprint``. The FAISS manifest records that
fingerprint and refuses to query an index built by a different provider
(``faiss_vector_store.py``), because vectors from different models are not
comparable — mixing them would return confident nonsense instead of an error.
"""

from __future__ import annotations

import hashlib
import math
import struct
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence


class EmbeddingProvider(Protocol):
    """Small provider contract shared by memory and knowledge indexes."""

    dimension: int
    fingerprint: str

    def embed(self, text: str) -> list[float]:
        """Return one normalized embedding vector."""
        raise NotImplementedError


class DeterministicEmbeddingProvider:
    """Offline character n-gram embeddings with a stable dimensionality.

    This provider keeps vector retrieval operational without sending workspace
    knowledge to a remote embedding API. It is intentionally the default: a
    fresh install with no API key and no model files still gets working recall.

    Limitation worth stating plainly: matching is lexical. Paraphrases that share
    no characters ("成本" vs "开销") score zero. Configure ``onnx`` or ``remote``
    when synonym recall matters more than zero-config operation.
    """

    VERSION = "char-ngram-feature-hash-v1"

    def __init__(self, dimension: int = 1536):
        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise TypeError("dimension must be an integer")
        if dimension < 64:
            raise ValueError("dimension must be at least 64")
        self.dimension = dimension
        self.fingerprint = f"{self.VERSION}:{dimension}"
        #: Set when a requested provider was unavailable and this one was used
        #: instead, so callers can surface why they did not get what they asked
        #: for rather than silently degrading.
        self.fallback_reason: str | None = None

    def embed(self, text: str) -> list[float]:
        normalized = str(text or "").casefold().strip()
        if not normalized:
            return [0.0] * self.dimension

        vector = [0.0] * self.dimension
        for ngram in self._ngrams(normalized):
            # Feature hashing for the offline embedding fallback; the digest is
            # used as a bucket index, so a non-cryptographic hash is intended.
            digest = hashlib.md5(ngram.encode("utf-8"), usedforsecurity=False).digest()
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


class OnnxEmbeddingProvider:
    """Local neural embeddings executed by ``onnxruntime``.

    No network, no API key. Model weights and a tokenizer are supplied by the
    operator because neither is bundled here: the model is tens of megabytes and
    its licence belongs to the publisher, not this repository.

    The tokenizer is injected rather than hard-coded so the provider does not
    take a dependency on a specific tokenizer library. A callable receiving text
    and returning a mapping of numpy-compatible arrays (``input_ids``,
    ``attention_mask``, and ``token_type_ids`` when the model expects it) is
    sufficient.
    """

    VERSION = "onnx-sentence-encoder-v1"

    def __init__(
        self,
        model_path: str | Path,
        *,
        dimension: int,
        tokenizer: Callable[[Sequence[str]], Mapping[str, Any]],
        model_name: str = "",
        session: Any = None,
    ) -> None:
        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise TypeError("dimension must be an integer")
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        if not callable(tokenizer):
            raise TypeError("tokenizer must be a callable producing model inputs")

        self.model_path = Path(model_path).expanduser()
        if session is None:
            # Only the real load path needs the file to exist; an injected
            # session is already open, so tests need no model on disk.
            if not self.model_path.is_file():
                raise FileNotFoundError(
                    f"ONNX embedding model not found: {self.model_path}. Set "
                    "memory.embedding_model_path to a local .onnx file."
                )
            session = _open_onnx_session(self.model_path)

        self.dimension = dimension
        self._tokenizer = tokenizer
        label = model_name.strip() or self.model_path.stem
        self.fingerprint = f"{self.VERSION}:{label}:{dimension}"
        self.fallback_reason: str | None = None
        self._session = session

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: Iterable[str]) -> list[list[float]]:
        items = [str(text or "") for text in texts]
        if not items:
            return []
        import numpy as np  # noqa: PLC0415 - optional dependency

        inputs = self._tokenizer(items)
        accepted = {item.name for item in self._session.get_inputs()}
        feed = {
            name: np.asarray(value)
            for name, value in dict(inputs).items()
            if name in accepted
        }
        output = self._session.run(None, feed)[0]
        matrix = np.asarray(output, dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        # Sentence encoders commonly return pooled output, but token-level output
        # is also common; mean pooling makes both shapes usable.
        if matrix.ndim == 3 and matrix.shape[0] == len(items):
            matrix = matrix.mean(axis=1)
        if matrix.shape[0] != len(items):
            raise ValueError(
                f"ONNX model returned {matrix.shape[0]} rows for {len(items)} inputs"
            )
        if matrix.shape[1] != self.dimension:
            raise ValueError(
                f"ONNX model returned dimension {matrix.shape[1]}, "
                f"expected {self.dimension}"
            )
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        np.divide(matrix, norms, out=matrix, where=norms > 0)
        return [row.tolist() for row in matrix]


def _open_onnx_session(model_path: Path) -> Any:
    """Open a CPU onnxruntime session, or explain what is missing.

    onnxruntime's exceptions derive straight from ``Exception`` with no shared
    base (``InvalidProtobuf``, ``EngineError``, ``RuntimeException``, ...), so a
    corrupt or incompatible model file cannot be caught by type. Converting here
    — at the single point where the file is loaded — lets callers degrade on one
    predictable exception instead of guessing at onnxruntime's hierarchy.
    """
    try:
        import onnxruntime  # noqa: PLC0415 - optional dependency
    except ImportError as error:  # pragma: no cover - depends on install
        raise ImportError(
            "onnxruntime is required for the onnx embedding provider. "
            "Install it with: pip install onnxruntime"
        ) from error

    try:
        return onnxruntime.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
    except Exception as error:  # noqa: BLE001 - see docstring
        raise RuntimeError(
            f"failed to load ONNX model {model_path}: {error}"
        ) from error


class RemoteEmbeddingProvider:
    """Embeddings from a remote API, reached through an LLM client.

    Accepts either a batch method (``embed_texts``/``embed_many``) or a
    single-text method (``embed``). The dimension must be configured explicitly:
    probing the API at construction time would make provider creation block on
    the network, and the index manifest needs the dimension before any call.
    """

    VERSION = "remote-embedding-v1"

    def __init__(
        self,
        client: Any,
        *,
        dimension: int,
        model: str = "",
    ) -> None:
        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise TypeError("dimension must be an integer")
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        if client is None:
            raise TypeError("client must be an embedding-capable object")

        batch = getattr(client, "embed_texts", None) or getattr(
            client, "embed_many", None
        )
        single = getattr(client, "embed", None)
        if not callable(batch) and not callable(single):
            raise TypeError(
                "client must expose embed_texts(list[str]) or embed(str)"
            )

        self.dimension = dimension
        self._client = client
        self._batch: Callable[[Sequence[str]], Any] | None = (
            batch if callable(batch) else None
        )
        self._single: Callable[[str], Any] | None = (
            single if callable(single) else None
        )
        label = model.strip() or type(client).__name__
        self.fingerprint = f"{self.VERSION}:{label}:{dimension}"
        self.fallback_reason: str | None = None

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: Iterable[str]) -> list[list[float]]:
        items = [str(text or "") for text in texts]
        if not items:
            return []
        if self._batch is not None:
            raw = self._batch(items)
        else:
            single = self._single
            if single is None:  # pragma: no cover - guarded in __init__
                raise RuntimeError("no embedding method is available on the client")
            raw = [single(item) for item in items]
        vectors = [[float(value) for value in vector] for vector in raw]
        for vector in vectors:
            if len(vector) != self.dimension:
                raise ValueError(
                    f"Remote embedding returned dimension {len(vector)}, "
                    f"expected {self.dimension}"
                )
        return vectors


#: Provider names accepted from configuration.
LOCAL_PROVIDER_NAMES = frozenset(
    {"local_feature_hash", "feature_hash", "deterministic", ""}
)
ONNX_PROVIDER_NAMES = frozenset({"onnx", "onnx_local", "local_onnx"})
REMOTE_PROVIDER_NAMES = frozenset({"remote", "api", "llm"})


def create_embedding_provider(
    config: Mapping[str, Any] | None = None,
    *,
    llm_client: Any = None,
    tokenizer: Callable[[Sequence[str]], Mapping[str, Any]] | None = None,
) -> EmbeddingProvider:
    """Build the configured embedding provider.

    Defaults to the offline feature-hash provider. A requested ``onnx`` or
    ``remote`` provider that cannot be constructed falls back to the local
    provider with ``fallback_reason`` set, so a misconfiguration degrades
    visibly instead of crashing a turn or silently pretending to be neural.
    """
    values = dict(config or {})
    requested = str(values.get("embedding_provider", "local_feature_hash")).strip().lower()
    dimension = _resolve_dimension(values, default=1536)

    if requested in LOCAL_PROVIDER_NAMES:
        return DeterministicEmbeddingProvider(dimension=dimension)

    if requested in ONNX_PROVIDER_NAMES:
        model_path = str(
            values.get("embedding_model_path") or values.get("embedding_model") or ""
        ).strip()
        if not model_path:
            return _local_fallback(
                dimension, "onnx provider requested without memory.embedding_model_path"
            )
        if tokenizer is None:
            return _local_fallback(
                dimension,
                "onnx provider requires a tokenizer; none is available in this "
                "environment (install a tokenizer library and pass it in)",
            )
        try:
            return OnnxEmbeddingProvider(
                model_path,
                dimension=dimension,
                tokenizer=tokenizer,
                model_name=str(values.get("embedding_model_name") or ""),
            )
        except (
            FileNotFoundError,
            ImportError,
            RuntimeError,
            TypeError,
            ValueError,
            OSError,
        ) as error:
            return _local_fallback(dimension, f"onnx provider unavailable: {error}")

    if requested in REMOTE_PROVIDER_NAMES:
        if llm_client is None:
            return _local_fallback(
                dimension, "remote provider requested but no LLM client is configured"
            )
        try:
            return RemoteEmbeddingProvider(
                llm_client,
                dimension=dimension,
                # Deliberately not `embedding_model`: the default config sets that
                # to the *local* provider's identifier, so reusing it here would
                # label an API provider as "char-ngram-feature-hash-v1" and make
                # the fingerprint lie about which vector space the index holds.
                model=str(values.get("embedding_model_name") or ""),
            )
        except (TypeError, ValueError) as error:
            return _local_fallback(dimension, f"remote provider unavailable: {error}")

    raise ValueError(f"Unsupported embedding provider: {requested}")


def _resolve_dimension(values: Mapping[str, Any], *, default: int) -> int:
    raw = values.get("embedding_dimension", default)
    try:
        dimension = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("embedding_dimension must be an integer") from error
    if isinstance(raw, bool):
        raise ValueError("embedding_dimension must be an integer")
    return dimension


def _local_fallback(dimension: int, reason: str) -> DeterministicEmbeddingProvider:
    provider = DeterministicEmbeddingProvider(dimension=dimension)
    provider.fallback_reason = reason
    return provider


__all__ = [
    "DeterministicEmbeddingProvider",
    "EmbeddingProvider",
    "OnnxEmbeddingProvider",
    "RemoteEmbeddingProvider",
    "create_embedding_provider",
]
