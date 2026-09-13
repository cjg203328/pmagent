"""Tests for the tool execution pipeline (pre/post policies + spill)."""

from __future__ import annotations

from pathlib import Path

import pytest

from artpm_agent.runtime.agent_loop import AgentLoop
from artpm_agent.runtime.pipeline import (
    ToolExecutionPipeline,
    load_spilled_result,
    spill_policy,
    spill_tool_result,
)
from artpm_agent.runtime.tools import (
    AgentTool,
    BeforeToolCallDecision,
    ToolCall,
    ToolRegistry,
    ToolResult,
)


def _tool(name: str = "echo", *, content: str = "ok") -> AgentTool:
    return AgentTool(name=name, description="echo", execute=lambda **_: content)


def _call(name: str = "echo") -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments={})


# ── pipeline policies ──


def test_pre_policies_short_circuit_on_first_decision():
    pipeline = ToolExecutionPipeline()
    order = []

    def first(call, tool, context, turn_ctx):
        order.append("first")
        return None

    def second(call, tool, context, turn_ctx):
        order.append("second")
        return BeforeToolCallDecision(block=True, approved=False, reason="no")

    def third(call, tool, context, turn_ctx):
        order.append("third")  # must not run

    pipeline.add_pre(first).add_pre(second).add_pre(third)
    combined = pipeline.compose_pre()

    decision = combined(_call(), _tool(), {}, {})

    assert decision is not None and decision.approved is False
    assert order == ["first", "second"]


def test_post_policies_chain_rewrites_in_order():
    pipeline = ToolExecutionPipeline()
    order = []

    def tag_a(call, tool, result, context):
        order.append("a")
        return ToolResult(content=result.content + "-a")

    def tag_b(call, tool, result, context):
        order.append("b")
        return ToolResult(content=result.content + "-b")

    pipeline.add_post(tag_a).add_post(tag_b)
    combined = pipeline.compose_post()

    final = combined(_call(), _tool(), ToolResult(content="x"), {})

    assert order == ["a", "b"]
    assert final.content == "x-a-b"


def test_post_policy_none_keeps_previous_result():
    pipeline = ToolExecutionPipeline()

    def pass_through(call, tool, result, context):
        return None  # keep previous

    pipeline.add_post(pass_through)
    combined = pipeline.compose_post()

    final = combined(_call(), _tool(), ToolResult(content="keep"), {})
    assert final.content == "keep"


def test_legacy_hook_runs_after_pipeline_policies():
    pipeline = ToolExecutionPipeline()
    pipeline.add_pre(lambda call, tool, ctx, tc: None)
    pipeline.add_post(lambda call, tool, result, ctx: ToolResult(content=result.content + "-p"))
    order = []

    def legacy_after(call, tool, result, context):
        order.append("legacy")
        return ToolResult(content=result.content + "-l")

    combined = pipeline.compose_post(legacy_after)
    final = combined(_call(), _tool(), ToolResult(content="x"), {})
    assert final.content == "x-p-l"
    assert order == ["legacy"]


def test_compose_returns_none_when_empty():
    pipeline = ToolExecutionPipeline()
    assert pipeline.compose_pre() is None
    assert pipeline.compose_post() is None


# ── spill ──


def test_spill_keeps_short_results_untouched(tmp_path):
    result = ToolResult(content="small")
    spilled = spill_tool_result(result, spill_dir=tmp_path)
    assert spilled is result  # unchanged object
    assert not (tmp_path / "tool_spill").exists() or not any(tmp_path.iterdir())


def test_spill_truncates_and_writes_full_payload(tmp_path):
    long_content = "x" * 10_000
    result = spill_tool_result(
        ToolResult(content=long_content),
        max_chars=2_000,
        spill_dir=tmp_path,
        tool_name="echo",
    )

    assert len(result.content) <= 2_000
    assert "truncated" in result.content
    assert result.details["spilled_original_length"] == 10_000
    spilled_path = result.details["spilled_path"]
    assert Path(spilled_path).is_file()
    assert load_spilled_result(spilled_path) == long_content


def test_spill_policy_skips_errors():
    policy = spill_policy(spill_dir="ignored")
    rewritten = policy(_call(), _tool(), ToolResult.error("boom"), {})
    assert rewritten is None


def test_spill_policy_returns_result_for_short_output():
    policy = spill_policy(max_chars=5_000, spill_dir="ignored")
    rewritten = policy(_call(), _tool(), ToolResult(content="tiny"), {})
    assert rewritten is not None
    assert rewritten.content == "tiny"


# ── AgentLoop integration ──


def test_agent_loop_pipeline_composes_with_legacy_hook():
    registry = ToolRegistry()
    registry.register(_tool())

    pipeline = ToolExecutionPipeline()
    seen = []
    pipeline.add_post(
        lambda call, tool, result, ctx: (
            seen.append("policy") or ToolResult(content="from-policy")
        )
    )

    loop = AgentLoop(
        registry,
        max_turns=1,
        max_tool_calls_per_turn=1,
        pipeline=pipeline,
        after_tool_call=lambda call, tool, result, ctx: (
            seen.append("legacy") or result
        ),
    )

    assert loop.tool_executor is not None
    assert seen == []  # hooks are composed lazily at call time


def test_agent_loop_rejects_non_pipeline():
    registry = ToolRegistry()
    with pytest.raises(TypeError, match="pipeline must be a ToolExecutionPipeline"):
        AgentLoop(registry, pipeline=object())  # type: ignore[arg-type]


def test_agent_loop_without_pipeline_still_works():
    registry = ToolRegistry()
    registry.register(_tool())
    loop = AgentLoop(registry, max_turns=1)
    assert loop.tool_executor.before_tool_call is None
    assert loop.tool_executor.after_tool_call is None
