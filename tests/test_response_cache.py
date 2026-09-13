"""Tests for the LLM response cache."""
import os
import threading
import time

from artpm_agent.providers.response_cache import (
    ResponseCache,
    response_cache_namespace,
)
from artpm_agent.tenancy import TenantContext


def test_put_then_get_hit():
    c = ResponseCache(enabled=True, ttl=60)
    assert c.get("m", "sys", "p", [], None) is None
    c.put("m", "sys", "p", [], None, "answer")
    assert c.get("m", "sys", "p", [], None) == "answer"


def test_disabled_cache_never_hits():
    c = ResponseCache(enabled=False, ttl=60)
    c.put("m", "sys", "p", [], None, "answer")
    assert c.get("m", "sys", "p", [], None) is None


def test_key_isolates_different_prompts():
    c = ResponseCache(enabled=True, ttl=60)
    c.put("m", "sys", "p1", [], None, "a1")
    c.put("m", "sys", "p2", [], None, "a2")
    assert c.get("m", "sys", "p1", [], None) == "a1"
    assert c.get("m", "sys", "p2", [], None) == "a2"


def test_expiry():
    import time

    c = ResponseCache(enabled=True, ttl=1)
    c.put("m", "sys", "p", [], None, "answer")
    assert c.get("m", "sys", "p", [], None) == "answer"
    time.sleep(1.1)
    assert c.get("m", "sys", "p", [], None) is None


def test_eviction_keeps_bounded():
    c = ResponseCache(enabled=True, ttl=600, max_size=3)
    for i in range(10):
        c.put("m", "sys", f"p{i}", [], None, f"a{i}")
    # only the last 3 should remain
    assert c.get("m", "sys", "p0", [], None) is None
    assert c.get("m", "sys", "p9", [], None) == "a9"


def test_canonical_history_ignores_mapping_order_and_volatile_metadata():
    c = ResponseCache(enabled=True, ttl=60)
    first = [{"role": "user", "content": "hello", "created_at": "old"}]
    reordered = [{"created_at": "new", "content": "hello", "role": "user"}]
    c.put("m", "sys", "p", first, None, "answer")
    assert c.get("m", "sys", "p", reordered, None) == "answer"


def test_meaningful_history_and_namespace_are_isolated():
    c = ResponseCache(enabled=True, ttl=60)
    history = [{"role": "user", "content": "alpha"}]
    c.put("m", "sys", "p", history, None, "answer-a", "tenant:a")
    assert c.get("m", "sys", "p", history, None, "tenant:b") is None
    changed = [{"role": "user", "content": "beta"}]
    assert c.get("m", "sys", "p", changed, None, "tenant:a") is None
    assert c.get("m", "sys", "p", history, None, "tenant:a") == "answer-a"


def test_structured_key_prevents_delimiter_collisions():
    c = ResponseCache(enabled=True, ttl=60)
    assert c._key("a|b", "c", "p", [], None) != c._key(
        "a", "b|c", "p", [], None
    )


def test_unicode_and_line_endings_are_canonical():
    c = ResponseCache(enabled=True, ttl=60)
    decomposed = "Cafe\u0301\r\nnext"
    composed = "Caf\u00e9\nnext"
    c.put("m", "sys", decomposed, [], None, "answer")
    assert c.get("m", "sys", composed, [], None) == "answer"


def test_images_use_content_fingerprint_not_temporary_path(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"same-image")
    second.write_bytes(b"same-image")
    c = ResponseCache(enabled=True, ttl=60)
    c.put("m", "sys", "p", [], [str(first)], "answer")
    assert c.get("m", "sys", "p", [], [str(second)]) == "answer"

    second.write_bytes(b"different-image")
    os.utime(second, None)
    assert c.get("m", "sys", "p", [], [str(second)]) is None


