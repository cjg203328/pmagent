# ArtPM Agent 文档索引

README 是入口文档；本页按读者角色和主题提供深入文档导航。新增文档时，按主题放入
对应子目录，不要继续把临时报告堆到 `docs/` 根目录。

## 快速开始

| 文档 | 用途 |
| --- | --- |
| [`../README.md`](../README.md) | 项目概览、安装、启动和运行方式 |
| [`guides/QUICKSTART.md`](guides/QUICKSTART.md) | 快速启动和用户操作 |
| [`guides/USER_GUIDE.md`](guides/USER_GUIDE.md) | 完整使用教程 |
| [`guides/STARTUP_GUIDE.md`](guides/STARTUP_GUIDE.md) | 启动和故障处理步骤 |
| [`guides/QUICK_REFERENCE.md`](guides/QUICK_REFERENCE.md) | 常用模块、配置和命令速查 |

## 架构与边界

| 文档 | 用途 |
| --- | --- |
| [`architecture/PROJECT_STRUCTURE.md`](architecture/PROJECT_STRUCTURE.md) | 当前仓库目录和入口文件职责 |
| [`architecture/ARCHITECTURE.md`](architecture/ARCHITECTURE.md) | 系统总体架构和请求流程 |
| [`architecture/EXECUTION_MAP.md`](architecture/EXECUTION_MAP.md) | 入口、上下文、记忆、工具和记录的唯一职责映射 |
| [`architecture/ARCHITECTURE_DIAGRAM.md`](architecture/ARCHITECTURE_DIAGRAM.md) | 详细架构图谱 |
| [`operations/CURRENT_STATUS.md`](operations/CURRENT_STATUS.md) | 已验证边界和待完成外部验证 |
| [`architecture/DSH_ALIGNMENT.md`](architecture/DSH_ALIGNMENT.md) | 运行时、插件和配置分层对齐说明 |
| [`architecture/MEMORY_EVOLUTION_DESIGN.md`](architecture/MEMORY_EVOLUTION_DESIGN.md) | 记忆和自进化设计 |
| [`architecture/MULTI_TENANT_ARCHITECTURE.md`](architecture/MULTI_TENANT_ARCHITECTURE.md) | 多租户隔离设计 |
| [`guides/MULTI_TENANT_USAGE_GUIDE.md`](guides/MULTI_TENANT_USAGE_GUIDE.md) | 多租户使用和接入说明 |

## 运维与质量

| 文档 | 用途 |
| --- | --- |
| [`operations/DEPLOY.md`](operations/DEPLOY.md) | Compose 快速部署和安全要求 |
| [`operations/DEPLOYMENT_GUIDE.md`](operations/DEPLOYMENT_GUIDE.md) | 部署文档入口和当前文档分流 |
| [`operations/API_GATEWAY.md`](operations/API_GATEWAY.md) | API 网关边界和身份头 |
| [`operations/API_GATEWAY_DOCUMENTATION.md`](operations/API_GATEWAY_DOCUMENTATION.md) | API 请求、错误码和审批契约 |
| [`operations/QUALITY_GATES.md`](operations/QUALITY_GATES.md) | 测试分层和质量门禁 |
| [`operations/DEPENDENCY_PROFILES.md`](operations/DEPENDENCY_PROFILES.md) | 本地、API、开发和生产依赖 |
| [`operations/PRODUCTION_MODERNIZATION.md`](operations/PRODUCTION_MODERNIZATION.md) | 缓存、RLS、向量和可观测改造 |
| [`operations/TROUBLESHOOTING.md`](operations/TROUBLESHOOTING.md) | 常见故障排查 |

## 集成与功能

| 文档 | 用途 |
| --- | --- |
| [`guides/OFFLINE_FALLBACK.md`](guides/OFFLINE_FALLBACK.md) | 离线模式和回退策略 |
| [`integrations/MINERU_INTEGRATION.md`](integrations/MINERU_INTEGRATION.md) | MinerU 文档解析集成 |
| [`integrations/LOCAL_MCP_GUIDE.md`](integrations/LOCAL_MCP_GUIDE.md) | 本地 MCP 工具 |
| [`integrations/LANGCHAIN_INTEGRATION.md`](integrations/LANGCHAIN_INTEGRATION.md) | LangChain 适配边界 |
| [`integrations/LANGGRAPH_COORDINATION.md`](integrations/LANGGRAPH_COORDINATION.md) | LangGraph 协作流程 |
| [`integrations/PLUGIN_SYSTEM.md`](integrations/PLUGIN_SYSTEM.md) | 插件系统概览 |
| [`dev/PLUGIN_DEVELOPMENT_GUIDE.md`](dev/PLUGIN_DEVELOPMENT_GUIDE.md) | 插件开发流程 |
| [`integrations/TENCENTDB_AGENT_MEMORY.md`](integrations/TENCENTDB_AGENT_MEMORY.md) | TencentDB Agent Memory 集成 |
| [`guides/UNLIMITED_OCR_GUIDE.md`](guides/UNLIMITED_OCR_GUIDE.md) | OCR 能力和运行配置 |
| [`guides/UI_DESIGN_SYSTEM.md`](guides/UI_DESIGN_SYSTEM.md) | UI 设计约束 |
| [`guides/UI_OPTIMIZATION_GUIDE.md`](guides/UI_OPTIMIZATION_GUIDE.md) | UI 优化说明 |

## 开发文档

| 文档 | 用途 |
| --- | --- |
| [`dev/AGENTS.md`](dev/AGENTS.md) | Agent 开发协作规范 |
| [`dev/alembic_setup_guide.md`](dev/alembic_setup_guide.md) | 数据库迁移开发 |
| [`dev/type_safety_guide.md`](dev/type_safety_guide.md) | 类型安全指南 |
| [`dev/CODE_REVIEW_REPORT.md`](dev/CODE_REVIEW_REPORT.md) | 代码审查基线 |
| [`dev/CODE_REVIEW_FOLLOWUP.md`](dev/CODE_REVIEW_FOLLOWUP.md) | 代码审查跟进 |
| [`dev/TECHNICAL_DEBT_TASKS.md`](dev/TECHNICAL_DEBT_TASKS.md) | 技术债务和工程任务 |

## 分析、报告与路线图

- [`analysis/`](analysis/)：深度分析、项目诊断和数据依据。
- [`reports/`](reports/)：阶段性实施报告和问题修复报告。
- [`roadmaps/`](roadmaps/)：路线图、下一步和阶段性规划。

历史修复记录和已过期方案统一放在 [`archive/`](archive/)，归档内容不作为当前实现契约。

## 目录约定

```text
docs/
├── INDEX.md                 # 本索引
├── architecture/            # 架构专题和图谱
├── analysis/                # 分析报告
├── reports/                 # 实施和修复报告
├── roadmaps/                # 路线图和后续计划
├── guides/                  # 用户、启动和速查
├── integrations/            # 外部服务和框架集成
├── operations/              # 部署、API、质量和故障排查
├── dev/                     # 开发规范、审查和技术债
├── mcp/                     # MCP 专题文档
└── archive/                 # 历史文档，只读参考
```

文档命名优先使用英文和明确主题；报告、路线图和临时记录应带日期或放入 `archive/`。
更新目录后同步维护本索引和受影响文档的相对链接。
