"""
Skill系统 - 可复用的业务流程
"""
from typing import Dict, Any, List, Optional
from abc import ABC, abstractmethod
from datetime import datetime
import json


class BaseSkill(ABC):
    """Skill基类 - 可复用的业务流程"""

    skill_name: str = ""
    description: str = ""
    version: str = "1.0.0"
    required_tools: List[str] = []

    def __init__(self, tool_registry=None):
        self.tool_registry = tool_registry
        self.execution_log = []

    @abstractmethod
    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行Skill"""
        pass

    async def call_tool(self, tool_name: str, **kwargs) -> Dict:
        """调用工具"""
        if not self.tool_registry:
            return {"success": False, "error": "Tool registry not available"}

        result = await self.tool_registry.execute(tool_name, **kwargs)
        self._log_tool_call(tool_name, kwargs, result)
        return result

    def _log_tool_call(self, tool_name: str, params: Dict, result: Dict):
        """记录工具调用"""
        self.execution_log.append({
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "params": params,
            "success": result.get("success", False)
        })

    def get_info(self) -> Dict:
        """获取Skill信息"""
        return {
            "skill_name": self.skill_name,
            "description": self.description,
            "version": self.version,
            "required_tools": self.required_tools
        }


class DocumentProcessingSkill(BaseSkill):
    """文档处理Skill - 完整的文档处理流程"""

    skill_name = "document_processing"
    description = "自动识别并处理上传的文档（Excel、图片、PDF）"
    version = "1.0.0"
    required_tools = ["parse_excel_quote", "ocr_image", "calculate_profit"]

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行文档处理流程

        context:
            - file_path: 文件路径
            - file_type: 文件类型 (excel, image, pdf)
            - hint: 用户提示（可选）
        """
        file_path = context.get("file_path")
        file_type = context.get("file_type", "excel")
        user_hint = context.get("hint", "")

        result = {
            "success": False,
            "steps": [],
            "file_path": file_path
        }

        # Step 1: 识别文档类型
        result["steps"].append({
            "name": "识别文档类型",
            "status": "completed",
            "data": {"detected_type": file_type}
        })

        # Step 2: 解析文档
        parse_result = None
        if file_type == "excel":
            parse_result = await self.call_tool(
                "parse_excel_quote",
                file_path=file_path,
                client_hint=user_hint
            )
        elif file_type == "image":
            ocr_result = await self.call_tool("ocr_image", image_path=file_path)
            if ocr_result.get("success"):
                # TODO: 从OCR文本中提取结构化信息
                parse_result = {
                    "success": True,
                    "document_type": "报价单（图片）",
                    "raw_text": ocr_result["text"]
                }

        result["steps"].append({
            "name": "解析文档",
            "status": "completed" if parse_result and parse_result.get("success") else "failed",
            "data": parse_result
        })

        if not parse_result or not parse_result.get("success"):
            result["error"] = "文档解析失败"
            return result

        # Step 3: 提取关键信息
        quote_info = self._extract_quote_info(parse_result)
        result["quote_info"] = quote_info

        result["steps"].append({
            "name": "提取关键信息",
            "status": "completed",
            "data": quote_info
        })

        # Step 4: 计算利润（如果有成本信息）
        if "cost" in quote_info and quote_info["cost"]:
            profit_result = await self.call_tool(
                "calculate_profit",
                quote_amount=quote_info["amount"],
                cost=quote_info["cost"]
            )

            result["steps"].append({
                "name": "计算利润",
                "status": "completed" if profit_result.get("success") else "failed",
                "data": profit_result
            })

            result["profit_analysis"] = profit_result

        # Step 5: 保存到数据库
        # TODO: 实现数据库保存
        project_id = f"proj_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        result["steps"].append({
            "name": "保存数据",
            "status": "completed",
            "data": {"project_id": project_id}
        })

        result["success"] = True
        result["project_id"] = project_id
        result["next_steps"] = [
            "审核报价信息",
            "补充成本数据（如果缺失）",
            "分配项目任务"
        ]

        return result

    def _extract_quote_info(self, parse_result: Dict) -> Dict:
        """从解析结果中提取报价信息"""
        return {
            "client": parse_result.get("client"),
            "project_name": parse_result.get("project_name"),
            "amount": parse_result.get("total_amount"),
            "cost": None,  # 需要用户补充
            "deadline": parse_result.get("deadline"),
            "assets": parse_result.get("assets", [])
        }


