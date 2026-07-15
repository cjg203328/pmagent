"""
MCP Skills Integration
将 MCP 技能集成到 Agent 工作流
"""
from typing import Dict, Any, List
from artpm_agent.core.mcp_client import get_mcp_client


class MCPSkillsAdapter:
    """MCP 技能适配器 - 将 MCP 技能适配为 Agent 可用的格式"""

    def __init__(self):
        self.mcp_client = get_mcp_client()

    def get_available_skills(self) -> List[str]:
        """获取可用的 MCP 技能名称列表"""
        if not self.mcp_client.enabled:
            return []
        return [skill["name"] for skill in self.mcp_client.list_skills()]

    async def execute_skill(self, skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行 MCP 技能

        Args:
            skill_name: 技能名称
            params: 参数

        Returns:
            执行结果
        """
        return await self.mcp_client.call_skill(skill_name, params)

    def enhance_prompt_with_skills(self, user_input: str) -> str:
        """
        在提示词中添加可用的 MCP 技能信息

        Args:
            user_input: 用户输入

        Returns:
            增强后的提示词
        """
        if not self.mcp_client.enabled:
            return user_input

        skills = self.mcp_client.list_skills()
        if not skills:
            return user_input

        skill_list = "\n".join([f"- {s['name']}: {s['description']}" for s in skills])

        enhanced = f"""
用户输入: {user_input}

可用的 MCP 技能:
{skill_list}

如果需要使用这些技能，请在回复中明确说明要调用哪个技能以及参数。
"""
        return enhanced

    def parse_skill_call_from_response(self, response: str) -> List[Dict[str, Any]]:
        """
        从 LLM 响应中解析技能调用请求

        Args:
            response: LLM 响应

        Returns:
            技能调用列表
        """
        # 简单的解析逻辑，实际应该更复杂
        # TODO: 实现更智能的解析
        skill_calls = []

        for skill in self.mcp_client.list_skills():
            if skill["name"] in response.lower():
                skill_calls.append({
                    "skill": skill["name"],
                    "params": {}
                })

        return skill_calls


# 全局适配器实例
_mcp_adapter = None


def get_mcp_adapter() -> MCPSkillsAdapter:
    """获取全局 MCP 适配器实例"""
    global _mcp_adapter
    if _mcp_adapter is None:
        _mcp_adapter = MCPSkillsAdapter()
    return _mcp_adapter
