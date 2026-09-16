# ArtPM Agent 架构图谱

## 1. 系统全局视图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户界面层 (Streamlit)                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐    │
│  │  对话页面    │  │  设置页面    │  │  可观测面板              │    │
│  │  chat.py    │  │ settings.py │  │ observability.py        │    │
│  └──────┬──────┘  └──────┬──────┘  └────────┬────────────────┘    │
└─────────┼─────────────────┼──────────────────┼──────────────────────┘
          │                 │                  │
          │    ┌────────────▼──────────────┐   │
          │    │  Session State Manager    │   │
          │    │  - conversation_history   │   │
          │    │  - attachments            │   │
          │    │  - agent_profile          │   │
          │    └───────────────────────────┘   │
          │                                     │
┌─────────▼─────────────────────────────────────▼──────────────────────┐
│                     核心 Agent 层 (agent.py)                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  ArtPMAgent                                                   │   │
│  │  - config: Config                                             │   │
│  │  - runtime: AgentRuntime                                      │   │
│  │  - model_gateway: ModelGateway                                │   │
│  │  - skill_router: SkillRouter                                  │   │
│  │  - memory_manager: MemoryManager                              │   │
│  └──────────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                ┌───────────────┼───────────────┐
                │               │               │
       ┌────────▼────────┐ ┌───▼────────┐ ┌───▼──────────────┐
       │ Intent Router   │ │ TurnService│ │ AgentSession     │
       │ routing/        │ │ harness/   │ │ harness/         │
       └────────┬────────┘ └───┬────────┘ └───┬──────────────┘
                │              │              │
                └──────────────┼──────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼──────┐  ┌─────▼────────┐  ┌───▼────────────┐
    │ Skill Handlers │  │ Runtime Loop │  │ Model Gateway  │
    └────────────────┘  └──────────────┘  └────────────────┘
```

## 2. Intent 路由决策树

```
用户输入
    │
    ▼
┌────────────────────────────────┐
│  快速意图识别 (chat_intent.py) │
│  - 关键词匹配                   │
│  - 正则表达式                   │
│  - 无 LLM 调用                  │
└────┬───────────────────────────┘
     │
     ├─→ is_exact_greeting() ────→ "你好!" (直接回复)
     ├─→ is_identity_query() ────→ "我是 ArtPM Agent..." (模板)
     ├─→ is_capability_query() ──→ 返回技能列表
     ├─→ is_model_query() ───────→ 返回当前模型信息
     │
     ├─→ 不匹配 ↓
     │
┌────▼──────────────────────────────┐
│  Skill 路由 (IntentRouter)        │
│  - HybridIntentScorer             │
│  - 基于规则 + 语义匹配             │
└────┬──────────────────────────────┘
     │
     ├─→ "报价.*成本.*利润" ──→ SmartTaskAllocator
     ├─→ "任务分配" ──────────→ SmartTaskAllocator
     ├─→ "进度.*预警" ────────→ SmartProgressTracker
     ├─→ "文档解析" + 附件 ───→ DocumentClassifierParser
     ├─→ "质量控制" ──────────→ QualityControlSkill
     │
     ├─→ 不匹配 ↓
     │
┌────▼──────────────────────────────┐
│  Artifact 匹配 (artifact_handler) │
│  - 检测工件生成意图                │
│  - 如: "生成报告", "创建文档"      │
└────┬──────────────────────────────┘
     │
     ├─→ 匹配 ──→ ArtifactCoordinator
     │
     ├─→ 不匹配 ↓
     │
┌────▼──────────────────────────────┐
│  Workflow 路由 (workflow_handler) │
│  - LangGraph 任务编排              │
│  - 多步复杂任务                    │
└────┬──────────────────────────────┘
     │
     ├─→ 匹配 ──→ WorkflowCoordinator
     │
     ├─→ 不匹配 ↓
     │
┌────▼──────────────────────────────┐
│  LLM Fallback (model_handler)     │
│  - ModelGateway.chat()             │
│  - 通用对话                        │
└───────────────────────────────────┘
```

## 3. ModelGateway 故障转移流程

```
用户请求
    │
    ▼
┌───────────────────────────────────┐
│  ModelGateway.chat()              │
│  - 主模型: claude-3-5-sonnet      │
│  - Provider: anthropic            │
└───┬───────────────────────────────┘
    │
    ▼
┌───────────────────────────────────┐
│  检查熔断器状态                    │
│  - 冷却期: 60 秒                   │
│  - 记录: _unavailable_until       │
└───┬───────────────────────────────┘
    │
    ├─→ 冷却中 → 跳过此候选
    │
    ▼
┌───────────────────────────────────┐
│  尝试主模型调用                    │
│  - create_llm_client(config)      │
│  - 超时: 12 秒                     │
└───┬───────────────────────────────┘
    │
    ├─→ 成功 ──→ 记录遥测 → 返回响应
    │
    ├─→ 失败 (网络/超时/限流) ↓
    │
