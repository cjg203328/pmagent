# ArtPM Agent 速查

本文只保留当前代码的入口、职责和常用命令。历史方案和阶段报告统一放在
[`docs/archive/`](../archive/)。

## 先看哪里

| 目标 | 文档或入口 |
| --- | --- |
| 安装和启动 | [`README.md`](../../README.md)、[`QUICKSTART.md`](QUICKSTART.md) |
| 理解目录职责 | [`PROJECT_STRUCTURE.md`](../architecture/PROJECT_STRUCTURE.md) |
| 理解一次请求 | [`EXECUTION_MAP.md`](../architecture/EXECUTION_MAP.md) |
| REST 接入 | [`API_GATEWAY_DOCUMENTATION.md`](../operations/API_GATEWAY_DOCUMENTATION.md) |
| 外部集成 | [`docs/integrations/`](../integrations/) |
| 测试和质量门禁 | [`QUALITY_GATES.md`](../operations/QUALITY_GATES.md) |
| 开发协作规范 | [`AGENTS.md`](../dev/AGENTS.md) |

## 目录地图

```text
pmagent/
├── README.md / pyproject.toml / .env.example   # 使用入口、依赖和配置模板
├── start_with_checks.py / start.bat / start.sh # 启动和配置检查
├── artpm_agent/                                # 正式源码包
│   ├── app.py                                  # Streamlit 启动器
│   ├── api/                                    # FastAPI REST 网关
│   ├── harness/                                # 回合编排和 handler
│   ├── runtime/                                # AgentLoop、事件、工具和请求服务
│   ├── memory/                                 # 会话、记忆、向量和工作区知识
│   ├── skills/                                 # 业务技能与技能路由
│   ├── providers/                              # 模型网关、故障转移和缓存
│   ├── workflows/                              # 声明式工作流与审批执行
│   ├── security/ / tenancy/                    # 权限、审批和租户上下文
│   ├── plugins/ / parsers/ / database/          # 插件、解析和数据库边界
│   └── views/ / ui_*.py                        # 页面和 UI 共享层
├── tests/                                      # 单元、契约、慢速和集成测试
├── scripts/                                    # 测试、迁移、备份和质量脚本
├── docs/                                       # 当前文档与历史归档
├── data/ / logs/                               # 本地运行数据，不属于源码
└── artifacts/                                  # 截图、报告和质量产物
```

根目录的 `agent.py`、`app.py`、`config.py`、`main.py` 和 `health_check.py` 只承担兼容
入口职责；新增业务代码进入 `artpm_agent/`。

## 一次请求怎么走

```text
Streamlit / REST / CLI
        |
        v
TurnContext + TurnServiceBundle
        |
        v
harness.run_turn()
  ├─ scope 校验
  ├─ fast meta response（仅白名单元问题）
  ├─ 记忆注入（摘要、长期记忆、工作区规则/资料、反馈、策略）
  ├─ profile / knowledge / artifact / workflow handler
  ├─ intent -> skill -> tenant bind -> approval -> execute
  └─ model fallback
        |
        v
TurnResult -> 消息渲染 + SessionStore 运行事实
```

几个不变约束：

- API 和 UI 都先构造 `TurnContext`，再调用 `artpm_agent.harness.run_turn()`。
- `TurnContext.scope` 是 tenant、workspace、actor 的可信快照；模型输入不能覆盖它。
- `TurnServiceBundle` 是请求级服务容器，优先于 `extra` 读取存储、工作流、审批和日志服务。
- 同一回合只识别一次意图，只解析一次附件；失败的记忆召回只降级，不阻塞主回答。
- 写入型 Skill 和模型工具调用都必须经过审批；`AGENT_MODEL_TOOL_CALLS_ENABLED` 不是审批替代品。

## 记忆和记录

| 数据 | 权威模块 | 作用域或规则 |
| --- | --- | --- |
| 会话消息 | `memory/conversation_store.py` | 用户可见的 user/assistant transcript |
| 回合和工具事件 | `memory/session_store.py` | append-only，保存 `turn_start/end` 和工具审计 |
| 通用长期记忆 | `memory/memory_manager.py` | 必须按 workspace 检索 |
| 工作区资料和规则 | `memory/workspace_knowledge_store.py` | 规则独立召回，确认后才 active |
| 远端记忆 | `memory/tencentdb_agent_memory.py` | tenant/workspace/principal/conversation scope |
| 反馈和策略 | `memory/feedback_store.py`、`evolution/strategy_store.py` | 仅注入 active 内容，失败可降级 |
| 性能遥测 | `runtime/telemetry.py`、`providers/gateway.py` | 延迟、模型、token 和 fallback |

不要在入口层手工拼接 `knowledge_context`，不要把完整附件正文、凭据或 `.env` 写入日志。

## 常用命令

### 启动

```powershell
# 本地 UI + API
python -m pip install -e ".[api]"
Copy-Item .env.example .env
python -m artpm_agent.tools.check_config
python start_with_checks.py

# 仅离线 UI
python -m pip install -e .
python -m streamlit run artpm_agent/app.py --server.address 127.0.0.1 --server.port 8501

# 只检查配置
python start_with_checks.py --check-only
```

### 质量门禁

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_fast.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_all.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_integration.ps1
powershell -ExecutionPolicy Bypass -File scripts/test_benchmark.ps1
powershell -ExecutionPolicy Bypass -File scripts/coverage_core.ps1
ruff check artpm_agent tests --select E9,F63,F7,F82
python -m compileall -q artpm_agent
```

默认快速测试不访问外部服务；PostgreSQL、真实 MCP、真实模型和 MinerU 服务必须显式 opt-in。

### 数据维护

```powershell
# 备份 data/ 下的数据库，默认保留 10 份
python scripts/backup_data.py

# 查看备份
python scripts/backup_data.py --list

# 清理 Python/测试缓存和过期日志，不删除业务数据库
python scripts/clean.py
```

## 关键配置

以根目录 [`.env.example`](../../.env.example) 为准。高频配置只有：

| 配置 | 作用 |
| --- | --- |
| `LLM_PROVIDER` / `LLM_MODEL` | 主模型和 Provider |
| `LLM_REQUEST_TIMEOUT_SECONDS` | 主模型请求超时 |
| `AGENT_MODEL_TOOL_CALLS_ENABLED` | 结构化模型工具循环开关，默认关闭 |
| `MEMORY_CONTEXT_MAX_TOKENS` | 单回合自动记忆注入预算，设为 `0` 可关闭 |
| `DB_PATH` / `MEMORY_DB_PATH` / `VECTOR_DB_PATH` | 本地业务、记忆和向量数据路径 |
| `DATABASE_URL` / `VECTOR_BACKEND` | PostgreSQL、Qdrant 等部署后端 |
| `ARTPM_GATEWAY_SHARED_SECRET` | 生产 REST 网关信任边界 |
| `MCP_ENABLED` / `TENCENTDB_AGENT_MEMORY_ENABLED` | 可选外部能力开关 |

## 文档入口

- [`docs/INDEX.md`](../INDEX.md)：完整索引。
- [`docs/architecture/EXECUTION_MAP.md`](../architecture/EXECUTION_MAP.md)：请求、上下文、记忆、工具和记录映射。
- [`docs/operations/TROUBLESHOOTING.md`](../operations/TROUBLESHOOTING.md)：故障排查。
- [`docs/integrations/MINERU_INTEGRATION.md`](../integrations/MINERU_INTEGRATION.md)：MinerU 集成。
- [`docs/integrations/TENCENTDB_AGENT_MEMORY.md`](../integrations/TENCENTDB_AGENT_MEMORY.md)：远端记忆集成。
