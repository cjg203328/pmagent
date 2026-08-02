# ArtPM Agent 项目全面分析

**分析日期**: 2026-07-22  
**项目版本**: v0.2.0  
**代码规模**: ~57k 行 Python 代码  
**核心定位**: 面向游戏美术外包项目管理的 AI 助手（离线优先设计）

---

## 1. 项目概览

### 1.1 核心价值主张

ArtPM Agent 是一个专为游戏美术外包行业设计的智能项目管理助手，具有以下独特优势：

1. **离线优先设计**: 核心业务功能无需 API 密钥即可运行
2. **垂直领域深度**: 针对美术外包特定场景的专业工具集
3. **多模态文档处理**: 支持复杂的业务文档解析（报价单、排期表、反馈单）
4. **混合架构**: 结合确定性规则路由与 LLM 智能对话
5. **企业级可观测**: 内置 Token 消耗追踪和连接健康监控

### 1.2 技术栈架构

**前端层**:
- Streamlit 1.59.0 - Web UI 框架
- Plotly + Kaleido - 数据可视化
- streamlit-option-menu - 导航组件

**AI 层**:
- LangChain 1.3.14 - 模型编排框架
- LangGraph 1.2.9 - 多智能体任务协调
- OpenAI + Anthropic SDK - 多 Provider 支持
- 自研 ModelGateway - 智能故障转移

**文档处理层**:
- MinerU 3.4.4 - 可选的多模态文档转换引擎
- PaddleOCR - 可选的 OCR 能力
- openpyxl/xlrd - Excel 解析
- pdfplumber + PyMuPDF - PDF 处理
- python-docx - Word 文档

**数据层**:
- SQLAlchemy 2.0 - ORM 框架
- SQLite - 业务数据库 (artpm.db) + 记忆库 (memory.db) + 遥测库 (telemetry.db)
- FAISS - 向量检索 (faiss-cpu)
- Redis - 可选的缓存加速层

**集成层**:
- MCP 1.12.0 - Model Context Protocol 标准客户端
- FastAPI 0.135.1 + Uvicorn - REST API Gateway
- 自研插件系统 - Python 技能扩展机制

---

## 2. 核心架构设计

### 2.1 分层架构

```
┌─────────────────────────────────────────────────┐
│          Streamlit UI (app.py)                  │
│     views/chat.py  views/settings.py            │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│          Agent 层 (agent.py)                    │
│  - 意图识别与路由                                │
│  - 对话历史管理                                  │
│  - 审批门控                                      │
└─────────────────┬───────────────────────────────┘
                  │
      ┌───────────┼───────────┐
      │           │           │
┌─────▼─────┐ ┌──▼────┐ ┌───▼──────┐
│  Skills   │ │Runtime│ │ Harness  │
│  Router   │ │ Loop  │ │ Services │
└───────────┘ └───────┘ └──────────┘
      │           │           │
┌─────▼───────────▼───────────▼─────┐
│      Provider Gateway              │
│  - ModelGateway (故障转移)         │
│  - StructuredProviderGateway       │
│  - ResponseCache (Redis/Local)     │
└─────────┬──────────────────────────┘
          │
┌─────────▼──────────────────────────┐
│    基础设施层                       │
│  - Database (业务数据)              │
│  - Memory (向量+记忆)               │
│  - Telemetry (可观测)               │
│  - MCP Client (外部工具)            │
└────────────────────────────────────┘
```

### 2.2 核心模块解析

#### 2.2.1 智能路由系统

**混合路由策略** ([artpm_agent/routing/](artpm_agent/routing/)):
1. **确定性意图识别**: 使用关键词匹配快速响应高频查询
   - 问候/身份查询 → 直接回复模板
   - 能力查询 → 返回技能列表
   - 本地快捷指令 → 直接执行

