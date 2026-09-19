"""Pure serialization helpers for the HTTP gateway.

Keeping JSON normalization and SSE framing outside the application factory
makes the transport contract testable without constructing FastAPI routes.
The functions intentionally keep their historical private names; ``app.py``
re-exports them for compatibility with existing internal callers.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from collections.abc import Mapping
from typing import Any, cast

from .services import ChatOutcome


def json_safe(value: Any, *, depth: int = 0) -> Any:
    """Bound metadata/results before handing them to a JSON response."""
    if depth > 16:
        return "[depth limited]"
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {
            str(key): json_safe(item, depth=depth + 1) for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item, depth=depth + 1) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return json_safe(asdict(cast(Any, value)), depth=depth + 1)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return json_safe(model_dump(mode="json"), depth=depth + 1)
    return str(value)[:4000]


def safe_validation_errors(errors: Any) -> list[dict[str, Any]]:
    """Keep validation responses actionable without echoing request values."""
    safe: list[dict[str, Any]] = []
    for item in list(errors or [])[:20]:
        if not isinstance(item, Mapping):
            continue
        location = [str(part)[:80] for part in list(item.get("loc", ()))[:8]]
        safe.append(
            {
                "loc": location,
                "type": str(item.get("type", "validation_error"))[:120],
                "message": "request field failed validation",
            }
        )
    return safe


def model_json(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return json_safe(value)


def coerce_chat_outcome(value: Any) -> ChatOutcome | None:
    """Normalize an optional stream result without accepting arbitrary fields."""
    if isinstance(value, ChatOutcome):
        return value
    if not isinstance(value, Mapping) or "type" in value or "event" in value:
        return None
    if not any(key in value for key in ("response", "success", "error")):
        return None
    allowed = {
        "response",
        "success",
        "awaiting_approval",
        "handled_by",
        "metadata",
        "artifacts",
        "error",
    }
    try:
        return ChatOutcome(**{key: value[key] for key in allowed if key in value})
    except (TypeError, ValueError):
        return None


def stream_event_payload(
    value: Any, *, run_id: str, turn_id: str
) -> dict[str, Any] | None:
    """Turn an AgentEvent or a small provider mapping into public JSON."""
    raw: dict[str, Any]
    if isinstance(value, str):
        raw = {"type": "message_update", "delta": value}
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            raw = cast(dict[str, Any], value.to_dict())
        except Exception:  # pragma: no cover - defensive provider boundary
            return None
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        return None
    raw_event_type = raw.get("type") or raw.get("event") or ""
    event_type = str(getattr(raw_event_type, "value", raw_event_type)).strip()
    if not event_type:
        if "delta" in raw or "content" in raw:
            event_type = "message_update"
        else:
            return None
    payload = json_safe(raw)
    if not isinstance(payload, dict):
        return None
    normalized_payload = cast(dict[str, Any], payload)
    normalized_payload["type"] = event_type
    normalized_payload["run_id"] = run_id
    normalized_payload["turn_id"] = turn_id
    normalized_event_type = event_type.casefold()
    error_event = bool(
        normalized_payload.get("is_error")
        or normalized_payload.get("error")
        or any(
            marker in normalized_event_type
            for marker in ("error", "exception", "failure", "failed")
        )
    )
    if error_event:
        normalized_payload["is_error"] = True
        normalized_payload["error"] = "chat_failed"
        normalized_payload.pop("exception", None)
        normalized_payload["metadata"] = {"error_code": "chat_failed"}
    return normalized_payload


def sse_frame(payload: Mapping[str, Any], *, event_id: str) -> str:
    """Encode one compact SSE frame with JSON data and a resumable id."""
    event_type = str(payload.get("type") or "message_update")
    data = json.dumps(json_safe(payload), ensure_ascii=False, separators=(",", ":"))
    body = "".join(f"data: {line}\n" for line in data.splitlines() or [""])
    return f"id: {event_id}\nevent: {event_type}\n{body}\n"
