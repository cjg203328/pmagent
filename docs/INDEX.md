# 📚 ArtPM Agent 文档索引

**项目版本**：v0.1.0  
**更新时间**：2026-07-16

---

## 🚀 快速开始

| 文档 | 说明 | 路径 |
|------|------|------|
| [README](../README.md) | 项目概述和快速开始 | 根目录 |
| [用户指南](guides/USER_GUIDE.md) | 完整使用教程 | docs/guides/ |
| [快速启动](guides/QUICKSTART.md) | 5分钟上手指南 | docs/guides/ |
| [启动指南](guides/STARTUP_GUIDE.md) | 详细启动步骤 | docs/guides/ |

---

## 📖 核心文档

### 架构设计

| 文档 | 说明 |
|------|------|
| [ARCHITECTURE](ARCHITECTURE.md) | 系统架构设计 |
| [OFFLINE_FALLBACK](OFFLINE_FALLBACK.md) | 离线模式设计 |
| [MEMORY_EVOLUTION_DESIGN](MEMORY_EVOLUTION_DESIGN.md) | 记忆系统设计 |

### 功能指南

| 文档 | 说明 |
|------|------|
| [UNLIMITED_OCR_GUIDE](UNLIMITED_OCR_GUIDE.md) | 无限制OCR使用指南 |
| [LOCAL_MCP_GUIDE](LOCAL_MCP_GUIDE.md) | 本地MCP工具指南 |

---

## 🔧 开发文档

### 开发指南

| 文档 | 说明 | 路径 |
|------|------|------|
| [AGENTS](dev/AGENTS.md) | Agent开发指南 | docs/dev/ |
| [CODE_REVIEW_FOLLOWUP](dev/CODE_REVIEW_FOLLOWUP.md) | 代码审查跟进 | docs/dev/ |
| [CODE_REVIEW_REPORT](dev/CODE_REVIEW_REPORT.md) | 代码审查报告 | docs/dev/ |
| [alembic_setup_guide](dev/alembic_setup_guide.md) | 数据库迁移指南 | docs/dev/ |
| [type_safety_guide](dev/type_safety_guide.md) | 类型安全指南 | docs/dev/ |

---

## 🛠️ MCP 功能

### MCP 技能文档

| 文档 | 说明 | 路径 |
|------|------|------|
| [MCP_SKILLS_README](mcp/MCP_SKILLS_README.md) | MCP技能概述 | docs/mcp/ |
| [MCP_SKILLS_QUICKSTART](mcp/MCP_SKILLS_QUICKSTART.md) | MCP快速开始 | docs/mcp/ |
| [MCP_SKILLS_完成总结](mcp/MCP_SKILLS_完成总结.md) | MCP完成总结 | docs/mcp/ |
| [REAL_MCP_QUICKSTART](mcp/REAL_MCP_QUICKSTART.md) | 真实MCP快速开始 | docs/mcp/ |
| [REAL_MCP_SUMMARY](mcp/REAL_MCP_SUMMARY.md) | 真实MCP总结 | docs/mcp/ |

---

## 📦 归档文档

历史版本、修复记录、优化报告等文档已归档到 [archive/](archive/) 目录。

### 归档分类

| 分类 | 说明 | 数量 |
|------|------|------|
| BUG修复 | 所有BUG分析和修复报告 | ~10个 |
| 功能优化 | 各阶段优化报告 | ~15个 |
| 架构重构 | 重构方案和阶段报告 | ~10个 |
| 交付文档 | 历史交付检查清单 | ~5个 |
| 临时文档 | 开发过程中的临时记录 | ~20个 |

**查看归档**：[docs/archive/](archive/)

---

## 📂 项目结构

