"""
Quality Control skill tests.

Uses an in-memory fake DatabaseManager so no real SQLite is required.
Mirrors the methods the skill relies on: get_task / get_tasks / update_task.
"""
from skills.quality_control_skill import QualityControlSkill


class FakeTask:
    def __init__(self, tid, name, status="进行中", quality_score=None, revision_count=0):
        self.id = tid
        self.task_name = name
        self.status = status
        self.quality_score = quality_score
        self.revision_count = revision_count


class FakeDB:
    def __init__(self, tasks):
        self._tasks = {t.id: t for t in tasks}
        self.updates = []

    def get_task(self, task_id):
        return self._tasks.get(task_id)

    def get_tasks(self, project_id=None):
        return list(self._tasks.values())

    def update_task(self, task_id, **fields):
        t = self._tasks.get(task_id)
        if t is None:
            return False
        for k, v in fields.items():
            setattr(t, k, v)
        self.updates.append((task_id, fields))
        return True


def make_skill(tasks):
    db = FakeDB(tasks)
    skill = QualityControlSkill({"database": db, "config": {}})
    return skill, db


def test_submit_for_review_transitions_to_pending():
    task = FakeTask(1, "角色建模", status="进行中")
    skill, db = make_skill([task])

    res = skill.submit_for_review(task_id=1)

    assert res["success"] is True
    assert res["status"] == "待审核"
    assert task.status == "待审核"
    assert db.updates == [(1, {"status": "待审核"})]


def test_submit_completed_task_is_rejected():
    task = FakeTask(2, "已完结", status="已完成")
    skill, db = make_skill([task])

    res = skill.submit_for_review(task_id=2)

    assert res["success"] is False
    assert db.updates == []


def test_record_review_accept_sets_done_and_score():
    task = FakeTask(3, "场景贴图", status="待审核")
    skill, db = make_skill([task])

    res = skill.record_review(task_id=3, decision="accept", quality_score=4.5)

    assert res["success"] is True
    assert res["decision"] == "accept"
    assert task.status == "已完成"
    assert task.quality_score == 4.5


def test_record_review_reject_increments_revision_and_reopens():
    task = FakeTask(4, "特效", status="待审核", revision_count=1)
    skill, db = make_skill([task])

    res = skill.record_review(task_id=4, decision="驳回", quality_score=1.5)

    assert res["decision"] == "reject"
    assert task.status == "进行中"
    assert task.revision_count == 2


def test_record_review_revise_keeps_pending_and_counts_revision():
    task = FakeTask(5, "UI", status="待审核")
    skill, db = make_skill([task])

    res = skill.record_review(task_id=5, decision="返工", quality_score=3.0)

    assert res["decision"] == "revise"
    assert task.status == "待审核"
    assert task.revision_count == 1


def test_record_review_accept_requires_score():
    task = FakeTask(6, "动画", status="待审核")
    skill, _ = make_skill([task])

    res = skill.record_review(task_id=6, decision="通过")
    assert res["success"] is False


def test_quality_report_aggregates_metrics():
    tasks = [
        FakeTask(1, "A", status="已完成", quality_score=4.5),
        FakeTask(2, "B", status="已完成", quality_score=2.0),
        FakeTask(3, "C", status="待审核"),
        FakeTask(4, "D", status="进行中", quality_score=1.0, revision_count=3),
    ]
    skill, _ = make_skill(tasks)

    report = skill.quality_report()

    assert report["success"] is True
    assert report["total_tasks"] == 4
    assert report["reviewed_tasks"] == 3
    assert report["passed_tasks"] == 1          # only 4.5 >= 3.0
    assert report["avg_quality_score"] == 2.5    # (4.5+2.0+1.0)/3
    assert report["pending_review"] == 2          # 待审核 + 进行中
    assert len(report["needs_rework"]) == 1
    assert report["needs_rework"][0]["task_id"] == 4


def test_execute_dispatches_by_action():
    task = FakeTask(7, "原画", status="进行中")
    skill, _ = make_skill([task])

    submit = skill.execute({"action": "submit", "task_id": 7})
    review = skill.execute({"action": "review", "task_id": 7, "decision": "accept", "quality_score": 5})
    report = skill.execute({"action": "report"})

    assert submit["status"] == "待审核"
    assert review["status"] == "已完成"
    assert report["success"] is True


def test_unknown_action_errors():
    skill, _ = make_skill([])
    res = skill.execute({"action": "frobnicate"})
    assert res["success"] is False
