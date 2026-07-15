"""
统一 MCP 客户端 (Unified MCP Client)
=====================================

将两套 MCP 后端聚合成单一入口，调用方无需关心后端差异：

1. 本地工具后端 (EnhancedMCPClient)
   - 连接 Claude Code 能力：read_file / search_files / search_content /
     analyze_data / execute_command
   - 默认始终可用（workspace 内操作）

2. 远程技能后端 (MCPClient / Skills Forge)
   - 通过 HTTP API 调用远程技能市场
   - 仅当 MCP_ENABLED=true 且配置了有效 API Key/URL 时启用

对外暴露与两个后端兼容的接口：
- list_skills()    聚合两个后端的技能/工具清单
- call_skill(name, params)  按名称路由到对应后端（本地优先）
- enabled         任一后端可用即为 True
- get_skills_summary()  生成提示词可用的技能摘要

历史入口 ``get_enhanced_mcp_client`` 仍保留在 core.mcp_client_enhanced，
旧代码（含未提交的 WIP agent.py）无需改动即可继续工作。
"""
import os
from typing import Any, Dict, List, Optional

from pathlib import Path


class UnifiedMCPClient:
    """统一 MCP 客户端：聚合本地工具与远程技能后端。"""

    def __init__(self, workspace_path: str = None, api_key: str = None):
        self._workspace_path = workspace_path
        self._api_key = api_key
        self._enhanced: Optional[Any] = None
        self._remote: Optional[Any] = None
        self._remote_failed = False

    # ──────────────────────────────────────────────
    # 后端惰性加载（避免无谓的 import / 网络请求）
    # ──────────────────────────────────────────────

    def _get_enhanced(self):
        if self._enhanced is None:
            from artpm_agent.core.mcp_client_enhanced import EnhancedMCPClient

            self._enhanced = EnhancedMCPClient(self._workspace_path)
        return self._enhanced

    def _get_remote(self):
        if self._remote is not None or self._remote_failed:
            return self._remote
        # 远程后端默认关闭，避免未配置时产生 import / 网络开销。
        if os.getenv("MCP_ENABLED", "false").lower() != "true":
            self._remote_failed = True
            return None
        try:
            from artpm_agent.core.mcp_client import MCPClient

            self._remote = MCPClient(self._api_key)
        except Exception:
            self._remote = None
            self._remote_failed = True
        return self._remote

    # ──────────────────────────────────────────────
    # 统一对外接口
    # ──────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """任一后端可用即视为启用（本地工具默认可用）。"""
        try:
            enhanced = self._get_enhanced()
            if enhanced is not None and enhanced.is_enabled():
                return True
        except Exception:
            pass
        remote = self._get_remote()
        return bool(remote is not None and remote.is_enabled())

    def list_skills(self) -> List[Dict[str, Any]]:
        """聚合两个后端的技能/工具清单。"""
        result: List[Dict[str, Any]] = []
        try:
            enhanced = self._get_enhanced()
            if enhanced is not None and enhanced.is_enabled():
                result.extend(enhanced.list_skills())
        except Exception:
            pass
        remote = self._get_remote()
        if remote is not None and remote.is_enabled():
            result.extend(remote.list_skills())
        return result

    def list_tools(self) -> List[Dict[str, Any]]:
        """本地工具的别名（与 EnhancedMCPClient 保持一致）。"""
        try:
            enhanced = self._get_enhanced()
            if enhanced is not None and enhanced.is_enabled():
                return enhanced.list_tools()
        except Exception:
            pass
        return []

    async def call_skill(self, skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        按名称路由到对应后端：本地工具优先，其次远程技能。

        Args:
            skill_name: 工具/技能名称
            params: 调用参数

        Returns:
            {"success": bool, ...}
        """
        # 本地工具优先
        try:
            enhanced = self._get_enhanced()
            if enhanced is not None:
                local = {t["name"]: t for t in enhanced.list_tools()}
                if skill_name in local:
                    return await enhanced.call_tool(skill_name, params)
        except Exception as exc:  # pragma: no cover - 防御性兜底
            return {"success": False, "error": f"Local tool call failed: {exc}"}

        # 远程技能
        remote = self._get_remote()
        if remote is not None and remote.is_enabled():
            return await remote.call_skill(skill_name, params)

        return {
            "success": False,
            "error": f"Skill or tool '{skill_name}' not found in any MCP backend",
        }

    def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """同步调用本地工具（供非 async 上下文使用）。"""
        enhanced = self._get_enhanced()
        if enhanced is None or not enhanced.is_enabled():
            return {"success": False, "error": "Local MCP tools are not available"}
        import asyncio

        return asyncio.run(self.call_skill(tool_name, params))

    def get_skills_summary(self) -> str:
        """生成提示词可用的技能摘要（聚合两个后端）。"""
        lines: List[str] = []
        try:
            enhanced = self._get_enhanced()
            if enhanced is not None and enhanced.is_enabled():
                summary = enhanced.get_skills_summary()
                if summary:
                    lines.append(summary)
        except Exception:
            pass
        remote = self._get_remote()
        if remote is not None and remote.is_enabled():
            summary = remote.get_skills_summary()
            if summary:
                lines.append(summary)
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════

_unified_mcp_client: Optional[UnifiedMCPClient] = None


def get_unified_mcp_client(
    workspace_path: str = None, api_key: str = None
) -> UnifiedMCPClient:
    """获取统一 MCP 客户端单例。"""
    global _unified_mcp_client
    if workspace_path:
        requested = Path(workspace_path).resolve()
        current = (
            Path(_unified_mcp_client._workspace_path).resolve()
            if _unified_mcp_client is not None
            and _unified_mcp_client._workspace_path
            else None
        )
        if current != requested:
            _unified_mcp_client = UnifiedMCPClient(workspace_path, api_key)
    elif _unified_mcp_client is None:
        _unified_mcp_client = UnifiedMCPClient(workspace_path, api_key)
    return _unified_mcp_client
