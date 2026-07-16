"""
Stdio MCP Client - 通过 stdio 协议连接 Skills Forge 的 MCP 服务器
=================================================================

使用官方 mcp Python SDK 的 ``stdio_client`` 拉起用户提供的
``@skills-forge/mcp-server``（npx 启动），走真正的 MCP 协议
（initialize -> list_tools -> call_tool），替代旧版仅支持自定义
HTTP REST 的 ``MCPClient``。

与 MCPClient 的差异：
- MCPClient 假定 Skills Forge 暴露 ``GET /skills`` / ``POST /skills/{name}/execute``
  这类 REST 端点（实测 404 / 返回 HTML，协议不匹配）。
- 本客户端走标准 MCP 协议，由 npx 包内部负责与云端握手，最稳。

设计取舍（v1）：
- 惰性连接：构造不发起任何网络/进程操作，首次 list/call 才 spawn 子进程。
- 每次 call_skill 重新 spawn 子进程（npx 包缓存后启动约 2-5s）。
  list_skills 结果进程内缓存，避免重复拉起。
  后续 v2 可改为常驻 session（后台线程持有 ClientSession）以降低延迟。
- 嵌套 event loop 防护：通过 ``_run`` 在线程内运行 ``asyncio.run``，
  兼容 Streamlit 同步回调与异步调用链。
"""
import asyncio
import logging
import os
import shutil
from typing import Any, Dict, List, Optional

try:
    from artpm_agent.utils.llm_client import is_valid_api_key
except Exception:  # pragma: no cover - 防御性兜底
    def is_valid_api_key(key):
        return bool(key) and isinstance(key, str) and key.startswith("sk_")

logger = logging.getLogger(__name__)

# stdio 启动配置：与用户提供的 mcpServers.skills-forge 完全一致
_STDIO_COMMAND = "npx"
_STDIO_ARGS = ["-y", "@skills-forge/mcp-server@latest"]


def _resolve_npx() -> str:
    """解析 npx 可执行文件；Windows 下 shutil.which 会返回 npx.cmd 完整路径。"""
    found = shutil.which("npx")
    return found or _STDIO_COMMAND


