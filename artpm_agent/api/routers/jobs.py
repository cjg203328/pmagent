"""Job routes (PRD §4): the task list the workbench's first column reads from.

Jobs are the first-class navigation object. These routes expose list / get /
create so office-flow work is observable outside the chat thread. Execution of
a Job still happens through ``run_turn``; this router does not run jobs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from artpm_agent.api.errors import GatewayError
from artpm_agent.api.services import GatewayServices, RequestPrincipal


class JobCreatePayload(BaseModel):
    job_name: str = Field(min_length=1, max_length=200)
    job_type: Optional[str] = None
    trigger: str = "manual"
    project_id: Optional[int] = None
    inputs: dict[str, Any] = Field(default_factory=dict)


def create_jobs_router(
    *,
    services: GatewayServices,
    principal_for_request: Callable[[Request], RequestPrincipal],
    require_workspace: Callable[[GatewayServices, RequestPrincipal, Request], object],
    normalize_json: Callable[[object], object],
) -> APIRouter:
    router = APIRouter()

    def _jobs() -> Any:
        if services.jobs is None:
            raise GatewayError(503, "jobs_unavailable", "job service is not configured")
        return services.jobs

    @router.get("/v1/jobs", tags=["jobs"])
    def list_jobs(
        request: Request,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        jobs = _jobs().list(status=status, project_id=project_id)
        return {"jobs": [normalize_json(job) for job in jobs]}

    @router.get("/v1/jobs/{job_id}", tags=["jobs"])
    def get_job(job_id: int, request: Request) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        try:
            job = _jobs().get(job_id)
        except Exception as error:  # noqa: BLE001 - map store misses to 404
            raise GatewayError(404, "job_not_found", f"job {job_id} not found") from error
        artifacts = _jobs().list_artifacts(job_id)
        return {
            "job": normalize_json(job),
            "artifacts": [normalize_json(item) for item in artifacts],
        }

    @router.post("/v1/jobs", tags=["jobs"], status_code=201)
    def create_job(request: Request, payload: JobCreatePayload) -> dict[str, object]:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        try:
            job = _jobs().create(
                payload.job_name,
                project_id=payload.project_id,
                job_type=payload.job_type,
                trigger=payload.trigger,
                inputs=payload.inputs,
            )
        except Exception as error:  # noqa: BLE001 - invalid trigger/status etc.
            raise GatewayError(400, "job_create_rejected", str(error)) from error
        return {"job": normalize_json(job)}

    return router
