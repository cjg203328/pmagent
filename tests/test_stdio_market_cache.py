"""
StdioMCPClient 市场技能列表缓存层单元测试
=========================================

验证 call_skill("list_skills") 的 TTL 缓存行为：
- 首次调用打底层（_acall），命中缓存后第二次 0 开销；
- force=True 绕过缓存；
- list_market_skills 解析为结构化技能列表；
- _parse_market_skills 对不同返回格式容错。

全部用 monkeypatch 绕过真实 npx 连接，快速稳定。
"""

import asyncio
from types import SimpleNamespace

from artpm_agent.core.mcp_client_stdio import StdioMCPClient


def _make_client(monkeypatch):
    """构造客户端并绕过真实连接，mock _acall 计数。"""
    c = StdioMCPClient(api_key="sk_test", enabled=True)
    c.enabled = True  # 强制启用，避免 is_valid_api_key 依赖
    monkeypatch.setattr(c, "_ensure_connected", lambda force=False: True)
    calls = {"n": 0}

    async def fake_acall(name, params):
        calls["n"] += 1
        return {
            "success": True,
            "result": '[{"name":"a","description":"A"}]',
            "data": '[{"name":"a","description":"A"}]',
        }

    monkeypatch.setattr(c, "_acall", fake_acall)
    c._calls = calls
    return c


def test_cache_avoids_second_cloud_fetch(monkeypatch):
    c = _make_client(monkeypatch)
    r1 = asyncio.run(c.call_skill("list_skills", {}))
    r2 = asyncio.run(c.call_skill("list_skills", {}))
    assert c._calls["n"] == 1, f"底层只应调用 1 次，实际 {c._calls['n']}"
    assert r1.get("cached") is not True
    assert r2.get("cached") is True
    assert c.last_market_cache_hit is True


def test_force_bypasses_cache(monkeypatch):
    c = _make_client(monkeypatch)
    asyncio.run(c.call_skill("list_skills", {}))
    asyncio.run(c.call_skill("list_skills", {}))  # 命中缓存
    assert c._calls["n"] == 1
    r3 = asyncio.run(c.call_skill("list_skills", {}, force=True))
    assert c._calls["n"] == 2
    assert r3.get("cached") is not True
    assert c.last_market_cache_hit is False


def test_non_list_skill_not_cached(monkeypatch):
    c = _make_client(monkeypatch)
    asyncio.run(c.call_skill("resolve_skill", {"query": "test"}))
    asyncio.run(c.call_skill("resolve_skill", {"query": "test"}))
    # 非 list_skills 不缓存，每次都打底层
    assert c._calls["n"] == 2
    assert c.last_market_cache_hit is None


def test_side_effect_tool_is_blocked_before_backend_call(monkeypatch):
    c = _make_client(monkeypatch)
    result = asyncio.run(c.call_skill("phase_advance", {}))

    assert result == {
        "success": False,
        "code": "MCP_TOOL_NOT_ALLOWED",
        "error": "Skills Forge tool is not allowed: phase_advance",
        "retryable": False,
    }
    assert c._calls["n"] == 0


def test_remote_tool_list_exposes_only_read_only_allowlist():
    class FakeSession:
        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="resolve_skill", description="resolve", inputSchema={}
                    ),
                    SimpleNamespace(
                        name="phase_advance", description="write", inputSchema={}
                    ),
                ]
            )

    c = StdioMCPClient(api_key="sk_test", enabled=True)
    c._session = FakeSession()

    assert [tool["name"] for tool in asyncio.run(c._a_list_tools())] == [
        "resolve_skill"
    ]


def test_list_market_skills_parses(monkeypatch):
    c = _make_client(monkeypatch)
    market = asyncio.run(c.list_market_skills())
    assert len(market) == 1
    assert market[0] == {"name": "a", "description": "A"}


def test_parse_market_skills_json_list():
    c = StdioMCPClient(api_key="sk_test", enabled=True)
    out = c._parse_market_skills('[{"name":"foo","description":"bar"},{"name":"baz"}]')
    assert out == [
        {"name": "foo", "description": "bar"},
        {"name": "baz", "description": ""},
    ]


