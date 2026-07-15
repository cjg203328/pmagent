# 离线降级行为说明（Offline Fallback）

本文说明 ArtPM Agent 在**无 LLM API 密钥**时的行为，便于部署与排障。
无密钥并不等于不可用——技能路由、数据库、向量检索、文件解析、MCP 本地工具
均可正常工作。

---

## 1. 快速验证离线模式

### 1.1 启动验证

```bash
# Windows
start.bat

# 或手动启动
python -m streamlit run artpm_agent/app.py --server.address 127.0.0.1
```

查看日志输出，若看到：
```
进入离线模式 - Skill路由正常工作,对话需要API密钥
```
表示系统已进入离线模式，核心功能可用。

### 1.2 健康检查

```bash
python artpm_agent/health_check.py
```

预期输出：
```json
{
  "status": "degraded",
  "llm": {"status": "offline", "reason": "No API key configured"},
  "vector_store": {"status": "healthy"},
  "database": {"status": "healthy"}
}
```

`degraded` 状态表示系统可用但功能受限（离线模式），**不是** `unhealthy`。

---

## 2. 离线可用功能矩阵

| 功能 | 离线可用 | 说明 |
|------|---------|------|
| **利润测算** | ✅ | 报价/成本/利润/税费/风险计算 |
| **任务分配** | ✅ | 基于技能/经验/负载智能分配 |
| **进度预警** | ✅ | 从业务数据库读取项目并检查截止日期 |
| **催办提醒** | ✅ | 生成提醒（投递企业微信需配置 Webhook） |
| **Excel 解析** | ✅ | `.xlsx/.xls` 报价单解析 |
| **文档解析** | ✅ | TXT/CSV/JSON/Excel/PDF 读取与分析 |
| **文件搜索** | ✅ | MCP 本地工具（`file_search`） |
| **内容搜索** | ✅ | MCP 本地工具（`content_search`） |
| **项目评估** | ✅ | MCP 本地工具（`project_evaluator`） |
| **向量检索** | ✅ | FAISS 本地索引 + 离线 hash embedding |
| **数据库查询** | ✅ | SQLite 业务数据（项目/任务/人员） |
| **智能对话** | ❌ | 需 LLM API 密钥 |
| **意图分类（LLM 层）** | ❌ | 关键词+embedding 层仍可用 |
| **图片 OCR** | ⚠️ | 需 PaddleOCR 和 UnlimitedOCR 配置 |
| **远程 MCP Skills Forge** | ❌ | 需网络和配置 |

**图例**：✅ 完全可用 | ⚠️ 可选依赖 | ❌ 需外部服务

---

## 3. 离线功能使用示例

### 3.1 利润测算

**输入**：
```
报价12万成本8万帮我算利润
```

**预期行为**：
- 意图路由 → `quote_calculator` 技能（关键词层命中）
- 提取参数：`quote_amount=120000`, `cost=80000`
- 计算利润、税费、管理费、风险储备
- 返回结构化报告

**无需 API 密钥**。

### 3.2 任务分配

**输入**：
```
分配建模任务给团队成员
```

**预期行为**：
- 意图路由 → `task_allocator` 技能（关键词层命中）
- 从数据库查询团队成员技能和当前负载
- 基于匹配度和负载均衡算法分配
- 返回分配建议

**无需 API 密钥**。

### 3.3 进度检查

**输入**：
```
检查项目进度有没有卡住的
```

**预期行为**：
- 意图路由 → `progress_tracker` 技能
- 查询数据库中的项目和任务状态
- 识别超期任务和风险项目
- 生成预警报告

**无需 API 密钥**。

### 3.4 文档解析

**操作**：上传 Excel 报价单 + 输入「解析这份报价单」

**预期行为**：
- 识别 Excel 文件
- 调用 `ExcelQuoteParser` 解析
- 提取表头、项目清单、价格
- 返回结构化数据

**无需 API 密钥**。

### 3.5 智能对话（需密钥）

**输入**：
```
这个项目的风险点在哪？
```

**预期行为（离线模式）**：
```
⚠️ LLM 未配置 — 请设置 API 密钥以启用智能对话。

当前可用功能（离线模式）：
• 💰 利润计算 — 输入「报价X万成本Y万帮我算利润」
• 📋 任务分配 — 输入「分配XX任务」
• 📊 进度检查 — 输入「检查项目进度」
• 📨 催办提醒 — 输入「催一下进度」
• 📄 文档解析 — 输入「解析报价单」

配置方式：编辑 `.env` 文件，设置 `ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY`。
```

---

## 4. 技能路由机制（离线优先）

### 4.1 三层路由

```
用户输入
  ↓
关键词层（离线，精确匹配）
  ↓ 未命中
Embedding 层（离线，hash 向量相似度）
  ↓ 未命中
LLM 分类层（需 API，可选）
  ↓ 未命中
模型对话（需 API）
```

