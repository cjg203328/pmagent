"""Contract tests for the optional Redis cache layer.

The whole point of ``redis_cache`` is that the application must keep working
(every cache helper a safe no-op, ``get_redis`` returning None) when Redis is
absent, unreachable, or the ``redis`` package is not installed. These tests
lock that contract so a missing Redis can never break a deployment.
"""

from artpm_agent.core import redis_cache


def test_get_redis_none_without_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    redis_cache.reset_redis_cache()
    assert redis_cache.get_redis() is None


def test_get_redis_graceful_when_unreachable(monkeypatch):
    # A valid-but-dead URL: connection refused is immediate (no long hang).
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    redis_cache.reset_redis_cache()
    assert redis_cache.get_redis() is None
    # Second call is negative-cached and still None (no repeated ping storm).
    assert redis_cache.get_redis() is None


def test_cache_helpers_safe_without_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    redis_cache.reset_redis_cache()
    # All of these must be no-ops / safe returns, never raise.
    assert redis_cache.cache_get_json("artpm:tok:today") is None
    redis_cache.cache_set_json("artpm:tok:today", {"tokens": 1}, ttl=5)
    redis_cache.cache_delete("artpm:tok:today")
    redis_cache.cache_delete_prefix("artpm:tok")
    assert redis_cache.key("tok", "today") == "artpm:tok:today"