2. **Skill 路由**: 业务功能映射到专用技能
   - 利润测算 (SmartTaskAllocator)
   - 任务分配 (SmartProgressTracker)
   - 文档解析 (DocumentClassifierParser)
   - 质量控制、需求评估、成本控制等 9 个内置技能

3. **LangGraph 任务编排**: 复杂多步任务的并行协调

4. **LLM Fallback**: 无法匹配技能时退回通用对话

#### 2.2.2 Agent Runtime 系统

**核心组件** ([artpm_agent/runtime/](artpm_agent/runtime/)):

```python
# 低级别的模型调用循环
AgentLoop:
  - 管理多轮对话上下文
  - 工具调用执行与审批
  - 协作取消机制
  
# 高级别的运行时环境
AgentRuntime:
  - 事件发布/订阅
  - 消息历史管理
  - 能力注册表

# 业务集成层
TurnService (harness/turn_service.py):
  - Profile 提案检测
  - Knowledge 导入检测
  - Artifact 生成匹配
  - Skill 路由编排
  - 模型 Fallback 决策
```

**设计亮点**:
- **分离关注点**: 低级循环不知道 Streamlit/数据库/业务规则
- **可测试性**: 纯数据流输入输出，易于单元测试
- **可扩展性**: 通过钩子函数注入自定义逻辑

#### 2.2.3 Provider Gateway

**智能故障转移架构** ([artpm_agent/providers/gateway.py](artpm_agent/providers/gateway.py)):

```python
ModelGateway:
  核心职责:
  1. 模型选择与健康检查
  2. 熔断器冷却时间管理 (60s)
  3. 跨 Provider 故障转移
  4. 视觉能力检测
  
  故障转移策略:
  - 候选模型必须来自同一 Provider
  - 失败模型进入冷却期
  - 记录每次切换的遥测数据
  
  问题修复历史:
  - 2024-07 修复跨 Provider 切换导致认证失败
  - 添加配置检查工具 (artpm_agent/tools/check_config.py)
```

#### 2.2.4 Memory 系统

**多层记忆架构** ([artpm_agent/memory/](artpm_agent/memory/)):

```
MemoryManager (memory_manager.py)
├── SQLiteManager: 结构化长期记忆
│   ├── documents 表: 文档元数据
│   ├── conversations 表: 对话历史
│   └── feedback 表: 用户反馈
│
├── VectorStore: 语义检索
│   ├── FAISS 索引 (1536 维)
│   ├── AdaptiveVectorStore: 动态维度调整
│   └── local_feature_hash: 本地嵌入方案
│
├── SessionStore: 会话管理
├── EpisodeStore: 任务片段
└── CrossSessionMemory: 跨会话知识传递
```

**特性**:
- 离线向量化 (DeterministicEmbeddingProvider)
- 自动文档同步到向量索引
- 上下文 Token 预算控制 (MEMORY_CONTEXT_MAX_TOKENS=1200)

#### 2.2.5 技能系统

**内置技能列表** ([artpm_agent/skills/](artpm_agent/skills/)):

| 技能类 | 功能 | 文件 |
|--------|------|------|
| DocumentClassifierParser | 智能文档分类与解析 | skill_router.py |
| SmartTaskAllocator | 任务智能分配 | smart_task_allocator.py |
| SmartProgressTracker | 进度跟踪与预警 | smart_progress_tracker.py |
| QualityControlSkill | 质量管理 | quality_control_skill.py |
| RequirementsAssessmentSkill | 需求评估 | requirements_assessment_skill.py |
| CostControlSkill | 成本控制 | cost_control_skill.py |
| QuoteSchedulingSkill | 排期管理 | quote_scheduling_skill.py |
| ProgressManagementSkill | 进度管理 | progress_management_skill.py |
| DeliverySkill | 交付管理 | delivery_skill.py |
| RetrospectiveSkill | 项目复盘 | retrospective_skill.py |

**MCP 技能集成**:
- 本地文件工具 (file_search, content_search)
- 远程 Skills Forge 集成 (可选)
- 命令执行能力 (默认关闭, MCP_ALLOW_COMMANDS=false)

