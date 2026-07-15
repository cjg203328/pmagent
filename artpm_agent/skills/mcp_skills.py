"""
MCP-based Skills - 基于增强型MCP客户端的技能
"""
from datetime import datetime
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd
from .base_skill import BaseSkill
from .input_schemas import BUILTIN_SKILL_INPUT_SCHEMAS
from artpm_agent.core.mcp_client_enhanced import get_enhanced_mcp_client


# ═══════════════════════════════════════════════════════════
# 文件操作 Skills
# ═══════════════════════════════════════════════════════════

class FileReaderSkill(BaseSkill):
    """文件读取Skill - 读取项目文件、报价单等"""

    skill_name = "file_reader"
    description = "读取和分析项目文件(Excel, PDF, TXT, CSV等)"
    version = "1.0.0"
    required_tools = ["read_file"]
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def __init__(self, context: Dict[str, Any]):
        super().__init__(context)
        self.mcp_client = get_enhanced_mcp_client()

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行文件读取

        输入:
        - file_path: 文件路径(必需)
        - encoding: 文件编码(可选,默认utf-8)
        - lines_limit: 行数限制(可选)
        - summary: 是否生成摘要(可选,默认False)

        输出:
        - success: bool
        - content: 文件内容
        - metadata: 文件元数据
        - summary: 内容摘要(如果请求)
        """
        file_path = inputs.get("file_path")
        if not file_path:
            return {"success": False, "error": "file_path is required"}

        # 调用MCP工具读取文件
        result = await self.mcp_client.call_tool("read_file", {
            "file_path": file_path,
            "encoding": inputs.get("encoding", "utf-8"),
            "lines_limit": inputs.get("lines_limit")
        })

        if not result.get("success"):
            return result

        # 如果请求摘要,生成内容摘要
        if inputs.get("summary") and self.context.get("llm_client"):
            content = result["content"]
            # 截取前1000字符用于摘要
            preview = content[:1000] if len(content) > 1000 else content

            summary_prompt = f"""请为以下文件内容生成简短摘要(100字以内):

文件: {result['metadata']['file_name']}
内容预览:
{preview}

摘要:"""
            try:
                summary = await self.call_llm(summary_prompt)
                result["summary"] = summary
            except Exception as error:
                result["summary_error"] = str(error)

        return result


class FileSearchSkill(BaseSkill):
    """文件搜索Skill - 在项目中搜索文件"""

    skill_name = "file_search"
    description = "搜索项目目录中的文件和文档"
    version = "1.0.0"
    required_tools = ["search_files"]
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def __init__(self, context: Dict[str, Any]):
        super().__init__(context)
        self.mcp_client = get_enhanced_mcp_client()

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        搜索文件

        输入:
        - pattern: 搜索模式(glob pattern,例如 *.xlsx)
        - directory: 搜索目录(可选)
        - recursive: 是否递归搜索(可选,默认True)
        - limit: 结果数量限制(可选,默认100)
        - content_search: 内容搜索关键词(可选)

        输出:
        - success: bool
        - files: 匹配的文件列表
        - count: 文件数量
        - matches: 内容匹配结果(如果指定content_search)
        """
        pattern = inputs.get("pattern")
        if not pattern:
            return {"success": False, "error": "pattern is required"}

        # 搜索文件
        result = await self.mcp_client.call_tool("search_files", {
            "pattern": pattern,
            "directory": inputs.get("directory"),
            "recursive": inputs.get("recursive", True),
            "limit": inputs.get("limit", 100)
        })

        if not result.get("success"):
            return result

        # 如果指定了content_search,搜索文件内容
        if inputs.get("content_search") and result.get("files"):
            content_result = await self.mcp_client.call_tool("search_content", {
                "query": inputs["content_search"],
                "file_pattern": pattern,
                "directory": inputs.get("directory"),
                "limit": inputs.get("limit", 50)
            })

            if content_result.get("success"):
                result["content_matches"] = content_result["matches"]
                result["content_count"] = content_result["count"]

        return result


# ═══════════════════════════════════════════════════════════
# 数据分析 Skills
# ═══════════════════════════════════════════════════════════

