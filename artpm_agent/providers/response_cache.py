"""Best-effort response cache for deterministic LLM requests.

Keys are canonical, tenant/workspace scoped, and include image content rather
than temporary file paths. Redis is used as an optional shared L2 when it is
configured; the bounded in-process L1 remains the fast path.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional


_CACHE_SCHEMA = "response-v2"
_DEFAULT_NAMESPACE = "local:default"
_HISTORY_FIELDS = ("role", "content", "name", "tool_call_id")


def _text(value: object) -> str:
    normalized = unicodedata.normalize("NFC", "" if value is None else str(value))
    return normalized.replace("\r\n", "\n").replace("\r", "\n").strip()


def _canonical(value: object, *, history_item: bool = False) -> Any:
    """Return a JSON-safe, deterministic representation without losing meaning."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, Mapping):
        if history_item and ("role" in value or "content" in value):
            return {
                key: _canonical(value[key])
                for key in _HISTORY_FIELDS
                if key in value and value[key] is not None
            }
        return {
            _text(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: _text(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item, history_item=True) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        )
    return _text(value)


def response_cache_namespace(context: object = None) -> str:
    """Build a stable namespace from trusted tenant/workspace context."""

    mapping = context if isinstance(context, Mapping) else {}
    tenant_context = mapping.get("tenant_context") if mapping else context
    tenant_id = getattr(tenant_context, "tenant_id", None)
    workspace_id = getattr(tenant_context, "workspace_id", None)
    if mapping:
        tenant_id = tenant_id or mapping.get("tenant_id")
        workspace_id = workspace_id or mapping.get("workspace_id")
    tenant = _text(tenant_id) or "local"
    workspace = _text(workspace_id) or "default"
    return f"{tenant}:{workspace}"


@dataclass
class _CacheEntry:
    value: str
    expires_at: float


class ResponseCache:
    """Bounded L1 response cache with an optional Redis-backed shared L2."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        ttl: int = 1800,
        max_size: int = 512,
        redis_enabled: bool = True,
    ) -> None:
        self.enabled = bool(enabled)
        self.ttl = max(0, int(ttl))
        self.max_size = max(1, int(max_size))
        self.redis_enabled = bool(redis_enabled)
        self._store: "OrderedDict[str, _CacheEntry]" = OrderedDict()
        self._lock = threading.RLock()
        self._flights: dict[str, threading.Event] = {}
        self._image_fingerprints: dict[tuple[str, int, int], str] = {}
        self._stats = {
            "lookups": 0,
            "hits": 0,
            "misses": 0,
            "writes": 0,
            "evictions": 0,
            "redis_hits": 0,
            "errors": 0,
        }

    def _image_fingerprint(self, value: object) -> Any:
        if isinstance(value, Mapping):
            for key in ("sha256", "content_sha256", "digest"):
                digest = _text(value.get(key))
                if digest:
                    return {"sha256": digest, "page": value.get("page")}
            return _canonical(value)

        raw_path = _text(value)
        if not raw_path:
            return ""
        try:
            path = Path(raw_path)
            stat = path.stat()
            marker = (str(path.resolve()), int(stat.st_size), int(stat.st_mtime_ns))
            with self._lock:
                cached = self._image_fingerprints.get(marker)
            if cached:
                return {"sha256": cached, "size": stat.st_size}
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            fingerprint = digest.hexdigest()
            with self._lock:
                self._image_fingerprints[marker] = fingerprint
                if len(self._image_fingerprints) > self.max_size * 2:
                    self._image_fingerprints.pop(next(iter(self._image_fingerprints)))
            return {"sha256": fingerprint, "size": stat.st_size}
        except (OSError, ValueError):
            return {"reference": raw_path}

    def _key(
        self,
        model: str,
        system_prompt: Optional[str],
        prompt: str,
        history: object,
        images: object,
        namespace: str = _DEFAULT_NAMESPACE,
    ) -> str:
        image_values = images if isinstance(images, (list, tuple)) else ([] if images is None else [images])
        payload = {
            "schema": _CACHE_SCHEMA,
            "namespace": _text(namespace) or _DEFAULT_NAMESPACE,
            "model_contract": _text(model),
            "system": _text(system_prompt),
            "prompt": _text(prompt),
            "history": _canonical(history),
            "images": [self._image_fingerprint(item) for item in image_values],
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _redis_key(digest: str) -> str:
        from artpm_agent.core.redis_cache import key

        return key("llm-response", _CACHE_SCHEMA, digest)

    def _redis_get(self, digest: str) -> Optional[str]:
        if not self.redis_enabled or not os.getenv("REDIS_URL"):
            return None
        from artpm_agent.core.redis_cache import cache_get_json

        payload = cache_get_json(self._redis_key(digest))
        if not isinstance(payload, Mapping):
            return None
        value = payload.get("value")
        return value if isinstance(value, str) and value else None

    def _redis_put(self, digest: str, value: str) -> None:
        if not self.redis_enabled or not os.getenv("REDIS_URL") or self.ttl <= 0:
            return
        from artpm_agent.core.redis_cache import cache_set_json

        cache_set_json(self._redis_key(digest), {"value": value}, ttl=self.ttl)

    def _put_local(self, digest: str, value: str) -> None:
        self._store[digest] = _CacheEntry(
            value=value,
            expires_at=time.monotonic() + self.ttl,
        )
        self._store.move_to_end(digest)
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)
            self._stats["evictions"] += 1

    def _get_local_locked(self, digest: str) -> Optional[str]:
        entry = self._store.get(digest)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._store.pop(digest, None)
            return None
        self._store.move_to_end(digest)
        return entry.value

    def get(
        self,
        model: str,
        system_prompt: Optional[str],
        prompt: str,
        history: object,
        images: object,
        namespace: str = _DEFAULT_NAMESPACE,
    ) -> Optional[str]:
        if not self.enabled:
            return None
        try:
            digest = self._key(model, system_prompt, prompt, history, images, namespace)
            with self._lock:
                self._stats["lookups"] += 1
                local = self._get_local_locked(digest)
                if local is not None:
                    self._stats["hits"] += 1
                    return local

            shared = self._redis_get(digest)
            with self._lock:
                if shared is not None:
                    self._put_local(digest, shared)
                    self._stats["hits"] += 1
                    self._stats["redis_hits"] += 1
                    return shared
                self._stats["misses"] += 1
            return None
        except Exception:
            with self._lock:
                self._stats["errors"] += 1
                self._stats["misses"] += 1
            return None

    def acquire(
        self,
        model: str,
        system_prompt: Optional[str],
        prompt: str,
        history: object,
        images: object,
        namespace: str = _DEFAULT_NAMESPACE,
        *,
        wait_timeout: float = 60.0,
    ) -> tuple[Optional[str], Optional[str]]:
        """Return a hit or reserve one key for a single provider request.

        The reservation token must be released by the owner in a ``finally``
        block. Waiters wake after either a complete cache write or a failure.
        """

        if not self.enabled:
            return None, None
        try:
            digest = self._key(model, system_prompt, prompt, history, images, namespace)
            with self._lock:
                self._stats["lookups"] += 1
                local = self._get_local_locked(digest)
                if local is not None:
                    self._stats["hits"] += 1
                    return local, None

            shared = self._redis_get(digest)
            if shared is not None:
                with self._lock:
                    self._put_local(digest, shared)
                    self._stats["hits"] += 1
                    self._stats["redis_hits"] += 1
                return shared, None

            deadline = time.monotonic() + max(0.0, float(wait_timeout))
            while True:
                with self._lock:
                    local = self._get_local_locked(digest)
                    if local is not None:
                        self._stats["hits"] += 1
                        return local, None
                    event = self._flights.get(digest)
                    if event is None:
                        self._flights[digest] = threading.Event()
                        self._stats["misses"] += 1
                        return None, digest
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not event.wait(remaining):
                    with self._lock:
                        self._stats["misses"] += 1
                    return None, None
        except Exception:
            with self._lock:
                self._stats["errors"] += 1
                self._stats["misses"] += 1
            return None, None

    def release(self, reservation: Optional[str]) -> None:
        if not reservation:
            return
        with self._lock:
            event = self._flights.pop(reservation, None)
        if event is not None:
            event.set()

    def put(
        self,
        model: str,
        system_prompt: Optional[str],
        prompt: str,
        history: object,
        images: object,
        value: str,
        namespace: str = _DEFAULT_NAMESPACE,
    ) -> None:
        if not self.enabled or not value or self.ttl <= 0:
            return
        try:
            digest = self._key(model, system_prompt, prompt, history, images, namespace)
            with self._lock:
                self._put_local(digest, value)
                self._stats["writes"] += 1
            self._redis_put(digest, value)
        except Exception:
            with self._lock:
                self._stats["errors"] += 1

    def stats(self) -> dict[str, object]:
        with self._lock:
            lookups = int(self._stats["lookups"])
            hits = int(self._stats["hits"])
            return {
                "enabled": self.enabled,
                "ttl": self.ttl,
                "max_size": self.max_size,
                "size": len(self._store),
                "in_flight": len(self._flights),
                **self._stats,
                "hit_rate": (hits / lookups) if lookups else 0.0,
                "shared": bool(self.redis_enabled and os.getenv("REDIS_URL")),
            }
