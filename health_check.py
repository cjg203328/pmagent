"""Compatibility shim for the legacy flat health-check module."""

from artpm_agent.health_check import *  # noqa: F401,F403
from artpm_agent.health_check import run_health_check as _run_health_check


if __name__ == "__main__":
    _result = _run_health_check()
    if isinstance(_result, dict) and _result.get("overall_status") == "unhealthy":
        raise SystemExit(1)
