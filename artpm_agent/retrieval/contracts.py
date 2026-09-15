"""Stable, provider-neutral retrieval DTOs.

These types are shared by the API, Harness and future web/embed clients. They
keep workspace authorization explicit and prevent UI code from depending on
the internal shape returned by a vector store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping, Optional


def _frozen_mapping(value: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True, slots=True)
class SearchTarget:
    """Authorized search scope; workspace is never inferred from the query."""

    tenant_id: str
    workspace_id: str
    resource_types: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    include_rules: bool = True

    def __post_init__(self) -> None:
        for name in ("tenant_id", "workspace_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        for name in ("resource_types", "source_types"):
            values = tuple(
                item.strip()
                for item in getattr(self, name)
                if isinstance(item, str) and item.strip()
            )
            object.__setattr__(self, name, tuple(dict.fromkeys(values)))
        if not isinstance(self.include_rules, bool):
            raise TypeError("include_rules must be a boolean")


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    """Bounded plan executed by one retriever for one turn."""

    query: str
    target: SearchTarget
    limit: int = 10
    max_text_chars: int = 4000
    confidence_floor: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be a non-empty string")
        object.__setattr__(self, "query", self.query.strip())
        if len(self.query) > 4000:
            raise ValueError("query cannot exceed 4000 characters")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if isinstance(self.max_text_chars, bool) or not isinstance(self.max_text_chars, int) or not 100 <= self.max_text_chars <= 20_000:
            raise ValueError("max_text_chars must be between 100 and 20000")
        if isinstance(self.confidence_floor, bool) or not isinstance(
            self.confidence_floor, (int, float)
        ):
            raise ValueError("confidence_floor must be between 0 and 1")
        try:
            normalized_floor = float(self.confidence_floor)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("confidence_floor must be between 0 and 1") from error
        if not isfinite(normalized_floor) or not 0.0 <= normalized_floor <= 1.0:
            raise ValueError("confidence_floor must be between 0 and 1")
        object.__setattr__(self, "confidence_floor", normalized_floor)


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    """Normalized result suitable for citations and API responses."""

    id: str
    workspace_id: str
    tenant_id: str
    title: str
    text: str
    score: float
    match_type: str
    source_uri: str = ""
    resource_type: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        for name in ("id", "workspace_id", "tenant_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if isinstance(self.score, bool):
            raise TypeError("score must be a finite number")
        try:
            score = float(self.score)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("score must be a finite number") from error
        if not isfinite(score):
            raise ValueError("score must be a finite number")
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))

    @property
    def citation(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "title": self.title,
            "source_uri": self.source_uri,
            "resource_type": self.resource_type,
            "score": self.score,
            "match_type": self.match_type,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.citation,
            "tenant_id": self.tenant_id,
            "text": self.text,
            "metadata": dict(self.metadata),
        }
