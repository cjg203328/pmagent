# 本地MCP服务调用指南

## 🎯 目标

在ArtPM Agent中调用本地MCP服务器,实现真实的tool调用。

---

## 📋 方案选择

### 方案1: 使用MCP Python SDK (推荐)

#### 安装MCP SDK

```bash
pip install mcp
```

#### 配置MCP服务器

创建 `.claude/mcp_config.json`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "python",
      "args": ["-m", "mcp.server.filesystem"],
      "env": {
        "WORKSPACE_PATH": "d:\\桌面\\pmagent"
      }
    },
    "sqlite": {
      "command": "python", 
      "args": ["-m", "mcp.server.sqlite"],
      "env": {
        "DB_PATH": "d:\\桌面\\pmagent\\data\\artpm.db"
      }
    }
  }
}
```

---

### 方案2: 使用Claude Desktop的MCP配置

如果你已经在Claude Desktop中配置了MCP服务器,可以直接使用:

#### Windows配置路径

```
%APPDATA%\Claude\claude_desktop_config.json
```

#### 配置示例

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "d:\\桌面\\pmagent"]
    },
    "sqlite": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sqlite", "--db-path", "d:\\桌面\\pmagent\\data\\artpm.db"]
    },
    "puppeteer": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-puppeteer"]
    }
  }
}
```

---

### 方案3: 自建MCP服务器 (最灵活)

创建自己的MCP服务器,提供项目特定的tools。

---

## 🚀 快速开始 (推荐方案1)

### Step 1: 安装MCP SDK

```bash
pip install mcp anthropic
```

### Step 2: 创建MCP配置

```bash
# 创建配置目录
mkdir -p .claude

# 创建配置文件
cat > .claude/mcp_config.json << EOF
{
  "mcpServers": {
    "filesystem": {
      "command": "python",
      "args": ["-m", "mcp.server.filesystem"],
      "env": {
        "WORKSPACE_PATH": "d:\\\\桌面\\\\pmagent"
      }
    }
  }
}
EOF
```

### Step 3: 测试MCP连接

创建 `test_real_mcp.py`:

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def test_mcp():
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "mcp.server.filesystem"],
        env={"WORKSPACE_PATH": "d:\\桌面\\pmagent"}
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 初始化
            await session.initialize()
            
            # 列出可用工具
            tools = await session.list_tools()
            print("Available tools:")
            for tool in tools.tools:
                print(f"  - {tool.name}: {tool.description}")
            
            # 调用工具
            result = await session.call_tool("read_file", {
                "path": "README.md"
            })
            print(f"\nResult: {result.content[0].text[:200]}...")

if __name__ == "__main__":
    asyncio.run(test_mcp())