class DataAnalyzerSkill(BaseSkill):
    """数据分析Skill - 分析项目数据、报表等"""

    skill_name = "data_analyzer"
    description = "分析结构化数据(Excel, CSV, JSON)"
    version = "1.0.0"
    required_tools = ["analyze_data"]
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def __init__(self, context: Dict[str, Any]):
        super().__init__(context)
        self.mcp_client = get_enhanced_mcp_client()

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        数据分析

        输入:
        - data_source: 数据源(文件路径或数据对象)
        - analysis_type: 分析类型(descriptive/summary/statistics)
        - metrics: 要分析的指标(可选)
        - visualize: 是否生成可视化建议(可选)

        输出:
        - success: bool
        - analysis: 分析结果
        - insights: 分析洞察
        - preview: 数据预览
        - visualizations: 可视化建议(如果请求)
        """
        data_source = inputs.get("data_source")
        if data_source is None:
            return {"success": False, "error": "data_source is required"}

        # 调用MCP工具分析数据
        result = await self.mcp_client.call_tool("analyze_data", {
            "data_source": data_source,
            "analysis_type": inputs.get("analysis_type", "descriptive"),
            "metrics": inputs.get("metrics")
        })

        if not result.get("success"):
            return result

        # 如果请求可视化建议
        if inputs.get("visualize") and self.context.get("llm_client"):
            viz_prompt = f"""基于以下数据分析结果,建议3种合适的可视化方式:

数据特征:
- 行数: {result['analysis']['rows']}
- 列数: {result['analysis']['columns']}
- 列名: {', '.join(result['analysis']['column_names'])}

请简洁推荐可视化类型(例如:柱状图、折线图、饼图)和原因。"""

            try:
                viz_suggestions = await self.call_llm(viz_prompt)
                result["visualizations"] = viz_suggestions
            except Exception as error:
                result["visualization_error"] = str(error)

        return result


class TrendAnalyzerSkill(BaseSkill):
    """趋势分析Skill - 分析项目利润、成本、进度等趋势"""

    skill_name = "trend_analyzer"
    description = "分析项目利润、成本、进度等趋势"
    version = "1.0.0"
    required_tools = ["analyze_data"]
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def __init__(self, context: Dict[str, Any]):
        super().__init__(context)
        self.mcp_client = get_enhanced_mcp_client()

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        趋势分析

        输入:
        - data_series: 时间序列数据(文件路径或数据对象)
        - time_column: 时间列名(可选,默认自动检测)
        - value_column: 值列名(可选,默认自动检测)
        - period: 分析周期(daily/weekly/monthly,可选)
        - forecast: 是否预测(可选,默认False)

        输出:
        - success: bool
        - trend: 趋势方向(上升/下降/稳定)
        - statistics: 统计信息
        - forecast: 预测值(如果请求)
        - insights: 分析洞察
        """
        data_series = inputs.get("data_series")
        if data_series is None:
            return {"success": False, "error": "data_series is required"}

        # 先分析数据
        analysis_result = await self.mcp_client.call_tool("analyze_data", {
            "data_source": data_series,
            "analysis_type": "statistics"
        })

        if not analysis_result.get("success"):
            return analysis_result

        try:
            frame = self._load_frame(data_series)
        except (OSError, TypeError, ValueError) as error:
            return {"success": False, "error": f"无法加载趋势数据: {error}"}

        time_column = inputs.get("time_column") or self._detect_time_column(frame)
        value_column = inputs.get("value_column") or self._detect_value_column(frame, time_column)
        if value_column not in frame.columns:
            return {"success": False, "error": "未找到可分析的数值列"}

        values = pd.to_numeric(frame[value_column], errors="coerce")
        trend_frame = pd.DataFrame({"value": values})
        if time_column and time_column in frame.columns:
            trend_frame["time"] = pd.to_datetime(frame[time_column], errors="coerce")
            trend_frame = trend_frame.dropna(subset=["time", "value"]).sort_values("time")
            period = inputs.get("period")
            period_rules = {"daily": "D", "weekly": "W", "monthly": "ME"}
            if period in period_rules:
                trend_frame = (
                    trend_frame.set_index("time")["value"]
                    .resample(period_rules[period]).mean().dropna().reset_index()
                )
        else:
            trend_frame = trend_frame.dropna(subset=["value"])

        if len(trend_frame) < 2:
            return {"success": False, "error": "趋势分析至少需要2个有效数据点"}

        series = trend_frame["value"].astype(float).to_numpy()
        slope = float(np.polyfit(np.arange(len(series)), series, 1)[0])
        baseline = max(abs(float(np.mean(series))) * 0.01, 1e-9)
        trend = "稳定" if abs(slope) <= baseline else ("上升" if slope > 0 else "下降")
        growth_rate = None if series[0] == 0 else float((series[-1] - series[0]) / abs(series[0]))
        insights = [f"{value_column}整体呈{trend}趋势", f"每期平均变化 {slope:.2f}"]
        if growth_rate is not None:
            insights.append(f"首尾变化率 {growth_rate * 100:.1f}%")

        result = {
            "success": True,
            "trend": trend,
            "statistics": analysis_result.get("analysis"),
            "insights": insights,
            "data_preview": analysis_result.get("preview"),
            "time_column": time_column,
            "value_column": value_column,
            "slope": slope,
            "growth_rate": growth_rate,
            "points": len(series),
        }

        if inputs.get("forecast"):
            result["forecast_value"] = float(series[-1] + slope)

        # 如果需要预测且有LLM
        if inputs.get("forecast") and self.context.get("llm_client"):
            forecast_prompt = f"""基于以下数据趋势,预测未来发展:

数据统计: {result['statistics']}
当前趋势: {trend}

请给出:
1. 未来趋势预测
2. 可能的风险
3. 建议"""

            try:
                forecast = await self.call_llm(forecast_prompt)
                result["forecast"] = forecast
            except Exception as error:
                result["forecast_error"] = str(error)

        return result

    def _load_frame(self, source: Any) -> pd.DataFrame:
        if isinstance(source, pd.DataFrame):
            return source.copy()
        if isinstance(source, (list, dict)):
            return pd.DataFrame(source)
        if isinstance(source, str):
            path = self.mcp_client._resolve_path(source)
            suffix = path.suffix.lower()
            if suffix in {".xlsx", ".xls"}:
                return pd.read_excel(path)
            if suffix == ".csv":
                return pd.read_csv(path)
            if suffix == ".json":
                return pd.read_json(path)
            raise ValueError(f"不支持的文件格式: {suffix}")
        raise TypeError(f"不支持的数据类型: {type(source).__name__}")

    @staticmethod
    def _detect_time_column(frame: pd.DataFrame) -> Optional[str]:
        keywords = ("date", "time", "日期", "时间")
        for column in frame.columns:
            if any(keyword in str(column).lower() for keyword in keywords):
                return column
        return None

    @staticmethod
    def _detect_value_column(frame: pd.DataFrame, time_column: Optional[str]) -> Optional[str]:
        for column in frame.columns:
            if column == time_column:
                continue
            if pd.to_numeric(frame[column], errors="coerce").notna().sum() >= 2:
                return column
        return None