class ProjectManagementSkill(BaseSkill):
    """项目管理Skill - 完整的项目管理流程"""

    skill_name = "project_management"
    description = "项目管理完整流程：任务分解、分配、排期、提醒"
    version = "1.0.0"
    required_tools = ["allocate_tasks"]

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行项目管理流程

        context:
            - project_id: 项目ID
            - assets: 资产列表
            - team_members: 团队成员
            - deadline: 截止日期
        """
        project_id = context.get("project_id")
        assets = context.get("assets", [])
        team_members = context.get("team_members", [])
        deadline = context.get("deadline")

        result = {
            "success": False,
            "project_id": project_id,
            "steps": []
        }

        # Step 1: 分解任务
        tasks = self._break_down_tasks(assets)
        result["steps"].append({
            "name": "任务分解",
            "status": "completed",
            "data": {"tasks": tasks, "count": len(tasks)}
        })

        # Step 2: 分配任务
        allocation_result = await self.call_tool(
            "allocate_tasks",
            tasks=tasks,
            team_members=team_members
        )

        result["steps"].append({
            "name": "任务分配",
            "status": "completed" if allocation_result.get("success") else "failed",
            "data": allocation_result
        })

        # Step 3: 创建排期
        schedule = self._create_schedule(allocation_result.get("allocations", []), deadline)
        result["steps"].append({
            "name": "创建排期",
            "status": "completed",
            "data": schedule
        })

        # Step 4: 设置提醒
        reminders = self._setup_reminders(schedule)
        result["steps"].append({
            "name": "设置提醒",
            "status": "completed",
            "data": reminders
        })

        result["success"] = True
        result["tasks"] = tasks
        result["allocations"] = allocation_result.get("allocations", [])
        result["schedule"] = schedule
        result["reminders"] = reminders

        return result

    def _break_down_tasks(self, assets: List[Dict]) -> List[Dict]:
        """分解任务"""
        tasks = []
        for asset in assets:
            asset_name = asset.get("name", "未命名资产")
            # 根据资产类型分解任务
            tasks.extend([
                {
                    "name": f"{asset_name} - 建模",
                    "type": "modeling",
                    "estimated_hours": 16,
                    "asset": asset_name
                },
                {
                    "name": f"{asset_name} - 贴图",
                    "type": "texturing",
                    "estimated_hours": 8,
                    "asset": asset_name
                },
                {
                    "name": f"{asset_name} - 优化",
                    "type": "optimization",
                    "estimated_hours": 4,
                    "asset": asset_name
                }
            ])
        return tasks

    def _create_schedule(self, allocations: List[Dict], deadline: str) -> Dict:
        """创建排期"""
        # TODO: 实现智能排期算法
        return {
            "start_date": datetime.now().strftime("%Y-%m-%d"),
            "end_date": deadline,
            "milestones": [
                {"name": "建模完成", "date": "2026-08-05"},
                {"name": "贴图完成", "date": "2026-08-10"},
                {"name": "最终交付", "date": deadline}
            ],
            "gantt_chart_url": None  # TODO: 生成甘特图
        }

    def _setup_reminders(self, schedule: Dict) -> List[Dict]:
        """设置提醒"""
        reminders = []
        for milestone in schedule.get("milestones", []):
            reminders.append({
                "type": "milestone",
                "title": milestone["name"],
                "date": milestone["date"],
                "notify_before_days": 2
            })
        return reminders


class ProfitAnalysisSkill(BaseSkill):
    """利润分析Skill"""

    skill_name = "profit_analysis"
    description = "深度利润分析和优化建议"
    version = "1.0.0"
    required_tools = ["calculate_profit"]

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行利润分析"""
        quote_amount = context.get("quote_amount")
        cost = context.get("cost")

        # 基础计算
        profit_result = await self.call_tool(
            "calculate_profit",
            quote_amount=quote_amount,
            cost=cost
        )

        # 敏感度分析
        sensitivity = self._sensitivity_analysis(quote_amount, cost)

        # 历史对比
        historical_comparison = self._compare_with_history(
            profit_result.get("profit_rate", 0)
        )

        return {
            "success": True,
            "basic_analysis": profit_result,
            "sensitivity_analysis": sensitivity,
            "historical_comparison": historical_comparison,
            "recommendations": self._generate_recommendations(profit_result, sensitivity)
        }

    def _sensitivity_analysis(self, quote: float, cost: float) -> Dict:
        """敏感度分析"""
        # 分析成本变化对利润的影响
        scenarios = []
        for cost_change in [-0.1, -0.05, 0, 0.05, 0.1]:
            new_cost = cost * (1 + cost_change)
            net_profit = quote - new_cost - quote * 0.15 - quote * 0.06
            scenarios.append({
                "cost_change": f"{cost_change * 100:+.0f}%",
                "new_cost": new_cost,
                "net_profit": net_profit,
                "profit_rate": net_profit / quote
            })

        return {
            "scenarios": scenarios,
            "conclusion": "成本每变化1%，利润率变化约X%"
        }

    def _compare_with_history(self, current_rate: float) -> Dict:
        """历史对比"""
        # TODO: 从数据库获取历史数据
        avg_rate = 0.25
        return {
            "current_rate": current_rate,
            "avg_rate": avg_rate,
            "vs_average": current_rate - avg_rate,
            "percentile": 65  # 当前项目在历史项目中的百分位
        }

    def _generate_recommendations(self, profit_result: Dict, sensitivity: Dict) -> List[str]:
        """生成优化建议"""
        recommendations = profit_result.get("recommendations", [])

        # 基于敏感度分析添加建议
        recommendations.append("💡 成本控制是提高利润的关键，建议优化外包成本")

        return recommendations


