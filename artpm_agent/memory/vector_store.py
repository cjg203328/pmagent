"""Vector backend selector: Qdrant first, local FAISS fallback."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .faiss_vector_store import VectorStore as FaissVectorStore


class VectorStore:
    """Compatibility facade selecting a network-native vector backend."""

    def __init__(
        self,
        store_path: str | Path,
        *,
        dimension: int = 1536,
        embedding_fingerprint: str = "unspecified:1536",
    ) -> None:
        self.fallback_reason: str | None = None
        self._backend: Any = None
        self._remote_backend = False
        self._fallback_options = {
            "store_path": store_path,
            "dimension": dimension,
            "embedding_fingerprint": embedding_fingerprint,
        }
        qdrant_url = os.getenv("QDRANT_URL", "").strip()
        requested = os.getenv("VECTOR_BACKEND", "qdrant" if qdrant_url else "faiss")
        if requested.casefold() == "qdrant" and qdrant_url:
            try:
                from .qdrant_vector_store import QdrantVectorStore

                self._backend = QdrantVectorStore(
                    url=qdrant_url,
                    api_key=os.getenv("QDRANT_API_KEY") or None,
                    collection=os.getenv("QDRANT_COLLECTION", "artpm_memory"),
                    dimension=dimension,
                    embedding_fingerprint=embedding_fingerprint,
                    timeout=float(os.getenv("QDRANT_TIMEOUT_SECONDS", "5")),
                )
                self._remote_backend = True
            except Exception as error:
                self.fallback_reason = f"Qdrant unavailable: {error}"
        if self._backend is None:
            self._activate_faiss()

    def _activate_faiss(self, error: Exception | None = None) -> None:
        if error is not None:
            self.fallback_reason = f"Qdrant runtime failure: {error}"
        self._backend = FaissVectorStore(**self._fallback_options)
        self._remote_backend = False

    def _invoke(self, name: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._backend, name)(*args, **kwargs)
        except Exception as error:
            if self._remote_backend:
                self._activate_faiss(error)
                return getattr(self._backend, name)(*args, **kwargs)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    @property
    def count(self) -> int:
        try:
            return int(self._backend.count)
        except Exception as error:
            if not self._remote_backend:
                raise
            self._activate_faiss(error)
            return int(self._backend.count)

    @property
    def available(self) -> bool:
        return bool(self._backend.available)

    @available.setter
    def available(self, value: bool) -> None:
        self._backend.available = bool(value)

    @property
    def needs_rebuild(self) -> bool:
        return bool(self._backend.needs_rebuild)

    @needs_rebuild.setter
    def needs_rebuild(self, value: bool) -> None:
        self._backend.needs_rebuild = value

    @property
    def last_error(self) -> str | None:
        return self.fallback_reason or self._backend.last_error

    @last_error.setter
    def last_error(self, value: str | None) -> None:
        self._backend.last_error = value

    def status(self) -> dict[str, Any]:
        status = dict(self._invoke("status"))
        status.setdefault("backend", "qdrant" if self._remote_backend else "faiss")
        status["fallback_reason"] = self.fallback_reason
        return status

    def add(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke("add", *args, **kwargs)

    def upsert(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke("upsert", *args, **kwargs)

    def sync(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke("sync", *args, **kwargs)

    def replace(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke("replace", *args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke("delete", *args, **kwargs)

    def list_entries(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke("list_entries", *args, **kwargs)

    def search(self, *args: Any, **kwargs: Any) -> Any:
        return self._invoke("search", *args, **kwargs)

    def clear(self) -> Any:
        return self._invoke("clear")


__all__ = ["VectorStore", "FaissVectorStore"]
