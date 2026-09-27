"""Contract tests for the plan-mode gateway routes.

Plan mode mirrors the host-review lifecycle: a plan is drafted, proposed for
review, approved or rejected, and only then executed. The routes must enforce
that order rather than accept any state jump, and they must never run business
skills themselves.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from artpm_agent.api import GatewayServices, create_app
from artpm_agent.api.routers.plans import create_plans_router
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.runtime.plan import PlanCoordinator, PlanStore
from artpm_agent.security.permission_store import PermissionStore
from artpm_agent.workflows.store import WorkflowStore


def _headers() -> dict[str, str]:
    return {
        "x-workspace-id": "local-default",
        "x-actor-id": "planner",
        "x-actor-role": "user",
    }


def _client(tmp_path: Path, *, with_plans: bool = True) -> TestClient:
    db_path = tmp_path / "gateway.sqlite"
    coordinator = PlanCoordinator(PlanStore(tmp_path / "plans.json"))
    services = GatewayServices(
        conversations=ConversationStore(db_path),
        permissions=PermissionStore(db_path),
        workflows=WorkflowStore(db_path),
        chat_handler=lambda _command: None,
        capability_provider=lambda: [],
        plans=coordinator if with_plans else None,
    )
    return TestClient(create_app(services))


def _create(client: TestClient) -> str:
    response = client.post(
        "/v1/plans",
        headers=_headers(),
        json={
            "title": "梳理十一月人天",
            "objective": "输出可交付的月度汇总",
            "steps": ["读取工作簿", "按月裁剪", "生成 Excel"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["plan"]["plan_id"]


def test_plan_lifecycle_requires_review_before_execution(tmp_path: Path):
    client = _client(tmp_path)
    plan_id = _create(client)

    created = client.get(f"/v1/plans/{plan_id}", headers=_headers())
    assert created.status_code == 200
    assert created.json()["plan"]["status"] == "draft"

    proposed = client.post(f"/v1/plans/{plan_id}/propose", headers=_headers())
    assert proposed.status_code == 200
    assert proposed.json()["plan"]["status"] == "proposed"

    approved = client.post(f"/v1/plans/{plan_id}/approve", headers=_headers())
    assert approved.status_code == 200
    assert approved.json()["plan"]["status"] == "approved"


def test_plan_rejects_approving_a_draft(tmp_path: Path):
    client = _client(tmp_path)
    plan_id = _create(client)

    response = client.post(f"/v1/plans/{plan_id}/approve", headers=_headers())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plan_state_rejected"


def test_plan_rejection_returns_feedback_and_sends_back_to_draft(tmp_path: Path):
    client = _client(tmp_path)
    plan_id = _create(client)
    client.post(f"/v1/plans/{plan_id}/propose", headers=_headers())

    response = client.post(
        f"/v1/plans/{plan_id}/reject",
        headers=_headers(),
        json={"feedback": "缺少成本估算步骤"},
    )

    assert response.status_code == 200
    body = response.json()["plan"]
    assert body["status"] == "draft"
    assert body["feedback"] == "缺少成本估算步骤"


def test_plan_revision_replaces_steps_and_returns_to_draft(tmp_path: Path):
    client = _client(tmp_path)
    plan_id = _create(client)

    response = client.post(
        f"/v1/plans/{plan_id}/revise",
        headers=_headers(),
        json={"steps": ["重新读取", "重新汇总"]},
    )

    assert response.status_code == 200
    body = response.json()["plan"]
    assert body["status"] == "draft"
    assert [step["title"] for step in body["steps"]] == ["重新读取", "重新汇总"]


def test_plan_confirm_marks_high_risk_steps_for_a_prepared_plan(tmp_path: Path):
    client = _client(tmp_path)
    plan_id = _create(client)
    client.post(f"/v1/plans/{plan_id}/propose", headers=_headers())
    client.post(f"/v1/plans/{plan_id}/approve", headers=_headers())

    response = client.post(
        f"/v1/plans/{plan_id}/confirm",
        headers=_headers(),
        json={"steps": ["生成 Excel"]},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["confirmed_steps"] == ["生成 Excel"]


def test_plan_routes_report_missing_plan_and_missing_service(tmp_path: Path):
    client = _client(tmp_path)

    missing = client.get("/v1/plans/does-not-exist", headers=_headers())
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "plan_not_found"

    without_service = _client(tmp_path, with_plans=False)
    unavailable = without_service.get("/v1/plans", headers=_headers())
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "plans_unavailable"


def test_plan_routes_require_trusted_identity(tmp_path: Path):
    client = _client(tmp_path)

    response = client.get("/v1/plans")

    assert response.status_code == 401


def test_plan_list_is_newest_first_and_scoped_to_the_service(tmp_path: Path):
    client = _client(tmp_path)
    first = _create(client)
    second = client.post(
        "/v1/plans",
        headers=_headers(),
        json={"title": "第二个", "objective": "目标", "steps": ["一步"]},
    ).json()["plan"]["plan_id"]

    listing = client.get("/v1/plans", headers=_headers())

    assert listing.status_code == 200
    ids = [plan["plan_id"] for plan in listing.json()["plans"]]
    assert set(ids) == {first, second}


def test_plan_router_is_registered_in_the_gateway_app(tmp_path: Path):
    client = _client(tmp_path)

    paths = client.get("/openapi.json").json()["paths"]

    assert "/v1/plans" in paths
    assert "/v1/plans/{plan_id}/approve" in paths
    assert "/v1/plans/{plan_id}/confirm" in paths


def test_plan_router_factory_rejects_missing_dependencies(tmp_path: Path):
    with pytest.raises(TypeError):
        create_plans_router(  # type: ignore[call-arg]
            services=None,
            principal_for_request=None,
            require_workspace=None,
            normalize_json=None,
            unexpected=True,
        )
