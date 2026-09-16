"""Pure resource/version validation and serialization helpers."""

from __future__ import annotations

from hashlib import sha256
import json
from collections.abc import Iterable, Mapping
from typing import Any


MAX_SEARCHABLE_TEXT_CHARS = 2_000_000
MAX_SEARCH_LIMIT = 100


def required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or None")
    normalized = value.strip()
    return normalized or None


def serialize_json(value: Any, field: str, *, mapping: bool = False) -> str:
    if value is None:
        value = {} if mapping else None
    if mapping and not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be JSON serializable") from error


def deserialize_json(value: str, fallback: Any = None) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def normalize_searchable_text(
    searchable_text: Any,
    structured_data_json: str,
    *,
    max_chars: int = MAX_SEARCHABLE_TEXT_CHARS,
) -> str:
    if searchable_text is None:
        searchable_text = ""
    if not isinstance(searchable_text, str):
        raise ValueError("searchable_text must be a string")
    normalized = searchable_text.strip()
    if not normalized and structured_data_json != "null":
        normalized = structured_data_json
    if not normalized:
        raise ValueError("searchable_text or structured_data is required")
    if len(normalized) > max_chars:
        raise ValueError(f"searchable_text exceeds {max_chars} characters")
    return normalized


def content_hash(searchable_text: str, structured_data_json: str, mime_type: str | None) -> str:
    payload = "\0".join([searchable_text, structured_data_json, mime_type or ""])
    return sha256(payload.encode("utf-8")).hexdigest()


def validate_limit(value: int, *, maximum: int = MAX_SEARCH_LIMIT) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


def normalize_filter(values: Iterable[str] | str | None, field: str) -> frozenset[str]:
    if values is None:
        return frozenset()
    if isinstance(values, str):
        values = [values]
    return frozenset(required_text(value, field) for value in values)


__all__ = [
    "MAX_SEARCHABLE_TEXT_CHARS",
    "MAX_SEARCH_LIMIT",
    "content_hash",
    "deserialize_json",
    "normalize_filter",
    "normalize_searchable_text",
    "optional_text",
    "required_text",
    "serialize_json",
    "validate_limit",
]
