# 📂 ArtPM Agent 项目结构

**版本**：v0.1.0  
**更新时间**：2026-07-16

---

## 🌲 目录结构

```
pmagent/
│
├── 📄 配置文件
│   ├── README.md                   # 项目主文档
│   ├── .gitignore                  # Git忽略规则
│   ├── .env.example                # 环境配置示例
│   ├── pyproject.toml              # 项目配置（主配置文件）
│   ├── start.bat                   # Windows启动脚本
│   ├── restart.bat                 # Windows重启脚本
│   └── restart.sh                  # Linux/Mac重启脚本
│
├── 📚 docs/                        # 文档目录
│   ├── INDEX.md                    # 文档索引
│   ├── ARCHITECTURE.md             # 架构设计文档
│   ├── OFFLINE_FALLBACK.md         # 离线模式文档
│   ├── UNLIMITED_OCR_GUIDE.md      # OCR功能指南
│   ├── LOCAL_MCP_GUIDE.md          # MCP本地工具指南
│   ├── MEMORY_EVOLUTION_DESIGN.md  # 记忆系统设计
│   │
│   ├── guides/                     # 使用指南
│   │   ├── USER_GUIDE.md           # 用户完整指南
│   │   ├── QUICKSTART.md           # 快速开始
│   │   └── STARTUP_GUIDE.md        # 启动详细指南
│   │
│   ├── dev/                        # 开发文档
│   │   ├── AGENTS.md               # Agent开发指南
│   │   ├── CODE_REVIEW_FOLLOWUP.md # 代码审查跟进
│   │   ├── CODE_REVIEW_REPORT.md   # 代码审查报告
│   │   ├── alembic_setup_guide.md  # 数据库迁移指南
│   │   └── type_safety_guide.md    # 类型安全指南
│   │
│   ├── mcp/                        # MCP功能文档
│   │   ├── MCP_SKILLS_README.md
│   │   ├── MCP_SKILLS_QUICKSTART.md
│   │   ├── MCP_SKILLS_完成总结.md
│   │   ├── REAL_MCP_QUICKSTART.md
│   │   └── REAL_MCP_SUMMARY.md
│   │
│   └── archive/                    # 归档文档（历史）
│       ├── BUG_*.md                # BUG修复记录
│       ├── OPTIMIZATION_*.md       # 优化报告
│       ├── PHASE*.md               # 开发阶段文档
│       └── ...
│
├── 🎨 artpm_agent/                 # 核心应用代码
│   ├── __init__.py
│   ├── app.py                      # Streamlit应用入口
│   ├── agent.py                    # 核心Agent实现
│   ├── config.py                   # 配置管理
│   ├── main.py                     # CLI命令行入口
│   ├── ui_helpers.py               # UI辅助函数
│   ├── ui_style.py                 # UI样式定义
│   ├── health_check.py             # 健康检查
│   │
│   ├── views/                      # 🖥️ 视图层（UI页面）
│   │   ├── __init__.py
│   │   ├── chat.py                 # 聊天页面
│   │   └── settings.py             # 设置页面
│   │
│   ├── skills/                     # 🎯 业务技能模块
│   │   ├── __init__.py
│   │   ├── base_skill.py           # 技能基类
│   │   ├── skill_router.py         # 技能路由
│   │   ├── cost_control_skill.py   # 成本控制
│   │   ├── delivery_skill.py       # 交付管理
│   │   ├── progress_management_skill.py    # 进度管理
│   │   ├── quality_control_skill.py        # 质量控制
│   │   ├── quote_scheduling_skill.py       # 报价排期
│   │   ├── requirements_assessment_skill.py # 需求评估
│   │   ├── retrospective_skill.py          # 项目复盘
│   │   ├── smart_progress_tracker.py       # 智能进度跟踪
│   │   ├── smart_task_allocator.py         # 智能任务分配
│   │   ├── mcp_skills.py           # MCP技能集成
│   │   └── input_schemas.py        # 输入模式定义
│   │
│   ├── core/                       # ⚙️ 核心功能模块
│   │   ├── __init__.py
│   │   ├── mcp_client.py           # MCP客户端
│   │   ├── mcp_client_enhanced.py  # MCP增强客户端
│   │   ├── mcp_client_unified.py   # MCP统一客户端
│   │   ├── mcp_skills.py           # MCP技能
│   │   └── token_monitor.py        # Token监控
│   │
│   ├── memory/                     # 🧠 记忆系统
│   │   ├── __init__.py
│   │   ├── memory_manager.py       # 记忆管理器
│   │   ├── vector_store.py         # 向量存储
│   │   └── workspace_knowledge_store.py # 工作区知识库
│   │
│   ├── database/                   # 💾 数据层
│   │   ├── __init__.py
│   │   ├── models.py               # 数据模型
│   │   └── migrate.py              # 数据迁移
│   │
│   ├── utils/                      # 🛠️ 工具模块
│   │   ├── __init__.py
│   │   ├── llm_client.py           # LLM客户端
│   │   ├── logger.py               # 日志系统
│   │   ├── cache.py                # 缓存管理
│   │   ├── chat_attachments.py     # 附件处理
│   │   ├── chat_intent.py          # 意图识别
│   │   ├── unlimited_ocr.py        # OCR服务
│   │   └── exceptions.py           # 异常定义
│   │
│   ├── parsers/                    # 📄 文档解析
│   │   ├── __init__.py
│   │   └── excel_parser.py         # Excel解析器
│   │
│   ├── artifacts/                  # 🎨 工件生成
│   │   ├── __init__.py
│   │   ├── coordinator.py          # 工件协调器
│   │   └── generator.py            # 工件生成器
│   │
│   ├── internal/                   # 🔒 内部模块（不暴露）
│   │   ├── __init__.py
│   │   └── chat_harness_integration.py # 聊天harness集成
│   │
│   └── logs/                       # 📋 日志目录
│       └── artpm_YYYYMMDD.log      # 每日日志
│
├── 📊 data/                        # 数据目录
│   ├── artpm.db                    # 业务数据库（项目/任务/成员）
│   ├── conversations.db            # 对话历史数据库
│   ├── episodes.db                 # Agent执行记录
│   ├── consolidation.db            # 整合数据库
│   ├── vector_store/               # 向量存储（FAISS）
│   ├── chat_attachments/           # 聊天附件
│   ├── artifacts/                  # 生成的工件
│   └── backups/                    # 数据库备份
│
├── 🧪 tests/                       # 测试目录
│   ├── test_basic.py
│   └── ...
│
├── 📦 alembic/                     # 数据库迁移
│   ├── versions/                   # 迁移版本
│   ├── env.py                      # 迁移环境
│   └── alembic.ini                 # Alembic配置
│
└── 🔧 scripts/                     # 脚本目录
    ├── backup_data.py              # 数据备份
    └── optimize_db.py              # 数据库优化
```