def _run(coro):
    """
    在可能存在 running event loop 的上下文中安全运行协程。
    若当前已在 loop 内，则派生一个临时线程执行 asyncio.run，避免
    'asyncio.run() cannot be called from a running event loop'。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


class StdioMCPClient:
    """通过 stdio 协议连接 Skills Forge MCP 服务器（真正的 MCP 实现）。"""

    def __init__(self, api_key: Optional[str] = None, enabled: Optional[bool] = None):
        self.api_key = api_key or os.getenv("SKILLS_FORGE_KEY")
        self.enabled = (
            enabled
            if enabled is not None
            else (os.getenv("MCP_ENABLED", "false").lower() == "true")
        ) and is_valid_api_key(self.api_key)

        # 分类诊断（与 MCPClient 同语义）
        self.last_error: Optional[str] = None
        self.last_error_category: Optional[str] = None  # network|http|auth|parse|url|config|unknown

        self._tools_cache: Optional[List[Dict[str, Any]]] = None
        self._npx = _resolve_npx()

        if not self.enabled:
            reasons = []
            if os.getenv("MCP_ENABLED", "false").lower() != "true":
                reasons.append("未启用")
            if not is_valid_api_key(self.api_key):
                reasons.append("API Key 无效或为空")
            if reasons:
                self.last_error = "、".join(reasons)
                self.last_error_category = "config"
            logger.info("[StdioMCP] disabled: %s", self.last_error or "未知原因")

    # ──────────────────────────────────────────────
    # 连接参数构建
    # ──────────────────────────────────────────────
    def _build_params(self):
        from mcp import StdioServerParameters

        env = {**os.environ, "SKILLS_FORGE_KEY": self.api_key}
        return StdioServerParameters(command=self._npx, args=list(_STDIO_ARGS), env=env)

    # ──────────────────────────────────────────────
    # 协议层（基于 mcp SDK）
    # ──────────────────────────────────────────────
    async def _fetch_tools(self) -> List[Dict[str, Any]]:
        """拉起子进程，initialize + list_tools，返回技能字典列表（含 name/description）。"""
        if self._tools_cache is not None:
            return self._tools_cache

        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        skills: List[Dict[str, Any]] = []
        params = self._build_params()
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), timeout=150)
                    result = await session.list_tools()
                    tools = getattr(result, "tools", result) or []
                    for t in tools:
                        skills.append(
                            {
                                "name": t.name,
                                "description": getattr(t, "description", "") or "",
                                "input_schema": getattr(t, "inputSchema", None),
                            }
                        )
            self._tools_cache = skills
            self.last_error = None
            self.last_error_category = None
            logger.info("[StdioMCP] Loaded %d skills via stdio", len(skills))
            return skills
        except Exception as e:  # 连接失败：标记并上抛，由调用方决定降级
            self.last_error = self._classify_error(e)
            self.last_error_category = getattr(self, "_last_cat", "unknown")
            self.enabled = False
            logger.warning("[StdioMCP] fetch failed: %s", self.last_error, exc_info=True)
            raise

    async def _call_once(self, skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """单次调用：拉起子进程 -> initialize -> call_tool -> 规范化结果。"""
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        sp = self._build_params()
        async with stdio_client(sp) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=150)
                result = await asyncio.wait_for(
                    session.call_tool(skill_name, params or {}), timeout=60
                )
                return self._normalize_result(result, skill_name)

    def _classify_error(self, exc: Exception) -> str:
        msg = str(exc)
        low = msg.lower()
        if "timeout" in low or "TimeoutError" in type(exc).__name__:
            self._last_cat = "network"
            return (
                "连接 Skills Forge MCP 服务器超时（npx 拉起子进程较慢，或 npm 网络受限）"
            )
        if "enoent" in low or "not found" in low or "file not found" in low:
            self._last_cat = "config"
            return "未找到 npx 可执行文件，请确认 Node.js / npm 已安装并在 PATH 中"
        if "403" in msg or "401" in msg or "unauthorized" in low or "forbidden" in low:
            self._last_cat = "auth"
            return "Skills Forge API Key 无效或已过期"
        self._last_cat = "unknown"
        return f"stdio MCP 连接失败: {msg[:200]}"

    @staticmethod
    def _normalize_result(result, skill_name: str) -> Dict[str, Any]:
        """将 mcp CallToolResult 规整为统一 {success, result, data} 结构。"""
        texts: List[str] = []
        for c in getattr(result, "content", []) or []:
            txt = getattr(c, "text", None)
            if txt is not None:
                texts.append(txt)
        payload = "\n".join(texts) if texts else str(result)
        return {
            "success": not getattr(result, "isError", False),
            "result": payload,
            "data": payload,
            "skill": skill_name,
        }

    # ──────────────────────────────────────────────
    # 对外接口（与 MCPClient 对齐，供 UnifiedMCPClient / MCPSkillsAdapter 直接复用）
    # ──────────────────────────────────────────────
    async def call_skill(self, skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """调用技能（每次重新拉起子进程）。"""
        if not self.enabled:
            return {"success": False, "error": "MCP (stdio) is not enabled"}
        try:
            return await self._call_once(skill_name, params)
        except Exception as e:
            return {"success": False, "error": f"MCP (stdio) call failed: {e}"}

    def list_skills(self) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            return _run(self._fetch_tools())
        except Exception:
            return []

    def get_skill_info(self, skill_name: str) -> Optional[Dict[str, Any]]:
        for s in self.list_skills():
            if s["name"] == skill_name:
                return s
        return None

    def is_enabled(self) -> bool:
        return self.enabled

    def get_skills_summary(self) -> str:
        if not self.enabled:
            return ""
        skills = self.list_skills()
        if not skills:
            return ""
        lines = ["可用的 MCP 技能 (stdio):"]
        for s in skills:
            lines.append(f"  • {s['name']}: {s['description']}")
        return "\n".join(lines)

    def ping(self) -> tuple[bool, str]:
        """轻量连通性测试：拉一次工具清单并计数。"""
        if not self.enabled:
            return False, self.last_error or "未启用"
        try:
            tools = _run(self._fetch_tools())
            return True, f"Skills Forge (stdio) 已连接，{len(tools)} 个技能可用"
        except Exception as e:
            return False, self.last_error or str(e)
