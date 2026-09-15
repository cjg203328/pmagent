"""Three-domain event bus with subscribe/publish and durable session log.

Mirrors the deepseek-harness event model at the runtime level:

- SESSION events are durable facts appended to a JSONL session log and
  replayed later (session-query style), surviving process restarts.
- AGENT events are live observations of a running agent; existing streaming
  consumers keep iterating the agent-loop generator unchanged, and may
  additionally forward events into this bus for cross-cutting subscribers.
- CAPABILITY events notify seams (tools/fs/telemetry) so policy or adapters
  can attach without circular imports.

The bus guarantees subscriber isolation: one failing subscriber never blocks
or breaks other subscribers, and never crashes the publisher.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from .events import AgentEvent, AgentEventType, EventDomain

logger = logging.getLogger(__name__)

#: A subscriber: receives one event and may return anything (ignored).
EventHandler = Callable[[AgentEvent], Any]
#: A sink receives every published event (durable append, telemetry, ...).
EventSink = Callable[[AgentEvent], Any]

_DOMAIN_FILTERS = frozenset(domain.value for domain in EventDomain)
_TYPE_FILTERS = frozenset(event_type.value for event_type in AgentEventType)


@dataclass(frozen=True, slots=True)
class Subscription:
    """One registered subscription with optional filters."""

    handler: EventHandler
    domain: Optional[EventDomain] = None
    event_type: Optional[AgentEventType] = None
    filter: Optional[Callable[[AgentEvent], bool]] = None
    subscription_id: int = 0

    def matches(self, event: AgentEvent) -> bool:
        if self.domain is not None and event.domain != self.domain:
            return False
        if self.event_type is not None and event.type != self.event_type:
            return False
        if self.filter is not None:
            try:
                return bool(self.filter(event))
            except Exception:  # a broken filter must not drop the event
                logger.warning("event filter raised", exc_info=True)
                return True
        return True


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    """Result of publishing one event."""

    event_type: str
    domain: str
    delivered: int = 0
    failures: tuple[tuple[str, str], ...] = ()

    @property
    def failed(self) -> bool:
        return bool(self.failures)


class EventBus:
    """Thread-safe publish/subscribe event bus with subscriber isolation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscriptions: list[Subscription] = []
        self._sinks: list[tuple[object, EventSink]] = []
        self._next_id = 1

    # ── subscription ──

    def subscribe(
        self,
        handler: EventHandler,
        *,
        domain: Optional[EventDomain | str] = None,
        event_type: Optional[AgentEventType | str] = None,
        filter: Optional[Callable[[AgentEvent], bool]] = None,
    ) -> Callable[[], None]:
        """Register a subscriber and return an idempotent unsubscribe callable.

        ``domain`` / ``event_type`` may be enum members or their string values.
        """
        if not callable(handler):
            raise TypeError("event handler must be callable")
        if domain is not None:
            if isinstance(domain, str):
                if domain not in _DOMAIN_FILTERS:
                    raise ValueError(f"unsupported event domain: {domain}")
                domain = EventDomain(domain)
            elif not isinstance(domain, EventDomain):
                raise TypeError("domain must be an EventDomain or string")
        if event_type is not None:
            if isinstance(event_type, str):
                if event_type not in _TYPE_FILTERS:
                    raise ValueError(f"unsupported event type: {event_type}")
                event_type = AgentEventType(event_type)
            elif not isinstance(event_type, AgentEventType):
                raise TypeError("event_type must be an AgentEventType or string")
        with self._lock:
            subscription = Subscription(
                handler=handler,
                domain=domain,
                event_type=event_type,
                filter=filter,
                subscription_id=self._next_id,
            )
            self._next_id += 1
            self._subscriptions.append(subscription)
        subscription_id = subscription.subscription_id

        def unsubscribe() -> None:
            with self._lock:
                self._subscriptions[:] = [
                    item
                    for item in self._subscriptions
                    if item.subscription_id != subscription_id
                ]

        return unsubscribe

    def attach_sink(self, sink: EventSink) -> Callable[[], None]:
        """Attach a sink that receives every published event (never filtered).

        Sinks are used for durable logging and telemetry. A failing sink is
        isolated exactly like a failing subscriber.
        """
        if not callable(sink):
            raise TypeError("event sink must be callable")
        sink_token = object()
        with self._lock:
            self._sinks.append((sink_token, sink))
        detached = False

        def detach() -> None:
            nonlocal detached
            with self._lock:
                if detached:
                    return
                detached = True
                self._sinks[:] = [
                    (token, registered_sink)
                    for token, registered_sink in self._sinks
                    if token is not sink_token
                ]

        return detach

    # ── publication ──

    def publish(self, event: AgentEvent) -> DeliveryReport:
        """Deliver one event to sinks and matching subscribers, isolated.

        Sinks run first (durable facts must be written before observers react),
        then matching subscriptions. Any subscriber exception is recorded in
        the report and swallowed.
        """
        if not isinstance(event, AgentEvent):
            raise TypeError("publish() expects an AgentEvent")
        with self._lock:
            subscriptions = list(self._subscriptions)
            sinks = [sink for _, sink in self._sinks]

        failures: list[tuple[str, str]] = []
        delivered = 0
        for sink in sinks:
            try:
                sink(event)
            except Exception as error:
                failures.append(("sink", f"{type(error).__name__}: {error}"))
                logger.warning("event sink failed", exc_info=True)
        for subscription in subscriptions:
            if not subscription.matches(event):
                continue
            try:
                subscription.handler(event)
                delivered += 1
            except Exception as error:
                failures.append(
                    (
                        f"subscriber#{subscription.subscription_id}",
                        f"{type(error).__name__}: {error}",
                    )
                )
                logger.warning("event subscriber failed", exc_info=True)
        return DeliveryReport(
            event_type=event.type.value,
            domain=event.domain.value,
            delivered=delivered,
            failures=tuple(failures),
        )

    def forward(
        self,
        events: Iterable[AgentEvent],
        *,
        domains: Sequence[EventDomain] = (EventDomain.AGENT,),
    ) -> int:
        """Forward a generator/iterable of events into the bus.

        Returns how many events were published. This is the non-invasive bridge
        for existing streaming consumers: they keep their generator, and one
        extra adapter forwards each event here.
        """
        published = 0
        for event in events:
            if event.domain not in domains:
                continue
            self.publish(event)
            published += 1
        return published

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    def clear(self) -> None:
        with self._lock:
            self._subscriptions.clear()
            self._sinks.clear()