### 4.2 离线覆盖能力

- **关键词层**：完全离线，`INTENT_KEYWORDS` 定义 18 个技能的关键词
- **Embedding 层**：使用 `DeterministicEmbeddingProvider` 的离线 hash 向量（SHA-256）
  - 无 API 调用
  - 相似度阈值：0.30-0.40（可配置）
- **LLM 分类层**：仅当 `llm.intent_classification_enabled=true` 且有密钥时启用

**实测**：常见业务意图（报价/分配/进度/催办/解析）在关键词层即可命中，
无需 embedding 或 LLM。

### 4.3 置信度门槛

| 层级 | 条件 | 直接放行 |
|------|------|---------|
| 关键词 | action+entity 双命中 | ✅ |
| 关键词 | kw_score ≥ 4 | ✅ |
| Embedding | sim ≥ 0.40 | ✅ |
| Embedding | sim ≥ 0.30 且有 entity | ✅ |
| LLM | action OR entity | ✅ |

---

## 5. 从离线切换到在线

### 5.1 配置 API 密钥

编辑 `.env` 文件：

```bash
# Anthropic（推荐）
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=sk-ant-...

# 或 OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-proj-...

# 或智谱
LLM_PROVIDER=zhipu
LLM_MODEL=glm-4
ZHIPU_API_KEY=...
```

### 5.2 重启应用

```bash
# Windows
start.bat

# 或手动
python -m streamlit run artpm_agent/app.py
```

### 5.3 验证在线模式

查看日志输出：
```
LLM客户端已就绪
```

在聊天界面输入：
```
你是谁？
```

预期响应：
```
我是 ArtPM 智能助手，当前身份是游戏美术资产项目管理智能体...
当前对话模型：`claude-3-5-sonnet-20241022`。我可以协助...
```

---

## 6. 模型故障转移（有密钥时）

### 6.1 回退策略

主模型请求失败时，按以下顺序尝试备用模型：

**标准模型**（`_fallback_model_ids`）：
1. 主模型（如 `claude-3-5-sonnet-20241022`）
2. `gpt-4o`
3. `gpt-3.5-turbo`
4. `glm-4`

**视觉模型**（`_vision_fallback_model_ids`）：
1. 主视觉模型（如 `gpt-4o`）
2. `claude-3-5-sonnet-20241022`
3. `gemini-pro-vision`

### 6.2 OCR 透明降级

若所有模型失败但文档 OCR 已产出本地结果，返回该 OCR 文本作为**透明降级**：

```
生成模型暂时不可用，已保留附件提取结果。以下内容可继续分析：

[OCR 提取的文本...]
```

这避免了丢失用户的数据，即使模型不可用。

### 6.3 冷却时间

模型故障后进入 60 秒冷却期（`MODEL_FAILOVER_COOLDOWN_SECONDS`），
避免频繁重试导致的 API 限流。

---

## 7. 常见问题排查

### 7.1 启动时报错「LLM不可用」

**症状**：日志显示 `LLM不可用: ...`

**原因**：
- API 密钥未配置或无效
- 网络无法访问 API 端点
- 依赖包缺失（如 `openai`/`anthropic`）

**解决**：
1. 检查 `.env` 文件是否存在且正确配置
2. 验证 API 密钥有效性（可用 `curl` 测试）
3. 检查网络和代理设置
4. 安装缺失的依赖：`pip install openai anthropic`

**注意**：这不是致命错误，系统仍可在离线模式下工作。

### 7.2 技能路由失败

**症状**：输入「报价12万成本8万帮我算利润」无响应或返回通用对话

**诊断**：
```python
from artpm_agent.agent import ArtPMAgent
agent = ArtPMAgent()
intent = agent._detect_intent("报价12万成本8万帮我算利润")
print(f"Detected intent: {intent}")
```

**预期输出**：`Detected intent: quote_calculator`

**可能原因**：
- 意图路由被禁用
- 技能未注册
- 输入语句不匹配关键词

**解决**：检查 `config.json` 中的 `intent_router.enabled`。

### 7.3 向量检索失败

**症状**：知识库搜索报错或返回空结果

**诊断**：
```bash
python -c "
from artpm_agent.memory import create_embedding_provider
provider = create_embedding_provider({})
vec = provider.get_embedding('测试文本')
print(f'Embedding dim: {len(vec)}')
"
```

**预期输出**：`Embedding dim: 512`（离线 hash 向量）

**可能原因**：
- FAISS 未安装：`pip install faiss-cpu`
- 向量数据库路径不存在
- 权限问题

**解决**：
1. 安装 FAISS：`pip install faiss-cpu`
2. 检查 `data/vector_store/` 目录权限
3. 重建索引：删除 `data/vector_store/` 后重启

