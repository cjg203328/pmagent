"""办公流技能 weekly_report：真实数据 → 可下载 .docx。

PRD §4.5 / M3.5 的验收锚点。与 S0 的教训一致：只调用库函数的测试证明不了
功能可用，因此这里必须有一条走 run_turn 的端到端断言。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping

import pytest

from artpm_agent.database.models import DatabaseManager
from artpm_agent.harness import (
    BaseHarnessRuntime,
    RuntimeCapabilities,
    TurnContext,
    run_turn,
)
from artpm_agent.presentation.skill_results import format_skill_result
from artpm_agent.routing.service import IntentDecision
from artpm_agent.skills.skill_router import SKILL_METADATA, SkillRouter
from artpm_agent.skills.weekly_report_skill import WeeklyReportSkill


def _monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def _database_with_week(tmp_path) -> DatabaseManager:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'biz.db').as_posix()}")
    project = database.create_project(
        {"project_name": "角色外包", "client": "甲方", "quote_amount": 100000}
    )
    member = database.create_member({"name": "罗旭", "skills": ["建模"]})
    monday = _monday()
    database.create_task(
        {
            "project_id": project.id,
            "assignee_id": member.id,
            "task_name": "麦迪高模",
            "status": "已完成",
            "progress": 100,
            "completed_date": monday + timedelta(days=1),
        }
    )
    database.create_task(
        {
            "project_id": project.id,
            "assignee_id": member.id,
            "task_name": "艾弗森贴图",
            "status": "进行中",
            "progress": 30,
            "due_date": date.today() - timedelta(days=2),
        }
    )
    database.create_task(
        {
            "project_id": project.id,
            "assignee_id": member.id,
            "task_name": "场景白模",
            "status": "未开始",
            "progress": 0,
            "due_date": date.today() + timedelta(days=9),
        }
    )
    return database


class _WeeklyRuntime(BaseHarnessRuntime):
    capabilities = RuntimeCapabilities(
        turn_processing=True,
        skill_routing=True,
        model_chat=False,
        direct_response=False,
    )
    model_available = False

    def __init__(self, router: SkillRouter) -> None:
        self._router = router

    def detect_intent(self, _user_input: str) -> str | None:
        return "weekly_report"

    def detect_intent_decision(self, _user_input: str) -> IntentDecision:
        # keyword 层的执行门要求 confidence ≥ 0.80 且 margin ≥ 0.05；
        # 「生成这周周报」只命中一个关键词，margin 取置信度本身。
        return IntentDecision(
            intent="weekly_report", confidence=0.9, tier="keyword", margin=0.9
        )

    def skill_names(self) -> set[str]:
        return {"weekly_report"}

    def skill_metadata(self, skill_name: str) -> Mapping[str, Any]:
        return SKILL_METADATA[skill_name]

    def build_skill_input(self, user_input: str, _context: Mapping[str, Any]) -> str:
        return user_input

    def extract_skill_inputs(
        self,
        _user_input: str,
        _intent: str,
        _context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {}

    def execute_skill(
        self, intent: str, inputs: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return self._router.execute_skill(intent, dict(inputs))

    def format_skill_result(self, skill_name: str, result: Mapping[str, Any]) -> str:
        return format_skill_result(skill_name, result)


def test_skill_refuses_to_invent_a_report_without_data(tmp_path):
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
    skill = WeeklyReportSkill({"database": database, "config": {}})

    result = skill.execute({})

    assert result["success"] is False
    assert "没有任何任务数据" in result["error"]


def test_skill_builds_sections_from_real_tasks(tmp_path):
    database = _database_with_week(tmp_path)
    skill = WeeklyReportSkill({"database": database, "config": {}})

    result = skill.execute({})

    assert result["success"] is True
    names = {row["name"] for row in result["sections"]["progress"]}
    assert "麦迪高模" in names
    risks = {row["name"] for row in result["sections"]["risks"]}
    assert "艾弗森贴图" in risks
    assert (
        next(row for row in result["sections"]["risks"] if row["name"] == "艾弗森贴图")[
            "overdue_days"
        ]
        == 2
    )
    assert {row["name"] for row in result["sections"]["next_week"]} == {"场景白模"}


def test_skill_reuses_workbench_job_and_attaches_report_to_it(tmp_path):
    from artpm_agent.jobs import JobService

    database = _database_with_week(tmp_path)
    jobs = JobService(database)
    selected = jobs.create("选中的周报", job_type="office_weekly")
    skill = WeeklyReportSkill(
        {"database": database, "config": {"data_root": str(tmp_path)}}
    )

    result = skill.execute({"job_id": selected["id"]})

    assert result["success"] is True
    assert result["job_id"] == selected["id"]
    assert len(jobs.list()) == 1
    persisted = jobs.get(selected["id"])
    assert persisted["status"] == "succeeded"
    assert len(persisted["artifacts"]) == 1
    assert persisted["artifacts"][0]["verification_status"] == "passed"


def test_router_accepts_workbench_trigger_and_reuses_selected_job(tmp_path):
    from artpm_agent.jobs import JobService

    database = _database_with_week(tmp_path)
    selected = JobService(database).create("路由周报", job_type="office_weekly")
    router = SkillRouter({"database": database, "config": {"data_root": str(tmp_path)}})

    result = router.execute_skill(
        "weekly_report", {"trigger": "manual", "job_id": selected["id"]}
    )

    assert result["success"] is True
    assert result["job_id"] == selected["id"]
    assert len(JobService(database).list()) == 1


def test_tenant_bound_router_uses_injected_profile_scoped_artifact_generator(tmp_path):
    from artpm_agent.artifacts.generator import WorkspaceArtifactGenerator
    from artpm_agent.jobs import JobService
    from artpm_agent.tenancy import TenantContext

    database = _database_with_week(tmp_path)
    selected = JobService(database).create("租户周报", job_type="office_weekly")
    tenant = TenantContext(
        tenant_id="local",
        workspace_id="local-default",
        principal_id="local-user",
    )
    artifact_root = tmp_path / "tenant-artifacts" / "custom-profile"
    generator = WorkspaceArtifactGenerator(artifact_root)
    router = SkillRouter(
        {
            "database": database,
            "config": {"data_root": str(tmp_path / "fallback")},
            "tenant_context": tenant,
            "profile_id": "custom-profile",
            "artifact_generator": generator,
        }
    )

    result = router.for_tenant(tenant).execute_skill(
        "weekly_report", {"trigger": "manual", "job_id": selected["id"]}
    )

    assert result["success"] is True
    assert len(result["artifacts"]) == 1
    artifact = result["artifacts"][0]
    assert (artifact_root / artifact["stored_path"]).is_file()
    assert not (tmp_path / "fallback").exists()
    assert JobService(database).get(selected["id"])["status"] == "succeeded"


def test_skill_retries_failed_workbench_job_without_creating_another(tmp_path):
    from artpm_agent.jobs import FAILED, JobService

    database = _database_with_week(tmp_path)
    jobs = JobService(database)
    selected = jobs.create("重试周报", job_type="office_weekly", status=FAILED)
    skill = WeeklyReportSkill(
        {"database": database, "config": {"data_root": str(tmp_path)}}
    )

    result = skill.execute({"job_id": selected["id"]})

    assert result["success"] is True
    assert result["job_id"] == selected["id"]
    assert jobs.get(selected["id"])["status"] == "succeeded"
    assert len(jobs.list()) == 1


@pytest.mark.parametrize(
    ("job_type", "status", "expected_error"),
    [
        ("manual", "draft", "不是周报任务"),
        ("office_weekly", "succeeded", "不能重复执行"),
    ],
)
def test_skill_rejects_incompatible_workbench_job(
    tmp_path, job_type, status, expected_error
):
    from artpm_agent.jobs import JobService

    database = _database_with_week(tmp_path)
    jobs = JobService(database)
    selected = jobs.create("不可执行任务", job_type=job_type, status=status)
    skill = WeeklyReportSkill({"database": database, "config": {}})

    result = skill.execute({"job_id": selected["id"]})

    assert result["success"] is False
    assert expected_error in result["error"]
    assert len(jobs.list()) == 1
    assert jobs.get(selected["id"])["status"] == status


@pytest.mark.parametrize("job_id", [True, 0, -1, 1.5, "1.5", "bad"])
def test_skill_rejects_invalid_job_id_without_creating_job(tmp_path, job_id):
    from artpm_agent.jobs import JobService

    database = _database_with_week(tmp_path)
    skill = WeeklyReportSkill({"database": database, "config": {}})

    result = skill.execute({"job_id": job_id})

    assert result["success"] is False
    assert "job_id 必须是正整数" in result["error"]
    assert JobService(database).list() == []


def test_run_turn_delivers_a_verified_weekly_docx(tmp_path):
    """端到端：请求必须产出可下载且核验通过的 .docx，而不是聊天文本。"""
    database = _database_with_week(tmp_path)
    router = SkillRouter({"database": database, "config": {"data_root": str(tmp_path)}})
    context = TurnContext(
        turn_id="turn-weekly",
        conversation_id="conv-weekly",
        user_input="生成这周周报",
        runtime=_WeeklyRuntime(router),
    )

    result = run_turn(context, request_conversation_id="conv-weekly")

    assert result.handled_by == "skill:weekly_report"
    assert result.artifacts, "周报必须交付文件"
    artifact = result.artifacts[0]
    assert artifact["format"] == "docx"
    assert artifact["verification"]["status"] == "passed"
    delivered = (
        tmp_path
        / "artifacts"
        / "local"
        / "local-default"
        / "local-default"
        / artifact["stored_path"]
    )
    assert delivered.is_file()
    assert "交付文件：" in result.response

    # PRD §4：交付物必须挂在 Job 名下，办公流与任务模型不再是两条平行线。
    from artpm_agent.jobs import JobService

    jobs = JobService(database)
    listed = jobs.list()
    assert len(listed) == 1
    job = listed[0]
    assert job["job_type"] == "office_weekly"
    assert job["status"] == "succeeded"
    attached = jobs.list_artifacts(job["id"])
    assert len(attached) == 1
    assert attached[0]["verification_status"] == "passed"
