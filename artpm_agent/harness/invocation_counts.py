"""Per-turn call counts for expensive or semantically unique operations."""

from __future__ import annotations

from typing import Any

from artpm_agent.runtime.counters import increment_counter


_NAMES = ("intent", "parse", "retrieval")


def increment_turn_invocation(ctx: Any, name: str) -> int:
    if name not in _NAMES:
        raise ValueError(f"unsupported turn invocation: {name}")
    extra = getattr(ctx, "extra", None)
    if not isinstance(extra, dict):
        extra = {}
        ctx.extra = extra
    counts = extra.setdefault("_invocation_counts", {})
    value = int(counts.get(name, 0)) + 1
    counts[name] = value
    increment_counter(f"harness.invocations.{name}")
    if value > 1:
        increment_counter(f"harness.invocations.{name}.duplicate")
    return value


def turn_invocation_snapshot(ctx: Any) -> dict[str, int]:
    extra = getattr(ctx, "extra", None)
    counts = extra.get("_invocation_counts", {}) if isinstance(extra, dict) else {}
    return {name: int(counts.get(name, 0)) for name in _NAMES}


__all__ = ["increment_turn_invocation", "turn_invocation_snapshot"]
