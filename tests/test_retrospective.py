"""复盘总结技能单元测试：结项复盘 / 经验沉淀（fake DB）"""
from artpm_agent.skills.retrospective_skill import RetrospectiveSkill


class Task:
    def __init__(self, tid, status, quality=None, revision=0, due=None, done=None):
        self.id = tid
        self.status = status
        self.quality_score = quality
        self.revision_count = revision
        self.due_date = due
        self.completed_date = done


class Project:
    def __init__(self, pid, name, client, quote, cost, created, completed):
        self.id = pid
        self.project_name = name
        self.client = client
        self.quote_amount = quote
        self.cost = cost
        self.created_at = created
        self.completed_date = completed


class FakeDB:
    def __init__(self, project, tasks):
        self._project = project
        self._tasks = tasks
        self.knowledge = []

    def get_project(self, pid):
        return self._project

    def get_tasks(self, project_id=None):
        return self._tasks

    def add_knowledge(self, title, content, category="lessons", source=None,
                      client=None, tags=None):
        rec = type("KB", (), {"id": len(self.knowledge) + 1})()
        self.knowledge.append(rec)
        return rec


def _skill(project, tasks):
    return RetrospectiveSkill(context={"database": FakeDB(project, tasks)})


def _tasks():
    return [
        Task(1, "已完成", quality=4.5, revision=0, due="2026-08-10", done="2026-08-09"),
        Task(2, "已完成", quality=3.0, revision=2, due="2026-08-12", done="2026-08-15"),
        Task(3, "进行中", quality=None, revision=0),
    ]


def _project():
    return Project(1, "角色外包", "客户A", 50000, 42000,
                   "2026-08-01", "2026-08-20")


def test_report():
    r = _skill(_project(), _tasks()).report(1)
    assert r["success"]
    assert r["total_tasks"] == 3
    assert r["completed_tasks"] == 2
    assert r["on_time_rate"] == 0.5          # 1/2 准时
    assert r["avg_quality_score"] == 3.75    # (4.5+3.0)/2
    assert r["total_revisions"] == 2
    assert r["cost_variance"] == 8000
    assert r["duration_days"] == 19


def test_save_lessons():
    db = FakeDB(_project(), _tasks())
    r = RetrospectiveSkill(context={"database": db}).save_lessons(
        1, ["提前确认风格参考", "绑定环节易返工"], title="角色外包复盘"
    )
    assert r["success"]
    assert r["lesson_count"] == 2
    assert len(db.knowledge) == 1
    assert r["client"] == "客户A"


def test_no_db():
    assert not RetrospectiveSkill(context={}).report(1)["success"]


def test_execute_dispatch():
    r = _skill(_project(), _tasks()).execute({"action": "report", "project_id": 1})
    assert r["success"]
    assert not _skill(_project(), _tasks()).execute({"action": "x"})["success"]
