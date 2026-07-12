"""
Real MCP Client - 连接本地MCP服务器
支持多种MCP服务器: filesystem, sqlite, puppeteer等
"""
import json
from typing import Dict, Any, List
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("[RealMCP] mcp package not installed. Install: pip install mcp")


class RealMCPClient:
    """真实的MCP客户端 - 连接本地MCP服务器"""

    def __init__(self, config_path: str = ".claude/mcp_config.json"):
        """
        初始化MCP客户端

        Args:
            config_path: MCP配置文件路径
        """
        self.config_path = config_path
        self.servers = {}
        self.sessions = {}
        self.enabled = MCP_AVAILABLE

        if self.enabled:
            self._load_config()
            print(f"[RealMCP] Initialized with {len(self.servers)} server(s)")
        else:
            print("[RealMCP] Disabled - mcp package not available")

    def _load_config(self):
        """加载MCP配置"""
        try:
            config_file = Path(self.config_path)
            if config_file.exists():
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.servers = config.get("mcpServers", {})
                print(f"[RealMCP] Loaded config from {self.config_path}")
            else:
                print(f"[RealMCP] Config not found: {self.config_path}")
                print("[RealMCP] Using fallback configuration")
                self._create_default_config()
        except Exception as e:
            print(f"[RealMCP] Failed to load config: {e}")
            self._create_default_config()

    def _create_default_config(self):
        """创建默认配置"""
        workspace = str(Path.cwd())
        self.servers = {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", workspace]
            }
        }

    async def connect_server(self, server_name: str) -> bool:
        """连接到MCP服务器"""
        if not self.enabled:
            return False

        if server_name in self.sessions:
            return True

        server_config = self.servers.get(server_name)
        if not server_config:
            print(f"[RealMCP] Server '{server_name}' not configured")
            return False

        try:
            print(f"[RealMCP] Connecting to {server_name}...")

            server_params = StdioServerParameters(
                command=server_config["command"],
                args=server_config.get("args", []),
                env=server_config.get("env", {})
            )

            # 使用context manager
            client = stdio_client(server_params)
            read, write = await client.__aenter__()

            session = ClientSession(read, write)
            await session.__aenter__()
            await session.initialize()

            self.sessions[server_name] = {
                "session": session,
                "client": client
            }

            print(f"[RealMCP] Connected to {server_name}")
            return True

        except Exception as e:
            print(f"[RealMCP] Failed to connect to {server_name}: {e}")
            return False

    async def list_tools(self, server_name: str) -> List[Dict[str, Any]]:
        """列出服务器的可用工具"""
        if not await self.connect_server(server_name):
            return []

        try:
            session = self.sessions[server_name]["session"]
            tools_response = await session.list_tools()

            tools = []
            for tool in tools_response.tools:
                tools.append({
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema if hasattr(tool, 'inputSchema') else {}
                })

            return tools

        except Exception as e:
            print(f"[RealMCP] Failed to list tools: {e}")
            return []

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用MCP工具

        Args:
            server_name: MCP服务器名称
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            执行结果
        """
        if not await self.connect_server(server_name):
            return {
                "success": False,
                "error": f"Failed to connect to server: {server_name}"
            }

        try:
            session = self.sessions[server_name]["session"]

            print(f"[RealMCP] Calling {server_name}.{tool_name} with args: {arguments}")

            result = await session.call_tool(tool_name, arguments)

            # 解析结果
            if result.content:
                content = result.content[0]
                if hasattr(content, 'text'):
                    result_text = content.text
                else:
                    result_text = str(content)

                return {
                    "success": True,
                    "result": result_text,
                    "server": server_name,
                    "tool": tool_name,
                    "isError": getattr(result, 'isError', False)
                }
            else:
                return {
                    "success": True,
                    "result": None,
                    "server": server_name,
                    "tool": tool_name
                }

        except Exception as e:
            print(f"[RealMCP] Tool call failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "server": server_name,
                "tool": tool_name
            }

    async def close(self):
        """关闭所有连接"""
        for server_name, session_data in self.sessions.items():
            try:
                session = session_data["session"]
                client = session_data["client"]

                await session.__aexit__(None, None, None)
                await client.__aexit__(None, None, None)

                print(f"[RealMCP] Closed connection to {server_name}")
            except Exception as e:
                print(f"[RealMCP] Error closing {server_name}: {e}")

        self.sessions.clear()

    def is_enabled(self) -> bool:
        """检查MCP是否可用"""
        return self.enabled

    def list_servers(self) -> List[str]:
        """列出所有配置的服务器"""
        return list(self.servers.keys())


# 全局实例
_real_mcp_client = None


def get_real_mcp_client(config_path: str = ".claude/mcp_config.json") -> RealMCPClient:
    """获取真实MCP客户端实例"""
    global _real_mcp_client
    if _real_mcp_client is None:
        _real_mcp_client = RealMCPClient(config_path)
    return _real_mcp_client