### 2.3 数据流示意

```
用户输入
  │
  ▼
UI Layer (Streamlit)
  │
  ▼
Intent Recognition
  ├─→ Deterministic Fast Path
  │   └─→ 直接回复 (无 API 调用)
  │
  ├─→ Skill Router
  │   ├─→ 利润测算 (纯计算)
  │   ├─→ 任务分配 (数据库查询)
  │   ├─→ 文档解析 (本地解析)
  │   └─→ MCP Tools (本地工具)
  │
  └─→ LLM Fallback
      ├─→ ModelGateway
      │   ├─→ 主模型
      │   └─→ Fallback 候选
      │
      ├─→ Response Cache (Redis/Local)
      ├─→ Telemetry Recording
      └─→ 返回响应
```

---

## 3. 关键特性深度分析

### 3.1 离线优先设计

**核心理念**: 核心业务功能无需 API 密钥即可运行

**实现机制**:

1. **确定性意图路由**: 无需 LLM 即可识别常见指令
   ```python
   # artpm_agent/utils/chat_intent.py
   is_local_fast_intent()  # 关键词匹配
   is_exact_greeting()     # 问候检测
   is_capability_query()   # 能力查询
   ```

2. **本地技能执行**: 纯计算或数据库操作
   ```python
   # 利润测算 (无 API 调用)
   revenue - cost - management_fee - tax - risk_reserve
   
   # 任务分配 (SQLite 查询)
   SELECT * FROM tasks WHERE status='pending'
   ```

3. **本地文档解析器**:
   - Excel: openpyxl/xlrd
   - PDF: pdfplumber + PyMuPDF
   - 图片: Pillow
   - 可选 MinerU: 重量级多模态引擎

4. **本地向量化**: 无需调用 OpenAI Embeddings API
   ```python
   DeterministicEmbeddingProvider
   └─→ TF-IDF + 哈希降维 → 1536 维向量
   ```

**离线可用功能清单**:
- ✅ 利润/管理费/税费/风险测算
- ✅ 任务分配 (技能/经验/负载)
- ✅ 进度预警 (截止日期检查)
- ✅ Excel 报价单解析
- ✅ 文件搜索/内容搜索
- ✅ 本地知识库检索
- ❌ 通用 AI 对话 (需要 API Key)

### 3.2 多模态文档处理

**分层处理策略**:

```
用户上传文档
  │
  ▼
DocumentClassifierParser.execute()
  │
  ├─→ 尝试 MinerU (可选高级引擎)
  │   ├─ 支持: PDF/图片/DOCX/PPTX/XLSX
  │   ├─ 模式: auto/txt/ocr
  │   ├─ 能力: 公式/表格/图像分析
  │   └─→ 成功 → 返回 Markdown + JSON
  │       失败 ↓
  │
  ├─→ 回退到本地解析器
  │   ├─ Excel: ExcelQuoteParser
  │   ├─ PDF: pdfplumber 提取文本
  │   ├─ 图片: PaddleOCR (可选)
  │   └─ TXT/CSV/JSON: 直接读取
  │
  └─→ 返回统一结构
      {
        "success": true,
        "document_type": "报价单",
        "extracted_data": {...},
        "raw_text": "...",
        "confidence": 0.95
      }
```

**MinerU 集成特点**:
- **可选依赖**: 未安装时自动回退
- **多模式**: auto (智能选择) / local (本地 CLI) / remote (HTTP API)
- **企业级配置**: 超时/大小限制/缓存/安全控制

### 3.3 可观测系统

**设计哲学**: 只读、非侵入、Best-Effort

**架构** ([artpm_agent/runtime/telemetry.py](artpm_agent/runtime/telemetry.py)):