---

## 📝 核心模块说明

### 1. 视图层 (views/)

**作用**：Streamlit页面UI

| 文件 | 说明 | 主要功能 |
|------|------|---------|
| `chat.py` | 聊天页面 | 对话交互、消息渲染、附件上传、通过统一 harness 走单一回合主链 |
| `settings.py` | 设置页面 | 配置管理、API密钥设置 |

---

### 2. 技能模块 (skills/)

**作用**：垂直领域业务技能

| 技能 | 说明 | 离线可用 |
|------|------|---------|
| `cost_control_skill` | 成本控制（利润测算、税费计算） | ✅ |
| `smart_task_allocator` | 智能任务分配 | ✅ |
| `smart_progress_tracker` | 智能进度跟踪 | ✅ |
| `quality_control_skill` | 质量控制 | ✅ |
| `delivery_skill` | 交付管理 | ✅ |
| `retrospective_skill` | 项目复盘 | ✅ |
| `requirements_assessment_skill` | 需求评估 | ❌ (需LLM) |
| `quote_scheduling_skill` | 报价排期 | ✅ |
| `progress_management_skill` | 进度管理 | ✅ |

---

### 3. 核心功能 (core/)

**作用**：系统核心能力

| 模块 | 说明 |
|------|------|
| `mcp_client_unified.py` | 统一MCP客户端（文件工具、命令执行） |
| `token_monitor.py` | Token使用监控 |

---

### 4. 记忆系统 (memory/)

**作用**：工作区知识管理

| 模块 | 说明 |
|------|------|
| `memory_manager.py` | 记忆管理器 |
| `vector_store.py` | FAISS向量存储 |
| `workspace_knowledge_store.py` | 工作区知识库 |

---

### 5. 数据层 (database/)

**作用**：业务数据持久化