def test_image_hash_ignores_unchanged_path_metadata(tmp_path):
    image = tmp_path / "reused.png"
    image.write_bytes(b"first-image!")
    original = image.stat()
    cache = ResponseCache(enabled=True, ttl=60)
    cache.put("m", "sys", "p", [], [str(image)], "first")

    image.write_bytes(b"other-image!")
    os.utime(image, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert image.stat().st_size == original.st_size
    assert image.stat().st_mtime_ns == original.st_mtime_ns
    assert cache.get("m", "sys", "p", [], [str(image)]) is None


def test_cache_stats_report_real_lookups_and_hits():
    c = ResponseCache(enabled=True, ttl=60)
    assert c.get("m", "sys", "p", [], None) is None
    c.put("m", "sys", "p", [], None, "answer")
    assert c.get("m", "sys", "p", [], None) == "answer"
    stats = c.stats()
    assert stats["lookups"] == 2
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["writes"] == 1
    assert stats["hit_rate"] == 0.5


def test_namespace_uses_trusted_tenant_and_workspace():
    tenant = TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
    )
    assert response_cache_namespace({"tenant_context": tenant}) == (
        "tenant-a:workspace-a"
    )


def test_v3_schema_invalidates_historical_keys():
    cache = ResponseCache(enabled=True, ttl=60)
    digest = cache._key("m", "sys", "prompt", [], None)
    assert "response-v3" in cache._redis_key(digest)


def test_unreadable_image_is_never_keyed_by_path(tmp_path):
    missing = tmp_path / "reused-upload.png"
    cache = ResponseCache(enabled=True, ttl=60)
    cache.put("m", "sys", "p", [], [str(missing)], "unsafe")
    assert cache.get("m", "sys", "p", [], [str(missing)]) is None
    assert cache.stats()["errors"] == 2


def test_v3_image_digest_bytes_and_canonical_values():
    cache = ResponseCache(enabled=True, ttl=60)
    digest_key = cache._key(
        "m", None, "p", {"flags": {"b", "a"}},
        [{"content_sha256": "ABC", "page": 2}],
    )
    bytes_key = cache._key("m", None, "p", [], [b"image"])
    assert digest_key != bytes_key
    assert cache._image_fingerprint({"digest": "ABC"})["sha256"] == "abc"
    assert cache._image_fingerprint(b"image")["size"] == 5


def test_redis_l2_hit_and_write(monkeypatch):
    from artpm_agent.core import redis_cache

    stored = {}
    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setattr(redis_cache, "cache_get_json", lambda key: stored.get(key))
    monkeypatch.setattr(
        redis_cache,
        "cache_set_json",
        lambda key, value, ttl: stored.update({key: value, "ttl": ttl}),
    )
    cache = ResponseCache(enabled=True, ttl=45)
    cache.put("m", "sys", "p", [], None, "shared")
    assert stored["ttl"] == 45
    cache._store.clear()
    assert cache.get("m", "sys", "p", [], None) == "shared"
    assert cache.stats()["redis_hits"] == 1


def test_single_flight_waiter_receives_owner_value():
    cache = ResponseCache(enabled=True, ttl=60)
    hit, reservation = cache.acquire("m", "sys", "p", [], None)
    assert hit is None and reservation
    result = []

    def waiter():
        result.append(cache.acquire("m", "sys", "p", [], None, wait_timeout=1))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    cache.put("m", "sys", "p", [], None, "answer")
    cache.release(reservation)
    thread.join(timeout=1)
    assert result == [("answer", None)]
    cache.release(None)


def test_single_flight_timeout_disabled_and_error_paths(monkeypatch):
    cache = ResponseCache(enabled=False, ttl=60)
    assert cache.acquire("m", "sys", "p", [], None) == (None, None)

    cache = ResponseCache(enabled=True, ttl=60)
    _, reservation = cache.acquire("m", "sys", "p", [], None)
    assert cache.acquire("m", "sys", "p", [], None, wait_timeout=0) == (None, None)
    cache.release(reservation)
    monkeypatch.setattr(cache, "_key", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError()))
    assert cache.acquire("m", "sys", "p", [], None) == (None, None)
    cache.put("m", "sys", "p", [], None, "ignored")
    assert cache.stats()["errors"] >= 2