```
TelemetryRecorder
├── Token 消耗追踪
│   ├─ prompt_tokens
│   ├─ completion_tokens
│   ├─ cached_tokens
│   └─ estimated_cost_usd
│
├── 连接事件记录
│   ├─ 每次 Failover 尝试
│   ├─ HTTP 状态码
│   ├─ 错误类型
│   └─ 响应延迟
│
└── 熔断器状态
    └─ 冷却期倒计时
```

**成本估算逻辑** ([artpm_agent/runtime/pricing.py](artpm_agent/runtime/pricing.py)):
```python
# USD 定价表
MODEL_PRICING = {
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "claude-3-5-sonnet-20241022": {"input": 0.003, "output": 0.015},
    # 缓存命中成本为 0
    # 未知模型按层级回退
}
```

**可观测面板** (views/observability.py):
- Token 消耗趋势图
- 按模型/Provider 成本明细
- 连接成功率与错误分布
- 端点健康度仪表盘

**存储**: `data/telemetry.db` (独立库, WAL 模式)

### 3.4 企业级安全与多租户

**租户隔离** ([artpm_agent/tenancy/](artpm_agent/tenancy/)):
```python
TenantContext:
  - tenant_id: 租户标识
  - workspace_id: 工作空间
  - user_id: 用户身份
  
WorkspaceAccessDenied: 权限拒绝异常
tenant_context_from_host(): 从 HTTP Header 提取租户信息
```

**安全机制**:
1. **API Gateway 认证**:
   ```env
   ARTPM_GATEWAY_SHARED_SECRET=  # 生产必需
   X-Gateway-Token Header        # 请求验证
   ```

2. **Basic Auth** (Caddy 层):
   ```env
   BASIC_AUTH_USER=admin
   BASIC_AUTH_HASH=$2a$14$...  # bcrypt 哈希
   ```

3. **CORS 白名单**:
   ```env
   ARTPM_CORS_ORIGINS=http://127.0.0.1:8501,http://localhost:8501
   ```

4. **命令执行控制**:
   ```env
   MCP_ALLOW_COMMANDS=false       # 默认禁用
   MCP_COMMAND_ALLOWLIST=ls,cat   # 显式白名单
   ```

5. **插件沙箱**:
   ```env
   ARTPM_PLUGINS_ENABLED=false
   ARTPM_PLUGIN_ALLOWLIST=[]      # 显式声明
   # 每个 manifest 必须包含模块 SHA-256
   ```

---

## 4. 项目演进历史

### 4.1 版本里程碑

**v0.1 (早期)**: 基础对话能力  
**v0.2 (2024-06)**: 安全默认 + 单一回合主链 + DATA_ROOT 迁移  

**v0.2.x 优化周期 (2024-07)**:
- Phase 1: 快赢优化 (连接池/懒加载/流式进度)
- Phase 2-5: 四视角全量优化 (性能提升 3-5x)
- 进化闭环: 元记忆 + 策略自适应
- MCP 性能优化: Stdio 常驻 + 市场技能缓存

**最近修复 (2024-07)**:
- fc9d491: 修复对话页模型切换后自动跳转设置页
- ffd7369: 添加对话页模型选择器 + 友好错误提示
- f8975e8: 修复 Failover 跨 Provider 切换认证失败
- 4cf0231: 优化确认回调 (50%+ 响应提升)

### 4.2 架构演进

**阶段 1: 单体 Agent**
```
app.py → agent.py → LLM Client
```

**阶段 2: 技能分离**
```
app.py → agent.py → SkillRouter
                  → LLM Fallback
```

**阶段 3: 运行时解耦** (当前)
```
app.py → TurnService → Runtime/Loop
                     → Harness Handlers
                     → ModelGateway
                     → Skills/MCP
```

**未来方向 (推测)**:
- LangGraph 深度集成 (已有基础设施)
- 工作流持久化 (WorkflowEngine + task_graph)
- 多智能体协同 (agent-team 编排)

---

## 5. 代码质量与工程实践

### 5.1 代码组织

**模块化评分: ⭐⭐⭐⭐☆ (4/5)**

