"""Trusted, conversation-scoped access mode policy.

The model never selects this mode. A host may place a normalized value in the
execution context after an explicit user action. Server risk policy remains the
upper bound: full access only pre-authorizes trusted low/medium-risk actions.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Literal


ACCESS_MODE_CONTROLLED = "controlled"
ACCESS_MODE_FULL = "full_access"
ACCESS_MODES = frozenset({ACCESS_MODE_CONTROLLED, ACCESS_MODE_FULL})

AccessDecision = Literal["allow", "confirm"]

_RISK_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3, "untrusted": 4}
_HIGH_RISK_MARKERS = frozenset(
    {
        "bulk",
        "delete",
        "dispatch",
        "export",
        "import",
        "notify",
        "overwrite",
        "publish",
        "purge",
        "remove",
        "replace",
        "send",
    }
)
_CRITICAL_MARKERS = frozenset(
    {"bash", "cmd", "command", "execute", "powershell", "pwsh", "shell", "terminal"}
)


def effective_access_risk(
    declared_risk: Any,
    *,
    action: Any = None,
    arguments: Mapping[str, Any] | None = None,
) -> str:
    """Apply non-downgradable floors for destructive or external actions."""

    risk = str(declared_risk or "untrusted").strip().casefold()
    if risk not in _RISK_RANK:
        risk = "untrusted"
    candidates = [action]
    if isinstance(arguments, Mapping):
        for key in ("action", "operation", "capability", "command"):
            if key in arguments:
                candidates.extend((key, arguments.get(key)))
    normalized = " ".join(str(item or "").casefold() for item in candidates)
    tokens = set(re.findall(r"[a-z]+", normalized))
    if tokens & _CRITICAL_MARKERS:
        floor = "critical"
    elif tokens & _HIGH_RISK_MARKERS:
        floor = "high"
    else:
        floor = "low"
    return max((risk, floor), key=_RISK_RANK.__getitem__)


def normalize_access_mode(value: Any) -> str:
    mode = str(value or "").strip().casefold()
    return mode if mode in ACCESS_MODES else ACCESS_MODE_CONTROLLED


def access_mode_from_context(context: Mapping[str, Any] | None) -> str:
    if not isinstance(context, Mapping):
        return ACCESS_MODE_CONTROLLED
    return normalize_access_mode(context.get("permission_mode"))


def access_decision(
    context: Mapping[str, Any] | None,
    *,
    read_only: bool,
    requires_approval: bool,
    risk: str,
    required_role: str = "user",
    auto_approval_allowed: bool = False,
) -> AccessDecision:
    """Return the host decision without weakening server-owned risk floors."""

    if read_only and not requires_approval:
        return "allow"
    mode = access_mode_from_context(context)
    permission_store = context.get("permission_store") if isinstance(context, Mapping) else None
    # Full access is a convenience mode, never a replacement for the durable
    # approval/audit boundary.  Fail closed when the store is unavailable.
    permission_runtime_ready = callable(
        getattr(permission_store, "create_request", None)
    )
    normalized_risk = str(risk or "untrusted").strip().casefold()
    normalized_role = str(required_role or "user").strip().casefold()
    if (
        mode == ACCESS_MODE_FULL
        and permission_runtime_ready
        and auto_approval_allowed
        and normalized_risk in {"low", "medium"}
        and normalized_role == "user"
    ):
        return "allow"
    return "confirm"


__all__ = [
    "ACCESS_MODE_CONTROLLED",
    "ACCESS_MODE_FULL",
    "ACCESS_MODES",
    "access_decision",
    "access_mode_from_context",
    "effective_access_risk",
    "normalize_access_mode",
]
