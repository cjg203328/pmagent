"""Tests for the LLM response cache."""
from artpm_agent.providers.response_cache import ResponseCache


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