优点:
- 清晰的分层架构
- 关注点分离良好 (UI/业务/基础设施)
- 丰富的文档注释

改进空间:
- 部分模块职责重叠 (editing/ vs evolution/)
- 向后兼容代码较多 (legacy paths)

**目录结构**:
```
artpm_agent/
├── api/              REST Gateway
├── artifacts/        工件生成协调
├── core/             MCP 客户端 + Redis + Token 监控
├── database/         SQLAlchemy 模型
├── editing/          文档编辑工具
├── evolution/        元学习与策略优化
├── harness/          Turn 服务与处理器
├── internal/         内部集成逻辑
├── memory/           记忆系统
├── parsers/          文档解析器
├── presentation/     结果格式化
├── profiles/         Agent 配置文件
├── providers/        模型网关
├── routing/          意图路由
├── runtime/          运行时环境 + 遥测
├── security/         安全审批
├── skills/           业务技能
├── tenancy/          多租户
├── tools/            CLI 工具 (check_config)
├── ui/               UI 优化
├── utils/            工具函数
├── views/            Streamlit 页面
├── visualization/    图表
└── workflows/        任务编排
```

### 5.2 测试覆盖

**测试基础设施**:
```bash
pytest>=8.0.0
pytest-asyncio>=0.23.0
pytest-cov>=4.0.0
```

**覆盖率要求**:
```bash
# 工作流模块: 50%+ 覆盖率门禁
pytest tests/test_workflow_*.py \
  --cov=artpm_agent.workflows \
  --cov-fail-under=50
```

**性能回归门禁**:
```bash
python -m benchmarks.core_performance \
  --samples 30 \
  --enforce  # 性能劣化时失败
```

### 5.3 代码质量工具

```bash
ruff check artpm_agent tests  # Linter
mypy>=1.8.0                   # Type checking
bandit>=1.7.0                 # Security scanning
safety>=3.0.0                 # Dependency auditing
```

### 5.4 配置管理

**配置来源优先级**:
1. 环境变量 (.env)
2. 默认配置 (config_data/default_config.json)
3. 代码硬编码默认值

**关键设计**:
- 占位 API Key 不会触发无效请求
- 配置检查工具 (artpm_agent/tools/check_config.py)
- 启动时自动验证 (start_with_checks.py)

---

## 6. 部署架构

### 6.1 部署模式

**模式 1: 本地开发**
```bash
python -m streamlit run artpm_agent/app.py
# 或
python start_with_checks.py
```

**模式 2: Docker 单容器**
```bash
docker build -t artpm-agent .
docker run -p 8501:8501 artpm-agent
```

**模式 3: Docker Compose 生产栈**
```yaml
services:
  app:
    build: .
    depends_on: [redis]
    environment:
      REDIS_URL: redis://redis:6379/0
      
  redis:
    image: redis:7-alpine
    
  caddy:
    image: caddy:2-alpine
    # 自动 HTTPS + Basic Auth
```

### 6.2 数据持久化

**数据目录**: `./data/`
```
data/
├── artpm.db          业务数据 (项目/任务/人员)
├── memory.db         文档记忆
├── telemetry.db      遥测数据
├── vector_store/     FAISS 索引
└── mineru/           MinerU 输出缓存
```

**备份策略** (scripts/backup_data.py):
```bash
python scripts/backup_data.py
# 自动备份所有数据库和向量索引
```

### 6.3 扩展性考虑

**水平扩展瓶颈**:
- ❌ SQLite 不支持高并发写入
- ❌ FAISS 索引需要内存加载
- ✅ Redis 缓存可分担查询压力
- ✅ MinerU remote 模式可独立扩展

**推荐生产架构** (>100 并发用户):
```
Load Balancer
  │
  ├─→ ArtPM Instance 1 ─┐
  ├─→ ArtPM Instance 2 ─┼─→ Shared Redis
  └─→ ArtPM Instance 3 ─┘
           │
           ├─→ PostgreSQL (替换 SQLite)
           └─→ MinerU API Cluster
```

