# ArtPM Agent 源码包

项目的启动、配置、架构和测试说明统一位于仓库根目录：
[`README.md`](../README.md)。本文件只说明包内职责，避免在源码目录重复维护产品文档。

## 入口

- `app.py`：Streamlit Web 应用入口。
- `main.py`：命令行入口。
- `api/`：FastAPI 网关及 API 模型。
- `agent.py`：兼容 facade 和业务 Agent 组装。
- `harness/`、`runtime/`：请求主链、回合运行时和工具流水线。

## 主要模块

- `skills/`：业务技能和技能路由。
- `memory/`：会话、记忆、向量和 workspace 知识存储。
- `providers/`：模型 provider、故障转移、结构化输出和响应缓存。
- `database/`：数据库模型、连接和迁移适配。
- `security/`、`tenancy/`：权限审批和租户上下文。
- `views/`：Streamlit 页面；`ui_*.py`：共享 UI 状态、样式和反馈。
- `core/`、`utils/`：基础设施客户端和通用工具。

新增业务代码应进入对应子包，不要在包根目录新增产品文档、脚本或运行产物。
历史说明保存在 [`docs/archive/`](../docs/archive/)，当前文档入口见
[`docs/INDEX.md`](../docs/INDEX.md)。
