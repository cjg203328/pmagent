"""Tests for the subagent delegation capability."""

from __future__ import annotations

import threading
import time

import pytest

from artpm_agent.runtime.subagent import (
    InProcessSubagentExecutor,
    SubagentPool,
    SubagentRequest,
    subagent_delegate_tool,
)


def _pool(runner=None, **kwargs):
    executor = InProcessSubagentExecutor(
        runner or (lambda request: f"answer:{request.task}")
    )
    return SubagentPool(executor, **kwargs)


def test_delegate_tool_runs_subtask_and_returns_output():
    tool = subagent_delegate_tool(_pool())
    result = tool.execute(task="调研报价公式")

    assert result["output"] == "answer:调研报价公式"
    assert "error" not in result


def test_delegate_tool_supports_agent_tool_invoke_contract():
    tool = subagent_delegate_tool(_pool())

    result = tool.invoke(
        "call-1",
        {"task": "调研报价公式"},
        threading.Event(),
    )

    assert result.details["output"] == "answer:调研报价公式"
    assert not result.is_error


def test_delegate_tool_rejects_empty_task():
    tool = subagent_delegate_tool(_pool())
    result = tool.execute(task="   ")
    assert "error" in result


def test_delegate_tool_propagates_runner_error_as_error_result():
    def runner(request):
        raise RuntimeError("child failed")

    tool = subagent_delegate_tool(_pool(runner))
    result = tool.execute(task="boom")
    assert "error" in result
    assert "child failed" in result["error"]


def test_pool_enforces_max_depth():
    pool = SubagentPool(
        InProcessSubagentExecutor(lambda request: "ok"),
        max_depth=2,
    )
    result = pool.run(SubagentRequest(task="deep", depth=3))
    assert result.is_error
    assert "exceeds limit" in (result.error or "")


def test_pool_runs_concurrently_bounded_by_semaphore():
    entered = threading.Semaphore(0)
    release = threading.Event()
    lock = threading.Lock()
    active = []
    peak = {"value": 0}

    def runner(request):
        with lock:
            active.append(1)
            peak["value"] = max(peak["value"], len(active))
        if len(active) >= 2:
            entered.release()
        try:
            release.wait(timeout=5)
        finally:
            with lock:
                active.pop()
        return "ok"

    pool = SubagentPool(
        InProcessSubagentExecutor(runner),
        max_concurrency=2,
        timeout_seconds=10,
    )
    results = []
    threads = [
        threading.Thread(
            target=lambda: results.append(pool.run(SubagentRequest(task="t")))
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    assert entered.acquire(timeout=5)  # first two are running concurrently
    assert peak["value"] == 2
    release.set()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not result.is_error for result in results)
    assert len(results) == 4


def test_pool_timeout_returns_error():
    def slow(request):
        time.sleep(5)
        return "late"

    pool = SubagentPool(
        InProcessSubagentExecutor(slow),
        timeout_seconds=0.2,
    )
    result = pool.run(SubagentRequest(task="slow"))
    assert result.is_error
    assert "timed out" in (result.error or "")


def test_pool_runner_string_output_becomes_result():
    pool = SubagentPool(InProcessSubagentExecutor(lambda request: "plain"))
    result = pool.run(SubagentRequest(task="t"))
    assert result.output == "plain"
    assert not result.is_error


def test_pool_validates_arguments():
    with pytest.raises(ValueError):
        SubagentPool(InProcessSubagentExecutor(lambda r: "x"), max_concurrency=0)
    with pytest.raises(ValueError):
        SubagentPool(InProcessSubagentExecutor(lambda r: "x"), max_depth=0)
    with pytest.raises(ValueError):
        SubagentPool(InProcessSubagentExecutor(lambda r: "x"), timeout_seconds=-1)
    with pytest.raises(TypeError):
        subagent_delegate_tool("not-a-pool")


def test_active_count_tracks_running_subtasks():
    release = threading.Event()

    def runner(request):
        release.wait(timeout=5)
        return "ok"

    pool = SubagentPool(
        InProcessSubagentExecutor(runner),
        max_concurrency=2,
        timeout_seconds=10,
    )
    result_box = []

    def _work():
        result_box.append(pool.run(SubagentRequest(task="t")))

    thread = threading.Thread(target=_work)
    thread.start()
    time.sleep(0.2)

    assert pool.active_count == 1
    release.set()
    thread.join(timeout=5)
    assert pool.active_count == 0
    assert not result_box[0].is_error
