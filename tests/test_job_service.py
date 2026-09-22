"""Job 一等公民测试：建 / 列 / 查 / 状态推进 / 成果版本追加。

对应 PRD §4 的对象模型与 `prototype/交互规格.md` §1、§3、§8 的验收判据
（A1 任务优先于会话、A3 成果可下载、A7 无对话也能建/推进/完成）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from artpm_agent.database.models import DatabaseManager
from artpm_agent.jobs import (
    ALLOWED_TRANSITIONS,
    CANCELLED,
    DRAFT,
    FAILED,
    JobError,
    JobNotFoundError,
    JobService,
    JobStateError,
    NEEDS_INPUT,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    WAITING,
    can_transition,
)


@pytest.fixture()
def service(tmp_path: Path) -> JobService:
    db = DatabaseManager(f"sqlite:///{tmp_path / 'jobs.db'}")
    try:
        yield JobService(db)
    finally:
        db.close()


# ── A7：无对话也能创建、推进、完成 ─────────────────────────────


def test_job_can_be_created_without_conversation(service):
    """硬判据：Job 不依赖 conversation 存在。"""
    job = service.create("11月人天汇总", job_type="s0_monthly")

    assert job["id"] > 0
    assert job["conversation_id"] is None
    assert job["workflow_run_id"] is None
    assert job["status"] == DRAFT
    assert job["trigger"] == "manual"


def test_scheduled_job_runs_to_completion_without_any_dialogue(service):
    """F3 定时任务：无人对话时从 queued 一路走到 succeeded。"""
    job = service.create(
        "每日巡检", job_type="s2_inspect", trigger="scheduled", status=QUEUED
    )

    service.transition(job["id"], RUNNING)
    final = service.transition(job["id"], SUCCEEDED, outputs={"checked": 7})

    assert final["status"] == SUCCEEDED
    assert final["conversation_id"] is None
    assert final["started_at"] is not None
    assert final["completed_at"] is not None

    detail = service.get(job["id"])
    assert detail["outputs"] == {"checked": 7}


def test_job_can_link_to_conversation_when_started_from_message(service):
    job = service.create("腾讯报价", trigger="from_message", conversation_id="conv-1")
    assert job["trigger"] == "from_message"
    assert job["conversation_id"] == "conv-1"


# ── 状态机 ──────────────────────────────────────────────────


def test_unknown_trigger_is_rejected(service):
    with pytest.raises(JobError, match="触发方式"):
        service.create("x", trigger="telepathy")


def test_blank_job_name_is_rejected(service):
    with pytest.raises(JobError, match="job_name"):
        service.create("   ")


def test_illegal_transition_is_rejected_not_silently_applied(service):
    """draft 不能直接跳到 running；静默放行会让进度条卡死无处可查。"""
    job = service.create("任务")

    with pytest.raises(JobStateError, match="不允许的状态跳转"):
        service.transition(job["id"], RUNNING)

    assert service.get(job["id"])["status"] == DRAFT


def test_terminal_state_cannot_be_left(service):
    job = service.create("任务", status=QUEUED)
    service.transition(job["id"], RUNNING)
    service.transition(job["id"], SUCCEEDED)

    with pytest.raises(JobStateError):
        service.transition(job["id"], RUNNING)


def test_failed_job_can_be_requeued(service):
    """失败可从失败处重试，而不是「全部重来」也无需新建 Job。"""
    job = service.create("任务", status=QUEUED)
    service.transition(job["id"], RUNNING)
    service.transition(job["id"], FAILED, error="附件解析失败")

    assert service.get(job["id"])["error"] == "附件解析失败"

    retried = service.transition(job["id"], QUEUED)
    assert retried["status"] == QUEUED
    # 重试不清除失败原因，UI 需要它解释「为什么重试」
    assert retried["error"] == "附件解析失败"


def test_failed_without_reason_still_records_something(service):
    job = service.create("任务", status=QUEUED)
    service.transition(job["id"], RUNNING)
    final = service.transition(job["id"], FAILED)

    assert final["error"]


def test_waiting_and_needs_input_return_to_running(service):
    job = service.create("任务", status=QUEUED)
    service.transition(job["id"], RUNNING)

    assert service.transition(job["id"], WAITING)["status"] == WAITING
    assert service.transition(job["id"], RUNNING)["status"] == RUNNING

    assert service.transition(job["id"], NEEDS_INPUT)["status"] == NEEDS_INPUT
    assert service.transition(job["id"], RUNNING)["status"] == RUNNING


def test_cancel_is_allowed_from_every_non_terminal_state():
    for state in (DRAFT, QUEUED, RUNNING, WAITING, NEEDS_INPUT, FAILED):
        assert can_transition(state, CANCELLED) is True, state
    # 终态不可再「离开」，但同态重复调用是幂等空操作，不算跳转。
    for state in (SUCCEEDED, CANCELLED):
        assert can_transition(state, CANCELLED) is (state == CANCELLED), state


def test_terminal_state_is_idempotent_on_repeat_but_cannot_change():
    assert can_transition(SUCCEEDED, SUCCEEDED) is True
    assert can_transition(SUCCEEDED, FAILED) is False
    assert can_transition(SUCCEEDED, CANCELLED) is False


def test_same_state_transition_is_a_noop_not_an_error():
    for state in ALLOWED_TRANSITIONS:
        assert can_transition(state, state) is True


def test_can_transition_rejects_unknown_names():
    assert can_transition("draft", "exploded") is False
    assert can_transition("exploded", "running") is False


def test_transition_unknown_job_raises(service):
    with pytest.raises(JobNotFoundError):
        service.transition(9999, RUNNING)


def test_transition_unknown_status_raises(service):
    job = service.create("任务")
    with pytest.raises(JobError, match="未知 Job 状态"):
        service.transition(job["id"], "exploded")


def test_started_at_is_set_once_not_overwritten(service):
    """waiting → running 是同一 Job 的续跑，不是第二次启动。"""
    job = service.create("任务", status=QUEUED)
    first = service.transition(job["id"], RUNNING)
    started = first["started_at"]
    service.transition(job["id"], WAITING)
    second = service.transition(job["id"], RUNNING)

    assert second["started_at"] == started


# ── 列与查 ──────────────────────────────────────────────────


def test_list_filters_by_project_and_status(service):
    a = service.create("A", project_id=1, status=QUEUED)
    b = service.create("B", project_id=2, status=QUEUED)
    c = service.create("C", project_id=1)

    by_project = [row["id"] for row in service.list(project_id=1)]
    assert sorted(by_project) == sorted([a["id"], c["id"]])
    # 最新在前
    assert by_project == [c["id"], a["id"]]
    assert [row["id"] for row in service.list(project_id=2)] == [b["id"]]
    assert [row["id"] for row in service.list(status=DRAFT)] == [c["id"]]
    assert len(service.list(statuses=[QUEUED, DRAFT])) == 3


def test_list_rejects_unknown_status(service):
    with pytest.raises(JobError, match="未知 Job 状态"):
        service.list(status="exploded")


def test_list_grouped_splits_active_scheduled_and_done(service):
    active = service.create("进行中任务", status=RUNNING)
    from datetime import datetime, timedelta

    scheduled = service.create(
        "每日巡检",
        trigger="scheduled",
        status=QUEUED,
        next_run_at=datetime.now() + timedelta(hours=1),
    )
    done = service.create("已完成任务", status=QUEUED)
    service.transition(done["id"], RUNNING)
    service.transition(done["id"], SUCCEEDED)

    groups = service.list_grouped()

    assert [row["id"] for row in groups["进行中"]] == [active["id"]]
    assert [row["id"] for row in groups["计划中"]] == [scheduled["id"]]
    assert [row["id"] for row in groups["已完成"]] == [done["id"]]


def test_scheduled_job_exposes_next_run_time(service):
    from datetime import datetime, timedelta

    when = datetime.now() + timedelta(hours=2)
    job = service.create("巡检", trigger="scheduled", next_run_at=when)

    assert service.get(job["id"])["next_run_at"] is not None
    assert job["next_run_at"] is not None


def test_get_unknown_job_raises(service):
    with pytest.raises(JobNotFoundError):
        service.get(4242)


# ── 成果挂载与版本追加（A3） ───────────────────────────────────


def test_artifact_attaches_to_job(service):
    job = service.create("11月汇总")

    artifact = service.attach_artifact(
        job["id"],
        filename="11月人天.xlsx",
        sha256="a" * 64,
        size_bytes=2048,
        verification_status="passed",
        verification={"status": "passed"},
    )

    assert artifact["version"] == 1
    assert artifact["artifact_type"] == "xlsx"
    assert artifact["downloadable"] is True
    assert service.get(job["id"])["artifacts"][0]["filename"] == "11月人天.xlsx"


def test_same_filename_rerun_appends_version_instead_of_overwriting(service):
    """PRD §9：同一 Job 重跑不得覆盖历史成果。"""
    job = service.create("11月汇总")

    first = service.attach_artifact(
        job["id"],
        filename="11月人天.xlsx",
        sha256="a" * 64,
        verification_status="passed",
    )
    second = service.attach_artifact(
        job["id"],
        filename="11月人天.xlsx",
        sha256="b" * 64,
        verification_status="passed",
    )

    assert first["version"] == 1
    assert second["version"] == 2
    versions = [item["version"] for item in service.list_artifacts(job["id"])]
    assert versions == [1, 2]


def test_different_filenames_each_start_at_version_one(service):
    job = service.create("任务")

    a = service.attach_artifact(job["id"], filename="a.xlsx")
    b = service.attach_artifact(job["id"], filename="b.docx")

    assert a["version"] == 1
    assert b["version"] == 1
    assert b["artifact_type"] == "docx"


def test_unverified_artifact_is_not_downloadable(service):
    """只有核验通过才可下载（③栏契约）。"""
    job = service.create("任务")
    service.attach_artifact(job["id"], filename="pending.xlsx")
    service.attach_artifact(job["id"], filename="ok.xlsx", verification_status="passed")
    service.attach_artifact(
        job["id"], filename="bad.xlsx", verification_status="failed"
    )

    assert service.downloadable(job["id"]) == [
        item
        for item in service.list_artifacts(job["id"])
        if item["filename"] == "ok.xlsx"
    ]
    assert [item["filename"] for item in service.downloadable(job["id"])] == ["ok.xlsx"]


def test_downloadable_can_filter_by_filename(service):
    job = service.create("任务")
    service.attach_artifact(job["id"], filename="a.xlsx", verification_status="passed")
    service.attach_artifact(job["id"], filename="b.xlsx", verification_status="passed")

    assert len(service.downloadable(job["id"])) == 2
    assert len(service.downloadable(job["id"], filename="a.xlsx")) == 1


def test_artifact_carries_step_id_for_targeted_edits(service):
    """F4 定点改需要靠 step_id 定位该重跑哪个 step。"""
    job = service.create("任务")
    artifact = service.attach_artifact(
        job["id"], filename="x.xlsx", step_id="generate_xlsx"
    )
    assert artifact["step_id"] == "generate_xlsx"


def test_artifact_rejects_empty_filename(service):
    job = service.create("任务")
    with pytest.raises(JobError, match="filename"):
        service.attach_artifact(job["id"], filename="  ")


def test_artifact_rejects_unknown_verification_status(service):
    job = service.create("任务")
    with pytest.raises(JobError, match="核验状态"):
        service.attach_artifact(
            job["id"], filename="x.xlsx", verification_status="也许吧"
        )


def test_artifact_rejects_unknown_type(service):
    job = service.create("任务")
    with pytest.raises(JobError, match="交付物类型"):
        service.attach_artifact(job["id"], filename="x.txt", artifact_type="txt")


def test_artifact_on_unknown_job_raises(service):
    with pytest.raises(JobNotFoundError):
        service.attach_artifact(9999, filename="x.xlsx")


def test_list_artifacts_on_unknown_job_raises(service):
    with pytest.raises(JobNotFoundError):
        service.list_artifacts(9999)


def test_artifacts_are_owned_by_their_job(service):
    """成果是 Job 的附属物：Job 行消失后不应留下孤儿成果。

    用 ORM 会话删除以走 relationship 的 delete-orphan 级联；SQLite 默认也不
    开启外键约束，所以不能依赖数据库级 ON DELETE CASCADE 兜底。
    """
    from artpm_agent.database.models import Job, JobArtifact

    job = service.create("任务")
    service.attach_artifact(job["id"], filename="a.xlsx")

    session = service.db.get_session()
    try:
        assert session.query(JobArtifact).count() == 1
        session.delete(session.query(Job).filter(Job.id == job["id"]).one())
        session.commit()
    finally:
        session.close()

    session = service.db.get_session()
    try:
        assert session.query(Job).count() == 0
        assert session.query(JobArtifact).count() == 0
    finally:
        session.close()


# ── 输入输出为 JSON ───────────────────────────────────────────


def test_job_inputs_round_trip():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(f"sqlite:///{Path(tmp) / 'x.db'}")
        try:
            svc = JobService(db)
            payload = {"month": "2026-11", "assets": [1, 2, 3]}
            job = svc.create("任务", inputs=payload)
            assert svc.get(job["id"])["inputs"] == payload
        finally:
            db.close()


def test_job_with_no_inputs_reports_none_not_empty_dict(service):
    job = service.create("任务")
    assert service.get(job["id"])["inputs"] is None


# ── to_dict 契约 ──────────────────────────────────────────────
# Dict 字面量里的重复键会**静默**丢弃前者。lint（F601）能抓到本仓库里的这一处，
# 但只要有人把 to_dict 改成动态构造、或 lint 配置收窄，覆盖就会悄悄发生。
# 因此这里按契约断言键集合与取值。


JOB_KEYS = {
    "id",
    "job_name",
    "project_id",
    "job_type",
    "trigger",
    "status",
    "workflow_run_id",
    "conversation_id",
    "error",
    "next_run_at",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
}


def test_job_to_dict_exposes_every_documented_key(service):
    job = service.create("任务")
    assert JOB_KEYS <= set(job)


def test_job_to_dict_preserves_trigger_and_status(service):
    job = service.create("任务", trigger="scheduled", status=QUEUED)

    assert job["trigger"] == "scheduled"
    assert job["status"] == QUEUED
    # 重新读一次，确认不是只在创建返回值里对
    assert service.get(job["id"])["trigger"] == "scheduled"


def test_job_to_dict_time_fields_are_iso_or_none(service):
    job = service.create("任务")

    assert job["created_at"] is not None
    assert job["completed_at"] is None
    assert job["started_at"] is None


ARTIFACT_KEYS = {
    "id",
    "job_id",
    "step_id",
    "filename",
    "artifact_type",
    "artifact_path",
    "sha256",
    "size_bytes",
    "version",
    "verification_status",
    "downloadable",
    "created_at",
}


def test_artifact_to_dict_exposes_every_documented_key(service):
    job = service.create("任务")
    artifact = service.attach_artifact(job["id"], filename="a.xlsx")

    assert ARTIFACT_KEYS <= set(artifact)


def test_artifact_downloadable_flag_follows_verification(service):
    job = service.create("任务")

    pending = service.attach_artifact(job["id"], filename="p.xlsx")
    failed = service.attach_artifact(
        job["id"], filename="f.xlsx", verification_status="failed"
    )
    passed = service.attach_artifact(
        job["id"], filename="ok.xlsx", verification_status="passed"
    )

    assert pending["downloadable"] is False
    assert failed["downloadable"] is False
    assert passed["downloadable"] is True