---

## 7. 技术债务与改进机会

### 7.1 已知技术债

1. **数据库限制**:
   - SQLite 并发写入瓶颈
   - 缺少迁移工具 (已有 Alembic 但未激活)

2. **向后兼容负担**:
   - 多套配置路径 (legacy/new)
   - editing/ 与 evolution/ 功能重叠

3. **测试覆盖不足**:
   - 部分模块缺少单元测试
   - 端到端测试缺失

4. **文档债务**:
   - API Gateway 文档不完整
   - 插件开发指南缺失

### 7.2 性能优化机会

**当前优化成果** (Phase 1-5):
- 启动时间: 2.0s → 0.5s (75% 提升)
- 响应速度: 50%+ 提升 (确认回调优化)
- MCP 调用: 消除 npx 重拉开销

**进一步优化方向**:
1. **向量检索加速**:
   - 考虑 Qdrant/Milvus 替代 FAISS
   - 添加近似最近邻 (ANN) 索引

2. **缓存策略优化**:
   - 响应缓存命中率监控
   - 自适应 TTL 策略

3. **异步 I/O**:
   - 部分同步数据库调用可异步化
   - Streamlit reruns 优化

### 7.3 功能增强方向

1. **AI 能力**:
   - 添加多模态输入 (语音/视频)
   - Fine-tune 专用行业模型
   - Agent-to-Agent 协作 (已有 LangGraph 基础)

2. **业务功能**:
   - 自动报表生成
   - 项目风险预测模型
   - 集成第三方项目管理工具 (Jira/Asana)

3. **用户体验**:
   - 移动端适配
   - 实时协作 (多用户同时编辑)
   - 消息通知系统 (企业微信已有基础)

---

## 8. 竞争力分析

### 8.1 核心优势

1. **垂直领域深度**: 专为美术外包行业设计，非通用聊天机器人
2. **离线优先**: 核心功能无需网络，适合保密项目
3. **企业级工程**: 遥测/多租户/安全控制完备
4. **可扩展架构**: MCP 标准 + 插件系统 + LangGraph

### 8.2 潜在风险

1. **技术栈依赖**: LangChain 生态快速变化，升级成本高
2. **文档处理依赖**: MinerU 为可选依赖，本地解析器功能受限
3. **SQLite 扩展性**: 生产环境需要迁移到 PostgreSQL

### 8.3 市场定位

**目标用户**:
- 游戏美术外包公司 (10-200 人规模)
- 项目经理/制作人
- 财务/运营团队

**替代方案对比**:
- vs. 通用 PM 工具 (Jira/Asana): 更了解美术外包流程
- vs. AI 聊天机器人 (ChatGPT/Claude): 有专用业务技能
- vs. 定制开发: 开箱即用 + 持续更新

---

## 9. 总结与建议

### 9.1 项目健康度评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 代码质量 | ⭐⭐⭐⭐☆ | 架构清晰, 部分技术债 |
| 功能完整性 | ⭐⭐⭐⭐☆ | 核心功能完备, 可扩展 |
| 文档完整性 | ⭐⭐⭐☆☆ | README 详尽, API 文档不足 |
| 测试覆盖 | ⭐⭐⭐☆☆ | 关键模块有测试, 覆盖率待提升 |
| 可维护性 | ⭐⭐⭐⭐☆ | 模块化好, 向后兼容负担略重 |
| 性能表现 | ⭐⭐⭐⭐☆ | 已完成多轮优化, 生产可用 |
| 安全性 | ⭐⭐⭐⭐☆ | 基础安全机制完备 |

**综合评分: 27/35 (77%)**

### 9.2 关键建议

**短期 (1-2 月)**:
1. ✅ 补充 API Gateway 文档
2. ✅ 提升测试覆盖率到 70%+
3. ✅ 清理 editing/ 与 evolution/ 重叠代码
4. ✅ 添加端到端集成测试

