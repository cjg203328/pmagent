"""Turn-level UI contracts.

Execution remains owned by ``artpm_agent.harness``; this module only carries
stable names for future rendering adapters.
"""

from typing import Any


def turn_id_from_result(result: Any) -> str | None:
    run = getattr(result, "run", None)
    turn_id = getattr(run, "turn_id", None) or getattr(result, "turn_id", None)
    return str(turn_id) if turn_id else None


__all__ = ["turn_id_from_result"]
