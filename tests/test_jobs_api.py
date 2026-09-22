"""/v1/jobs 路由：Job 作为一等导航对象的最小公开面（PRD §4）。"""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from artpm_agent.api import create_app
from artpm_agent.api.services import build_default_services


def _headers(workspace: str = "local-default", *, actor: str = "alice") -> dict:
    return {"x-workspace-id": workspace, "x-actor-id": actor, "x-actor-role": "user"}


def _client(tmp_path: Path) -> TestClient:
    services = build_default_services(tmp_path / "gateway.sqlite")
    return TestClient(create_app(services))


def test_jobs_routes_expose_create_list_and_detail(tmp_path: Path):
    client = _client(tmp_path)

    created = client.post(
        "/v1/jobs",
        headers=_headers(),
        json={"job_name": "周报-2026-09-14", "job_type": "office_weekly"},
    )
    assert created.status_code == 201
    job_id = created.json()["job"]["id"]

    listed = client.get("/v1/jobs", headers=_headers())
    assert listed.status_code == 200
    assert [job["id"] for job in listed.json()["jobs"]] == [job_id]

    detail = client.get(f"/v1/jobs/{job_id}", headers=_headers())
    assert detail.status_code == 200
    body = detail.json()
    assert body["job"]["status"] == "draft"
    assert body["artifacts"] == []


def test_jobs_routes_reject_unknown_job_and_missing_name(tmp_path: Path):
    client = _client(tmp_path)

    missing = client.get("/v1/jobs/9999", headers=_headers())
    assert missing.status_code == 404

    blank = client.post("/v1/jobs", headers=_headers(), json={"job_name": "  "})
    assert blank.status_code in {400, 422}


def test_jobs_routes_require_identity(tmp_path: Path):
    client = _client(tmp_path)

    anonymous = client.get("/v1/jobs")
    assert anonymous.status_code == 401
