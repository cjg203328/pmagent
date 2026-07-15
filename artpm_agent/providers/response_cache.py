"""Response cache for LLM calls (performance optimization).

Caches identical ``(model, system, prompt, history, images)`` requests so that
repeated questions — very common in a project-management assistant — skip the
API call entirely. This is the single biggest cost/latency win for this app.

The cache is **opt-in** (``enabled`` defaults to ``True`` but the gateway only
builds an enabled instance when the operator turns it on) and **best-effort**:
any error in the cache layer is swallowed so it can never break a real reply.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional


def _norm(value: object) -> str:
    """Stable-ish stringification for cache keys (history/images may be lists)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(_norm(v) for v in value)
    return str(value)


@dataclass
class _CacheEntry:
    value: str
    expires_at: float


class ResponseCache:
    """Bounded, time-limited in-memory cache for model responses."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        ttl: int = 3600,
        max_size: int = 2000,
    ) -> None:
        self.enabled = bool(enabled)
        self.ttl = max(0, int(ttl))
        self.max_size = max(1, int(max_size))
        self._store: "OrderedDict[str, _CacheEntry]" = OrderedDict()
        self._lock = threading.RLock()

    def _key(
        self,
        model: str,
        system_prompt: Optional[str],
        prompt: str,
        history: object,
        images: object,
    ) -> str:
        raw = "|".join(
            (
                str(model or ""),
                _norm(system_prompt),
                _norm(prompt),
                _norm(history),
                _norm(images),
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def get(
        self,
        model: str,
        system_prompt: Optional[str],
        prompt: str,
        history: object,
        images: object,
    ) -> Optional[str]:
        if not self.enabled:
            return None
        key = self._key(model, system_prompt, prompt, history, images)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at <= time.monotonic():
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return entry.value

    def put(
        self,
        model: str,
        system_prompt: Optional[str],
        prompt: str,
        history: object,
        images: object,
        value: str,
    ) -> None:
        if not self.enabled or not value:
            return
        key = self._key(model, system_prompt, prompt, history, images)
        with self._lock:
            self._store[key] = _CacheEntry(
                value=value, expires_at=time.monotonic() + self.ttl
            )
            self._store.move_to_end(key)
            while len(self._store) > self.max_size:
                self._store.popitem(last=False)