┌───▼───────────────────────────────┐
│  提取候选模型列表                  │
│  - LLM_AVAILABLE_MODELS           │
│  - 过滤: 必须同 Provider           │
│  - 排除: 已在冷却期的模型         │
└───┬───────────────────────────────┘
    │
    ▼
┌───────────────────────────────────┐
│  遍历候选模型 (最多 2 次尝试)      │
│  - 候选 1: gpt-4o-mini            │
│  - 候选 2: gpt-3.5-turbo          │
└───┬───────────────────────────────┘
    │
    ├─→ 成功 ──→ 记录切换事件 → 返回响应
    │           │
    │           └─→ 原模型进入冷却期 60 秒
    │
    ├─→ 全部失败 ↓
    │
┌───▼───────────────────────────────┐
│  返回错误                          │
│  - 记录所有尝试的遥测数据         │
│  - 显示友好错误提示               │
└───────────────────────────────────┘
```

## 4. Memory 系统数据流

```
文档上传
    │
    ▼
┌──────────────────────────────────────┐
│  DocumentClassifierParser.execute()  │
│  - 识别文档类型                       │
│  - 提取结构化数据                     │
└──────┬───────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│  MemoryManager.save_document()       │
└──────┬───────────────────────────────┘
       │
       ├─→ SQLiteManager (memory.db)
       │   └─→ documents 表
       │       ├─ id (UUID)
       │       ├─ document_type
       │       ├─ extracted_data (JSON)
       │       ├─ raw_text
       │       └─ confidence
       │
       └─→ VectorStore (FAISS)
           └─→ 生成嵌入向量
               ├─ DeterministicEmbeddingProvider
               │  └─→ TF-IDF + 哈希 → 1536 维
               │
               └─→ FAISS Index
                   └─→ 添加 (id, vector, metadata)

查询请求
    │
    ▼
┌──────────────────────────────────────┐
│  MemoryManager.retrieve(query)       │
└──────┬───────────────────────────────┘
       │
       ├─→ 向量搜索 (FAISS)
       │   └─→ Top-K 最相似文档
       │
       ├─→ 结构化查询 (SQLite)
       │   └─→ WHERE filters
       │
       └─→ 合并结果 + 去重
           └─→ 返回文档列表
```

## 5. Skill 执行生命周期

```
Skill 调用请求
    │
    ▼
┌─────────────────────────────────────┐
│  SkillRouter.route_to_skill()       │
│  - 查找技能实例                      │
│  - 提取输入参数                      │
└─────┬───────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────┐
│  权限前置检查 (security/)            │
│  - permission_preflight()            │
│  - 检查写入型操作                    │
└─────┬───────────────────────────────┘
      │
      ├─→ 需要审批 → 等待用户确认
      │
      ▼
┌─────────────────────────────────────┐
│  BaseSkill.execute(inputs)          │
│  - 子类实现具体逻辑                  │
│  - 访问 self.config, self.context    │
└─────┬───────────────────────────────┘
      │
      ├─→ 数据库操作 (DatabaseManager)
      ├─→ 文档解析 (Parsers)
      ├─→ MCP 工具调用 (mcp_client)
      │
      ▼
