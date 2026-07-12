# ArtPM Agent

面向游戏美术外包项目管理的 Streamlit 助手，支持离线利润测算、任务分配、进度预警、报价单解析和本地文件分析。通用自然语言对话需要有效的 LLM API Key。

## 快速启动

Windows：

```bat
start.bat
```

手动启动：

```powershell
python -m pip install -r requirements.txt
python -m streamlit run artpm_agent/app.py --server.address 127.0.0.1
```

访问 `http://localhost:8501`。

## 核心能力

| 能力 | 实现状态 |
|---|---|
| 利润、管理费、税费和风险测算 | 可离线使用 |
| 基于技能、经验和当前负载的任务分配 | 可离线使用 |
| 从业务数据库读取项目并进行截止日期预警 | 可离线使用 |
| 生成提醒及可选企业微信 Webhook 投递 | 可离线生成，投递需配置 |
| Excel `.xlsx/.xls` 报价单解析 | 可用 |
| TXT、CSV、JSON、Excel、PDF 文件读取与分析 | 可用 |
| 文件搜索、内容搜索、趋势分析、项目评估 | 可用 |
| 通用 AI 对话 | 需要 OpenAI、Anthropic 或智谱 Key |

## 配置

```powershell
Copy-Item .env.example .env
```

关键配置：

```env
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=

DB_PATH=./data/artpm.db
MEMORY_DB_PATH=./data/memory.db
VECTOR_DB_PATH=./data/vector_store
```

占位或空 API Key 会进入离线模式，不会发起无效网络请求。

远程 Skills Forge 是可选能力，只有同时配置 `MCP_ENABLED=true`、`SKILLS_FORGE_URL` 和有效 Key 时才启用。本地文件工具不依赖远程服务。命令执行默认关闭，需显式设置 `MCP_ALLOW_COMMANDS=true`。

## 架构

```text
artpm_agent/app.py                 Streamlit 界面
artpm_agent/agent.py               对话与确定性意图路由
artpm_agent/skills/                业务 Skill 和本地 MCP Skill
artpm_agent/parsers/               Excel 与可选 OCR 解析
artpm_agent/database/models.py     SQLAlchemy 业务数据
artpm_agent/memory/                文档记忆与离线检索
artpm_agent/core/                  工具客户端和可选基础设施
```

业务库和记忆库必须分离：`artpm.db` 用于项目、任务和人员，`memory.db` 用于文档记忆。代码不会在 schema 不匹配时自动删表。

## 测试与检查

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
ruff check artpm_agent tests
python artpm_agent/health_check.py
```

OCR、FAISS 和标准 MCP SDK 是可选依赖，未安装时核心应用保持可用并明确报告降级状态。

实际审计结果和后续策略见 [OPTIMIZATION_REPORT_20260711_ACTUAL.md](OPTIMIZATION_REPORT_20260711_ACTUAL.md)。
