"""Deployment-only environment adapter for the secure plugin manager."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
from typing import Any

from .discovery import PluginPolicy
from .loader import PluginManager


PLUGIN_ENABLED_ENV = "ARTPM_PLUGINS_ENABLED"
PLUGIN_ROOTS_ENV = "ARTPM_PLUGIN_ROOTS"
PLUGIN_ALLOWLIST_ENV = "ARTPM_PLUGIN_ALLOWLIST"


class PluginConfigurationError(ValueError):
    """Raised when operator-provided plugin environment is unsafe or malformed."""


def _enabled(value: Any) -> bool:
    normalized = str(value or "false").strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    raise PluginConfigurationError(
        f"{PLUGIN_ENABLED_ENV} must be a boolean value"
    )


def _json_string_array(raw: Any, field: str) -> tuple[str, ...]:
    if not isinstance(raw, str) or not raw.strip():
        raise PluginConfigurationError(f"{field} must be a non-empty JSON array")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PluginConfigurationError(f"{field} is not valid JSON: {error.msg}") from None
    if not isinstance(parsed, list) or not parsed:
        raise PluginConfigurationError(f"{field} must be a non-empty JSON array")
    values: list[str] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, str) or not item.strip():
            raise PluginConfigurationError(
                f"{field}[{index}] must be a non-empty string"
            )
        values.append(item.strip())
    if len(values) != len(set(values)):
        raise PluginConfigurationError(f"{field} cannot contain duplicates")
    return tuple(values)


def build_plugin_manager_from_environment(
    environ: Mapping[str, str] | None = None,
) -> PluginManager:
    """Build a fail-closed manager from deployment-owned process environment.

    No request path or application setting is consulted. Hash verification and
    symbolic-link rejection are intentionally fixed on for this entrypoint.
    """

    values = os.environ if environ is None else environ
    if not _enabled(values.get(PLUGIN_ENABLED_ENV)):
        return PluginManager()
    roots = _json_string_array(values.get(PLUGIN_ROOTS_ENV), PLUGIN_ROOTS_ENV)
    allowlist = _json_string_array(
        values.get(PLUGIN_ALLOWLIST_ENV), PLUGIN_ALLOWLIST_ENV
    )
    try:
        policy = PluginPolicy(
            enabled=True,
            trusted_roots=tuple(Path(item).expanduser() for item in roots),
            allowed_plugin_ids=frozenset(allowlist),
            require_hash=True,
            reject_symlinks=True,
        )
    except ValueError as error:
        raise PluginConfigurationError(str(error)) from None
    return PluginManager(policy)
