"""
统一 MCP 客户端 (UnifiedMCPClient) 单元测试
==========================================

聚合本地工具后端 (EnhancedMCPClient) 与远程技能后端 (MCPClient) 为单一入口。
使用 fake 本地后端注入，避免依赖真实文件系统 / 网络 / 环境变量。
"""
import asyncio

from core.mcp_client_unified import UnifiedMCPClient, get_unified_mcp_client


class FakeEnhanced:
    """模拟本地工具后端，提供 read_file / search_files 两个工具。"""

    def __init__(self):
        self.enabled = True

    def is_enabled(self):
        return self.enabled

    def list_tools(self):
        return [
            {"name": "read_file", "description": "读取文件"},
            {"name": "search_files", "description": "搜索文件"},
        ]

    def list_skills(self):
        return self.list_tools()

    async def call_tool(self, name, params):
        return {"success": True, "tool": name, "params": params}

    def get_skills_summary(self):
        return "本地工具:\n  • read_file: 读取文件\n  • search_files: 搜索文件"


def _make_client(monkeypatch):
    """构造使用 fake 本地后端的统一客户端，并关闭远程后端。"""
    # 远程后端默认关闭，确保不会触发 core.mcp_client 的 import / 网络。
    monkeypatch.setenv("MCP_ENABLED", "false")
    client = UnifiedMCPClient()
    client._enhanced = FakeEnhanced()
    return client


def test_enabled_true_when_local_available(monkeypatch):
    client = _make_client(monkeypatch)
    assert client.enabled is True


def test_list_skills_aggregates_local_tools(monkeypatch):
    client = _make_client(monkeypatch)
    names = [s["name"] for s in client.list_skills()]
    assert "read_file" in names
    assert "search_files" in names


def test_call_skill_routes_to_local_tool(monkeypatch):
    client = _make_client(monkeypatch)
    result = asyncio.run(client.call_skill("read_file", {"file_path": "x.txt"}))
    assert result["success"] is True
    assert result["tool"] == "read_file"


def test_call_skill_unknown_returns_error(monkeypatch):
    client = _make_client(monkeypatch)
    result = asyncio.run(client.call_skill("nonexistent_tool", {}))
    assert result["success"] is False
    assert "not found" in result["error"]


def test_get_skills_summary_includes_local(monkeypatch):
    client = _make_client(monkeypatch)
    summary = client.get_skills_summary()
    assert "read_file" in summary


def test_unified_client_is_singleton():
    a = get_unified_mcp_client()
    b = get_unified_mcp_client()
    assert a is b
