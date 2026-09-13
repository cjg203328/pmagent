"""Session query: replay, bounded reads, full-text search, and lineage.

Mirrors the deepseek-harness session-query capability over the durable
SESSION-domain event log:

- ``search``: case-insensitive full-text search across event fields.
- ``lineage``: reconstruct the event chain of one run (same run_id, ordered),
  grouped by turn, so a session can be replayed as a coherent story.
- ``summary``: cheap per-run statistics (messages, tool calls, errors, span).
- ``bounded_read``: read up to ``max_events`` / ``max_chars`` (dsh bounded
  reads) so a caller never pulls an unbounded log into memory.

The query layer is read-only and never writes to the session log.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .event_bus import SessionEventLog
from .events import AgentEventType


@dataclass(frozen=True, slots=True)
class TurnGroup:
    """One turn's event chain within a run lineage."""

    turn_id: str
    events: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class RunLineage:
    """Ordered event chain of one run, grouped by turn."""

    run_id: str
    turns: tuple[TurnGroup, ...]

    @property
    def event_count(self) -> int:
        return sum(len(turn.events) for turn in self.turns)


class SessionQuery:
    """Read-only query surface over a durable session event log."""

    def __init__(self, log: SessionEventLog):
        if not isinstance(log, SessionEventLog):
            raise TypeError("log must be a SessionEventLog")
        self.log = log

    # ── helpers ──

    @staticmethod
    def _text_values(record: dict[str, Any]) -> list[str]:
        """Collect every string leaf of a record (incl. nested message)."""
        values: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, dict):
                for child in value.values():
                    visit(child)

        visit(record)
        return values

    # ── replay ──

    def replay(
        self,
        *,
        event_type: Optional[AgentEventType | str] = None,
        run_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Replay matching events in append order (see SessionEventLog)."""
        return self.log.replay(
            event_type=event_type,
            run_id=run_id,
            limit=limit,
        )

    # ── full-text search ──

    def search(
        self,
        text: str,
        *,
        limit: Optional[int] = 50,
    ) -> list[dict[str, Any]]:
        """Case-insensitive substring search across every string field.

        Nested message content is included, so a search for a phrase inside
        a user/assistant message finds the owning event.
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueError("search text must be a non-empty string")
        needle = text.strip().casefold()
        matches: list[dict[str, Any]] = []
        for record in self.log.replay():
            if any(needle in value.casefold() for value in self._text_values(record)):
                matches.append(record)
        if limit is not None and limit > 0 and len(matches) > limit:
            matches = matches[-limit:]
        return matches

    # ── lineage ──

    def lineage(
        self,
        run_id: str,
        *,
        limit_per_turn: Optional[int] = None,
    ) -> RunLineage:
        """Reconstruct one run's event chain, grouped by turn in append order."""
        records = self.log.replay(run_id=run_id)
        by_turn: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for record in records:
            turn_id = record.get("turn_id") or "untracked"
            if turn_id not in by_turn:
                by_turn[turn_id] = []
                order.append(turn_id)
            by_turn[turn_id].append(record)
        turns = []
        for turn_id in order:
            events = by_turn[turn_id]
            if limit_per_turn is not None and limit_per_turn > 0:
                events = events[-limit_per_turn:]
            turns.append(TurnGroup(turn_id=turn_id, events=tuple(events)))
        return RunLineage(run_id=run_id, turns=tuple(turns))

    # ── summary ──

    def summary(self, run_id: str) -> dict[str, Any]:
        """Cheap per-run statistics over the durable event log."""
        records = self.log.replay(run_id=run_id)
        message_count = sum(
            1 for record in records if record.get("type") == "message_end"
        )
        tool_calls = sum(
            1
            for record in records
            if record.get("type") == "tool_execution_end"
        )
        errors = sum(1 for record in records if record.get("is_error"))
        first = records[0]["timestamp"] if records else None
        last = records[-1]["timestamp"] if records else None
        return {
            "run_id": run_id,
            "event_count": len(records),
            "message_count": message_count,
            "tool_call_count": tool_calls,
            "error_count": errors,
            "first_event_at": first,
            "last_event_at": last,
        }

    # ── bounded read ──

    def bounded_read(
        self,
        run_id: str,
        *,
        max_events: int = 200,
        max_chars: int = 64_000,
    ) -> list[dict[str, Any]]:
        """Read a run bounded by both event count and total content size."""
        records = self.log.replay(run_id=run_id)
        selected: list[dict[str, Any]] = []
        used_chars = 0
        for record in records:
            if len(selected) >= max_events:
                break
            size = sum(len(value) for value in self._text_values(record))
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            if size > remaining and selected:
                break
            selected.append(record)
            used_chars += size
        return selected