| 数据库 | 说明 | 表结构 |
|--------|------|--------|
| `artpm.db` | 业务数据 | projects, tasks, members, quotes |
| `conversations.db` | 对话历史 | conversations, messages |
| `episodes.db` | Agent记录 | episodes, feedback |

---

### 6. 工具模块 (utils/)

**作用**：通用工具函数

| 模块 | 说明 |
|------|------|
| `llm_client.py` | LLM API客户端 |
| `logger.py` | 日志系统 |
| `unlimited_ocr.py` | 无限制OCR |
| `chat_attachments.py` | 附件管理 |
| `chat_intent.py` | 意图识别 |

---

## 🔄 数据流

### 用户输入 → 响应流程

```
1. 用户输入（views/chat.py 接收，含附件落盘与一次性路径解析）
   ↓
2. ui_helpers.py 处理会话状态 / 历史裁剪
   ↓
3. internal/chat_harness_integration.execute_turn_with_harness()
   ↓
4. harness/run_turn() 单一回合主链（Step0 记忆注入后顺序调度）：
     profile → knowledge ingestion → knowledge rule
     → artifact → workflow → skill → model fallback
   ↓
5. 各 handler 内部按需调用 skills/*_skill.py 与 database/models.py
   ↓
6. 结果（response / artifacts / approval）回传 chat.py 渲染
```

> **单一回合主链（v0.2）**：chat.py 只保留两条路径 —— `local_fast` 极速直连
> 与统一 harness。原先散落在 chat.py 内的知识规则 / 工件 / 工作流 / 直接模型
> fallback 分支已移除，全部收敛到 `run_turn()` 的 handler 顺序调度，保证每回合
> 只走一条可预测的主链。

### 离线模式流程

```
用户输入（业务指令）
   ↓
意图识别（utils/chat_intent.py）
   ↓
本地技能路由（skills/skill_router.py）
   ↓
业务技能执行（skills/*_skill.py）
   ↓
直接返回结果（无LLM调用）
```

---

## 📦 依赖管理

### Python依赖

**主配置**：`pyproject.toml`

```toml
[project]
name = "artpm-agent"
version = "0.1.0"
requires-python = ">=3.10"

dependencies = [
    "streamlit>=1.44.0",
    "anthropic>=0.8.0",
    "openai>=1.0.0",
    "sqlalchemy>=2.0.0",
    "faiss-cpu>=1.14.3",
    ...
]
```

### 可选依赖

```toml
[project.optional-dependencies]
ocr = ["paddleocr>=2.7.0"]
dev = ["pytest>=8.0.0", "ruff>=0.15.0"]
```

---

## 🚀 入口文件

| 文件 | 用途 | 启动方式 |
|------|------|---------|
| `artpm_agent/app.py` | Streamlit Web界面 | `streamlit run artpm_agent/app.py` |
| `artpm_agent/main.py` | CLI命令行界面 | `python -m artpm_agent.main` |
| `start.bat` | Windows启动脚本 | 双击运行 |
| `restart.bat` | Windows重启脚本 | 双击运行 |

---

## 📁 重要目录

### 数据目录 (data/)

```
data/
├── artpm.db              # 业务数据（不要删除）
├── conversations.db      # 对话历史（可清理）
├── vector_store/         # 向量库（可清理）
└── backups/              # 备份（定期清理）
```

### 日志目录 (artpm_agent/logs/)

```
logs/
└── artpm_20260716.log    # 每日日志（自动轮转）
```

---

## 🔍 查找代码

### 按功能查找

**利润测算**
- 技能：`skills/cost_control_skill.py`
- 路由：`skills/skill_router.py`

**任务分配**
- 技能：`skills/smart_task_allocator.py`

**进度检查**
- 技能：`skills/smart_progress_tracker.py`
- 技能：`skills/progress_management_skill.py`

**文档解析**
- 解析器：`parsers/excel_parser.py`
- OCR：`utils/unlimited_ocr.py`

**对话管理**
- 视图：`views/chat.py`
- 辅助：`ui_helpers.py`

---

## 📖 相关文档

- [文档索引](INDEX.md) - 查找所有文档
- [架构设计](ARCHITECTURE.md) - 理解系统架构
- [用户指南](guides/USER_GUIDE.md) - 学习使用
- [开发指南](dev/AGENTS.md) - 参与开发

---

**最后更新**：2026-07-16  
**维护者**：ArtPM Agent Team
