# ArtPM Agent 项目结构

本文档描述当前源码仓库的职责边界。运行时数据、构建产物和本地验证结果不属于源码，
不要把它们混入 `artpm_agent/`、`tests/` 或文档目录。

## 顶层目录

```text
pmagent/
├── README.md                         # 面向使用者的唯一主入口
├── pyproject.toml                    # 包元数据、依赖 profile 和 CLI entry points
├── requirements*.txt                 # 兼容安装入口，依赖仍以 pyproject.toml 为准
├── pytest.ini / ruff.toml            # 测试和 lint 配置
├── .env.example / .gitignore         # 配置模板和忽略规则
├── Dockerfile / docker-compose.yml   # 容器镜像和生产单栈
├── Caddyfile / alembic.ini           # 反向代理和数据库迁移配置
├── start*.py / start*.bat / start*.sh # 启动入口和跨平台包装脚本
├── restart.bat / restart.sh          # 已运行实例的重启包装
│
├── agent.py / app.py / config.py     # 旧平面模块兼容 shim
├── main.py / health_check.py         # 旧命令和健康检查兼容入口
│
├── artpm_agent/                      # 正式 Python 包和业务源码
├── tests/                            # 单元、契约、慢速和集成测试
├── benchmarks/                       # 离线性能基准
├── scripts/                          # 维护、迁移、检查和报表脚本
├── alembic/                          # 数据库迁移脚本
├── deploy/                           # PostgreSQL、Caddy 和可观测部署资产
├── docs/                             # 分主题文档
│
├── data/                             # 本地数据库、向量索引和附件，运行时生成
├── logs/                             # 运行日志，运行时生成
├── artifacts/                        # 截图、基准、质量和报表产物
└── build/ / *.egg-info/              # 打包缓存，运行时生成
```

根目录的五个平面 Python 文件是兼容层，不应再向其中添加新业务逻辑。新代码进入
`artpm_agent/`，新维护脚本进入 `scripts/`，新文档按主题进入 `docs/`。

## 源码包

```text
artpm_agent/
├── app.py / main.py / health_check.py # Streamlit、CLI 和健康检查入口
├── agent.py                           # ArtPMAgent 兼容 facade
├── request_orchestrator.py             # 请求编排和兼容代理
├── components.py / config*.py          # 组件组装和配置分层
├── api/                                # FastAPI 网关、Protocol 和路由
│   ├── contracts.py                    # Gateway service Protocol ports
│   └── routers/                        # system/capabilities/permissions/workflows
├── harness/                            # 回合主链、知识、记忆和工作流 handler
├── runtime/                            # AgentLoop、事件总线、工具流水线、plan、subagent
│   ├── service_ports.py                 # Request/turn Protocol ports
│   ├── storage_contracts.py             # Authority and readiness contracts
│   └── keyed_locks.py                   # Bounded workspace lock lifecycle
├── routing/                            # 意图识别、任务分类和输入抽取
├── skills/                             # 业务技能和技能路由
├── providers/                          # Provider、故障转移、结构化输出和有界缓存
│   ├── contracts.py                    # Provider response metadata DTO
│   └── gateway_cache.py                # TTL/LRU/in-flight client lifecycle
├── memory/                             # 会话、记忆、FAISS/Qdrant 和 workspace 知识
├── retrieval/                          # workspace-scoped RetrievalPlan/Hit 适配层
├── database/                           # SQLAlchemy 模型、连接和 Alembic 适配
├── tenancy/ / security/                # 租户上下文、权限和审批
├── plugins/                            # 插件发现、allowlist 和注册
├── parsers/                            # Excel、OCR 和文档解析
├── workflows/                          # 工作流协调、引擎和持久化 facade
│   ├── store_schema.py                  # Schema/migration boundary
│   └── store_codec.py                   # Row/DTO serialization boundary
├── views/                              # Streamlit 页面
├── ui_formatters.py                    # 无副作用的 UI 格式化纯函数
├── ui_helpers.py                       # UI 兼容门面和交互编排
├── ui_style.py                         # UI 样式兼容出口（STYLE_CSS）
├── ui/                                  # P2 UI 纯职责和样式边界
│   ├── formatters.py                    # 无副作用格式化
│   ├── knowledge.py                     # 知识意图/上下文投影
│   ├── approvals.py                     # 审批 payload 摘要
│   ├── rendering.py                     # 消息/资源投影
│   ├── style_tokens.py                  # CSS token 懒加载边界
│   ├── style_layout.py                  # CSS layout 兼容边界
│   └── style_components.py              # CSS component 兼容边界
├── voice/                              # 可选 LiveKit worker
├── evolution/ / editing/               # 复盘、自进化和文档编辑能力
├── core/                               # MCP、Redis 和 Token 监控
├── utils/                              # Provider、日志、附件和通用工具
└── config_data/                        # 默认配置资源
```

