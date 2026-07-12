"""
MCP (Model Context Protocol) Client - 简化版
直接通过 HTTP API 连接到 Skills Forge
"""
import os
import requests
from typing import Dict, Any, List, Optional
from utils.llm_client import is_valid_api_key


class MCPClient:
    """MCP 客户端 - 连接 Skills Forge"""

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化 MCP 客户端

        Args:
            api_key: Skills Forge API Key
        """
        self.api_key = api_key or os.getenv("SKILLS_FORGE_KEY")
        self.base_url = os.getenv("SKILLS_FORGE_URL", "").rstrip("/")
        self.enabled = (
            os.getenv("MCP_ENABLED", "false").lower() == "true"
            and is_valid_api_key(self.api_key)
            and bool(self.base_url)
        )
        self.available_skills = []

        if self.enabled:
            print("[MCP] Initializing Skills Forge connection...")
            self._fetch_available_skills()
        else:
            print("[MCP] MCP is disabled or API key missing")

    def _fetch_available_skills(self):
        """获取可用技能列表"""
        try:
            response = requests.get(
                f"{self.base_url}/skills",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            skills = payload.get("skills", payload) if isinstance(payload, dict) else payload
            if not isinstance(skills, list):
                raise ValueError("Skills endpoint did not return a list")
            self.available_skills = skills
            print(f"[MCP] Loaded {len(self.available_skills)} skills")

        except Exception as e:
            print(f"[MCP] Failed to fetch skills: {e}")
            self.enabled = False

    async def call_skill(self, skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用 MCP 技能

        Args:
            skill_name: 技能名称
            params: 参数

        Returns:
            执行结果
        """
        if not self.enabled:
            return {
                "success": False,
                "error": "MCP is not enabled"
            }

        try:
            skill_info = self.get_skill_info(skill_name)
            if not skill_info:
                return {
                    "success": False,
                    "error": f"Skill '{skill_name}' not found"
                }

            response = requests.post(
                f"{self.base_url}/skills/{skill_name}/execute",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"parameters": params},
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            return result if isinstance(result, dict) else {"success": True, "data": result}

        except requests.RequestException as e:
            return {
                "success": False,
                "error": f"MCP request failed: {e}"
            }
        except (TypeError, ValueError) as e:
            return {
                "success": False,
                "error": f"Invalid MCP response: {e}"
            }

    def list_skills(self) -> List[Dict[str, Any]]:
        """
        列出所有可用技能

        Returns:
            技能列表
        """
        return self.available_skills

    def get_skill_info(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """
        获取技能信息

        Args:
            skill_name: 技能名称

        Returns:
            技能详情
        """
        for skill in self.available_skills:
            if skill["name"] == skill_name:
                return skill
        return None

    def is_enabled(self) -> bool:
        """检查 MCP 是否启用"""
        return self.enabled

    def get_skills_summary(self) -> str:
        """获取技能摘要（用于提示词）"""
        if not self.enabled:
            return ""

        lines = ["可用的 MCP 技能:"]
        for skill in self.available_skills:
            lines.append(f"  • {skill['name']}: {skill['description']}")

        return "\n".join(lines)


# 全局 MCP 客户端实例
_mcp_client = None


def get_mcp_client() -> MCPClient:
    """获取全局 MCP 客户端实例"""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client
