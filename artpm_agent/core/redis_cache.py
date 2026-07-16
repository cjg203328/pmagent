"""Optional Redis integration for ArtPM caches.

Redis is an *enhancement*, never a hard requirement. When ``REDIS_URL`` is unset
or Redis is unreachable, every caller transparently falls back to its local
SQLite store. The client is created lazily and its (un)availability is
negative-cached so a missing Redis never turns into a per-call hot path.

All public helpers are safe no-ops when Redis is unavailable, so importing or
calling them can never break the application (mirrors the "best-effort, never
raise" contract already used by the outcome recorder).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_client: Optional[Any] = None  # redis.Redis instance once connected
_lock = threading.Lock()
_available: Optional[bool] = None  # None=unknown, True/False cached (negative cache)


def get_redis_url() -> Optional[str]:
    """Return the configured Redis URL, or None when unset."""
    return os.environ.get("REDIS_URL")


def get_redis() -> Optional[Any]:
    """Return a connected, working Redis client, or ``None`` if unavailable.

    Returns ``None`` (without raising) when:
      * ``REDIS_URL`` is not set,
      * the ``redis`` package is not installed,
      * the server cannot be reached / authenticated.
    """
    global _client, _available
    url = get_redis_url()
    if not url:
        return None
    with _lock:
        if _client is not None:
            return _client
        if _available is False:
            return None
        try:
            import redis  # lazy: keep Redis an optional dependency
        except ImportError:
            logger.info("redis package not installed; Redis cache disabled")
            _available = False
            return None
        try:
            client = redis.Redis.from_url(
                url,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
                decode_responses=True,
            )
            client.ping()
            _client = client
            _available = True
            logger.info("Redis connected at %s", _masked(url))
            return client
        except Exception as exc:  # noqa: BLE001 - never block the app on Redis
            logger.warning("Redis unavailable (%s); using local store", exc)
            _available = False
            return None


def redis_available() -> bool:
    """Convenience boolean for callers that branch on availability."""
    return get_redis() is not None


def reset_redis_cache() -> None:
    """Drop the cached client/availability (after config change or in tests)."""
    global _client, _available
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
        _client = None
        _available = None


def key(*parts: str) -> str:
    """Build a namespaced Redis key, e.g. ``key("tok", "today")``."""
    return ":".join(["artpm", *parts])


def cache_get_json(cache_key: str) -> Optional[Any]:
    """Return decoded JSON from Redis, or None when absent/unavailable."""
    r = get_redis()
    if r is None:
        return None
    try:
        raw = r.get(cache_key)
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    import json

    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def cache_set_json(cache_key: str, value: Any, ttl: int = 30) -> None:
    """Store ``value`` as JSON in Redis with a TTL (seconds). No-op if absent."""
    r = get_redis()
    if r is None:
        return
    import json

    try:
        r.set(cache_key, json.dumps(value, ensure_ascii=False), ex=ttl)
    except Exception:  # noqa: BLE001
        pass


def cache_delete(*keys: str) -> None:
    """Delete specific keys. No-op if Redis is unavailable."""
    if not keys:
        return
    r = get_redis()
    if r is None:
        return
    try:
        r.delete(*keys)
    except Exception:  # noqa: BLE001
        pass


def cache_delete_prefix(prefix: str) -> None:
    """Delete every key matching ``prefix*``. Used to invalidate a cache group
    on write. No-op if Redis is unavailable."""
    r = get_redis()
    if r is None:
        return
    try:
        for k in r.scan_iter(match=f"{prefix}*"):
            r.delete(k)
    except Exception:  # noqa: BLE001
        pass


def _masked(url: str) -> str:
    """Hide credentials in log output."""
    if "@" in url:
        scheme, _, rest = url.partition("://")
        _, _, host = rest.partition("@")
        return f"{scheme}://***@{host}"
    return url