**中期 (3-6 月)**:
1. 📋 PostgreSQL 迁移方案 + 数据库迁移工具激活
2. 📋 插件系统文档与示例
3. 📋 性能监控仪表盘增强 (P95/P99 延迟)
4. 📋 移动端 UI 适配

**长期 (6-12 月)**:
1. 🔮 Fine-tune 行业专用模型
2. 🔮 多智能体协作深度场景
3. 🔮 SaaS 化部署 (多租户完全隔离)
4. 🔮 第三方集成市场 (Jira/Slack/Feishu)

### 9.3 最佳实践沉淀

项目中值得借鉴的设计模式:

1. **离线优先架构**: 核心功能不依赖外部 API
2. **渐进式增强**: 可选依赖 (MinerU/OCR/Redis) 降级优雅
3. **Provider 抽象**: ModelGateway 隔离供应商差异
4. **遥测驱动运维**: 内置可观测系统
5. **配置检查前置**: 启动时验证配置完整性
6. **分层测试**: 单元/集成/性能回归三层门禁

---

## 10. 附录

### 10.1 关键文件索引

**核心逻辑**:
- [artpm_agent/agent.py](artpm_agent/agent.py) - Agent 主类
- [artpm_agent/runtime/agent_loop.py](artpm_agent/runtime/agent_loop.py) - 底层循环
- [artpm_agent/harness/turn_service.py](artpm_agent/harness/turn_service.py) - Turn 编排

**技能系统**:
- [artpm_agent/skills/skill_router.py](artpm_agent/skills/skill_router.py) - 路由器
- [artpm_agent/skills/smart_task_allocator.py](artpm_agent/skills/smart_task_allocator.py) - 任务分配
- [artpm_agent/skills/mcp_skills.py](artpm_agent/skills/mcp_skills.py) - MCP 集成

**基础设施**:
- [artpm_agent/providers/gateway.py](artpm_agent/providers/gateway.py) - 模型网关
- [artpm_agent/memory/memory_manager.py](artpm_agent/memory/memory_manager.py) - 记忆管理
- [artpm_agent/runtime/telemetry.py](artpm_agent/runtime/telemetry.py) - 遥测系统

**UI 层**:
- [artpm_agent/app.py](artpm_agent/app.py) - 应用入口
- [artpm_agent/views/chat.py](artpm_agent/views/chat.py) - 对话页
- [artpm_agent/views/observability.py](artpm_agent/views/observability.py) - 可观测面板

### 10.2 环境变量速查

**必需配置**:
```env
LLM_PROVIDER=anthropic|openai
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=  # 留空进入离线模式
DB_PATH=./data/artpm.db
```

**性能优化**:
```env
ENABLE_CONNECTION_POOLING=true
ENABLE_LAZY_SKILL_LOADING=true
ENABLE_STREAMING_PROGRESS=true
ENABLE_ADAPTIVE_VECTORS=true
```

**生产必需**:
```env
ARTPM_ENV=production
ARTPM_GATEWAY_SHARED_SECRET=<random-secret>
BASIC_AUTH_USER=admin
BASIC_AUTH_HASH=<bcrypt-hash>
```

### 10.3 命令速查

```bash
# 启动 (推荐)
python start_with_checks.py

# 配置检查
python -m artpm_agent.tools.check_config

# 测试
pytest -q
pytest --cov=artpm_agent --cov-report=html

# 代码质量
ruff check artpm_agent tests
mypy artpm_agent

# 性能基准
python -m benchmarks.core_performance --samples 30

# API Gateway
python -m artpm_agent.api

# 数据备份
python scripts/backup_data.py

# 健康检查
python health_check.py
```

---

**分析完成日期**: 2026-07-22  
**分析师**: Claude (Fable 5)  
**项目版本**: v0.2.0  
**总代码行数**: ~57,000 行 Python
