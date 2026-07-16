"""
MCP (Model Context Protocol) Client - 增强诊断版
直接通过 HTTP API 连接到 Skills Forge
"""
import json
import logging
import os

import requests
from typing import Dict, Any, List, Optional

from artpm_agent.utils.llm_client import is_valid_api_key

logger = logging.getLogger(__name__)


class MCPClient:
    """MCP 客户端 - 连接 Skills Forge（带分类错误诊断）"""

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
        self.available_skills: List[Dict[str, Any]] = []
        # 分类诊断信息，供 UI 展示具体原因
        self.last_error: Optional[str] = None
        self.last_error_category: Optional[str] = None  # network | http | auth | parse | url

        if self.enabled:
            logger.info("[MCP] Initializing Skills Forge connection to %s ...", self.base_url)
            self._fetch_available_skills()
        else:
            reasons = []
            if os.getenv("MCP_ENABLED", "false").lower() != "true":
                reasons.append("未启用")
            if not is_valid_api_key(self.api_key):
                reasons.append("API Key 无效或为空")
            if not self.base_url:
                reasons.append("URL 为空")
            if reasons:
                self.last_error = "、".join(reasons)
                self.last_error_category = "config"
            logger.info("[MCP] MCP disabled: %s", self.last_error or "未知原因")

    def _classify_error(self, response=None, exc=None) -> str:
        """将异常/HTTP响应翻译成用户可读的诊断信息，并记录类别。"""
        if isinstance(exc, requests.exceptions.ConnectionError):
            self.last_error_category = "network"
            return (
                f"无法连接到 {self.base_url} —— 请检查地址是否正确、"
                f"服务是否在线、网络是否通畅"
            )

        if isinstance(exc, requests.exceptions.Timeout):
            self.last_error_category = "network"
            return f"连接 {self.base_url} 超时（>10s），服务可能繁忙或不可达"

        if isinstance(exc, requests.exceptions.SSLError):
            self.last_error_category = "network"
            return f"{self.base_url} 的 SSL/TLS 证书验证失败"

        if isinstance(exc, requests.RequestException):
            self.last_error_category = "network"
            return f"网络请求失败: {exc}"

        if response is not None:
            status = response.status_code
            ct = response.headers.get("content-type", "")

            if status == 401 or status == 403:
                self.last_error_category = "auth"
                return f"API Key 无效或已过期（HTTP {status}）"

            if status == 404:
                self.last_error_category = "http"
                return (
                    f"接口不存在（HTTP 404）：{self.base_url}/skills —— "
                    f"请确认 URL 是否为 API 地址（通常含 /api），而非网站前台地址"
                )

            if status >= 400:
                body_preview = response.text[:200].replace("\n", " ")
                self.last_error_category = "http"
                return f"服务器返回 HTTP {status}: {body_preview}"

            # 2xx 但 content-type 不是 JSON → 返回了 HTML 页面
            if "html" in ct.lower():
                self.last_error_category = "url"
                return (
                    f"{self.base_url} 返回的是网页内容（HTML），不是 API 接口。"
                    f"请检查 Skills Forge URL 是否正确 —— 通常应为 "
                    f"https://api.skillsforge.xyz 这类带 /api 的地址，"
                    f"而不是网站前台地址。"
                )

        if isinstance(exc, json.JSONDecodeError):
            self.last_error_category = "parse"
            return (
                f"{self.base_url}/skills 返回了非 JSON 内容 —— "
                f"该地址可能是网站前台而非 API 服务端。"
                f"请确认 URL 是否正确（如 https://api.skillsforge.xyz）。"
            )

        if isinstance(exc, ValueError):
            self.last_error_category = "parse"
            return f"数据解析失败: {exc}"

        # 兜底
        self.last_error_category = "unknown"
        return str(exc)

    def _fetch_available_skills(self):
        """获取可用技能列表（带分类诊断）。"""
        try:
            response = requests.get(
                f"{self.base_url}/skills",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            response.raise_for_status()

            # 先校验 content-type，避免对 HTML 做 .json()
            ct = response.headers.get("content-type", "")
            if "json" not in ct and "html" in ct.lower():
                self.last_error = self._classify_error(response=response)
                self.enabled = False
                logger.warning("[MCP] %s", self.last_error)
                return

            payload = response.json()
            skills = payload.get("skills", payload) if isinstance(payload, dict) else payload
            if not isinstance(skills, list):
                raise ValueError("Skills endpoint did not return a list")
            self.available_skills = skills
            self.last_error = None
            self.last_error_category = None
            logger.info("[MCP] Loaded %d skills from %s", len(self.available_skills), self.base_url)

        except Exception as e:
            self.last_error = self._classify_error(response=locals().get("response"), exc=e)
            self.enabled = False
            logger.warning("[MCP] Failed to fetch skills [%s]: %s",
                           self.last_error_category, self.last_error, exc_info=True)

    def ping(self) -> tuple[bool, str]:
        """
        轻量级连通性测试（不拉全量技能列表）。
        
        Returns:
            (success, message) — success=True 表示可达且返回合法 JSON
        """
        if not self.base_url:
            return False, "URL 未配置"
        try:
            resp = requests.get(
                f"{self.base_url}/skills",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=8,
            )
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "json" not in ct and "html" in ct.lower():
                return False, self._classify_error(response=resp)
            data = resp.json()
            count = len(data.get("skills", data)) if isinstance(data, dict) else len(data) if isinstance(data, list) else "?"
            return True, f"连接正常，{count} 个技能可用"
        except Exception as e:
            return False, self._classify_error(response=locals().get("resp"), exc=e)

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
_mcp_client: Optional[MCPClient] = None


def get_mcp_client() -> MCPClient:
    """获取全局 MCP 客户端实例"""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client


def reset_mcp_client() -> None:
    """清除全局单例，下次 get_mcp_client() 会重新初始化（读取最新环境变量）。"""
    global _mcp_client
    _mcp_client = None