### 7.4 数据库错误

**症状**：`No such table: projects`

**诊断**：
```bash
python -c "
import sqlite3
conn = sqlite3.connect('data/artpm.db')
cursor = conn.cursor()
cursor.execute('SELECT name FROM sqlite_master WHERE type=\"table\"')
print('Tables:', [row[0] for row in cursor.fetchall()])
conn.close()
"
```

**解决**：运行 Alembic 迁移
```bash
python -m alembic upgrade head
```

---

## 8. 部署建议

### 8.1 内网离线部署

**场景**：企业内网环境，无法访问公网 LLM API

**方案**：
1. 部署 ArtPM Agent 到内网服务器
2. 不配置 API 密钥（离线模式）
3. 配置企业微信 Webhook（可选，内网可达）
4. 业务数据和向量索引本地存储

**可用功能**：利润测算、任务分配、进度预警、文档解析、数据查询

**限制**：无智能对话、无意图 LLM 分类层

### 8.2 混合部署

**场景**：PM 在办公室使用在线模式，外出时使用离线模式

**方案**：
1. 桌面应用部署（Windows/Mac）
2. `.env` 配置 API 密钥（在线模式）
3. 网络不可用时自动降级到离线模式
4. 数据库和向量索引同步到云存储

**优势**：无缝切换，离线时核心功能仍可用

### 8.3 容器部署

**Dockerfile 配置**：

```dockerfile
FROM python:3.10-slim

# 只安装核心依赖，OCR 可选
RUN pip install --no-cache-dir streamlit pandas faiss-cpu sqlalchemy

# 离线模式：不配置 API 密钥
ENV LLM_PROVIDER=""
ENV ANTHROPIC_API_KEY=""

COPY artpm_agent/ /app/artpm_agent/
WORKDIR /app

CMD ["streamlit", "run", "artpm_agent/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

**注意**：容器内无需 API 密钥也可运行核心业务功能。

---

## 9. 性能对比

| 指标 | 离线模式 | 在线模式 |
|------|---------|---------|
| **启动时间** | ~2s | ~2.5s |
| **意图路由延迟** | <10ms（关键词/embedding） | <10ms（前两层）+ 500-2000ms（LLM 层） |
| **技能执行延迟** | 50-200ms（本地计算） | 50-200ms（技能本身） |
| **对话延迟** | N/A（不可用） | 1-5s（模型 API） |
| **吞吐量** | 无限制（本地） | 受 API 限流约束 |
| **成本** | $0 | $0.003-0.06/1K tokens |

**结论**：离线模式适合高频、确定性业务操作（测算/分配/预警），
在线模式适合需要语义理解和生成的场景。

---

## 10. 外部依赖清单

| 依赖 | 离线可用 | 必需/可选 | 说明 |
|------|---------|----------|------|
| **SQLite** | ✅ | 必需 | 业务数据库（`data/artpm.db`） |
| **FAISS** | ✅ | 必需 | 向量索引（`data/vector_store/`） |
| **Streamlit** | ✅ | 必需 | Web UI |
| **Pandas/NumPy** | ✅ | 必需 | 数据处理 |
| **OpenPyXL/xlrd** | ✅ | 必需 | Excel 解析 |
| **pdfplumber** | ✅ | 必需 | PDF 解析 |
| **Anthropic API** | ❌ | 可选 | LLM 对话 |
| **OpenAI API** | ❌ | 可选 | LLM 对话 |
| **智谱 API** | ❌ | 可选 | LLM 对话 |
| **PaddleOCR** | ⚠️ | 可选 | 图片 OCR（重依赖） |
| **UnlimitedOCR** | ⚠️ | 可选 | 远程 OCR 服务 |
| **MCP Skills Forge** | ❌ | 可选 | 远程工具服务 |
| **企业微信 Webhook** | ⚠️ | 可选 | 提醒投递 |

**图例**：✅ 本地离线 | ⚠️ 可选配置 | ❌ 需外部服务

---

## 11. 总结

ArtPM Agent 采用**离线优先**设计：

1. **核心业务功能无需 API 密钥**：利润测算、任务分配、进度预警、文档解析全部离线工作
2. **渐进式在线增强**：配置 API 密钥后解锁智能对话和高级语义理解
3. **透明降级**：模型故障时自动回退到离线结果，不丢失用户数据
4. **零成本运行**：离线模式无 API 调用成本，适合高频操作

**适用场景**：
- 内网离线部署
- 外出移动办公
- API 预算受限
- 对延迟敏感的操作

**升级路径**：
- 配置 API 密钥 → 启用智能对话
- 配置企业微信 → 启用提醒投递
- 安装 PaddleOCR → 启用图片 OCR
