"""
Tool框架 - 原子级工具定义和注册
"""
from typing import Dict, Any, List, Optional
from abc import ABC, abstractmethod
from datetime import datetime
import json


class BaseTool(ABC):
    """工具基类"""

    name: str = ""
    description: str = ""
    parameters: Dict = {}

    @abstractmethod
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行工具"""
        pass

    def get_schema(self) -> Dict:
        """获取工具的Function Calling Schema"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters
        }

    def validate_params(self, params: Dict) -> bool:
        """验证参数"""
        required = self.parameters.get("required", [])
        for param in required:
            if param not in params:
                return False
        return True


class ToolRegistry:
    """工具注册表"""

    def __init__(self):
        self.tools: Dict[str, BaseTool] = {}
        self.execution_history = []

    def register(self, tool: BaseTool):
        """注册工具"""
        if not tool.name:
            raise ValueError("Tool must have a name")

        self.tools[tool.name] = tool
        print(f"✓ Registered tool: {tool.name}")

    def get(self, name: str) -> Optional[BaseTool]:
        """获取工具"""
        return self.tools.get(name)

    def list_tools(self) -> List[Dict]:
        """列出所有工具"""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters
            }
            for tool in self.tools.values()
        ]

    def get_schemas(self) -> List[Dict]:
        """获取所有工具的Schema（用于Function Calling）"""
        return [tool.get_schema() for tool in self.tools.values()]

    async def execute(self, tool_name: str, **kwargs) -> Dict:
        """执行工具"""
        tool = self.get(tool_name)
        if not tool:
            return {
                "success": False,
                "error": f"Tool '{tool_name}' not found"
            }

        # 验证参数
        if not tool.validate_params(kwargs):
            return {
                "success": False,
                "error": f"Invalid parameters for tool '{tool_name}'"
            }

        # 执行
        try:
            start_time = datetime.now()
            result = await tool.execute(**kwargs)
            latency = (datetime.now() - start_time).total_seconds()

            # 记录执行历史
            self.execution_history.append({
                "tool": tool_name,
                "timestamp": start_time.isoformat(),
                "latency": latency,
                "success": result.get("success", True)
            })

            return result

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def get_execution_stats(self) -> Dict:
        """获取执行统计"""
        if not self.execution_history:
            return {"total": 0}

        total = len(self.execution_history)
        success = sum(1 for h in self.execution_history if h["success"])
        avg_latency = sum(h["latency"] for h in self.execution_history) / total

        return {
            "total": total,
            "success": success,
            "failed": total - success,
            "success_rate": success / total,
            "avg_latency": avg_latency
        }


# ===== 示例工具 =====

