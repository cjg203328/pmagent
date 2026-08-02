"""Tests for the LLM response cache."""
import os

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
