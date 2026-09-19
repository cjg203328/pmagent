"""Typed provider DTOs shared with runtime and presentation adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelResponseMetadata:
    """Model selection metadata for the current request execution context."""

    model_id: str | None
    fallback_from: str | None = None


__all__ = ["ModelResponseMetadata"]
