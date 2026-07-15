"""产品交付技能单元测试：交付清单 / 验收单 / 记录交付 / 记录版本（fake DB）"""
from artpm_agent.skills.delivery_skill import DeliverySkill


class Asset:
    def __init__(self, aid, name, atype, status, progress=0, req=None):
        self.id = aid
        self.asset_name = name
        self.asset_type = atype
        self.status = status
        self.progress = progress
        self.requirements = req


class FakeDB:
    def __init__(self, assets, versions=None):
        self._assets = assets
        self._versions = versions or {}
        self.deliveries = []
        self.asset_versions = []

    def get_project_assets(self, pid):
        return self._assets

    def get_asset_versions(self, aid):
        return self._versions.get(aid, [])

    def create_delivery(self, project_id, delivery_no, title=None, items=None,
                        delivered_by=None, status="草稿"):
        rec = type("D", (), {"id": len(self.deliveries) + 1})()
        self.deliveries.append(rec)
        return rec

    def create_asset_version(self, asset_id, version, status="待审核", note=None, file_ref=None):
        rec = type("V", (), {"id": len(self.asset_versions) + 1})()
        self.asset_versions.append(rec)
        return rec


def _skill(assets, versions=None, db=None):
    if db is None:
        db = FakeDB(assets, versions)
    return DeliverySkill(context={"database": db})


def test_manifest():
    assets = [Asset(1, "主角", "角色", "已完成", 100, "写实PBR"),
              Asset(2, "场景", "场景", "制作中", 40)]
    r = _skill(assets).manifest(1)
    assert r["success"]
    assert r["asset_count"] == 2
    assert r["ready_count"] == 1
    assert r["items"][0]["acceptance_criteria"] == "写实PBR"


def test_acceptance():
    assets = [Asset(1, "主角", "角色", "已完成", 100, "写实PBR")]
    r = _skill(assets).acceptance(1)
    assert r["success"]
    assert r["rows"][0]["sign_off"] == ""


def test_record_delivery():
    assets = [Asset(1, "主角", "角色", "已完成")]
    db = FakeDB(assets)
    r = _skill(assets, db=db).record_delivery(1, "D-001", items=[{"asset_id": 1}])
    assert r["success"]
    assert r["delivery_no"] == "D-001"
    assert len(db.deliveries) == 1


def test_record_version():
    assets = [Asset(1, "主角", "角色", "已完成")]
    db = FakeDB(assets)
    r = _skill(assets, db=db).record_version(1, "v2", status="已通过")
    assert r["success"]
    assert r["version"] == "v2"
    assert len(db.asset_versions) == 1


def test_no_db():
    assert not DeliverySkill(context={}).manifest(1)["success"]


def test_execute_dispatch():
    assets = [Asset(1, "主角", "角色", "已完成")]
    r = _skill(assets).execute({"action": "manifest", "project_id": 1})
    assert r["success"]
    assert not _skill(assets).execute({"action": "x"})["success"]
