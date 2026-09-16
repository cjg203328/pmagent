"""Pure validation for approved workspace rules."""

from __future__ import annotations

from typing import Any

RULE_STATUSES = frozenset({"proposed", "accepted", "rejected", "revoked"})
CONFIRMER_TYPES = frozenset({"user", "admin"})


def normalize_rule_statement(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("statement must be a non-empty string")
    return value.strip()


def validate_rule_status(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized not in RULE_STATUSES:
        raise ValueError(f"unsupported rule status: {value}")
    return normalized


def validate_confirmer_type(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized not in CONFIRMER_TYPES:
        raise ValueError(f"unsupported confirmer type: {value}")
    return normalized


__all__ = [
    "CONFIRMER_TYPES",
    "RULE_STATUSES",
    "normalize_rule_statement",
    "validate_confirmer_type",
    "validate_rule_status",
]