class SkillRegistry:
    """Skill注册表"""

    def __init__(self, tool_registry=None):
        self.skills: Dict[str, BaseSkill] = {}
        self.tool_registry = tool_registry

    def register(self, skill: BaseSkill):
        """注册Skill"""
        skill.tool_registry = self.tool_registry
        self.skills[skill.skill_name] = skill
        print(f"✓ Registered skill: {skill.skill_name}")

    def get(self, skill_name: str) -> Optional[BaseSkill]:
        """获取Skill"""
        return self.skills.get(skill_name)

    def list_skills(self) -> List[Dict]:
        """列出所有Skill"""
        return [skill.get_info() for skill in self.skills.values()]

    async def execute(self, skill_name: str, context: Dict) -> Dict:
        """执行Skill"""
        skill = self.get(skill_name)
        if not skill:
            return {
                "success": False,
                "error": f"Skill '{skill_name}' not found"
            }

        try:
            result = await skill.execute(context)
            return result
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "skill": skill_name
            }


# 初始化Skill注册表
def init_default_skills(tool_registry) -> SkillRegistry:
    """初始化默认Skill"""
    registry = SkillRegistry(tool_registry)

    # 注册内置Skill
    registry.register(DocumentProcessingSkill())
    registry.register(ProjectManagementSkill())
    registry.register(ProfitAnalysisSkill())

    return registry


# 使用示例
async def demo():
    """演示Skill使用"""
    from .tools import init_default_tools

    # 初始化
    tool_registry = init_default_tools()
    skill_registry = init_default_skills(tool_registry)

    print("已注册的Skills:")
    for skill in skill_registry.list_skills():
        print(f"- {skill['skill_name']}: {skill['description']}")

    # 执行文档处理Skill
    print("\n\n执行Skill: document_processing")
    result = await skill_registry.execute(
        "document_processing",
        context={
            "file_path": "/path/to/quote.xlsx",
            "file_type": "excel",
            "hint": "腾讯项目"
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    import asyncio
    asyncio.run(demo())
