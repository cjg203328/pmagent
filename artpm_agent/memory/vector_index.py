"""Rebuildable vector-index primitives.

The SQLite-backed store owns synchronization and retrieval.  Chunking is kept
here because it is deterministic, bounded, and independent of the backend.
"""

from __future__ import annotations


DEFAULT_CHUNK_CHARS = 1200
DEFAULT_CHUNK_OVERLAP = 200
DEFAULT_MAX_CHUNKS = 64


def chunk_text(
    text: str,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
) -> list[str]:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if chunk_chars <= 0 or overlap < 0 or overlap >= chunk_chars:
        raise ValueError("overlap must be non-negative and smaller than chunk_chars")
    if max_chunks <= 0:
        raise ValueError("max_chunks must be positive")
    normalized = text.strip()
    if not normalized:
        return []
    step = chunk_chars - overlap
    chunks = [normalized[start : start + chunk_chars] for start in range(0, len(normalized), step)]
    return [chunk for chunk in chunks[:max_chunks] if chunk]


__all__ = [
    "DEFAULT_CHUNK_CHARS",
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_MAX_CHUNKS",
    "chunk_text",
]
