"""
Requirements Assessment Skill - 需求评估

面向美术外包项目经理的需求评估增强：
  1. 复杂度判定 (assess_complexity) —— 让 Asset.complexity 真正参与评估：
     依据资产类型 + 需求关键词启发式判定 simple/medium/complex，并给出评分与依据。
  2. 范围确认清单 (scope_checklist) —— 生成需求范围确认清单，避免遗漏关键项。
  3. 报价落库 (ingest) —— 解析后的报价/需求数据自动建 Project + Assets + Document，
     修复 intake→落库断层（之前解析结果不写入数据库）。

所有配置走内置默认值，config 可覆盖（不强制修改配置文件）。
"""
from typing import Dict, Any, List, Optional

from .base_skill import BaseSkill


# 复杂度关键词（按强度分级）。命中即累加评分。
_COMPLEX_KEYWORDS = {
    "complex": ["写实", "高精度", "影视级", "pbr", "次表面", "毛发", "骨骼绑定", "四足",
                "角色表演", "表情", "布料模拟", "流体", "特效", "粒子", "程序化",
                "模块化", "lod", "风格化高", "zbrush", "雕刻", "高模", "动画绑定"],
    "medium": ["绑定", "动画", "贴图", "材质", "场景", "道具", "怪物", "机甲", "角色",
               "载具", "武器", "建筑", "地形", "特效", "光照", "渲染"],
    "simple": ["基础模型", "低模", "图标", "ui", "静帧", "平面", "简单"],
}

# 资产类型默认基线复杂度
_ASSET_BASE_COMPLEXITY = {
    "角色": "medium", "场景": "medium", "特效": "complex", "动画": "medium",
    "ui": "simple", "道具": "simple", "怪物": "medium", "机甲": "medium",
    "建筑": "medium", "地形": "simple",
}

# 默认需求范围确认清单模板
_DEFAULT_SCOPE_ITEMS = [
    ("资产清单", "已确认全部资产名称、数量与类型，无歧义"),
    ("参考图/视频", "客户提供风格参考、三视图或类似作品"),
    ("风格指南", "明确写实/风格化、精度要求、技术规格"),
    ("交付格式", "明确文件格式、命名规范、引擎/软件版本"),
    ("验收标准", "明确质量门槛、退改轮次上限、返工界定"),
    ("周期与里程碑", "明确各阶段节点与整体截止日期"),
    ("版权与授权", "确认素材授权、字体/插件合规、可商用范围"),
    ("沟通节奏", "明确对接人、反馈周期与改稿流程"),
]


def _normalize_text(text: str) -> str:
    return (text or "").lower()


