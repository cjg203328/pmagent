"""
Delivery Skill - 产品交付

美术外包场景下的产品交付增强（此前生成器是通用的、不绑项目）：
  1. 交付清单 (manifest) —— 绑定 Project/Assets，列出各资产状态、最新版本与验收标准。
  2. 验收单 (acceptance) —— 逐资产生成验收条目（资产/验收标准/状态）。
  3. 记录交付 (record_delivery) —— 创建一次交付记录（Delivery 模型）。
  4. 记录版本 (record_version) —— 记录单个资产的交付版本（AssetVersion 模型）。

复用 models.py 新增的 Delivery / AssetVersion，不改动既有表。
"""
from typing import Dict, Any, Optional, List

from .base_skill import BaseSkill


class DeliverySkill(BaseSkill):
    """产品交付：交付清单、验收单、交付与版本记录"""

    skill_name = "delivery"
    description = "产品交付：交付清单、验收单、交付与版本记录"
    version = "1.0"
    requires_llm = False

    def __init__(self, context: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        self.db = self.context.get("database") if self.context else None

    def _assets(self, project_id):
        if self.db is None or not hasattr(self.db, "get_project_assets"):
            return None
        try:
            return self.db.get_project_assets(project_id)
        except Exception:
            return None

    # ── 交付清单 ──
    def manifest(self, project_id: int) -> Dict[str, Any]:
        assets = self._assets(project_id)
        if assets is None:
            return {"success": False, "error": "数据库不可用或未初始化"}
        items = []
        for a in assets:
            aid = getattr(a, "id", None)
            versions = []
            if self.db is not None and hasattr(self.db, "get_asset_versions"):
                try:
                    versions = [v.version for v in self.db.get_asset_versions(aid)]
                except Exception:
                    versions = []
            items.append({
                "asset_id": aid,
                "asset_name": getattr(a, "asset_name", None),
                "asset_type": getattr(a, "asset_type", None),
                "status": getattr(a, "status", None),
                "progress": getattr(a, "progress", 0),
                "latest_version": (versions[0] if versions else None),
                "acceptance_criteria": getattr(a, "requirements", None),
            })
        done = sum(1 for i in items if i["status"] == "已完成")
        return {
            "success": True,
            "project_id": project_id,
            "asset_count": len(items),
            "ready_count": done,
            "items": items,
            "summary": f"项目共 {len(items)} 个资产，{done} 个已就绪交付。",
        }

    # ── 验收单 ──
    def acceptance(self, project_id: int) -> Dict[str, Any]:
        assets = self._assets(project_id)
        if assets is None:
            return {"success": False, "error": "数据库不可用或未初始化"}
        rows = []
        for a in assets:
            rows.append({
                "asset_id": getattr(a, "id", None),
                "asset_name": getattr(a, "asset_name", None),
                "acceptance_criteria": getattr(a, "requirements") or "（未填写验收标准）",
                "status": getattr(a, "status", None),
                "sign_off": "",
            })
        return {
            "success": True,
            "project_id": project_id,
            "rows": rows,
            "summary": f"已生成 {len(rows)} 条验收条目，请客户逐项签收。",
        }

    # ── 记录交付 ──
    def record_delivery(self, project_id: int, delivery_no: str,
                        items: Optional[List[Dict[str, Any]]] = None,
                        delivered_by: Optional[str] = None,
                        title: Optional[str] = None) -> Dict[str, Any]:
        if self.db is None or not hasattr(self.db, "create_delivery"):
            return {"success": False, "error": "数据库不可用（缺少 create_delivery）"}
        try:
            d = self.db.create_delivery(
                project_id=project_id, delivery_no=delivery_no,
                title=title, items=items or [], delivered_by=delivered_by,
                status="已交付",
            )
        except Exception as e:
            return {"success": False, "error": f"创建交付记录失败: {e}"}
        return {
            "success": True,
            "delivery_id": getattr(d, "id", None),
            "delivery_no": delivery_no,
            "item_count": len(items or []),
            "message": f"已记录交付 {delivery_no}（{len(items or [])} 个资产）。",
        }

    # ── 记录版本 ──
    def record_version(self, asset_id: int, version: str,
                       status: str = "待审核", note: Optional[str] = None,
                       file_ref: Optional[str] = None) -> Dict[str, Any]:
        if self.db is None or not hasattr(self.db, "create_asset_version"):
            return {"success": False, "error": "数据库不可用（缺少 create_asset_version）"}
        try:
            v = self.db.create_asset_version(
                asset_id=asset_id, version=version, status=status,
                note=note, file_ref=file_ref,
            )
        except Exception as e:
            return {"success": False, "error": f"创建版本失败: {e}"}
        return {
            "success": True,
            "version_id": getattr(v, "id", None),
            "asset_id": asset_id,
            "version": version,
            "status": status,
            "message": f"资产 {asset_id} 记录版本 {version}（{status}）。",
        }

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        action = str(inputs.get("action") or "manifest").strip().lower()
        if action in ("manifest", "清单", "交付清单", "delivery_manifest"):
            return self.manifest(project_id=inputs.get("project_id"))
        if action in ("acceptance", "验收", "验收单"):
            return self.acceptance(project_id=inputs.get("project_id"))
        if action in ("record", "交付", "record_delivery"):
            return self.record_delivery(
                project_id=inputs.get("project_id"),
                delivery_no=inputs.get("delivery_no"),
                items=inputs.get("items"),
                delivered_by=inputs.get("delivered_by"),
                title=inputs.get("title"),
            )
        if action in ("version", "版本", "record_version"):
            return self.record_version(
                asset_id=inputs.get("asset_id"),
                version=inputs.get("version"),
                status=inputs.get("status", "待审核"),
                note=inputs.get("note"),
                file_ref=inputs.get("file_ref"),
            )
        return {"success": False, "error": f"不支持的 action: {action}",
                "hint": "支持 action=manifest / acceptance / record / version"}
