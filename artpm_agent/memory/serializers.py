"""Stable DTO serialization for workspace knowledge records.

SQLite rows are an implementation detail.  These helpers accept mappings and
return plain dictionaries suitable for API/UI boundaries without leaking row
objects or mutable metadata references.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _copy_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    keys = getattr(value, "keys", None)
    if callable(keys):
        try:
            return {str(key): value[key] for key in keys()}
        except (KeyError, TypeError, IndexError):
            return {}
    return {}


def serialize_resource(row: Mapping[str, Any]) -> dict[str, Any]:
    result = _copy_mapping(row)
    metadata = result.get("metadata")
    if metadata is None:
        metadata = result.get("metadata_json")
    result["metadata"] = _copy_mapping(metadata)
    result.pop("metadata_json", None)
    return result


def serialize_version(row: Mapping[str, Any]) -> dict[str, Any]:
    result = _copy_mapping(row)
    structured = result.get("structured_data")
    if structured is None:
        structured = result.get("structured_data_json")
    result["structured_data"] = structured
    result.pop("structured_data_json", None)
    metadata = result.get("metadata")
    if metadata is None:
        metadata = result.get("metadata_json")
    result["metadata"] = _copy_mapping(metadata)
    result.pop("metadata_json", None)
    return result


def serialize_rule(row: Mapping[str, Any]) -> dict[str, Any]:
    result = _copy_mapping(row)
    metadata = result.get("metadata")
    if metadata is None:
        metadata = result.get("metadata_json")
    result["metadata"] = _copy_mapping(metadata)
    result.pop("metadata_json", None)
    return result


__all__ = ["serialize_resource", "serialize_rule", "serialize_version"]
