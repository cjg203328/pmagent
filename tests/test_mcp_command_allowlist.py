"""MCP command-execution safety contract (P0).

execute_command must stay disabled by default, and when enabled it must honor an
executable basename allowlist so the agent cannot spawn arbitrary binaries.
"""
import sys
from pathlib import Path


from artpm_agent.core.mcp_client_enhanced import EnhancedMCPClient

# Use the running interpreter directly so the test does not depend on `python`
# being resolvable on PATH or on shell quoting.
_EXE = Path(sys.executable).name
_PY = [sys.executable, "-c", "print('ok')"]


async def test_command_execution_disabled_by_default(tmp_path):
    client = EnhancedMCPClient(str(tmp_path))
    result = await client.call_tool("execute_command", {"command": _PY})
    assert result["success"] is False
    assert "disabled" in result["error"]


async def test_allowlist_blocks_unlisted_executable(tmp_path):
    client = EnhancedMCPClient(str(tmp_path), allow_commands=True)
    client.command_allowlist = frozenset({"ls"})
    result = await client.call_tool("execute_command", {"command": _PY})
    assert result["success"] is False
    assert "not permitted" in result["error"].lower()
    assert _EXE in result["error"]


async def test_allowlist_permits_listed_executable(tmp_path):
    client = EnhancedMCPClient(str(tmp_path), allow_commands=True)
    client.command_allowlist = frozenset({_EXE})
    result = await client.call_tool("execute_command", {"command": _PY})
    assert result["success"] is True
    assert "ok" in result["stdout"]


async def test_empty_allowlist_allows_any_command_when_enabled(tmp_path):
    client = EnhancedMCPClient(str(tmp_path), allow_commands=True)
    client.command_allowlist = frozenset()
    result = await client.call_tool("execute_command", {"command": _PY})
    assert result["success"] is True


def test_allowlist_parsed_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_COMMAND_ALLOWLIST", "ls, cat ,python")
    client = EnhancedMCPClient(str(tmp_path), allow_commands=True)
    assert client.command_allowlist == frozenset({"ls", "cat", "python"})
