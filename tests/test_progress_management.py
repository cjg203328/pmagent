"""进度管理技能单元测试：里程碑视图 / 阻塞卡点 / 站会摘要（fake DB）"""
from skills.progress_management_skill import ProgressManagementSkill


class Task:
    def __init__(self, tid, name, status, progress=0, due_date=None):
        self.id = tid
        self.task_name = name
        self.status = status
        self.progress = progress
        self.due_date = due_date


class FakeDB:
    def __init__(self, tasks):
        self._tasks = tasks

    def get_tasks(self, project_id=None):
        return self._tasks


def _skill(tasks, config=None):
    return ProgressManagementSkill(context={"config": config or {}, "database": FakeDB(tasks)})


def _tasks():
    return [
        Task(1, "A", "已完成", 100),
        Task(2, "B", "进行中", 50, due_date="2026-01-01"),   # 逾期
        Task(3, "C", "进行中", 0),                            # 零进度
        Task(4, "D", "待审核", 80),
        Task(5, "E", "待开始", 0),
    ]


def test_milestone_view():
    r = _skill(_tasks()).milestone_view(1)
    assert r["success"]
    assert r["total_tasks"] == 5
    assert r["completed"] == 1
    assert r["by_status"]["进行中"] == 2
    assert r["overall_progress"] == 0.2


def test_blockers_overdue_and_zero():
    r = _skill(_tasks()).blockers(1, today="2026-08-01")
    assert r["success"]
    names = {b["task_name"] for b in r["blockers"]}
    assert "B" in names and "C" in names
    assert r["blocker_count"] == 2


def test_standup_summary():
    r = _skill(_tasks()).standup_summary(1, today="2026-08-01")
    assert r["success"]
    assert r["in_progress_count"] == 2
    assert r["blocker_count"] == 2
    assert "站会摘要" in r["summary"]


def test_no_db():
    s = ProgressManagementSkill(context={})
    assert not s.milestone_view(1)["success"]


def test_execute_dispatch():
    r = _skill(_tasks()).execute({"action": "view", "project_id": 1})
    assert r["success"]
    assert not _skill(_tasks()).execute({"action": "x"})["success"]
