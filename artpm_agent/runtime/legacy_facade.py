"""Observable compatibility boundary for pre-Harness request methods."""

from artpm_agent.runtime.counters import increment_counter


def record_legacy_facade_call(method: str) -> None:
    increment_counter(f"harness.legacy.{method}_calls")


__all__ = ["record_legacy_facade_call"]
