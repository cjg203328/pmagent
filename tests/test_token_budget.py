"""Tests for token-aware context budgeting (Phase 4 extension)."""
from artpm_agent.harness.memory_retrieval import (
    MemoryContextBudget,
    inject_memory_context,
)
from artpm_agent.harness.token_budget import (
    apply_token_budget,
    compact_text,
    estimate_tokens,
)


def _prio(block: str) -> int:
    if block.startswith("【元记忆"):
        return 1
    if block.startswith("【优化策略"):
        return 2
    if block.startswith("【用户偏好"):
        return 3
    if block.startswith("【相关记忆"):
        return 4
    return 0


# ── token counter ──


def test_estimate_tokens_heuristic_cjk():
    # 16 CJK chars ≈ 16/1.6 = 10 tokens
    assert estimate_tokens("中" * 16) == 10


def test_estimate_tokens_heuristic_latin():
    assert estimate_tokens("a" * 40) == 10  # 40/4


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_custom_counter():
    assert estimate_tokens("anything", counter=lambda t: 7) == 7


# ── compact_text ──


def test_compact_text_dedup_and_collapse():
    text = "a\na\n\nb"
    assert compact_text(text) == "a\nb"


def test_compact_text_keep_ratio():
    text = "x" * 100
    out = compact_text(text, keep_ratio=0.5)
    assert len(out) <= 50


# ── apply_token_budget ──


def test_token_budget_drops_lowest_priority():
    blocks = [
        "【相关记忆】\n" + "中" * 40,
        "【元记忆】\n" + "中" * 40,
    ]
    kept = apply_token_budget(blocks, max_tokens=20, priority_fn=_prio)
    # meta (lowest priority) dropped; memory kept
    assert all("【元记忆" not in b for b in kept)
    assert any("【相关记忆" in b for b in kept)


def test_token_budget_truncates_single_block():
    blocks = ["【用户偏好与纠正】\n" + "中" * 200]
    kept = apply_token_budget(blocks, max_tokens=20, priority_fn=_prio)
    assert len(kept) == 1
    assert "…" in kept[0]
    assert estimate_tokens(kept[0]) <= 22  # truncation granularity tolerance


def test_token_budget_compact_mode():
    block = "【用户偏好与纠正】\n" + "重复\n重复\n" + "中" * 300
    kept = apply_token_budget(
        [block], max_tokens=20, priority_fn=_prio, compression="compact"
    )
    assert len(kept) == 1
    # duplicate "重复" line removed by compact
    assert kept[0].count("重复") <= 1


def test_token_budget_summarize_hook():
    block = "【用户偏好与纠正】\n" + "中" * 300
    kept = apply_token_budget(
        [block],
        max_tokens=20,
        priority_fn=_prio,
        summarize_fn=lambda t: "摘要：用户偏好X",
    )
    assert kept == ["摘要：用户偏好X"]


def test_token_budget_within_limit_passes_through():
    blocks = ["【用户偏好与纠正】\n用户偏好X"]
    kept = apply_token_budget(blocks, max_tokens=1000, priority_fn=_prio)
    assert len(kept) == 1


# ── MemoryContextBudget + inject end-to-end ──


def test_budget_defaults_include_token_fields():
    b = MemoryContextBudget()
    assert b.max_total_tokens == 0
    assert b.compression == "truncate"


def test_inject_respects_token_budget_end_to_end():
    from artpm_agent.memory.feedback_store import FeedbackEntry

    entry = FeedbackEntry(
        kind="preference",
        content="中" * 200,  # long CJK preference → many tokens
        scope="global",
        active=True,
        metadata={},
        id="f1",
    )
    fb = type("F", (), {"active": lambda self: [entry]})()
    st = type("S", (), {"active": lambda self: []})()

    class Ctx:
        def __init__(self):
            self.user_input = "test unique query with no keywords"
            self.conversation_history = []
            self.agent = None
            self.extra = {}

    ctx = Ctx()
    inject_memory_context(
        ctx,
        feedback_store=fb,
        strategy_store=st,
        max_context_tokens=20,
    )
    # Feedback block present (meta dropped), and token-bounded.
    assert "【用户偏好" in ctx.knowledge_context
    assert estimate_tokens(ctx.knowledge_context) <= 22