class RequirementsAssessmentSkill(BaseSkill):
    """需求评估增强：复杂度判定、范围确认清单、报价解析自动落库"""

    skill_name = "requirements_assessment"
    description = "需求评估增强：资产复杂度判定、需求范围确认清单、报价解析自动建库"
    version = "1.0"
    requires_llm = False

    def __init__(self, context: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        self.db = self.context.get("database") if self.context else None

    # ── 复杂度判定 ──
    def assess_complexity(self, asset_type: Optional[str] = None,
                          requirements_text: Optional[str] = None,
                          asset_name: Optional[str] = None) -> Dict[str, Any]:
        text = _normalize_text(f"{requirements_text or ''} {asset_name or ''}")
        score = 1
        hits: Dict[str, List[str]] = {"complex": [], "medium": [], "simple": []}
        for level, kws in _COMPLEX_KEYWORDS.items():
            for kw in kws:
                if kw in text:
                    hits[level].append(kw)
                    score += 2 if level == "complex" else (1 if level == "medium" else 0)

        # 资产类型基线
        base = _ASSET_BASE_COMPLEXITY.get((asset_type or "").lower())
        if base == "complex":
            score += 2
        elif base == "medium":
            score += 1

        # 映射
        if score >= 6 or hits["complex"]:
            complexity = "complex"
        elif score >= 3 or hits["medium"]:
            complexity = "medium"
        else:
            complexity = "simple"

        return {
            "success": True,
            "asset_type": asset_type,
            "asset_name": asset_name,
            "complexity": complexity,
            "score": score,
            "factors": {k: v for k, v in hits.items() if v},
            "message": (
                f"判定为 {complexity}（评分 {score}）："
                + ("、".join(hits["complex"]) if hits["complex"] else "")
                + ("、".join(hits["medium"]) if hits["medium"] else "")
            ),
        }

    # ── 范围确认清单 ──
    def scope_checklist(self, asset_types: Optional[List[str]] = None,
                        requirements_text: Optional[str] = None) -> Dict[str, Any]:
        items = []
        for name, desc in _DEFAULT_SCOPE_ITEMS:
            items.append({"item": name, "detail": desc, "status": "待确认"})
        # 若已给出资产类型，补一条逐资产确认
        if asset_types:
            for at in asset_types:
                items.append({
                    "item": f"资产类型确认：{at}",
                    "detail": "确认该类型资产的交付标准与复杂度",
                    "status": "待确认",
                })
        return {
            "success": True,
            "checklist": items,
            "count": len(items),
            "summary": "请逐项确认以上需求范围，确认无误后再发起报价与排期。",
        }

    # ── 报价解析落库 ──
    def ingest(self, project_name: str, client: str,
               parsed_data: Dict[str, Any],
               document_file_name: Optional[str] = None,
               document_type: str = "报价单") -> Dict[str, Any]:
        if self.db is None:
            return {"success": False, "error": "数据库不可用，无法落库"}
        assets = parsed_data.get("assets") or []
        quote_amount = float(parsed_data.get("quote_amount", 0) or 0)
        try:
            project = self.db.create_project({
                "project_name": project_name,
                "client": client,
                "quote_amount": quote_amount,
                "status": "待开始",
            })
        except Exception as e:
            return {"success": False, "error": f"创建项目失败: {e}"}

        created_assets = []
        for a in assets:
            qty = int(a.get("quantity", 1) or 1)
            unit = float(a.get("unit_price", 0) or 0)
            complexity = a.get("complexity")
            if not complexity:
                c = self.assess_complexity(
                    asset_type=a.get("asset_type"),
                    requirements_text=a.get("requirements"),
                    asset_name=a.get("asset_name"),
                )
                complexity = c.get("complexity")
            try:
                asset = self.db.create_asset({
                    "project_id": project.id,
                    "asset_name": a.get("asset_name", "未命名资产"),
                    "asset_type": a.get("asset_type"),
                    "quantity": qty,
                    "unit_price": unit,
                    "total_price": unit * qty,
                    "complexity": complexity,
                    "requirements": a.get("requirements"),
                })
                created_assets.append({"asset_id": asset.id, "asset_name": asset.asset_name,
                                       "complexity": complexity})
            except Exception as e:
                return {"success": False, "error": f"创建资产失败: {e}",
                        "project_id": project.id}

        # 持久化原始解析文档
        doc_id = None
        try:
            doc = self.db.save_document(
                project_id=project.id,
                document_type=document_type,
                file_name=document_file_name or "parsed_quote",
                parsed_data=parsed_data,
                confidence_score=parsed_data.get("confidence"),
            )
            doc_id = doc.id
        except Exception:
            doc_id = None

        return {
            "success": True,
            "project_id": project.id,
            "document_id": doc_id,
            "asset_count": len(created_assets),
            "assets": created_assets,
            "message": f"已落库项目「{project_name}」及 {len(created_assets)} 个资产。",
        }

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        action = str(inputs.get("action") or "assess").strip().lower()
        if action in ("assess", "complexity", "复杂度", "评估复杂度"):
            return self.assess_complexity(
                asset_type=inputs.get("asset_type"),
                requirements_text=inputs.get("requirements_text"),
                asset_name=inputs.get("asset_name"),
            )
        if action in ("scope", "checklist", "范围", "范围确认", "清单"):
            return self.scope_checklist(
                asset_types=inputs.get("asset_types"),
                requirements_text=inputs.get("requirements_text"),
            )
        if action in ("ingest", "落库", "导入", "建库"):
            return self.ingest(
                project_name=inputs.get("project_name"),
                client=inputs.get("client"),
                parsed_data=inputs.get("parsed_data") or {},
                document_file_name=inputs.get("document_file_name"),
                document_type=inputs.get("document_type", "报价单"),
            )
        return {
            "success": False,
            "error": f"不支持的 action: {action}",
            "hint": "支持 action=assess / scope / ingest",
        }
