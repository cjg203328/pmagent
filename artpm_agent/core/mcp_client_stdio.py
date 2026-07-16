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

设计取舍（v2 常驻 session）：
- 惰性连接：构造不发起任何网络/进程操作，首次 list/call 才在后台线程
  拉起 npx 子进程并建立 MCP ClientSession。
- **常驻 session**：连接建立后由一个后台守护线程持有独立的 asyncio 事件循环
  与 ClientSession，后续所有 list/call 直接复用该连接（通过
  ``run_coroutine_threadsafe`` 投递到后台 loop），延迟从 2-5s 降到毫秒级。
- **断线自动重连**：若调用过程中会话失效（如云端断流），自动重建后台连接
  并重试一次，对调用方透明。
- 优雅关闭：``close()`` / ``reset_*`` 会干净地终止后台线程与 npx 子进程，
  并注册 ``atexit`` 兜底，避免进程退出时残留孤儿进程。
- 线程安全：连接建立/重建受锁保护，调用经后台 loop 串行化，可安全被多个
  Streamlit 会话并发调用。
"""
import atexit
import asyncio
import json
import logging
import os
import shutil
import threading
import time
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

# 连接建立的硬超时（首次 npx 拉包可能偏慢，但包缓存后通常几秒）
_CONNECT_TIMEOUT = 180
# 单次工具调用的超时
_CALL_TIMEOUT = 60


def _resolve_npx() -> str:
    """解析 npx 可执行文件；Windows 下 shutil.which 会返回 npx.cmd 完整路径。"""
    found = shutil.which("npx")
    return found or _STDIO_COMMAND


class StdioMCPClient:
    """通过 stdio 协议连接 Skills Forge MCP 服务器（真正的 MCP 实现，常驻 session）。"""

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

        # ── 市场技能列表缓存（list_skills 这个 MCP 工具会去云端拉 88 个技能，
        #    单次约 2.4s；按 TTL 缓存，命中后零网络开销）──
        self._market_cache: Optional[Dict[str, Any]] = None
        self._market_cache_ts: float = 0.0
        self._market_cache_lock = threading.Lock()
        try:
            self._market_cache_ttl = int(os.getenv("MCP_SKILLS_CACHE_TTL", "300") or "300")
        except ValueError:
            self._market_cache_ttl = 300
        # 上次 list_market_skills 是否命中本地缓存（True=0s 云端开销；False=刚拉云端）
        self.last_market_cache_hit: Optional[bool] = None

        # ── 常驻 session 状态 ──
        self._lock = threading.Lock()          # 保护连接生命周期（thread / loop / 启停）
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session = None                    # mcp ClientSession，仅在后台 loop 线程内读写
        self._ready = threading.Event()         # 连接就绪（session 可用）
        self._shutdown = threading.Event()      # 请求关闭后台线程
        self._broken = False                    # 连接已失效，需重建
        self._connect_error: Optional[str] = None

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
        else:
            # 进程退出时兜底关闭，避免遗留 npx 孤儿进程
            atexit.register(self.close)

    # ──────────────────────────────────────────────
    # 连接参数构建
    # ──────────────────────────────────────────────
    def _build_params(self):
        from mcp import StdioServerParameters

        env = {**os.environ, "SKILLS_FORGE_KEY": self.api_key}
        return StdioServerParameters(command=self._npx, args=list(_STDIO_ARGS), env=env)

    # ──────────────────────────────────────────────
    # 后台线程：持有长期存活的事件循环与 MCP session
    # ──────────────────────────────────────────────
    def _background_main(self) -> None:
        """后台线程入口：新建 event loop，建立并持有 MCP 连接直至收到关闭信号。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
        try:
            loop.run_until_complete(self._keep_alive())
        except Exception:  # pragma: no cover - 兜底，避免线程静默崩溃
            logger.exception("[StdioMCP] background loop unexpected error")
        finally:
            with self._lock:
                self._loop = None

    async def _keep_alive(self) -> None:
        """
        建立 MCP 连接并保持在后台 loop 中。
        连接成功后将 session 暴露给调用方；收到 _shutdown 信号后退出，
        上下文管理器自动终止 npx 子进程。
        """
        try:
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client

            async with stdio_client(self._build_params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), timeout=_CONNECT_TIMEOUT)
                    self._session = session
                    self._ready.set()
                    self._broken = False
                    self.last_error = None
                    self.last_error_category = None
                    logger.info("[StdioMCP] persistent session established")

                    # 保持 loop 存活，直到请求关闭
                    while not self._shutdown.is_set():
                        await asyncio.sleep(0.5)

                    self._session = None
                    logger.info("[StdioMCP] session closed by shutdown signal")
        except Exception as e:
            self._connect_error = self._classify_error(e)
            self._broken = True
            self.last_error = self._connect_error
            logger.warning("[StdioMCP] persistent connect failed: %s", self._connect_error, exc_info=True)
        finally:
            self._session = None
            self._ready.clear()

    # ──────────────────────────────────────────────
    # 连接生命周期管理
    # ──────────────────────────────────────────────
    def _ensure_connected(self, force: bool = False) -> bool:
        """
        确保后台连接已就绪。返回 True 表示可安全调用。

        - force=True 时无论当前状态如何都重建连接（用于断线重试）。
        - 首次或重建时会启动后台线程并阻塞等待连接就绪（受 _CONNECT_TIMEOUT 限制）。

        注意：``self._ready.wait()`` 必须在锁之外等待，否则会与后台线程
        （``_background_main`` 启动时需要获取同一把锁来写 ``self._loop``）形成
        死锁：主线程持锁等 _ready，后台线程拿不到锁永远无法 set _ready。
        """
        if not self.enabled or self._shutdown.is_set():
            return False
        if not force and self._ready.is_set() and not self._broken:
            return True

        # 仅用锁保护“判断是否需启动 + 启动线程”的临界区
        with self._lock:
            # 双重检查，避免并发重复启动
            if not force and self._ready.is_set() and not self._broken:
                return True
            if force:
                self._stop_background_locked()
            need_start = self._thread is None or not self._thread.is_alive()
            if need_start:
                self._broken = False
                self._connect_error = None
                self._shutdown.clear()
                self._ready.clear()
                self._thread = threading.Thread(target=self._background_main, daemon=True)
                self._thread.start()

        # 在锁外等待连接就绪（后台线程取得锁后才会 set _ready）
        if not self._ready.wait(timeout=_CONNECT_TIMEOUT):
            self._broken = True
            logger.warning("[StdioMCP] connect timed out after %ss", _CONNECT_TIMEOUT)
            return False
        return self._ready.is_set()

    def _stop_background_locked(self) -> None:
        """在持锁状态下停止后台线程并等待其退出（仅从 _ensure_connected 调用）。"""
        self._shutdown.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=10)
        self._session = None
        self._loop = None
        self._thread = None

    def close(self) -> None:
        """优雅关闭：终止后台线程与 npx 子进程。幂等，可安全重复调用。"""
        with self._lock:
            if self._thread is None and not self._ready.is_set():
                return
            self._shutdown.set()
            t = self._thread
            if t is not None and t.is_alive():
                t.join(timeout=10)
            self._session = None
            self._loop = None
            self._thread = None
            self._ready.clear()
        # 缓存与连接无关，但关闭后一并清空，避免下次连接误用旧数据
        with self._market_cache_lock:
            self._market_cache = None
            self._market_cache_ts = 0.0
            self.last_market_cache_hit = None

    # ──────────────────────────────────────────────
    # 跨线程调用投递
    # ──────────────────────────────────────────────
    def _submit_sync(self, coro, timeout: int):
        """从同步上下文把协程投递到后台 loop 并阻塞等待结果。"""
        loop = self._loop
        if loop is None:
            raise RuntimeError("stdio loop not started")
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    async def _submit_async(self, coro, timeout: int):
        """从异步上下文把协程投递到后台 loop 并 await 结果（不阻塞调用方 loop）。"""
        loop = self._loop
        if loop is None:
            raise RuntimeError("stdio loop not started")
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout)

    # ──────────────────────────────────────────────
    # 协议层协程（运行在后台 loop 线程内）
    # ──────────────────────────────────────────────
    async def _a_list_tools(self) -> List[Dict[str, Any]]:
        session = self._session
        if session is None:
            raise RuntimeError("stdio session not ready")
        result = await session.list_tools()
        tools = getattr(result, "tools", result) or []
        skills: List[Dict[str, Any]] = []
        for t in tools:
            skills.append(
                {
                    "name": t.name,
                    "description": getattr(t, "description", "") or "",
                    "input_schema": getattr(t, "inputSchema", None),
                }
            )
        return skills

    async def _a_call_tool(self, skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        session = self._session
        if session is None:
            raise RuntimeError("stdio session not ready")
        call = getattr(session, "call_tool", None) or session.invoke_tool
        result = await asyncio.wait_for(call(skill_name, params or {}), timeout=_CALL_TIMEOUT)
        return self._normalize_result(result, skill_name)

    def _classify_error(self, exc: Exception) -> str:
        msg = str(exc)
        low = msg.lower()
        if "timeout" in low or "TimeoutError" in type(exc).__name__:
            self.last_error_category = "network"
            return "连接 Skills Forge MCP 服务器超时（npx 拉起子进程较慢，或 npm 网络受限）"
        if "enoent" in low or "not found" in low or "file not found" in low:
            self.last_error_category = "config"
            return "未找到 npx 可执行文件，请确认 Node.js / npm 已安装并在 PATH 中"
        if "403" in msg or "401" in msg or "unauthorized" in low or "forbidden" in low:
            self.last_error_category = "auth"
            return "Skills Forge API Key 无效或已过期"
        self.last_error_category = "unknown"
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
    async def call_skill(
        self, skill_name: str, params: Dict[str, Any], force: bool = False
    ) -> Dict[str, Any]:
        """
        调用技能（复用常驻 session，仅首次/重连建连有开销）。

        force=True 时忽略市场技能缓存（用于显式刷新）。
        """
        if not self.enabled:
            return {"success": False, "error": "MCP (stdio) is not enabled"}

        # 市场技能列表缓存：list_skills 工具去云端拉 88 个技能，单次约 2.4s，
        # 命中 TTL 缓存直接返回，省掉云端拉取。
        if skill_name == "list_skills" and not force and self._market_cache_valid():
            self.last_market_cache_hit = True
            cached = self._get_market_cache() or {}
            return {
                "success": True,
                "result": cached.get("result", ""),
                "data": cached.get("data", ""),
                "skill": "list_skills",
                "cached": True,
            }

        try:
            result = await self._acall(skill_name, params)
        except Exception as e:
            return {"success": False, "error": f"MCP (stdio) call failed: {e}"}

        # 成功拉取市场技能后写入缓存（供后续 TTL 内复用）
        if skill_name == "list_skills" and result.get("success"):
            self.last_market_cache_hit = False
            self._set_market_cache(result)
        return result

    async def _acall(self, skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self._ensure_connected():
            raise RuntimeError(self._connect_error or "stdio connect failed")
        try:
            return await self._submit_async(self._a_call_tool(skill_name, params), _CALL_TIMEOUT)
        except Exception:
            # 会话可能已失效，重建并重试一次
            self._broken = True
            self._session = None
            if self._ensure_connected(force=True):
                return await self._submit_async(self._a_call_tool(skill_name, params), _CALL_TIMEOUT)
            raise

    def list_skills(self) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            skills = self._list_sync()
        except Exception:
            return self._tools_cache or []
        self._tools_cache = skills
        self.last_error = None
        self.last_error_category = None
        return skills

    def _list_sync(self) -> List[Dict[str, Any]]:
        if not self._ensure_connected():
            raise RuntimeError(self._connect_error or "stdio connect failed")
        try:
            return self._submit_sync(self._a_list_tools(), _CALL_TIMEOUT)
        except Exception:
            # 会话可能已失效，重建并重试一次
            self._broken = True
            self._session = None
            if self._ensure_connected(force=True):
                return self._submit_sync(self._a_list_tools(), _CALL_TIMEOUT)
            raise

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
        """轻量连通性测试：复用常驻连接拉一次工具清单并计数。"""
        if not self.enabled:
            return False, self.last_error or "未启用"
        try:
            tools = self.list_skills()
            return True, f"Skills Forge (stdio) 已连接，{len(tools)} 个技能可用"
        except Exception as e:
            return False, self.last_error or str(e)

    # ──────────────────────────────────────────────
    # 市场技能列表（list_skills 工具返回，TTL 缓存）
    # ──────────────────────────────────────────────
    def _market_cache_valid(self) -> bool:
        if self._market_cache is None:
            return False
        return (time.monotonic() - self._market_cache_ts) < self._market_cache_ttl

    def _set_market_cache(self, value: Dict[str, Any]) -> None:
        with self._market_cache_lock:
            self._market_cache = {
                "result": value.get("result", ""),
                "data": value.get("data", ""),
            }
            self._market_cache_ts = time.monotonic()

    def _get_market_cache(self) -> Optional[Dict[str, Any]]:
        with self._market_cache_lock:
            return self._market_cache

    async def list_market_skills(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        拉取 Skills Forge 市场技能清单（约 88 个），带 TTL 缓存。

        返回结构化技能列表 [{name, description}, ...]；首次/强制刷新时才会
        真正去云端拉取，之后命中缓存在毫秒级返回。
        """
        if not self.enabled:
            return []
        res = await self.call_skill("list_skills", {}, force=force)
        if not res.get("success"):
            return []
        return self._parse_market_skills(res.get("data") or res.get("result") or "")

    @staticmethod
    def _parse_market_skills(text: str) -> List[Dict[str, Any]]:
        """将 list_skills 返回的文本容错解析为结构化技能列表。"""
        if not text:
            return []
        text = text.strip()
        # 优先尝试 JSON 数组 / 对象
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, list):
            return StdioMCPClient._map_market_items(data)
        if isinstance(data, dict):
            for key in ("skills", "results", "items", "data", "list"):
                val = data.get(key)
                if isinstance(val, list):
                    return StdioMCPClient._map_market_items(val)
        # 退化：按行解析 "name: description" 或 "- name"
        out: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip().lstrip("-*•").strip()
            if not line:
                continue
            if ":" in line:
                name, desc = line.split(":", 1)
                out.append({"name": name.strip(), "description": desc.strip()})
            else:
                out.append({"name": line, "description": ""})
        return out

    @staticmethod
    def _map_market_items(items: List[Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                out.append(
                    {
                        "name": item.get("name") or item.get("id") or item.get("slug") or "",
                        "description": item.get("description")
                        or item.get("desc")
                        or item.get("summary")
                        or "",
                    }
                )
            elif isinstance(item, str):
                out.append({"name": item, "description": ""})
        return out
