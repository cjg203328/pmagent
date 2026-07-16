"""Outcome recorder — convert a TurnResult into an Episode.

This module lives in the harness package (same layer as ``turn_service``) but
is fully additive:

* Importing it has **no side effects** (``run_turn`` is imported lazily inside
  ``run_turn_recorded`` only).
* It does **not** alter the ``run_turn`` handler chain.

Use ``record_outcome(ctx, result)`` after a turn, or call
``run_turn_recorded(ctx, **kw)`` as a drop-in replacement for ``run_turn`` to
capture the outcome for the evolution/learning loop without touching any
existing caller.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from .turn_service import TurnContext, TurnResult
from artpm_agent.memory.episode_store import Episode, EpisodeStore

logger = logging.getLogger(__name__)

_DEFAULT_STORE: Optional[EpisodeStore] = None


def default_episode_db_path() -> str:
    """Resolve the default episodes database path.

    Honors the ``ARTPM_EPISODE_DB`` environment variable (explicit per-file
    override); otherwise the episodes db lives under the user-chosen data root
    so it relocates together with the knowledge base and caches.
    """
    env = os.environ.get("ARTPM_EPISODE_DB")
    if env:
        return env
    from artpm_agent.config import resolve_data_root
    return str(resolve_data_root() / "episodes.db")


def _default_store() -> Optional[EpisodeStore]:
    """Lazily build and cache the default store. Returns None if it cannot be created."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is not None:
        return _DEFAULT_STORE
    try:
        _DEFAULT_STORE = EpisodeStore(default_episode_db_path())
    except Exception:  # noqa: BLE001 - recording must never break startup
        logger.warning("Could not init default EpisodeStore; outcome recording disabled")
        _DEFAULT_STORE = None
    return _DEFAULT_STORE


def _classify_error(result: TurnResult) -> Optional[str]:
    """Map a failed TurnResult to a coarse error kind for later mining."""
    if result.success:
        return None
    err = (result.error or "")[:300].lower()
    if "vision" in err:
        return "vision"
    if "timeout" in err or "timed out" in err:
        return "timeout"
    if "model" in err or "api" in err or "key" in err:
        return "model"
    if "permission" in err or "denied" in err:
        return "permission"
    return "unknown"


def episode_from_turn(
    ctx: TurnContext,
    result: TurnResult,
    *,
    run_id: str = "",
    feedback: Optional[str] = None,
) -> Episode:
    """Build an Episode from a turn's context and result."""
    return Episode(
        turn_id=ctx.turn_id,
        conversation_id=ctx.conversation_id,
        handler=result.handled_by or "unknown",
        success=bool(result.success),
        error_kind=_classify_error(result),
        user_input_excerpt=(ctx.user_input or "")[:200],
        feedback=feedback,
        run_id=run_id,
        metadata={"awaiting_approval": bool(result.awaiting_approval)},
    )


def record_outcome(
    ctx: TurnContext,
    result: TurnResult,
    *,
    store: Optional[EpisodeStore] = None,
    run_id: str = "",
    feedback: Optional[str] = None,
) -> Optional[str]:
    """Persist one turn outcome. Best-effort: never raises.

    Returns the stored episode id, or None when recording is unavailable.
    """
    target = store or _default_store()
    if target is None:
        return None
    try:
        return target.record(
            episode_from_turn(ctx, result, run_id=run_id, feedback=feedback)
        )
    except Exception:  # noqa: BLE001 - recording must never break a turn
        logger.exception("Failed to record turn outcome (non-fatal)")
        return None


def run_turn_recorded(
    ctx: TurnContext,
    *,
    store: Optional[EpisodeStore] = None,
    run_id: str = "",
    **kw: Any,
) -> TurnResult:
    """Drop-in wrapper around ``harness.run_turn`` that also records the outcome.

    Use this instead of calling ``run_turn`` directly to enable learning
    signals without modifying ``turn_service``'s handler chain.
    """
    from .turn_service import run_turn

    result = run_turn(ctx, **kw)
    record_outcome(ctx, result, store=store, run_id=run_id)
    return result
