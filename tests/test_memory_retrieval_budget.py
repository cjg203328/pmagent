"""Tests for context token budgeting and routing cache."""
from artpm_agent.harness.memory_retrieval import (
    inject_memory_context,
    _apply_context_budget,
)
from artpm_agent.routing.service import IntentRouter


# ── context budget ──

def test_apply_context_budget_drops_lowest_priority():
    blocks = [
        "【相关记忆】\nlong historical memory content here",
        "【用户偏好与纠正】\nuser prefers X",
        "【优化策略（来自经验复盘）】\nuse strategy Y",
        "【元记忆：知识缺口与建议】\nsearch the web",
    ]
    kept = _apply_context_budget(blocks, max_chars=40)
    # meta (lowest priority) must be dropped first
    assert all("【元记忆" not in b for b in kept)


def test_apply_context_budget_keeps_all_when_under():
    blocks = ["【相关记忆】\nshort", "【用户偏好与纠正】\nx"]
    kept = _apply_context_budget(blocks, max_chars=1000)
    assert len(kept) == 2


def test_apply_context_budget_truncates_memory_when_only_block():
    blocks = ["【相关记忆】\n" + "a" * 500]
    kept = _apply_context_budget(blocks, max_chars=50)
    assert len(kept) == 1
    assert len(kept[0]) <= 50


def test_inject_respects_budget_end_to_end():
    from artpm_agent.memory.feedback_store import FeedbackEntry

    class Ctx:
        def __init__(self):
            self.user_input = "test unique query with no keywords"
            self.conversation_history = []
            self.agent = None
            self.extra = {}

    entry = FeedbackEntry(
        kind="preference",
        content="用户偏好X",
        scope="global",
        active=True,
        metadata={},
        id="f1",
    )
    fb = type("F", (), {"active": lambda self: [entry]})()
    st = type("S", (), {"active": lambda self: []})()
    ctx = Ctx()
    inject_memory_context(
        ctx,
        memory_manager=None,
        knowledge_store=None,
        feedback_store=fb,
        strategy_store=st,
        max_context_chars=20,
    )
    # Lowest-priority meta block is dropped; the higher-priority feedback block
    # is kept (truncated to fit the budget).
    assert "【元记忆" not in ctx.knowledge_context
    assert "【用户偏好" in ctx.knowledge_context


# ── routing cache ──

def test_route_cache_prevents_recompute():
    router = IntentRouter(lambda t: [0.1] * 8, lambda: None, {})
    q = "xyzqwertyuiop unique query with no keywords"
    r1 = router.detect(q)
    # If detection were recomputed, this would raise — proving the cache hit.
    router.detect_via_embedding = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("should-be-cached")
    )
    r2 = router.detect(q)
    assert r2 == r1
