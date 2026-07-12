"""需求评估技能单元测试：复杂度判定 / 范围清单 / 报价落库（fake DB，无需真实库）"""
from skills.requirements_assessment_skill import RequirementsAssessmentSkill


class FakeDB:
    """最小鸭子类型数据库，记录调用并返回可控对象。"""

    def __init__(self):
        self.projects = []
        self.assets = []
        self.docs = []
        self._pid = 0
        self._aid = 0
        self._did = 0

    def create_project(self, data):
        self._pid += 1
        rec = type("P", (), {"id": self._pid, **data})()
        self.projects.append(rec)
        return rec

    def create_asset(self, data):
        self._aid += 1
        rec = type("A", (), {"id": self._aid, **data})()
        self.assets.append(rec)
        return rec

    def save_document(self, project_id, document_type, file_name, parsed_data=None,
                      file_path=None, file_type=None, file_size=None, confidence_score=None):
        self._did += 1
        rec = type("D", (), {"id": self._did, "project_id": project_id})()
        self.docs.append(rec)
        return rec


def _skill(db=None):
    return RequirementsAssessmentSkill(context={"database": db})


def test_assess_complexity_simple():
    r = _skill().assess_complexity(asset_type="道具", requirements_text="一个简单低模图标")
    assert r["success"]
    assert r["complexity"] == "simple"


def test_assess_complexity_complex():
    r = _skill().assess_complexity(
        asset_type="角色",
        requirements_text="写实风格角色，需要PBR材质、毛发、骨骼绑定与表情动画",
    )
    assert r["success"]
    assert r["complexity"] == "complex"
    assert r["score"] > 5


def test_assess_complexity_medium_by_type():
    r = _skill().assess_complexity(asset_type="场景", requirements_text="普通场景")
    assert r["complexity"] == "medium"


def test_scope_checklist_has_items():
    r = _skill().scope_checklist(asset_types=["角色", "场景"])
    assert r["success"]
    assert r["count"] >= 8
    assert any(i["item"].startswith("资产类型确认") for i in r["checklist"])


def test_ingest_creates_project_and_assets():
    db = FakeDB()
    parsed = {
        "quote_amount": 50000,
        "assets": [
            {"asset_name": "主角", "asset_type": "角色",
             "quantity": 1, "unit_price": 30000,
             "requirements": "写实 PBR 毛发绑定"},
            {"asset_name": "场景A", "asset_type": "场景",
             "quantity": 2, "unit_price": 10000},
        ],
    }
    r = _skill(db).ingest("测试项目", "客户X", parsed, document_file_name="q.xlsx")
    assert r["success"]
    assert r["project_id"] == 1
    assert r["asset_count"] == 2
    assert r["document_id"] == 1
    # 复杂度自动判定生效
    assert db.assets[0].complexity == "complex"
    assert db.assets[1].complexity == "medium"


def test_ingest_no_db_fails():
    r = _skill(None).ingest("p", "c", {"assets": []})
    assert not r["success"]
    assert "数据库" in r["error"]


def test_execute_dispatch():
    r = _skill().execute({"action": "scope"})
    assert r["success"]
    bad = _skill().execute({"action": "nope"})
    assert not bad["success"]