```
pmagent/
├── README.md                   # 项目主文档
├── .gitignore                  # Git忽略规则
├── .env.example                # 环境配置示例
├── requirements.txt            # Python依赖（已废弃）
├── pyproject.toml              # 项目配置（主）
├── start.bat                   # Windows启动脚本
├── restart.bat                 # Windows重启脚本
├── restart.sh                  # Linux/Mac重启脚本
│
├── docs/                       # 📚 文档目录
│   ├── INDEX.md                # 文档索引（本文件）
│   ├── ARCHITECTURE.md         # 架构设计
│   ├── OFFLINE_FALLBACK.md     # 离线模式
│   ├── guides/                 # 使用指南
│   │   ├── USER_GUIDE.md       # 用户指南
│   │   ├── QUICKSTART.md       # 快速开始
│   │   └── STARTUP_GUIDE.md    # 启动指南
│   ├── dev/                    # 开发文档
│   │   ├── AGENTS.md
│   │   ├── CODE_REVIEW_*.md
│   │   └── ...
│   ├── mcp/                    # MCP文档
│   │   └── MCP_SKILLS_*.md
│   └── archive/                # 归档文档
│       ├── BUG_*.md
│       ├── OPTIMIZATION_*.md
│       └── ...
│
├── artpm_agent/                # 🎨 核心应用
│   ├── __init__.py
│   ├── app.py                  # Streamlit应用入口
│   ├── agent.py                # 核心Agent
│   ├── config.py               # 配置管理
│   ├── main.py                 # CLI入口
│   │
│   ├── views/                  # 视图层
│   │   ├── chat.py             # 聊天页面
│   │   └── settings.py         # 设置页面
│   │
│   ├── skills/                 # 业务技能
│   │   ├── base_skill.py
│   │   ├── skill_router.py
│   │   ├── cost_control_skill.py
│   │   ├── progress_management_skill.py
│   │   ├── quality_control_skill.py
│   │   └── ...
│   │
│   ├── core/                   # 核心功能
│   │   ├── mcp_client*.py      # MCP客户端
│   │   └── token_monitor.py
│   │
│   ├── memory/                 # 记忆系统
│   │   ├── memory_manager.py
│   │   ├── vector_store.py
│   │   └── workspace_knowledge_store.py
│   │
│   ├── database/               # 数据层
│   │   ├── models.py
│   │   └── migrate.py
│   │
│   ├── utils/                  # 工具函数
│   │   ├── llm_client.py
│   │   ├── logger.py
│   │   ├── unlimited_ocr.py
│   │   └── ...
│   │
│   ├── parsers/                # 文档解析
│   │   └── excel_parser.py
│   │
│   ├── internal/               # 内部模块
│   │   └── chat_harness_integration.py
│   │
│   └── logs/                   # 日志目录
│
├── data/                       # 📊 数据目录
│   ├── artpm.db                # 业务数据库
│   ├── conversations.db        # 对话历史
│   ├── episodes.db             # Agent记录
│   ├── vector_store/           # 向量库
│   ├── chat_attachments/       # 附件
│   └── backups/                # 备份
│
├── tests/                      # 🧪 测试
│   └── ...
│
└── alembic/                    # 📦 数据库迁移
    └── versions/
```

---

## 🔍 快速查找

### 按主题查找

**启动和安装**
- [README](../README.md) → 快速开始
- [QUICKSTART](guides/QUICKSTART.md) → 5分钟上手
- [STARTUP_GUIDE](guides/STARTUP_GUIDE.md) → 详细步骤

**使用教程**
- [USER_GUIDE](guides/USER_GUIDE.md) → 完整教程
- [OFFLINE_FALLBACK](OFFLINE_FALLBACK.md) → 离线模式

**功能指南**
- [UNLIMITED_OCR_GUIDE](UNLIMITED_OCR_GUIDE.md) → OCR功能
- [LOCAL_MCP_GUIDE](LOCAL_MCP_GUIDE.md) → MCP工具
- [MCP_SKILLS_QUICKSTART](mcp/MCP_SKILLS_QUICKSTART.md) → MCP技能

**开发指南**
- [ARCHITECTURE](ARCHITECTURE.md) → 架构设计
- [AGENTS](dev/AGENTS.md) → Agent开发
- [type_safety_guide](dev/type_safety_guide.md) → 类型安全
- [alembic_setup_guide](dev/alembic_setup_guide.md) → 数据库迁移

### 按角色查找

**👤 普通用户**
1. [README](../README.md) - 了解项目
2. [QUICKSTART](guides/QUICKSTART.md) - 快速开始
3. [USER_GUIDE](guides/USER_GUIDE.md) - 学习使用

**💼 项目管理员**
1. [OFFLINE_FALLBACK](OFFLINE_FALLBACK.md) - 离线部署
2. [STARTUP_GUIDE](guides/STARTUP_GUIDE.md) - 团队部署

**👨‍💻 开发者**
1. [ARCHITECTURE](ARCHITECTURE.md) - 理解架构
2. [AGENTS](dev/AGENTS.md) - 开发指南
3. [CODE_REVIEW_REPORT](dev/CODE_REVIEW_REPORT.md) - 代码规范

---

## 📝 文档贡献

### 添加新文档

1. **用户指南** → 放在 `docs/guides/`
2. **开发文档** → 放在 `docs/dev/`
3. **架构设计** → 放在 `docs/`
4. **临时文档** → 放在 `docs/archive/`

### 文档命名规范

- 使用英文大写 + 下划线：`USER_GUIDE.md`
- 功能指南加后缀：`*_GUIDE.md`
- 报告文档加后缀：`*_REPORT.md`
- 临时文档标注日期：`*_20260716.md`

---

## 🔗 相关链接

- **项目仓库**：（如果有的话）
- **问题追踪**：（如果有的话）
- **更新日志**：见归档文档

---

**最后更新**：2026-07-16  
**文档维护**：ArtPM Agent Team
