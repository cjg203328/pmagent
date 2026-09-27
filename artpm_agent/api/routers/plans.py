"""Plan routes: host-reviewed plan mode over HTTP.

Mirrors the EvoFlow plan-mode capability at the gateway level. A plan is
drafted, proposed for host review, then approved or sent back with feedback.
Only an approved plan may be executed, and high-risk steps stay blocked until
explicitly confirmed via ``/v1/plans/{plan_id}/confirm``.

Plans are metadata only: this router does not run business skills, it exposes
the review-and-execute lifecycle so a UI can render plan mode.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from artpm_agent.api.errors import GatewayError
from artpm_agent.api.services import GatewayServices, RequestPrincipal


class PlanCreatePayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1)
    steps: list[str] = Field(min_length=1)


class PlanStepsPayload(BaseModel):
    steps: list[str] = Field(min_length=1)


class PlanFeedbackPayload(BaseModel):
    feedback: str = Field(min_length=1)


class PlanConfirmPayload(BaseModel):
    steps: list[str] = Field(min_length=1)


def create_plans_router(
    *,
    services: GatewayServices,
    principal_for_request: Callable[[Request], RequestPrincipal],
    require_workspace: Callable[[GatewayServices, RequestPrincipal, Request], object],
    normalize_json: Callable[[object], object],
) -> APIRouter:
    """Create the plan-mode router with injected gateway dependencies."""

    router = APIRouter()

    def _coordinator() -> Any:
        if services.plans is None:
            raise GatewayError(
                503, "plans_unavailable", "plan service is not configured"
            )
        return services.plans

    def _snapshot(plan: Any) -> dict[str, object]:
        return {"plan": normalize_json(plan.to_dict())}

    @router.get("/v1/plans", tags=["plans"])
    def list_plans(request: Request) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        plans = _coordinator().store.list()
        return {"plans": [normalize_json(plan.to_dict()) for plan in plans]}

    @router.get("/v1/plans/{plan_id}", tags=["plans"])
    def get_plan(plan_id: str, request: Request) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        plan = _coordinator().store.get(plan_id)
        if plan is None:
            raise GatewayError(404, "plan_not_found", f"plan {plan_id} not found")
        return _snapshot(plan)

    @router.post("/v1/plans", tags=["plans"], status_code=201)
    def create_plan(request: Request, payload: PlanCreatePayload) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        try:
            plan = _coordinator().create_plan(
                payload.title, payload.objective, payload.steps
            )
        except Exception as error:  # noqa: BLE001 - invalid step list etc.
            raise GatewayError(400, "plan_create_rejected", str(error)) from error
        return _snapshot(plan)

    @router.post("/v1/plans/{plan_id}/propose", tags=["plans"])
    def propose_plan(plan_id: str, request: Request) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        return _transition(lambda: _coordinator().propose(plan_id))

    @router.post("/v1/plans/{plan_id}/approve", tags=["plans"])
    def approve_plan(plan_id: str, request: Request) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        return _transition(lambda: _coordinator().approve(plan_id))

    @router.post("/v1/plans/{plan_id}/reject", tags=["plans"])
    def reject_plan(
        plan_id: str, request: Request, payload: PlanFeedbackPayload
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        return _transition(
            lambda: _coordinator().reject(plan_id, payload.feedback),
            error_code="plan_reject_rejected",
        )

    @router.post("/v1/plans/{plan_id}/revise", tags=["plans"])
    def revise_plan(
        plan_id: str, request: Request, payload: PlanStepsPayload
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        return _transition(lambda: _coordinator().revise(plan_id, payload.steps))

    @router.post("/v1/plans/{plan_id}/confirm", tags=["plans"])
    def confirm_plan_steps(
        plan_id: str, request: Request, payload: PlanConfirmPayload
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        return _transition(lambda: _coordinator().confirm_steps(plan_id, payload.steps))

    def _transition(
        action: Callable[[], Any],
        *,
        error_code: str = "plan_state_rejected",
    ) -> dict[str, object]:
        try:
            plan = action()
        except Exception as error:  # noqa: BLE001 - invalid state transition
            raise GatewayError(409, error_code, str(error)) from error
        return _snapshot(plan)

    return router