```

运行测试:

```bash
cd artpm_agent
python test_real_mcp.py
```

---

## 🔧 集成到ArtPM Agent

### 更新MCP客户端

创建 `artpm_agent/core/mcp_client_real.py`:

```python
"""
Real MCP Client - 连接本地MCP服务器
"""
import asyncio
import json
from typing import Dict, Any, List, Optional
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("[MCP] mcp package not installed. Run: pip install mcp")


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
        else:
            print("[MCP] Disabled - mcp package not available")
    
    def _load_config(self):
        """加载MCP配置"""
        try:
            config_file = Path(self.config_path)
            if config_file.exists():
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    self.servers = config.get("mcpServers", {})
                print(f"[MCP] Loaded {len(self.servers)} server configs")
            else:
                print(f"[MCP] Config not found: {config_path}")
        except Exception as e:
            print(f"[MCP] Failed to load config: {e}")
    
    async def connect_server(self, server_name: str):
        """连接到MCP服务器"""
        if not self.enabled:
            return False
        
        if server_name in self.sessions:
            return True
        
        server_config = self.servers.get(server_name)
        if not server_config:
            print(f"[MCP] Server '{server_name}' not configured")
            return False
        
        try:
            server_params = StdioServerParameters(
                command=server_config["command"],
                args=server_config.get("args", []),
                env=server_config.get("env", {})
            )
            
            read, write = await stdio_client(server_params).__aenter__()
            session = await ClientSession(read, write).__aenter__()
            await session.initialize()
            
            self.sessions[server_name] = session
            print(f"[MCP] Connected to server: {server_name}")
            return True
            
        except Exception as e:
            print(f"[MCP] Failed to connect to {server_name}: {e}")
            return False
    
    async def list_tools(self, server_name: str) -> List[Dict[str, Any]]:
        """列出服务器的可用工具"""
        if not await self.connect_server(server_name):
            return []
        
        try:
            session = self.sessions[server_name]
            tools_response = await session.list_tools()
            
            tools = []
            for tool in tools_response.tools:
                tools.append({
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema
                })
            
            return tools
            
        except Exception as e:
            print(f"[MCP] Failed to list tools: {e}")
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
            session = self.sessions[server_name]
            result = await session.call_tool(tool_name, arguments)
            
            # 解析结果
            if result.content:
                content = result.content[0]
                if hasattr(content, 'text'):
                    return {
                        "success": True,
                        "result": content.text,
                        "server": server_name,
                        "tool": tool_name
                    }
                else:
                    return {
                        "success": True,
                        "result": str(content),
                        "server": server_name,
                        "tool": tool_name
                    }
            else:
                return {
                    "success": False,
                    "error": "No content in result"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def close(self):
        """关闭所有连接"""
        for server_name, session in self.sessions.items():
            try:
                await session.__aexit__(None, None, None)
                print(f"[MCP] Closed connection to {server_name}")
            except:
                pass
        
        self.sessions.clear()


# 全局实例
_real_mcp_client = None


def get_real_mcp_client(config_path: str = ".claude/mcp_config.json") -> RealMCPClient:
    """获取真实MCP客户端实例"""
    global _real_mcp_client
    if _real_mcp_client is None:
        _real_mcp_client = RealMCPClient(config_path)
    return _real_mcp_client
```

### 测试真实MCP调用

创建 `artpm_agent/test_real_mcp_integration.py`:

```python
"""测试真实MCP集成"""
import asyncio
from core.mcp_client_real import get_real_mcp_client


async def main():
    print("=" * 60)
    print("Testing Real MCP Integration")
    print("=" * 60)
    
    client = get_real_mcp_client()
    
    if not client.enabled:
        print("\n[ERROR] MCP not available")
        print("Install: pip install mcp")
        return
    
    # 测试文件系统服务器
    print("\n[Test 1] List filesystem tools:")
    tools = await client.list_tools("filesystem")
    for tool in tools:
        print(f"  - {tool['name']}: {tool['description']}")
    
    # 测试读取文件
    print("\n[Test 2] Read README.md:")
    result = await client.call_tool("filesystem", "read_file", {
        "path": "README.md"
    })
    
    if result["success"]:
        content = result["result"][:200]
        print(f"  Success! Content preview:\n  {content}...")
    else:
        print(f"  Failed: {result['error']}")
    
    # 测试列出文件
    print("\n[Test 3] List files:")
    result = await client.call_tool("filesystem", "list_directory", {
        "path": "artpm_agent"
    })
    
    if result["success"]:
        print(f"  Success! Result:\n  {result['result'][:200]}...")
    else:
        print(f"  Failed: {result['error']}")
    
    await client.close()
    
    print("\n" + "=" * 60)
    print("Test completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 📝 常见MCP服务器

### 1. Filesystem Server

```json
{
  "filesystem": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "d:\\桌面\\pmagent"]
  }
}
```

**提供工具**:
- `read_file` - 读取文件
- `write_file` - 写入文件
- `list_directory` - 列出目录
- `move_file` - 移动文件
- `search_files` - 搜索文件

### 2. SQLite Server

```json
{
  "sqlite": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-sqlite", "--db-path", "d:\\桌面\\pmagent\\data\\artpm.db"]
  }
}
```

**提供工具**:
- `execute_query` - 执行SQL查询
- `list_tables` - 列出表
- `describe_table` - 查看表结构

### 3. Puppeteer Server (浏览器自动化)

```json
{
  "puppeteer": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-puppeteer"]
  }
}
```

---

## 🎯 下一步

1. **选择方案**: 推荐从方案1开始
2. **安装依赖**: `pip install mcp`
3. **创建配置**: 配置你需要的MCP服务器
4. **测试连接**: 运行测试脚本
5. **集成到Agent**: 更新Skills使用真实MCP

---

需要我帮你配置哪种MCP服务器?
