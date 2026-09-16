# 🚀 本地MCP服务调用 - 快速开始

## ✅ 已完成

我已经为你创建了连接本地MCP服务的完整方案:

### 创建的文件

1. **MCP配置**: `.claude/mcp_config.json` - MCP服务器配置
2. **真实MCP客户端**: `artpm_agent/core/mcp_client_real.py` - 连接本地MCP服务
3. **测试脚本**: `artpm_agent/test_real_mcp.py` - 测试MCP连接
4. **使用指南**: `LOCAL_MCP_GUIDE.md` - 详细使用文档

---

## 🎯 快速开始

### Step 1: 安装MCP SDK

```bash
pip install mcp
```

如果上面的命令失败,尝试从GitHub安装:

```bash
pip install git+https://github.com/modelcontextprotocol/python-sdk.git
```

### Step 2: 安装MCP服务器 (选择你需要的)

#### Filesystem Server (文件操作)

```bash
npm install -g @modelcontextprotocol/server-filesystem
```

#### SQLite Server (数据库查询)

```bash
npm install -g @modelcontextprotocol/server-sqlite
```

#### Puppeteer Server (浏览器自动化)

```bash
npm install -g @modelcontextprotocol/server-puppeteer
```

### Step 3: 测试连接

```bash
cd artpm_agent
python test_real_mcp.py
```

**预期输出**:

```
======================================================================
Testing Real MCP Server Connection
======================================================================

[RealMCP] Initialized with 2 server(s)
[INFO] Configured servers: filesystem, sqlite

======================================================================
[Test 1] Filesystem Server
======================================================================

[1.1] List available tools:
  - read_file: Read file contents
  - write_file: Write content to a file
  - list_directory: List directory contents
  - move_file: Move or rename a file
  - search_files: Search for files

[1.2] Read README.md:
  Success! Content preview:
  # ArtPM Copilot...

[1.3] List files in artpm_agent/:
  Success! Files:
  app.py
  agent.py
  config.py
  ...

Test completed!
```

---

## 🔧 配置说明

### MCP配置文件 (`.claude/mcp_config.json`)

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "d:\\桌面\\pmagent"
      ]
    },
    "sqlite": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-sqlite",
        "--db-path",
        "d:\\桌面\\pmagent\\artpm_agent\\data\\artpm.db"
      ]
    }
  }
}
```

**说明**:
- `filesystem`: 文件系统操作服务器,工作目录为项目根目录
- `sqlite`: SQLite数据库查询服务器,连接到项目数据库

---

## 💡 使用示例

### 在Python代码中使用

```python
import asyncio
from core.mcp_client_real import get_real_mcp_client

async def example():
    # 获取MCP客户端
    client = get_real_mcp_client()

    # 读取文件
    result = await client.call_tool("filesystem", "read_file", {
        "path": "README.md"
    })

    if result["success"]:
        print(f"Content: {result['result']}")

    # 查询数据库
    result = await client.call_tool("sqlite", "list_tables", {})

    if result["success"]:
        print(f"Tables: {result['result']}")

    # 关闭连接
    await client.close()

# 运行
asyncio.run(example())
```

### 集成到Skills

更新 `artpm_agent/skills/mcp_skills.py`,使用真实MCP客户端:

```python
from core.mcp_client_real import get_real_mcp_client

class FileReaderSkill(BaseSkill):
    def __init__(self, context):
        super().__init__(context)
        # 使用真实MCP客户端
        self.mcp_client = get_real_mcp_client()

    async def execute(self, inputs):
        # 调用真实MCP服务
        result = await self.mcp_client.call_tool(
            "filesystem",
            "read_file",
            {"path": inputs["file_path"]}
        )

        return result
```

---

## 🔍 可用的MCP工具

### Filesystem Server 工具

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `read_file` | 读取文件内容 | `path` |
| `write_file` | 写入文件 | `path`, `content` |
| `list_directory` | 列出目录 | `path` |
| `move_file` | 移动/重命名文件 | `source`, `destination` |
| `search_files` | 搜索文件 | `path`, `pattern` |
| `get_file_info` | 获取文件信息 | `path` |

### SQLite Server 工具

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `list_tables` | 列出所有表 | 无 |
| `describe_table` | 查看表结构 | `table_name` |
| `execute_query` | 执行SQL查询 | `query` |

---

## ⚠️ 常见问题

### Q1: 提示"MCP SDK not available"

**解决方案**:
```bash
pip install mcp
```

### Q2: 提示"Server not available"

**解决方案**:
- 检查Node.js是否安装: `node --version`
- 安装MCP服务器: `npm install -g @modelcontextprotocol/server-filesystem`
- 检查配置文件路径是否正确

### Q3: 连接超时

**解决方案**:
- 检查服务器配置是否正确
- 确保工作目录路径存在
- 查看服务器日志输出

### Q4: 想使用其他MCP服务器

**解决方案**:

在 `.claude/mcp_config.json` 中添加新服务器:

```json
{
  "mcpServers": {
    "your_server": {
      "command": "python",
      "args": ["-m", "your.mcp.server"],
      "env": {
        "YOUR_CONFIG": "value"
      }
    }
  }
}
```

---

## 🎯 下一步

### 1. 测试基础连接

```bash
cd artpm_agent
python test_real_mcp.py
```

### 2. 更新Skills使用真实MCP

修改 `artpm_agent/skills/mcp_skills.py`:

```python
# 替换这一行:
# from core.mcp_client_enhanced import get_enhanced_mcp_client

# 改为:
from core.mcp_client_real import get_real_mcp_client as get_enhanced_mcp_client
```

### 3. 测试完整功能

```bash
python test_mcp_simple.py
```

### 4. 启动应用测试

```bash
cd ..
.\start.bat
```

在聊天界面测试:
- "帮我读取README.md文件"
- "列出artpm_agent目录下的所有文件"
- "查询数据库中有哪些表"

---

## 📚 参考资料

- **MCP官方文档**: https://modelcontextprotocol.io/
- **MCP Python SDK**: https://github.com/modelcontextprotocol/python-sdk
- **MCP服务器列表**: https://github.com/modelcontextprotocol/servers

---

## ✅ 检查清单

完成以下步骤后,你的本地MCP服务就配置好了:

- [ ] 安装MCP SDK: `pip install mcp`
- [ ] 安装Node.js (如果还没有)
- [ ] 安装MCP服务器: `npm install -g @modelcontextprotocol/server-filesystem`
- [ ] 测试连接: `python test_real_mcp.py`
- [ ] 更新Skills使用真实MCP客户端
- [ ] 启动应用测试功能

---

**准备好了吗? 运行 `python test_real_mcp.py` 开始测试!** 🚀

---

生成时间: 2026-07-11