规范请求入口是 `HarnessRuntime` 和 `run_turn()`。API/UI 通过请求级依赖进入运行时；
同步旧实现只作为隔离的兼容 fallback。业务库和记忆库分开，所有知识、缓存、向量和
审批操作都必须携带正确的 tenant/workspace 上下文。

架构依赖图由 `scripts/build_graph.py` 按需生成，不把生成物混入源码；查询结果只作
影响面加速器，改码前后仍须用 `rg` 和契约测试复核。

## 文档目录

```text
docs/
├── INDEX.md                            # 文档入口
├── architecture/                       # 架构专题和图谱
├── analysis/                           # 深度分析
├── reports/                             # 实施和修复报告
├── roadmaps/                           # 路线图和后续计划
├── guides/                             # 用户、启动和速查
├── integrations/                        # 外部服务和框架集成
├── operations/                          # 部署、API、质量和故障排查
├── dev/                                # 开发规范、审查和技术债
├── mcp/                                # MCP 历史文档，当前入口见 integrations/MCP.md
└── archive/                            # 历史文档
```

当前稳定文档也按主题归入 `docs/architecture`、`docs/operations`、`docs/integrations`、
`docs/guides` 和 `docs/dev`；新的分析、路线图和报告应放入对应子目录，避免再次形成
无分类文件堆。

## 当前规模与拆分状态

工作区 UI 适配层已从兼容门面中提取到
`artpm_agent/ui/workspace_selector.py`；其余大模块按
[`OPTIMIZATION_STRATEGY.md`](./OPTIMIZATION_STRATEGY.md) 分阶段拆分，旧导入路径继续保留。

以下数据由 2026-09-14 工作区实测，按源文件物理行数统计：

| 文件 | 行数 | 当前职责 | 下一步 |
| --- | ---: | --- | --- |
| `artpm_agent/ui_helpers.py` | 2394 | UI 状态、会话、审批和渲染兼容门面 | 继续迁移副作用到职责模块 |
| `artpm_agent/ui_style.py` | 2312 | 内联 CSS 兼容出口 | 通过 `ui/style_*.py` 分阶段替换片段 |
| `artpm_agent/ui/*.py` | P2 | UI 纯职责和 CSS 边界 | 新代码优先使用，旧入口保持兼容 |
| `artpm_agent/memory/workspace_knowledge_store.py` | 3013 | 事务 facade；schema、资源、规则、向量实现 | 新代码优先使用 `memory/knowledge.py` 纯契约 |
| `artpm_agent/retrieval/` | small | 检索计划、范围校验、引用 DTO | 扩展 sparse/dense/rerank 时保持 API 稳定 |
| `artpm_agent/views/chat.py` | 1889 | 聊天页面生命周期和渲染兼容 facade | 新状态/反馈/欢迎/回合投影使用 `views/chat_*.py` |
| `artpm_agent/agent.py` | 74 | 兼容 facade 和依赖组装 | 保持轻量，不新增业务逻辑 |

拆分采用兼容迁移策略：先提取无副作用模块并保留原导入路径，再按测试覆盖逐步迁移
调用方。禁止直接大规模改名或删除旧入口。

## 产物与数据

```text
artifacts/
├── benchmarks/                         # 性能 JSON 报告
├── screenshots/                        # UI 和参考截图
├── reports/                            # 遥测等 HTML 报告
└── quality/                            # coverage、类型检查和其他质量产物

data/
├── artpm.db                            # 业务库
├── memory.db / conversations.db        # 记忆和会话数据
├── vector_store/                       # 本地向量索引
├── chat_attachments/                   # 上传附件
└── backups/                            # 数据库备份
```

这些目录默认被 Git 忽略；不要提交真实 API Key、数据库、用户附件、日志或本地报告。
需要清理缓存时使用 `scripts/clean.py`，不要手工删除未知数据目录。

## 常用定位

| 需求 | 位置 |
| --- | --- |
| 修改聊天和页面 | `artpm_agent/views/`、`artpm_agent/ui_*.py` |
| 修改请求主链 | `artpm_agent/harness/`、`artpm_agent/runtime/` |
| 添加业务技能 | `artpm_agent/skills/` |
| 修改模型故障转移 | `artpm_agent/providers/` |
| 修改记忆或向量存储 | `artpm_agent/memory/` |
| 修改租户隔离或审批 | `artpm_agent/tenancy/`、`artpm_agent/security/` |
| 修改 API 契约 | `artpm_agent/api/` 和 `docs/operations/API_GATEWAY_DOCUMENTATION.md` |
| 修改迁移 | `alembic/`、`artpm_agent/database/` |
| 增加测试 | `tests/`；外部依赖测试放 `tests/integration/` |
| 增加维护命令 | `scripts/`，并同步 README/索引 |
