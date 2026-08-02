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
from concurrent.futures import TimeoutError as FutureTimeoutError
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
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
try:
    _HEARTBEAT_INTERVAL = max(
        0.0, float(os.getenv("MCP_HEARTBEAT_INTERVAL", "30") or "30")
    )
except ValueError:
    _HEARTBEAT_INTERVAL = 30.0

# Skills Forge also exposes specification/workflow mutation tools. ArtPM only
# needs task-to-skill discovery, so the remote boundary fails closed to this
# read-only set even if the upstream server adds more tools later.
SKILLS_FORGE_ALLOWED_TOOLS = frozenset(
    {
        "resolve_skill",
        "get_skill_raw",
        "list_skills",
        "list_bundles",
    }
)

# 磁盘缓存目录（进程重启后首次也能免云端拉取，TTL 内）。
# 存于项目根的 .cache/mcp，已被 .gitignore 排除，不会进版本库。
_DISK_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "mcp"
_MARKET_DISK_CACHE_FILE = _DISK_CACHE_DIR / "skills_forge_market.json"


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
        # Lifecycle operations may wait for the worker, but the state lock
        # must remain available to the worker while it is exiting.
        self._lifecycle_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session = None                    # mcp ClientSession，仅在后台 loop 线程内读写
        self._ready = threading.Event()         # 连接就绪（session 可用）
        self._shutdown = threading.Event()      # 请求关闭后台线程
        self._broken = False                    # 连接已失效，需重建
        self._connect_error: Optional[str] = None
        self._generation = 0                    # 成功连接代次，隔离过期调用失败
        self._atexit_callback = None

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
            self._atexit_callback = self.close
            atexit.register(self._atexit_callback)
            # 启动即从磁盘恢复市场技能缓存（TTL 内则首次调用 0s 云端开销）
            self._load_disk_cache()

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
            # ``run_coroutine_threadsafe`` callers can leave pending tasks
            # behind after a transport failure. Cancel and drain them before
            # closing the loop so Streamlit reruns do not accumulate warnings
            # or references to an already-dead subprocess.
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # pragma: no cover - cleanup must be best effort
                logger.debug("[StdioMCP] event loop cleanup failed", exc_info=True)
            finally:
                loop.close()
            with self._lock:
                # An old worker must not clear state belonging to a later
                # connection if a stop timed out.
                if self._loop is loop:
                    self._loop = None
                if self._thread is threading.current_thread():
                    self._thread = None

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
                    with self._lock:
                        self._session = session
                        self._generation += 1
                        self._broken = False
                        self.last_error = None
                        self.last_error_category = None
                        self._ready.set()
                    logger.info("[StdioMCP] persistent session established")

                    # Keep the transport alive and detect a dead pipe even
                    # when no user request is in flight. ``list_tools`` is
                    # read-only and does not mutate the remote service.
                    heartbeat_at = time.monotonic()
                    while not self._shutdown.is_set():
                        await asyncio.sleep(0.5)
                        if (
                            _HEARTBEAT_INTERVAL > 0
                            and time.monotonic() - heartbeat_at >= _HEARTBEAT_INTERVAL
                        ):
                            heartbeat_at = time.monotonic()
                            list_tools = getattr(session, "list_tools", None)
                            if callable(list_tools) and not self._shutdown.is_set():
                                await asyncio.wait_for(
                                    list_tools(),
                                    timeout=min(
                                        _CALL_TIMEOUT,
                                        max(1.0, _HEARTBEAT_INTERVAL),
                                    ),
                                )

                    self._session = None
        except Exception as e:
            if self._shutdown.is_set():
                return
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

        # Serialize stop/join/start as one lifecycle operation. The worker
        # only takes ``_lock``, so joining while this lock is held is unsafe.
        with self._lifecycle_lock:
            thread_to_join = None
            with self._lock:
                # 双重检查，避免并发重复启动
                if not force and self._ready.is_set() and not self._broken:
                    return True
                if force:
                    thread_to_join = self._stop_background_locked()

            # The worker takes ``_lock`` during cleanup; join only after the
            # state lock has been released.
            if force and not self._join_background(thread_to_join):
                with self._lock:
                    self._broken = True
                return False

            with self._lock:
                if force:
                    self._shutdown.clear()
                    self._ready.clear()
                    if (
                        thread_to_join is not None
                        and self._thread is thread_to_join
                        and not thread_to_join.is_alive()
                    ):
                        self._thread = None
                need_start = self._thread is None or not self._thread.is_alive()
                if need_start:
                    self._broken = False
                    self._connect_error = None
                    self._thread = threading.Thread(target=self._background_main, daemon=True)
                    self._thread.start()

        # 在锁外等待连接就绪（后台线程取得锁后才会 set _ready）。轮询
        # 线程状态，避免连接线程已经失败时仍然阻塞完整的 180 秒。
        deadline = time.monotonic() + _CONNECT_TIMEOUT
        while time.monotonic() < deadline:
            if self._ready.wait(
                timeout=min(0.25, max(0.0, deadline - time.monotonic()))
            ):
                return True
            with self._lock:
                thread = self._thread
                connect_error = self._connect_error
                broken = self._broken
            if connect_error and (thread is None or not thread.is_alive()):
                return False
            if broken and (thread is None or not thread.is_alive()):
                return False

        self._broken = True
        self._connect_error = (
            f"stdio MCP connection timed out after {_CONNECT_TIMEOUT}s"
        )
        self.last_error = self._connect_error
        self.last_error_category = "network"
        logger.warning("[StdioMCP] connect timed out after %ss", _CONNECT_TIMEOUT)
        return False

    def _stop_background_locked(self) -> Optional[threading.Thread]:
        """Request worker shutdown while ``_lock`` is held.

        The caller must release ``_lock`` before joining the returned thread;
        the worker takes that lock during its ``finally`` cleanup.
        """
        self._shutdown.set()
        t = self._thread
        self._session = None
        self._loop = None
        self._ready.clear()
        return t

    @staticmethod
    def _join_background(thread: Optional[threading.Thread]) -> bool:
        """Join a stopped worker without holding the state lock."""
        if thread is None or thread is threading.current_thread():
            return True
        if not thread.is_alive():
            return True
        thread.join(timeout=10)
        if thread.is_alive():
            logger.warning("[StdioMCP] background thread did not stop before timeout")
            return False
        return True

    def close(self) -> None:
        """优雅关闭：终止后台线程与 npx 子进程。幂等，可安全重复调用。"""
        with self._lifecycle_lock:
            with self._lock:
                t = self._stop_background_locked()
            self._join_background(t)
            with self._lock:
                # Keep a still-running worker visible so a later idempotent
                # close can retry the join; otherwise clear all references.
                if t is None or not t.is_alive():
                    if t is None or self._thread is t:
                        self._session = None
                        self._loop = None
                        self._thread = None
                        self._ready.clear()
        # 缓存与连接无关，但关闭后一并清空，避免下次连接误用旧数据
        with self._market_cache_lock:
            self._market_cache = None
            self._market_cache_ts = 0.0
            self.last_market_cache_hit = None
        callback = self._atexit_callback
        if callback is not None:
            self._atexit_callback = None
            try:
                atexit.unregister(callback)
            except Exception:  # pragma: no cover - interpreter shutdown guard
                pass

    # ──────────────────────────────────────────────
    # 跨线程调用投递
    # ──────────────────────────────────────────────
    def _submit_sync(self, coro, timeout: int):
        """从同步上下文把协程投递到后台 loop 并阻塞等待结果。"""
        loop = self._loop
        if loop is None:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            raise RuntimeError("stdio loop not started")
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            raise
        try:
            return fut.result(timeout=timeout)
        except (FutureTimeoutError, asyncio.CancelledError):
            # A timed-out request must not keep running against a session that
            # may immediately be torn down for reconnect.
            fut.cancel()
            raise
        except BaseException:
            fut.cancel()
            raise

    async def _submit_async(self, coro, timeout: int):
        """从异步上下文把协程投递到后台 loop 并 await 结果（不阻塞调用方 loop）。"""
        loop = self._loop
        if loop is None:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            raise RuntimeError("stdio loop not started")
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            raise
        try:
            return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout)
        except BaseException:
            fut.cancel()
            raise

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
            name = getattr(t, "name", None)
            if name not in SKILLS_FORGE_ALLOWED_TOOLS:
                continue
            skills.append(
                {
                    "name": name,
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
        if skill_name not in SKILLS_FORGE_ALLOWED_TOOLS:
            return {
                "success": False,
                "code": "MCP_TOOL_NOT_ALLOWED",
                "error": f"Skills Forge tool is not allowed: {skill_name}",
                "retryable": False,
            }

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
        if not await asyncio.to_thread(self._ensure_connected):
            raise RuntimeError(self._connect_error or "stdio connect failed")
        with self._lock:
            generation = self._generation
        try:
            return await self._submit_async(self._a_call_tool(skill_name, params), _CALL_TIMEOUT)
        except Exception:
            # 会话可能已失效，重建并重试一次。若另一个并发调用已经
            # 建立了更新代次，则直接复用，不能拆掉刚恢复的新会话。
            with self._lock:
                force_reconnect = self._generation == generation
                if force_reconnect:
                    self._broken = True
                    self._session = None
            connected = await asyncio.to_thread(
                self._ensure_connected,
                force_reconnect,
            )
            if connected:
                return await self._submit_async(self._a_call_tool(skill_name, params), _CALL_TIMEOUT)
            raise

    def list_skills(self) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            skills = self._list_sync()
        except Exception as error:
            self.last_error = self._classify_error(error)
            return self._tools_cache or []
        self._tools_cache = skills
        self.last_error = None
        self.last_error_category = None
        return skills

    def _list_sync(self) -> List[Dict[str, Any]]:
        if not self._ensure_connected():
            raise RuntimeError(self._connect_error or "stdio connect failed")
        with self._lock:
            generation = self._generation
        try:
            return self._submit_sync(self._a_list_tools(), _CALL_TIMEOUT)
        except Exception:
            # 与异步路径相同：过期代次的失败不得重置更新会话。
            with self._lock:
                force_reconnect = self._generation == generation
                if force_reconnect:
                    self._broken = True
                    self._session = None
            if self._ensure_connected(force=force_reconnect):
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
            # Do not use ``list_skills`` here: it intentionally falls back to
            # stale cache for normal callers, which would turn a dead session
            # into a false-positive health check.
            tools = self._list_sync()
            self._tools_cache = tools
            self.last_error = None
            self.last_error_category = None
            return True, f"Skills Forge (stdio) 已连接，{len(tools)} 个技能可用"
        except Exception as e:
            self.last_error = self._classify_error(e)
            return False, self.last_error

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
        # 锁外落盘（避免重入 _market_cache_lock；文件写很快，且原子 rename 保证一致性）
        self._save_disk_cache(self._market_cache)

    def _get_market_cache(self) -> Optional[Dict[str, Any]]:
        with self._market_cache_lock:
            return self._market_cache

    # ── 磁盘持久化（跨进程重启保留缓存）──
    def _load_disk_cache(self) -> None:
        """启动时从磁盘恢复市场技能缓存；过期或损坏则丢弃（下次调用走云端）。"""
        try:
            if not _MARKET_DISK_CACHE_FILE.exists():
                return
            with open(_MARKET_DISK_CACHE_FILE, encoding="utf-8") as f:
                doc = json.load(f)
            saved_at = doc.get("saved_at", 0)
            if not isinstance(saved_at, (int, float)):
                return
            age = time.time() - saved_at
            if age >= self._market_cache_ttl:
                logger.info(
                    "[StdioMCP] disk cache expired (age %.0fs >= ttl %ss), will refetch",
                    age,
                    self._market_cache_ttl,
                )
                return
            data = doc.get("data")
            if not isinstance(data, dict):
                return
            with self._market_cache_lock:
                self._market_cache = {
                    "result": data.get("result", ""),
                    "data": data.get("data", ""),
                }
                self._market_cache_ts = time.monotonic()
            logger.info(
                "[StdioMCP] loaded market cache from disk (%.0fs TTL left)",
                self._market_cache_ttl - age,
            )
        except Exception:
            logger.warning("[StdioMCP] failed to load disk cache", exc_info=True)

    def _save_disk_cache(self, payload: Dict[str, Any]) -> None:
        """原子写磁盘缓存：先写 .tmp 再 rename，避免半截文件。"""
        try:
            _DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            doc = {
                "saved_at": time.time(),
                "data": {
                    "result": payload.get("result", ""),
                    "data": payload.get("data", ""),
                },
            }
            tmp = _MARKET_DISK_CACHE_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
            tmp.replace(_MARKET_DISK_CACHE_FILE)
        except Exception:
            logger.warning("[StdioMCP] failed to save disk cache", exc_info=True)

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
