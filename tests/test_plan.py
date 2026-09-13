"""Tests for the plan collaboration state machine."""

from __future__ import annotations

import pytest

from artpm_agent.runtime.plan import (
    PlanCoordinator,
    PlanError,
    PlanStatus,
    PlanStepStatus,
    PlanStore,
)


@pytest.fixture()
def coordinator(tmp_path):
    store = PlanStore(tmp_path / "plans.json")
    return PlanCoordinator(store)


def test_create_propose_approve_cycle(coordinator):
    plan = coordinator.create_plan(
        "优化报价流程", "缩短报价测算耗时", ["分析现状", "提出方案", "实施"]
    )
    assert plan.status is PlanStatus.DRAFT
    assert len(plan.steps) == 3

    proposed = coordinator.propose(plan.plan_id)
    assert proposed.status is PlanStatus.PROPOSED

    approved = coordinator.approve(plan.plan_id)
    assert approved.status is PlanStatus.APPROVED

    # Persistence round-trip
    reloaded = coordinator.store.get(plan.plan_id)
    assert reloaded.status is PlanStatus.APPROVED
    assert [step.title for step in reloaded.steps] == ["分析现状", "提出方案", "实施"]


def test_reject_sends_back_to_draft_with_feedback(coordinator):
    plan = coordinator.create_plan("t", "o", ["a"])
    coordinator.propose(plan.plan_id)

    rejected = coordinator.reject(plan.plan_id, "缺少成本估算步骤")

    assert rejected.status is PlanStatus.DRAFT
    assert rejected.feedback == "缺少成本估算步骤"

    # Cannot approve a rejected plan without re-proposing
    with pytest.raises(PlanError, match="expected proposed"):
        coordinator.approve(plan.plan_id)


def test_revise_replaces_steps(coordinator):
    plan = coordinator.create_plan("t", "o", ["a", "b"])
    revised = coordinator.revise(plan.plan_id, ["a2", "b2", "c2"])
    assert [step.title for step in revised.steps] == ["a2", "b2", "c2"]
    assert revised.status is PlanStatus.DRAFT


def test_execute_requires_approved(coordinator):
    plan = coordinator.create_plan("t", "o", ["a"])
    with pytest.raises(PlanError, match="expected approved"):
        coordinator.execute(plan.plan_id, lambda plan, step: "x")


def test_execute_runs_steps_in_order(coordinator):
    plan = coordinator.create_plan("t", "o", ["a", "b", "c"])
    coordinator.propose(plan.plan_id)
    coordinator.approve(plan.plan_id)

    order = []

    def runner(plan, step):
        order.append(step.title)
        return f"done:{step.title}"

    executed = coordinator.execute(plan.plan_id, runner)

    assert executed.status is PlanStatus.EXECUTED
    assert order == ["a", "b", "c"]
    assert all(step.status is PlanStepStatus.COMPLETED for step in executed.steps)
    assert executed.steps[0].result == "done:a"


def test_execute_stops_on_runner_failure(coordinator):
    plan = coordinator.create_plan("t", "o", ["a", "b"])
    coordinator.propose(plan.plan_id)
    coordinator.approve(plan.plan_id)

    def runner(plan, step):
        if step.title == "b":
            raise RuntimeError("boom")
        return "ok"

    executed = coordinator.execute(plan.plan_id, runner)

    assert executed.status is PlanStatus.APPROVED  # not fully executed
    assert executed.steps[0].status is PlanStepStatus.COMPLETED
    assert executed.steps[1].status is PlanStepStatus.FAILED
    assert "boom" in (executed.steps[1].result or "")


def test_requires_approval_step_blocked_without_confirm(coordinator):
    plan = coordinator.create_plan(
        "t",
        "o",
        [
            {"title": "safe", "description": "read-only"},
            {
                "title": "risky",
                "description": "writes",
                "requires_approval": True,
            },
        ],
    )
    coordinator.propose(plan.plan_id)
    coordinator.approve(plan.plan_id)

    executed = coordinator.execute(plan.plan_id, lambda plan, step: "ran")

    assert executed.steps[0].status is PlanStepStatus.COMPLETED
    assert executed.steps[1].status is PlanStepStatus.BLOCKED
    assert executed.status is PlanStatus.EXECUTED  # blocked != failed


def test_confirm_steps_unblocks_high_risk_step(coordinator):
    plan = coordinator.create_plan(
        "t",
        "o",
        [
            {"title": "safe", "description": "read-only"},
            {
                "title": "risky",
                "description": "writes",
                "requires_approval": True,
            },
        ],
    )
    coordinator.propose(plan.plan_id)
    coordinator.approve(plan.plan_id)
    coordinator.confirm_steps(plan.plan_id, ["risky"])

    executed = coordinator.execute(plan.plan_id, lambda plan, step: "ran")

    assert executed.steps[1].status is PlanStepStatus.COMPLETED
    assert executed.steps[1].result == "ran"


def test_confirm_steps_requires_approved(coordinator):
    plan = coordinator.create_plan("t", "o", ["a"])
    with pytest.raises(PlanError, match="expected approved"):
        coordinator.confirm_steps(plan.plan_id, ["a"])


def test_state_transitions_validated(coordinator):
    plan = coordinator.create_plan("t", "o", ["a"])
    # Cannot reject a draft (only proposed plans can be rejected)
    with pytest.raises(PlanError, match="expected proposed"):
        coordinator.reject(plan.plan_id, "no")
    # Cannot propose twice in a row (draft -> proposed only once)
    coordinator.propose(plan.plan_id)
    with pytest.raises(PlanError, match="expected draft"):
        coordinator.propose(plan.plan_id)


def test_store_delete_and_list(tmp_path):
    store = PlanStore(tmp_path / "plans.json")
    coordinator = PlanCoordinator(store)
    first = coordinator.create_plan("one", "o", ["a"])
    second = coordinator.create_plan("two", "o", ["b"])

    plans = store.list()
    assert len(plans) == 2
    assert {plan.title for plan in plans} == {"one", "two"}

    assert store.delete(first.plan_id) is True
    assert store.delete(first.plan_id) is False
    assert len(store.list()) == 1
    assert second.title == "two"


def test_create_plan_validates_inputs(coordinator):
    with pytest.raises(PlanError):
        coordinator.create_plan("", "o", ["a"])
    with pytest.raises(PlanError):
        coordinator.create_plan("t", "", ["a"])
    with pytest.raises(PlanError):
        coordinator.create_plan("t", "o", [])
    with pytest.raises(PlanError):
        coordinator.create_plan("t", "o", ["a"] * 65)
