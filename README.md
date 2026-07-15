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

> 💡 **离线优先设计**：核心业务功能无需 API 密钥即可使用。[查看完整离线模式指南 →](OFFLINE_FALLBACK.md)

| 能力 | 实现状态 | 离线可用 |
|---|---|---|
| 利润、管理费、税费和风险测算 | ✅ | ✅ |
| 基于技能、经验和当前负载的任务分配 | ✅ | ✅ |
| 从业务数据库读取项目并进行截止日期预警 | ✅ | ✅ |
| 生成提醒及可选企业微信 Webhook 投递 | ✅ | ✅ 生成 / ⚠️ 投递需配置 |
| Excel `.xlsx/.xls` 报价单解析 | ✅ | ✅ |
| TXT、CSV、JSON、Excel、PDF 文件读取与分析 | ✅ | ✅ |
| 文件搜索、内容搜索、趋势分析、项目评估 | ✅ | ✅ |
| 通用 AI 对话 | ✅ | ❌ 需要 API Key |

## 配置

```powershell
Copy-Item .env.example .env
```

关键配置：

```env
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=
AGENT_MODEL_TOOL_CALLS_ENABLED=true

DB_PATH=./data/artpm.db
MEMORY_DB_PATH=./data/memory.db
VECTOR_DB_PATH=./data/vector_store
```

占位或空 API Key 会进入离线模式，不会发起无效网络请求。
模型驱动工具调用默认开启；模型参数会先经过 JSON Schema 校验，写入型工具仍需宿主显式审批。需要紧急回滚时，可设置 `AGENT_MODEL_TOOL_CALLS_ENABLED=false`。

远程 Skills Forge 是可选能力，只有同时配置 `MCP_ENABLED=true`、`SKILLS_FORGE_URL` 和有效 Key 时才启用。本地文件工具不依赖远程服务。命令执行默认关闭，需显式设置 `MCP_ALLOW_COMMANDS=true`。

## 离线模式

**ArtPM Agent 采用离线优先设计**，核心业务功能无需 API 密钥：

```bash
# 无需配置 API Key 即可运行
python -m streamlit run artpm_agent/app.py
```

### 离线可用功能

- ✅ 利润测算：报价/成本/利润/税费/风险计算
- ✅ 任务分配：基于技能/经验/负载智能分配
- ✅ 进度预警：从业务数据库读取项目并检查截止日期
- ✅ 文档解析：Excel/PDF/TXT/CSV/JSON 本地解析
- ✅ 文件工具：本地文件搜索/内容搜索/项目评估（MCP）
- ✅ 数据查询：SQLite 业务数据和 FAISS 向量检索

### 使用示例

离线模式下，直接输入业务指令：

```
报价12万成本8万帮我算利润
分配建模任务给团队成员
检查项目进度有没有卡住的
解析这份报价单（上传 Excel 附件）
```

系统会自动路由到对应技能并返回结果，**无需任何 API 调用**。

### 在线增强

配置 API 密钥后解锁智能对话和高级语义理解：

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

详见 [完整离线模式指南](OFFLINE_FALLBACK.md)。

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

OCR 和标准 MCP SDK 是可选依赖；FAISS 是知识库向量检索的核心依赖，未加载时应用会明确报告检索降级状态。

实际审计结果和后续策略见 [OPTIMIZATION_REPORT_20260711_ACTUAL.md](OPTIMIZATION_REPORT_20260711_ACTUAL.md)。