def test_parse_market_skills_dict_wrapped():
    c = StdioMCPClient(api_key="sk_test", enabled=True)
    out = c._parse_market_skills('{"skills":[{"name":"x","description":"y"}]}')
    assert out == [{"name": "x", "description": "y"}]


def test_parse_market_skills_line_fallback():
    c = StdioMCPClient(api_key="sk_test", enabled=True)
    out = c._parse_market_skills("- foo: bar\n- baz")
    assert out[0] == {"name": "foo", "description": "bar"}
    assert out[1] == {"name": "baz", "description": ""}


def test_parse_market_skills_empty():
    c = StdioMCPClient(api_key="sk_test", enabled=True)
    assert c._parse_market_skills("") == []
    assert c._parse_market_skills("not json at all") == [
        {"name": "not json at all", "description": ""}
    ]


def test_disk_cache_survives_new_instance(monkeypatch, tmp_path):
    """写入磁盘缓存后，新建实例（模拟进程重启）应能从磁盘恢复并命中，无需云端。"""
    monkeypatch.setenv("MCP_SKILLS_CACHE_TTL", "300")
    # 用临时目录替代默认 .cache，避免污染项目
    import artpm_agent.core.mcp_client_stdio as mod

    monkeypatch.setattr(mod, "is_valid_api_key", lambda k: True)
    monkeypatch.setattr(mod, "_DISK_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        mod, "_MARKET_DISK_CACHE_FILE", tmp_path / "skills_forge_market.json"
    )

    # 进程 #1：构造客户端，mock 连接后写入缓存（落盘）
    c1 = StdioMCPClient(api_key="sk_test", enabled=True)
    c1.enabled = True
    monkeypatch.setattr(c1, "_ensure_connected", lambda force=False: True)
    payload = {
        "success": True,
        "result": '[{"name":"a","description":"A"}]',
        "data": '[{"name":"a","description":"A"}]',
    }
    c1._set_market_cache(payload)
    assert tmp_path.joinpath("skills_forge_market.json").exists()

    # 进程 #2：全新实例，应能从磁盘加载并命中（不调 _acall）
    c2 = StdioMCPClient(api_key="sk_test", enabled=True)
    c2.enabled = True
    monkeypatch.setattr(c2, "_ensure_connected", lambda force=False: True)
    calls = {"n": 0}

    async def fake_acall(name, params):
        calls["n"] += 1
        return payload

    monkeypatch.setattr(c2, "_acall", fake_acall)
    # 内存缓存已在 __init__ 从磁盘恢复 → _market_cache_valid() 为真
    r = asyncio.run(c2.call_skill("list_skills", {}))
    assert calls["n"] == 0, "重启后应命中磁盘缓存，不应打云端"
    assert r.get("cached") is True
    assert c2.last_market_cache_hit is True


def test_disk_cache_expired_not_loaded(monkeypatch, tmp_path):
    """磁盘缓存过期（TTL=0）时启动不加载，首次调用仍走云端。"""
    monkeypatch.setenv("MCP_SKILLS_CACHE_TTL", "0")
    import artpm_agent.core.mcp_client_stdio as mod

    monkeypatch.setattr(mod, "is_valid_api_key", lambda k: True)
    monkeypatch.setattr(mod, "_DISK_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        mod, "_MARKET_DISK_CACHE_FILE", tmp_path / "skills_forge_market.json"
    )

    c1 = StdioMCPClient(api_key="sk_test", enabled=True)
    c1.enabled = True
    monkeypatch.setattr(c1, "_ensure_connected", lambda force=False: True)
    c1._set_market_cache(
        {"success": True, "result": "[]", "data": "[]"}
    )

    c2 = StdioMCPClient(api_key="sk_test", enabled=True)
    c2.enabled = True
    assert c2._market_cache is None, "TTL=0 时不应从过期磁盘加载"