class SessionEventLog:
    """Append-only JSONL event log for SESSION-domain durable facts.

    Mirrors the dsh session data plane: events are appended once and replayed
    in order. Corrupt tail lines are tolerated on replay so a partial write
    never bricks the whole log.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)

    # ── write ──

    def append(self, event: AgentEvent) -> None:
        """Append one event as a JSON line (best-effort durable fact)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            event.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    # ── read ──

    def replay(
        self,
        *,
        domain: Optional[EventDomain | str] = None,
        event_type: Optional[AgentEventType | str] = None,
        run_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Return matching events in append order, newest-kept bounded by limit.

        Filters accept enum members or string values; a filter error or a
        corrupt line logs and is skipped (never raises into the caller).
        """
        if domain is not None and not isinstance(domain, EventDomain):
            if isinstance(domain, str):
                if domain not in _DOMAIN_FILTERS:
                    raise ValueError(f"unsupported event domain: {domain}")
                domain = EventDomain(domain)
            else:
                raise TypeError("domain must be an EventDomain or string")
        if event_type is not None and not isinstance(event_type, AgentEventType):
            if isinstance(event_type, str):
                if event_type not in _TYPE_FILTERS:
                    raise ValueError(f"unsupported event type: {event_type}")
                event_type = AgentEventType(event_type)
            else:
                raise TypeError("event_type must be an AgentEventType or string")
        if not self.path.is_file():
            return []
        records: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "session log line %s is corrupt; skipped", line_number
                    )
                    continue
                if not isinstance(record, dict):
                    continue
                if domain is not None and record.get("domain") != str(domain.value):
                    continue
                if event_type is not None and record.get("type") != str(event_type.value):
                    continue
                if run_id is not None and record.get("run_id") != run_id:
                    continue
                records.append(record)
        if limit is not None and limit > 0 and len(records) > limit:
            records = records[-limit:]
        return records

    def event_count(self) -> int:
        return len(self.replay())


# ── convenience factory ──

def build_bus_with_session_log(
    log_path: Path | str,
) -> tuple[EventBus, SessionEventLog]:
    """Build a bus that durably records every SESSION-domain event."""
    bus = EventBus()
    session_log = SessionEventLog(log_path)
    bus.attach_sink(
        lambda event: (
            session_log.append(event)
            if event.domain is EventDomain.SESSION
            else None
        )
    )
    return bus, session_log
