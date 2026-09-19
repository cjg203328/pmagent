# ArtPM Agent 使用指南

ArtPM Agent 是面向游戏美术外包项目管理的离线优先助手。它可以在没有 LLM Key 的情况下运行
本地业务技能、文档解析和工作区知识库；配置模型后增加通用对话、语义意图识别和模型增强。

## 1. 启动

### 本地 UI + API

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[api]"
Copy-Item .env.example .env
python -m artpm_agent.tools.check_config
python start_with_checks.py
```

访问 `http://127.0.0.1:8501`；API 健康检查是
`http://127.0.0.1:8765/health`，文档是 `http://127.0.0.1:8765/docs`。

### 仅离线 UI

```powershell
python -m pip install -e .
python -m streamlit run artpm_agent/app.py --server.address 127.0.0.1 --server.port 8501
```

Windows 也可以运行 `start.bat`，Linux/macOS 可以运行 `./start.sh`。只检查配置时运行：

```powershell
python start_with_checks.py --check-only
```

## 2. 第一次使用

1. 在 `.env` 中选择 Provider；没有 API Key 时保留离线模式即可。
2. 在对话页输入明确的任务，例如：

   ```text
   报价 12 万，成本 8 万，帮我算利润和风险
   ```

3. 上传报价单、进度表或项目文档，等待附件解析完成。
4. 需要长期复用的资料进入当前工作区知识库；涉及规则或写入的操作按界面提示确认。

常用场景包括报价测算、任务分配、进度追踪、交付检查、文件解析、资料检索和提醒预览。

需要可下载交付物时，直接说明格式和内容。当前支持 Word、Excel、PowerPoint 和 PDF。
简单明确的内容可以离线生成；复杂内容需要已配置的模型生成结构化计划。下载卡片显示“已核验”
时，表示系统已经重新打开文件并核对内容和结构，而不是只根据模型回复判断成功。

## 3. 运行边界

每条消息都经过同一条回合主链：

```text
输入 -> TurnContext -> run_turn()
      -> scope 校验
      -> 记忆注入
      -> 知识/产物/工作流/Skill 路由
      -> 权限审批
      -> 模型回退
      -> TurnResult -> 页面或 API 响应
```

同一回合只识别一次意图、只解析一次附件。长期记忆、工作区资料、已采纳规则、用户反馈和
进化策略会在预算内注入；记忆后端不可用时不会阻塞普通回答。

涉及修改、外发、删除、发布、发送或未知插件的操作，系统会先创建审批请求。未确认前不执行，
也不会因为切换到“完全访问”而跳过高风险确认。

## 4. 模型配置

从 `.env.example` 复制后填写当前部署需要的值：

```dotenv
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=
LLM_REQUEST_TIMEOUT_SECONDS=12
AGENT_MODEL_TOOL_CALLS_ENABLED=false
MEMORY_CONTEXT_MAX_TOKENS=1200
```

支持 `anthropic`、`openai`、`zhipu`、`deepseek` 和兼容 OpenAI 协议的自定义 Provider。主模型
不支持图片时，可用 `LLM_VISION_PROVIDER` 和 `LLM_VISION_MODEL` 配置独立视觉模型。

模型工具调用默认关闭。打开它只代表允许进入结构化工具循环，不代表授予写入权限；写入型工具
仍由宿主审批和风险策略控制。

## 5. 文件、知识和记忆

默认运行数据位于 `data/`：

```text
data/
├── artpm.db             # 业务数据
├── conversations.db     # 会话消息、审批、工作流和回合事件
├── memory.db            # 长期记忆文档
├── vector_store/        # FAISS 或配置的向量后端缓存
├── chat_attachments/    # 会话附件
└── backups/             # 备份快照
```

工作区是知识、会话、审批和工作流的隔离边界。REST 请求由可信网关提供 tenant/workspace/actor
身份；本地 UI 使用本地租户和当前工作区。不要在聊天内容中伪造租户或工作区字段。

备份数据库：

```powershell
python scripts/backup_data.py
python scripts/backup_data.py --list
```

清理缓存和过期日志：

```powershell
python scripts/clean.py
```

不要手工删除正在运行的数据库或附件目录。需要彻底重置时先停止应用，再备份并按部署流程清理
对应 `data/`，生产环境优先使用数据库备份和迁移工具。

## 6. REST 接入

安装 API extra 后运行 `artpm-api` 或 `python -m artpm_agent.api`。主要路由：

- `GET /health`、`GET /ready`
- `GET /v1/capabilities`
- `POST /v1/chat`
- `GET/POST /v1/permissions/*`
- `GET/POST /v1/workflows/*`
- `GET/POST /v1/workflow-runs/*`

生产请求需要可信网关注入 `X-Gateway-Token`、`X-Tenant-ID`、`X-Workspace-ID`、`X-Actor-ID` 和
角色信息。请求体不能覆盖这些身份。完整请求格式见
[`API_GATEWAY_DOCUMENTATION.md`](../operations/API_GATEWAY_DOCUMENTATION.md)。

## 7. 可选集成

| 集成 | 作用 | 默认行为 |
| --- | --- | --- |
| MinerU | PDF、DOCX、PPTX、XLSX 等增强解析 | 失败回退内置解析器 |
| OCR | 扫描件文字提取 | 按需安装，缺失时降级 |
| Skills Forge MCP | 发现远程工具 | 默认关闭，命令能力需额外 allowlist |
| TencentDB Agent Memory | 远端记忆召回和捕获 | 默认关闭，要求 scope secret |
| Qdrant | 网络向量后端 | 未配置时使用本地 FAISS |
| Redis | 缓存和聚合加速 | 未配置时使用本地存储 |

集成配置和验证命令见 [`docs/integrations/`](../integrations/)。真实外部服务测试必须显式 opt-in，
离线测试的 `skip` 或 fake 不能当作生产验证。

## 8. 故障排查

1. 先执行 `python -m artpm_agent.tools.check_config`。
2. UI 无法打开时确认 `8501`，API 无法连接时确认 `8765`。
3. 模型失败时检查 Provider、模型名、Key 和候选模型是否属于同一 Provider。
4. 向量或远端记忆失败时查看日志，正常情况下会回退本地路径或空上下文。
5. PostgreSQL RLS 测试需要 `ARTPM_TEST_POSTGRES_URL`；没有外部数据库时会明确跳过。
6. 端口被占用时先确认监听者属于本项目，再使用配置项或启动参数切换端口。

详细排查见 [`TROUBLESHOOTING.md`](../operations/TROUBLESHOOTING.md)。

## 9. 开发验证

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_fast.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_all.ps1
ruff check artpm_agent tests --select E9,F63,F7,F82
python -m compileall -q artpm_agent
```

新增功能前先读 [`EXECUTION_MAP.md`](../architecture/EXECUTION_MAP.md) 和
[`AGENTS.md`](../dev/AGENTS.md)，确认应该修改主链、服务包、存储边界还是 UI 适配层。
