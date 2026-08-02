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
import threading
import time
from typing import Any, Dict, List, Optional

from pathlib import Path

from artpm_agent.core.mcp_client_stdio import SKILLS_FORGE_ALLOWED_TOOLS


class UnifiedMCPClient:
    """统一 MCP 客户端：聚合本地工具与远程技能后端。"""

    def __init__(self, workspace_path: str = None, api_key: str = None):
        self._workspace_path = workspace_path
        self._api_key = api_key
        self._enhanced: Optional[Any] = None
        self._remote: Optional[Any] = None
        self._remote_failed = False
        self._remote_error: Optional[str] = None
        self._remote_retry_at = 0.0
        self._backend_lock = threading.RLock()

    # ──────────────────────────────────────────────
    # 后端惰性加载（避免无谓的 import / 网络请求）
    # ──────────────────────────────────────────────

    def _get_enhanced(self):
        with self._backend_lock:
            if self._enhanced is None:
                from artpm_agent.core.mcp_client_enhanced import (
                    get_enhanced_mcp_client,
                )

                self._enhanced = get_enhanced_mcp_client(self._workspace_path)
            return self._enhanced

    def _get_remote(self):
        with self._backend_lock:
            if self._remote is not None:
                return self._remote
            # 远程后端默认关闭，避免未配置时产生 import / 网络开销。
            if os.getenv("MCP_ENABLED", "false").lower() != "true":
                self._remote_failed = True
                self._remote_error = "Skills Forge 未启用（开关关闭）"
                return None
            # Import/constructor failures are retried after a short cooldown;
            # a single transient npm/import error must not create a fake
            # permanently-disabled entry in the settings page.
            now = time.monotonic()
            if self._remote_failed and now < self._remote_retry_at:
                return None
            transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
            try:
                if transport == "stdio":
                    from artpm_agent.core.mcp_client_stdio import StdioMCPClient

                    self._remote = StdioMCPClient(self._api_key)
                elif transport == "http":
                    from artpm_agent.core.mcp_client import MCPClient

                    self._remote = MCPClient(self._api_key)
                else:
                    raise ValueError(f"Unsupported MCP_TRANSPORT: {transport}")
                self._remote_failed = False
                self._remote_error = None
            except Exception as error:
                self._remote = None
                self._remote_failed = True
                self._remote_retry_at = now + 5.0
                self._remote_error = f"MCP 后端初始化失败: {error}"
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
        try:
            return bool(remote is not None and remote.is_enabled())
        except Exception as error:
            self._remote_error = f"MCP 后端状态检查失败: {error}"
            return False

    # ── 诊断属性：透传远程后端的分类错误信息 ─────────

    @property
    def last_error(self) -> Optional[str]:
        """远程后端最后一次错误的人类可读描述（本地工具无此概念）。"""
        remote = self._get_remote()
        if remote is not None:
            return getattr(remote, "last_error", None)
        return self._remote_error

    @property
    def last_error_category(self) -> Optional[str]:
        """远程后端错误类别: network | http | auth | parse | url | config | unknown"""
        remote = self._get_remote()
        if remote is not None:
            return getattr(remote, "last_error_category", None)
        return None

    def ping(self) -> tuple[bool, str]:
        """轻量连通性测试：先测远程，失败则返回原因；本地工具始终可用。"""
        remote = self._get_remote()
        if remote is not None:
            try:
                ok, msg = (
                    remote.ping()
                    if hasattr(remote, "ping")
                    else (remote.is_enabled(), "")
                )
                if not ok or not remote.is_enabled():
                    return False, msg or getattr(remote, "last_error", None) or "Skills Forge 不可用"
                # ping() is a connectivity probe. Counting skills here forced
                # settings_page() to perform the same remote list call twice.
                return True, msg or "Skills Forge 已连接"
            except Exception as error:
                self._remote_error = f"MCP 健康检查失败: {error}"
                return False, self._remote_error
        # 无远程后端时检查是否因配置缺失而未加载
        if os.getenv("MCP_ENABLED", "false").lower() != "true":
            return False, "Skills Forge 未启用（开关关闭）"
        if not os.getenv("SKILLS_FORGE_KEY"):
            return False, "Skills Forge API Key 未配置"
        if (
            os.getenv("MCP_TRANSPORT", "stdio").lower() == "http"
            and not os.getenv("SKILLS_FORGE_URL")
        ):
            return False, "Skills Forge URL 未配置"
        return False, self._remote_error or "Skills Forge 远程后端初始化失败"

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
        try:
            if remote is not None and remote.is_enabled():
                result.extend(
                    skill
                    for skill in remote.list_skills()
                    if skill.get("name") in SKILLS_FORGE_ALLOWED_TOOLS
                )
        except Exception as error:
            self._remote_error = f"MCP 工具列表读取失败: {error}"
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

    async def call_skill(
        self, skill_name: str, params: Dict[str, Any], force: bool = False
    ) -> Dict[str, Any]:
        """
        按名称路由到对应后端：本地工具优先，其次远程技能。

        Args:
            skill_name: 工具/技能名称
            params: 调用参数
            force: 忽略市场技能缓存（透传到远程后端）

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
        remote_available = False
        if remote is not None:
            try:
                remote_available = bool(remote.is_enabled())
                if not remote_available and hasattr(remote, "ping"):
                    remote_available = bool(remote.ping()[0])
            except Exception as error:
                self._remote_error = f"MCP 后端恢复失败: {error}"
        if remote is not None and remote_available:
            if skill_name not in SKILLS_FORGE_ALLOWED_TOOLS:
                return {
                    "success": False,
                    "code": "MCP_TOOL_NOT_ALLOWED",
                    "error": f"Skills Forge tool is not allowed: {skill_name}",
                    "retryable": False,
                }
            return await remote.call_skill(skill_name, params, force=force)

        return {
            "success": False,
            "error": f"Skill or tool '{skill_name}' not found in any MCP backend",
        }

    async def list_market_skills(self, force: bool = False) -> List[Dict[str, Any]]:
        """拉取 Skills Forge 市场技能清单（远程后端，带 TTL 缓存）。"""
        remote = self._get_remote()
        try:
            if remote is not None and remote.is_enabled():
                if hasattr(remote, "list_market_skills"):
                    return await remote.list_market_skills(force=force)
        except Exception as error:
            self._remote_error = f"Skills Forge 市场列表读取失败: {error}"
        return []

    def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """同步调用本地工具（供非 async 上下文使用）。"""
        enhanced = self._get_enhanced()
        if enhanced is None or not enhanced.is_enabled():
            return {"success": False, "error": "Local MCP tools are not available"}
        import asyncio

        awaitable = self.call_skill(tool_name, params)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        result = []
        errors = []

        def runner() -> None:
            try:
                result.append(asyncio.run(awaitable))
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(
            target=runner,
            name="mcp-sync-bridge",
            daemon=True,
        )
        worker.start()
        worker.join()
        if errors:
            raise errors[0]
        return result[0]

    def close(self) -> None:
        """关闭所有后端持有的长连接（stdio 后台线程 / npx 子进程）。幂等。"""
        with self._backend_lock:
            remote = self._remote
            self._remote = None
            self._remote_failed = False
            self._remote_error = None
            self._remote_retry_at = 0.0
            self._enhanced = None
        if remote is not None and hasattr(remote, "close"):
            try:
                remote.close()
            except Exception:  # pragma: no cover - 防御性兜底
                pass

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
        try:
            if remote is not None and remote.is_enabled():
                skills = [
                    skill
                    for skill in remote.list_skills()
                    if skill.get("name") in SKILLS_FORGE_ALLOWED_TOOLS
                ]
                if skills:
                    lines.append(
                        "可用的远程 MCP 技能:\n"
                        + "\n".join(
                            f"  • {skill['name']}: {skill.get('description', '')}"
                            for skill in skills
                        )
                    )
        except Exception as error:
            self._remote_error = f"MCP 技能摘要读取失败: {error}"
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════

_unified_mcp_client: Optional[UnifiedMCPClient] = None
_unified_mcp_lock = threading.RLock()


def get_unified_mcp_client(
    workspace_path: str = None, api_key: str = None
) -> UnifiedMCPClient:
    """获取统一 MCP 客户端单例。"""
    global _unified_mcp_client
    old = None
    with _unified_mcp_lock:
        needs_new = _unified_mcp_client is None
        if not needs_new and workspace_path:
            requested = Path(workspace_path).resolve()
            current = (
                Path(_unified_mcp_client._workspace_path).resolve()
                if _unified_mcp_client._workspace_path
                else None
            )
            needs_new = current != requested
        if not needs_new and api_key is not None:
            needs_new = api_key != _unified_mcp_client._api_key
        if needs_new:
            old = _unified_mcp_client
            _unified_mcp_client = UnifiedMCPClient(workspace_path, api_key)
        client = _unified_mcp_client
    if old is not None and old is not client:
        old.close()
    return client


def reset_unified_mcp_client() -> None:
    """清除统一客户端全局单例，下次 get_unified_mcp_client() 会重新初始化（读取最新环境变量）。"""
    global _unified_mcp_client
    with _unified_mcp_lock:
        old = _unified_mcp_client
        _unified_mcp_client = None
    if old is not None:
        try:
            old.close()
        except Exception:  # pragma: no cover - 防御性兜底
            pass