class ExcelParserTool(BaseTool):
    """Excel解析工具"""

    name = "parse_excel_quote"
    description = "解析Excel格式的报价单，自动识别关键字段"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Excel文件路径"
            },
            "client_hint": {
                "type": "string",
                "description": "客户名称提示（可选，帮助识别模板格式）"
            }
        },
        "required": ["file_path"]
    }

    async def execute(self, file_path: str, client_hint: str = None) -> Dict:
        """执行Excel解析 — 使用真实 ExcelQuoteParser"""
        try:
            from parsers.excel_parser import ExcelQuoteParser
            parser = ExcelQuoteParser()
            # parse() is sync, wrapped in async for consistency
            result = parser.parse(file_path, client_hint=client_hint)
            return result
        except ImportError:
            return {
                "success": False,
                "error": "openpyxl 未安装。运行: pip install openpyxl"
            }
        except FileNotFoundError:
            return {
                "success": False,
                "error": f"文件不存在: {file_path}"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


class ProfitCalculatorTool(BaseTool):
    """利润计算工具"""

    name = "calculate_profit"
    description = "计算项目利润，包括毛利润、净利润、利润率"
    parameters = {
        "type": "object",
        "properties": {
            "quote_amount": {
                "type": "number",
                "description": "报价金额"
            },
            "cost": {
                "type": "number",
                "description": "制作成本"
            },
            "management_rate": {
                "type": "number",
                "description": "管理费率，默认0.15"
            },
            "tax_rate": {
                "type": "number",
                "description": "税率，默认0.06"
            }
        },
        "required": ["quote_amount", "cost"]
    }

    async def execute(self, quote_amount: float, cost: float,
                     management_rate: float = 0.15,
                     tax_rate: float = 0.06) -> Dict:
        """执行利润计算"""
        try:
            gross_profit = quote_amount - cost
            management_fee = quote_amount * management_rate
            tax = quote_amount * tax_rate
            net_profit = gross_profit - management_fee - tax
            profit_rate = net_profit / quote_amount

            # 风险评估
            risk_level = "low"
            if profit_rate < 0.1:
                risk_level = "high"
            elif profit_rate < 0.2:
                risk_level = "medium"

            return {
                "success": True,
                "gross_profit": gross_profit,
                "management_fee": management_fee,
                "tax": tax,
                "net_profit": net_profit,
                "profit_rate": profit_rate,
                "profit_rate_percent": f"{profit_rate * 100:.1f}%",
                "risk_level": risk_level,
                "breakdown": {
                    "报价金额": quote_amount,
                    "制作成本": cost,
                    "毛利润": gross_profit,
                    "管理费": management_fee,
                    "税费": tax,
                    "净利润": net_profit
                },
                "recommendations": self._get_recommendations(profit_rate)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _get_recommendations(self, profit_rate: float) -> List[str]:
        """获取优化建议"""
        recommendations = []

        if profit_rate < 0.1:
            recommendations.append("⚠️ 利润率过低（<10%），建议重新评估报价或降低成本")
        elif profit_rate < 0.2:
            recommendations.append("💡 利润率偏低（10-20%），可以考虑优化成本结构")
        else:
            recommendations.append("✅ 利润率健康（>20%），项目可行")

        if profit_rate < 0.15:
            recommendations.append("💡 考虑与客户协商提高报价")
            recommendations.append("💡 评估是否可以降低外包成本")

        return recommendations


class TaskAllocatorTool(BaseTool):
    """任务分配工具"""

    name = "allocate_tasks"
    description = "智能分配任务给团队成员"
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "任务列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "estimated_hours": {"type": "number"}
                    }
                }
            },
            "team_members": {
                "type": "array",
                "description": "团队成员列表"
            }
        },
        "required": ["tasks", "team_members"]
    }

    async def execute(self, tasks: List[Dict], team_members: List[Dict]) -> Dict:
        """执行任务分配 — 基于技能匹配的评分算法"""
        try:
            from datetime import datetime, timedelta

            allocations = []
            member_load: Dict[str, float] = {m.get("name", m.get("id", "")): 0 for m in team_members}

            for task in tasks:
                if isinstance(task, str):
                    task = {"name": task, "type": "通用", "estimated_hours": 8}

                task_name = task.get("name", "Unknown")
                task_type = task.get("type", "").lower()
                task_hours = float(task.get("estimated_hours", 8))

                # Score each member by skill match
                best_member = None
                best_score = -1.0
                best_name = ""

                for member in team_members:
                    m_name = member.get("name", member.get("id", ""))
                    skills = str(member.get("skills", "")).lower()
                    role = str(member.get("role", "")).lower()

                    # Base score: 0.5
                    score = 0.5

                    # Skill keyword matching
                    if task_type and task_type in skills:
                        score += 0.3
                    elif task_type and task_type in role:
                        score += 0.2

                    # Penalize high load
                    current_load = member_load.get(m_name, 0)
                    if current_load > 16:
                        score -= 0.2
                    elif current_load > 8:
                        score -= 0.1

                    # Bonus for seniority
                    level = str(member.get("level", member.get("skill_level", ""))).lower()
                    if "资深" in level or "senior" in level:
                        score += 0.1

                    if score > best_score:
                        best_score = score
                        best_member = member
                        best_name = m_name

                if best_member is None:
                    best_member = team_members[0]
                    best_name = best_member.get("name", best_member.get("id", ""))
                    best_score = 0.5

                # Update load
                member_load[best_name] = member_load.get(best_name, 0) + task_hours

                allocations.append({
                    "task": task_name,
                    "task_type": task_type,
                    "assigned_to": best_name,
                    "match_score": round(best_score, 2),
                    "estimated_hours": task_hours,
                    "estimated_completion": (datetime.now() + timedelta(days=max(1, task_hours / 8))).strftime("%Y-%m-%d")
                })

            return {
                "success": True,
                "allocations": allocations,
                "team_load": {name: min(round(load, 1), 100) for name, load in member_load.items()},
                "total_tasks": len(tasks)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


class OCRImageTool(BaseTool):
    """OCR图片识别工具，优先使用配置好的 Unlimited-OCR sidecar。"""

    name = "ocr_image"
    description = "识别图片中的文字（Unlimited-OCR sidecar，未配置时兼容 PaddleOCR）"
    parameters = {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "图片文件路径"
            },
            "language": {
                "type": "string",
                "description": "语言，默认'ch'（中文）"
            }
        },
        "required": ["image_path"]
    }

    async def execute(self, image_path: str, language: str = "ch") -> Dict:
        """执行 OCR；主动配置 sidecar 后不再隐式切换到另一种引擎。"""
        try:
            from utils.unlimited_ocr import UnlimitedOCRClient, UnlimitedOCRConfig

            unlimited = UnlimitedOCRClient(UnlimitedOCRConfig.from_env())
            if unlimited.ready():
                result = unlimited.parse(
                    [image_path],
                    prompt="document parsing.",
                    image_mode="gundam",
                )
                if result.ok:
                    return {
                        "success": True,
                        "text": result.text,
                        "raw_text": result.text,
                        "source": "unlimited-ocr",
                        "ocr": {
                            "engine": result.source,
                            "truncated": result.truncated,
                            "degraded": result.degraded,
                            "quality": "unreported",
                        },
                    }
                return {
                    "success": False,
                    "error": result.error or "Unlimited-OCR service unavailable",
                    "source": "unlimited-ocr",
                    "degraded": result.degraded,
                }
        except Exception:
            # Keep the legacy tool available when the optional adapter is not
            # importable in an older standalone invocation.
            pass
        try:
            from parsers.ocr_parser import OCRImageParser, PADDLEOCR_AVAILABLE

            if not PADDLEOCR_AVAILABLE:
                return {
                    "success": False,
                    "error": (
                        "PaddleOCR 未安装，无法进行图片文字识别。\n"
                        "请运行: pip install paddleocr paddlepaddle\n"
                        "或改用 Excel 格式上传报价单。"
                    ),
                    "paddleocr_available": False
                }

            parser = OCRImageParser(lang=language)
            result = parser.recognize(image_path)

            if result.get("success"):
                # Also try to parse as quote if it looks like one
                quote_info = parser.parse_quote_from_ocr(result)
                result["quote_info"] = quote_info

            return result

        except ImportError:
            return {
                "success": False,
                "error": "OCR 解析器模块未找到，请确保 parsers/ocr_parser.py 存在",
                "paddleocr_available": False
            }
        except FileNotFoundError:
            return {
                "success": False,
                "error": f"图片文件不存在: {image_path}"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


# 初始化工具注册表
def init_default_tools() -> ToolRegistry:
    """初始化默认工具"""
    registry = ToolRegistry()

    # 注册内置工具
    registry.register(ExcelParserTool())
    registry.register(ProfitCalculatorTool())
    registry.register(TaskAllocatorTool())
    registry.register(OCRImageTool())

    return registry


# 使用示例
async def demo():
    """演示工具使用"""
    registry = init_default_tools()

    print("已注册的工具:")
    for tool in registry.list_tools():
        print(f"- {tool['name']}: {tool['description']}")

    print("\n\n执行工具: calculate_profit")
    result = await registry.execute(
        "calculate_profit",
        quote_amount=30000,
        cost=20000
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n\n工具执行统计:")
    stats = registry.get_execution_stats()
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    import asyncio
    asyncio.run(demo())