┌─────────────────────────────────────┐
│  返回结构化结果                      │
│  {                                   │
│    "success": true,                  │
│    "data": {...},                    │
│    "metadata": {...}                 │
│  }                                   │
└─────┬───────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────┐
│  format_skill_result() (presentation│
│  - 转换为 Markdown                   │
│  - 生成图表 (Plotly)                 │
└─────┬───────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────┐
│  渲染到 Streamlit UI                 │
│  st.markdown(formatted_result)       │
└─────────────────────────────────────┘
```

## 6. 遥测数据采集管道

```
模型调用
    │
    ▼
┌──────────────────────────────────────┐
│  ModelGateway.chat()                 │
└──────┬───────────────────────────────┘
       │
       ├─→ 记录连接事件 (每次尝试)
       │   └─→ TelemetryRecorder.record_connection_event()
       │       ├─ model_id
       │       ├─ provider
       │       ├─ success/failure
       │       ├─ error_type
       │       ├─ http_status_code
       │       ├─ latency_ms
       │       └─→ 写入 telemetry.db (connection_events 表)
       │
       └─→ 记录 Token 消耗 (成功响应)
           └─→ TelemetryRecorder.record_token_usage()
               ├─ 读取 usage 数据
               │  ├─ client.last_usage (优先)
               │  └─ 启发式估算 (回退)
               │
               ├─ 计算成本
               │  └─→ pricing.py: MODEL_PRICING 查表
               │      ├─ input_tokens × input_price
               │      ├─ output_tokens × output_price
               │      └─ cached_tokens × 0.0
               │
               └─→ 写入 telemetry.db (token_usage 表)
                   ├─ conversation_id
                   ├─ turn_id
                   ├─ model_id
                   ├─ provider
                   ├─ prompt_tokens
                   ├─ completion_tokens
                   ├─ cached_tokens
                   ├─ total_tokens
                   ├─ estimated_cost_usd
                   └─ timestamp

查询可观测面板
    │
    ▼
┌──────────────────────────────────────┐
│  observability_page()                │
│  └─→ telemetry_dashboard.collect()  │
└──────┬───────────────────────────────┘
       │
       ├─→ Token 消耗聚合
       │   ├─ 总 Token 数
       │   ├─ 估算总成本
       │   ├─ 按模型分组
       │   └─ 按 Provider 分组
       │
       ├─→ 连接健康度计算
       │   ├─ 成功率 (last 24h)
       │   ├─ 错误类型分布
       │   ├─ P50/P95/P99 延迟
       │   └─ 端点可用性
       │
       └─→ 生成 Plotly 图表
           ├─ Token 趋势线
           ├─ 成本堆叠柱状图
           └─ 连接成功率热力图
```

## 7. 多模态文档处理管道

```
用户上传文件
    │
    ▼
┌──────────────────────────────────────┐
│  attachment_pipeline.parse_context() │
│  - 验证文件大小/类型                  │
│  - 提取文件路径                       │
└──────┬───────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│  DocumentClassifierParser.execute()  │
└──────┬───────────────────────────────┘
       │
       ├─→ 检查文件后缀
       │   ├─ .xlsx/.xls
       │   ├─ .pdf
       │   ├─ .docx
       │   ├─ .png/.jpg/.jpeg
       │   ├─ .txt/.md/.csv/.json
       │   └─ .pptx (需要 MinerU)
       │
       ├─→ 尝试 MinerU 转换 (优先)
       │   └─→ MinerUDocumentConverter.convert()
       │       ├─ 模式: auto/local/remote
       │       │
       │       ├─→ local 模式
       │       │   └─→ subprocess: mineru --input file.pdf
       │       │       └─→ 输出: output_dir/file/*.md
       │       │
       │       ├─→ remote 模式
       │       │   └─→ HTTP POST: mineru-api/file_parse
       │       │       └─→ 响应: {markdown, json, images}
       │       │
       │       └─→ 成功
       │           ├─ markdown 正文
       │           ├─ structured JSON (表格/公式)
       │           └─ 图像分析结果
       │
       ├─→ MinerU 失败/不可用 → 回退本地解析器
       │
       ├─→ Excel: ExcelQuoteParser
       │   └─→ openpyxl/xlrd
       │       ├─ 读取所有工作表
       │       ├─ 识别报价单结构
       │       └─→ 提取项目/单价/数量
       │
       ├─→ PDF: pdfplumber
       │   └─→ extract_text()
       │       └─→ 纯文本内容
       │
       ├─→ 图片: PaddleOCR (可选)
       │   └─→ OCRParser.extract_text()
       │       └─→ 识别文字 + 坐标
       │
       └─→ 其他: 直接读取文本
           └─→ Path.read_text(encoding='utf-8')

解析结果
    │
    ▼
┌──────────────────────────────────────┐
│  统一输出格式                         │
│  {                                    │
│    "success": true,                   │
│    "document_type": "报价单",         │
│    "extracted_data": {                │
│      "项目名称": "...",               │
│      "总金额": 120000,                │
│      "明细": [...]                    │
│    },                                 │
│    "raw_text": "...",                 │
│    "confidence": 0.95,                │
│    "source": "local",                 │
│    "backend": "mineru" | "local"      │
│  }                                    │
└──────┬───────────────────────────────┘
       │
       └─→ 保存到 MemoryManager
           └─→ 支持后续语义检索
```

## 8. API Gateway 请求流

```
HTTP Request
    │
    ▼
┌─────────────────────────────────────┐
│  Uvicorn + FastAPI                  │
│  artpm_agent/api/__main__.py        │
└─────┬───────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────┐
│  CORS 中间件                         │
│  - ARTPM_CORS_ORIGINS 白名单        │
└─────┬───────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────┐
│  认证中间件                          │
│  - X-Gateway-Token Header            │
│  - 比对 ARTPM_GATEWAY_SHARED_SECRET │
└─────┬───────────────────────────────┘
      │
      ├─→ 未授权 → HTTP 403
      │
      ▼
┌─────────────────────────────────────┐
│  租户上下文提取                      │
│  - X-Tenant-ID                       │
│  - X-Workspace-ID                    │
│  - X-User-ID                         │
│  └─→ TenantContext 构建             │
└─────┬───────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────┐
│  路由分发                            │
│  - POST /api/v1/chat                 │
│  - POST /api/v1/skills/execute       │
│  - GET  /api/v1/conversations        │
└─────┬───────────────────────────────┘
      │
      ▼ (示例: /chat)
┌─────────────────────────────────────┐
│  业务逻辑层                          │
│  - 创建 ArtPMAgent 实例              │
│  - agent.chat(message, context)      │
└─────┬───────────────────────────────┘
      │
      └─→ 返回 JSON 响应
          {
            "conversation_id": "...",
            "turn_id": "...",
            "response": "...",
            "metadata": {...}
          }
```

---

**图谱版本**: v1.0
**对应项目版本**: v0.2.0
**最后更新**: 2026-07-22