# ═══════════════════════════════════════════════════════════
# 智能决策 Skills
# ═══════════════════════════════════════════════════════════

class ProjectEvaluatorSkill(BaseSkill):
    """项目评估Skill - 评估项目的可行性、风险和收益"""

    skill_name = "project_evaluator"
    description = "评估项目的可行性、风险和收益"
    version = "1.0.0"
    required_tools = ["analyze_data", "read_file"]
    input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

    def __init__(self, context: Dict[str, Any]):
        super().__init__(context)
        self.mcp_client = get_enhanced_mcp_client()

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        项目评估

        输入:
        - project_data: 项目数据(字典或文件路径)
          必需字段: quote_amount, cost, deadline
        - historical_data: 历史项目数据源(可选)
        - constraints: 约束条件(可选)

        输出:
        - success: bool
        - feasibility_score: 可行性评分(0-100)
        - risk_analysis: 风险分析
        - profit_analysis: 利润分析
        - recommendations: 决策建议
        - similar_projects: 相似历史项目(如果有历史数据)
        """
        project_data = inputs.get("project_data")
        if project_data is None:
            return {"success": False, "error": "project_data is required"}

        # 如果是文件路径,先读取
        if isinstance(project_data, str):
            analysis_result = await self.mcp_client.call_tool("analyze_data", {
                "data_source": project_data,
                "analysis_type": "summary",
            })
            if not analysis_result.get("success"):
                return analysis_result
            preview = analysis_result.get("preview", [])
            if not preview:
                return {"success": False, "error": "项目文件不包含数据"}
            project_data = preview[0]

        if not isinstance(project_data, dict):
            return {"success": False, "error": "project_data 必须是字典或结构化文件路径"}

        # 提取项目关键信息
        try:
            quote_amount = float(project_data.get("quote_amount", 0))
            cost = float(project_data.get("cost", 0))
        except (TypeError, ValueError):
            return {"success": False, "error": "quote_amount 和 cost 必须是数字"}
        if quote_amount <= 0:
            return {"success": False, "error": "quote_amount 必须大于0"}
        if cost < 0:
            return {"success": False, "error": "cost 不能小于0"}
        deadline = project_data.get("deadline")

        # 计算基础指标
        profit_rate = (quote_amount - cost) / quote_amount

        # 评估可行性 (简化版评分逻辑)
        feasibility_score = 50  # 基准分

        # 利润率评分 (0-30分)
        if profit_rate >= 0.25:
            feasibility_score += 30
        elif profit_rate >= 0.15:
            feasibility_score += 20
        elif profit_rate >= 0.10:
            feasibility_score += 10

        days_left = None
        if deadline:
            try:
                deadline_date = datetime.fromisoformat(str(deadline)).date()
                days_left = (deadline_date - datetime.now().date()).days
                if days_left >= 14:
                    feasibility_score += 20
                elif days_left >= 7:
                    feasibility_score += 10
            except ValueError:
                return {"success": False, "error": "deadline 必须是 ISO 日期格式"}

        # 风险分析
        risks = []
        if profit_rate < 0.15:
            risks.append("利润率偏低 (<15%)")
        if profit_rate < 0:
            risks.append("项目亏损")
        if days_left is not None and days_left < 0:
            risks.append("项目截止日期已过")
        elif days_left is not None and days_left < 7:
            risks.append("交付周期少于7天")

        risk_level = "低"
        if profit_rate < 0 or (days_left is not None and days_left < 0):
            risk_level = "高"
        elif len(risks) > 0:
            risk_level = "中"

        # 生成建议
        recommendations = []
        if profit_rate < 0.15:
            recommendations.append("建议提高报价或降低成本")
        if profit_rate >= 0.20:
            recommendations.append("利润率良好,可以接单")
        if days_left is not None and days_left < 7:
            recommendations.append("建议延长交付周期或增加资源")
        if not recommendations:
            recommendations.append("项目指标处于可接受范围,建议持续监控成本和进度")

        result = {
            "success": True,
            "feasibility_score": min(feasibility_score, 100),
            "profit_rate": profit_rate,
            "risk_level": risk_level,
            "risk_analysis": {
                "risks": risks,
                "level": risk_level
            },
            "profit_analysis": {
                "quote_amount": quote_amount,
                "cost": cost,
                "profit": quote_amount - cost,
                "profit_rate": profit_rate
            },
            "recommendations": recommendations,
            "days_left": days_left,
        }

        historical_data = inputs.get("historical_data")
        if historical_data is not None:
            history_result = await self.mcp_client.call_tool("analyze_data", {
                "data_source": historical_data,
                "analysis_type": "summary",
            })
            if history_result.get("success"):
                result["historical_projects"] = history_result["analysis"]["rows"]

        return result


# ═══════════════════════════════════════════════════════════
# Skill注册表
# ═══════════════════════════════════════════════════════════

MCP_SKILLS = {
    "file_reader": FileReaderSkill,
    "file_search": FileSearchSkill,
    "data_analyzer": DataAnalyzerSkill,
    "trend_analyzer": TrendAnalyzerSkill,
    "project_evaluator": ProjectEvaluatorSkill,
}


def get_mcp_skill(skill_name: str, context: Dict[str, Any]) -> Optional[BaseSkill]:
    """获取MCP Skill实例"""
    skill_class = MCP_SKILLS.get(skill_name)
    if skill_class:
        return skill_class(context)
    return None


def list_mcp_skills() -> List[Dict[str, Any]]:
    """列出所有MCP Skills"""
    return [
        {
            "name": skill_class.skill_name,
            "description": skill_class.description,
            "version": skill_class.version,
            "input_schema": skill_class.input_schema,
        }
        for skill_class in MCP_SKILLS.values()
    ]
