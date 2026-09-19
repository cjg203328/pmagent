"""Tests for the tool execution pipeline (pre/post policies + spill)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from artpm_agent.runtime.agent_loop import AgentLoop, AssistantTurn
from artpm_agent.runtime.pipeline import (
    ToolExecutionPipeline,
    cleanup_spilled_results,
    delete_spilled_result,
    load_spilled_result,
    spill_policy,
    spill_scope_directory,
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
    assert load_spilled_result(spilled_path, spill_dir=tmp_path) == long_content


def test_spill_policy_bounds_error_output(tmp_path):
    content = "boom" * 2_000
    policy = spill_policy(max_chars=512, spill_dir=tmp_path)
    rewritten = policy(_call(), _tool(), ToolResult.error(content), {})

    assert rewritten is not None
    assert rewritten.is_error is True
    assert len(rewritten.content) <= 512
    assert load_spilled_result(
        rewritten.details["spilled_path"],
        spill_dir=tmp_path,
    ) == content


def test_spill_bounds_and_json_normalizes_large_details(tmp_path):
    result = spill_tool_result(
        ToolResult(
            content="small",
            details={
                "payload": "x" * 5_000,
                "not_finite": float("nan"),
                "path": tmp_path,
            },
        ),
        max_chars=1_000,
        details_max_chars=512,
        spill_dir=tmp_path,
        tool_name="details",
    )

    serialized = json.dumps(dict(result.details), allow_nan=False)
    assert len(serialized) <= 512
    details_path = result.details["spilled_details_path"]
    full_details = json.loads(
        load_spilled_result(details_path, spill_dir=tmp_path)
    )
    assert full_details["payload"] == "x" * 5_000
    assert full_details["not_finite"] == "nan"
    assert full_details["path"] == str(tmp_path)


def test_agent_loop_applies_result_bounds_without_optional_pipeline(tmp_path):
    calls = 0
    tool = AgentTool(
        name="large",
        description="large",
        execute=lambda *_: ToolResult(
            "x" * 2_000,
            {"payload": "y" * 2_000},
        ),
    )

    def provider(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AssistantTurn(tool_calls=(ToolCall("large"),))
        return AssistantTurn("done")

    events = list(
        AgentLoop(
            ToolRegistry([tool]),
            tool_result_max_chars=512,
            tool_result_details_max_chars=512,
            tool_spill_dir=tmp_path,
        ).run(
            "large",
            provider,
            context={"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        )
    )
    tool_end = next(
        event
        for event in events
        if event.type.value == "tool_execution_end"
    )

    assert len(tool_end.tool_result["content"]) <= 512
    assert "spilled_path" in tool_end.tool_result["details"]
    assert "spilled_details_path" in tool_end.tool_result["details"]
    expected_scope = spill_scope_directory(
        spill_dir=tmp_path,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    assert Path(tool_end.tool_result["details"]["spilled_path"]).parent == expected_scope


def test_spills_are_isolated_by_tenant_and_workspace(tmp_path):
    def spill(tenant_id, workspace_id, marker):
        result = spill_tool_result(
            ToolResult(marker * 1_000),
            max_chars=512,
            spill_dir=tmp_path,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            ttl_seconds=None,
        )
        return Path(result.details["spilled_path"])

    tenant_a_workspace_a = spill("tenant-a", "workspace-a", "a")
    tenant_a_workspace_b = spill("tenant-a", "workspace-b", "b")
    tenant_b_workspace_a = spill("tenant-b", "workspace-a", "c")

    parents = {
        tenant_a_workspace_a.parent,
        tenant_a_workspace_b.parent,
        tenant_b_workspace_a.parent,
    }
    assert len(parents) == 3
    assert load_spilled_result(
        tenant_a_workspace_a,
        spill_dir=tmp_path,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    ) == "a" * 1_000
    assert load_spilled_result(
        tenant_a_workspace_b,
        spill_dir=tmp_path,
        tenant_id="tenant-a",
        workspace_id="workspace-b",
    ) == "b" * 1_000
    assert load_spilled_result(
        tenant_b_workspace_a,
        spill_dir=tmp_path,
        tenant_id="tenant-b",
        workspace_id="workspace-a",
    ) == "c" * 1_000
    with pytest.raises(ValueError, match="managed scope"):
        load_spilled_result(
            tenant_a_workspace_a,
            spill_dir=tmp_path,
            tenant_id="tenant-b",
            workspace_id="workspace-a",
        )


def test_tenant_context_has_priority_over_untrusted_spill_scope_values(tmp_path):
    trusted_context = type(
        "TenantContext",
        (),
        {"tenant_id": "tenant-trusted", "workspace_id": "workspace-trusted"},
    )()
    result = spill_tool_result(
        ToolResult("x" * 1_000),
        max_chars=512,
        spill_dir=tmp_path,
        tenant_id="tenant-explicit",
        workspace_id="workspace-explicit",
        ttl_seconds=None,
    )
    explicit_path = Path(result.details["spilled_path"])
    tool = AgentTool(
        name="large",
        description="large",
        execute=lambda *_: ToolResult("y" * 1_000),
    )
    provider_calls = 0

    def provider(*_args):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return AssistantTurn(tool_calls=(ToolCall("large"),))
        return AssistantTurn("done")

    events = list(
        AgentLoop(
            ToolRegistry([tool]),
            tool_result_max_chars=512,
            tool_spill_dir=tmp_path,
        ).run(
            "large",
            provider,
            context={
                "tenant_context": trusted_context,
                "tenant_id": "tenant-forged",
                "workspace_id": "workspace-forged",
            },
        )
    )
    tool_result = next(
        event.tool_result
        for event in events
        if event.type.value == "tool_execution_end"
    )
    trusted_path = Path(tool_result["details"]["spilled_path"])

    assert explicit_path.parent == spill_scope_directory(
        spill_dir=tmp_path,
        tenant_id="tenant-explicit",
        workspace_id="workspace-explicit",
    )
    assert trusted_path.parent == spill_scope_directory(
        spill_dir=tmp_path,
        tenant_id="tenant-trusted",
        workspace_id="workspace-trusted",
    )
    assert trusted_path.parent != explicit_path.parent


def test_workspace_less_tenant_context_does_not_fall_back_to_forged_workspace(
    tmp_path,
):
    tenant_context = type(
        "TenantContext",
        (),
        {"tenant_id": "tenant-trusted", "workspace_id": ""},
    )()
    result = spill_tool_result(
        ToolResult("x" * 1_000),
        max_chars=512,
        spill_dir=tmp_path,
        tenant_id="tenant-forged",
        workspace_id="workspace-forged",
        ttl_seconds=None,
    )

    # The helper is exercised through the same context path used by spill_policy.
    policy = spill_policy(max_chars=512, spill_dir=tmp_path, ttl_seconds=None)
    rewritten = policy(
        _call(),
        _tool(),
        ToolResult("y" * 1_000),
        {
            "tenant_context": tenant_context,
            "tenant_id": "tenant-forged",
            "workspace_id": "workspace-forged",
        },
    )

    assert rewritten is not None
    trusted_path = Path(rewritten.details["spilled_path"])
    assert trusted_path.parent == spill_scope_directory(
        spill_dir=tmp_path,
        tenant_id="tenant-trusted",
        workspace_id=None,
    )
    assert trusted_path.parent != Path(result.details["spilled_path"]).parent


def test_malicious_scope_values_cannot_escape_spill_root(tmp_path):
    scope = spill_scope_directory(
        spill_dir=tmp_path,
        tenant_id="../../tenant:C:\\outside",
        workspace_id="..\\..//workspace",
    )
    scope.relative_to(tmp_path.resolve())
    assert ".." not in scope.relative_to(tmp_path.resolve()).parts

    result = spill_tool_result(
        ToolResult("x" * 1_000),
        max_chars=512,
        spill_dir=tmp_path,
        tenant_id="../../tenant:C:\\outside",
        workspace_id="..\\..//workspace",
        ttl_seconds=None,
    )
    spilled = Path(result.details["spilled_path"])
    assert spilled.parent == scope


def test_ttl_cleanup_preserves_new_and_declared_active_files(tmp_path):
    scope = {"tenant_id": "tenant-a", "workspace_id": "workspace-a"}

    def spill(marker):
        result = spill_tool_result(
            ToolResult(marker * 1_000),
            max_chars=512,
            spill_dir=tmp_path,
            ttl_seconds=None,
            **scope,
        )
        return Path(result.details["spilled_path"])

    old = spill("o")
    active = spill("a")
    new = spill("n")
    now = time.time()
    os.utime(old, (now - 120, now - 120))
    os.utime(active, (now - 120, now - 120))

    deleted = cleanup_spilled_results(
        spill_dir=tmp_path,
        ttl_seconds=60,
        active_paths=(active,),
        now=now,
        **scope,
    )

    assert deleted == (old,)
    assert not old.exists()
    assert active.exists()
    assert new.exists()


def test_new_spill_runs_ttl_cleanup_for_its_own_scope(tmp_path):
    scope = {"tenant_id": "tenant-a", "workspace_id": "workspace-a"}
    old_result = spill_tool_result(
        ToolResult("o" * 1_000),
        max_chars=512,
        spill_dir=tmp_path,
        ttl_seconds=None,
        **scope,
    )
    old = Path(old_result.details["spilled_path"])
    old_time = time.time() - 120
    os.utime(old, (old_time, old_time))

    new_result = spill_tool_result(
        ToolResult("n" * 1_000),
        max_chars=512,
        spill_dir=tmp_path,
        ttl_seconds=60,
        **scope,
    )

    assert not old.exists()
    assert Path(new_result.details["spilled_path"]).exists()


def test_explicit_delete_enforces_scope_and_is_idempotent(tmp_path):
    result = spill_tool_result(
        ToolResult("x" * 1_000),
        max_chars=512,
        spill_dir=tmp_path,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        ttl_seconds=None,
    )
    path = Path(result.details["spilled_path"])

    with pytest.raises(ValueError, match="managed scope"):
        delete_spilled_result(
            path,
            spill_dir=tmp_path,
            tenant_id="tenant-b",
            workspace_id="workspace-a",
        )

    assert delete_spilled_result(
        path,
        spill_dir=tmp_path,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )
    assert not delete_spilled_result(
        path,
        spill_dir=tmp_path,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )


def test_delete_rejects_an_unmanaged_path(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="managed scope"):
        delete_spilled_result(
            outside,
            spill_dir=tmp_path,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
        )

    assert outside.read_text(encoding="utf-8") == "keep"


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
