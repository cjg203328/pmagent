# 📁 ArtPM Agent - 项目结构

```
d:\桌面\pmagent\
│
├── 📄 README.md                              # 项目说明(已更新)
├── 📄 start.bat                              # 一键启动脚本
├── 📄 .env                                   # 环境配置
│
├── 📋 MCP_SKILLS_DEVELOPMENT_PLAN.md        # MCP开发计划
├── 📋 MCP_SKILLS_COMPLETION_REPORT.md       # MCP完成报告
├── 📋 MCP_SKILLS_QUICKSTART.md              # MCP快速指南
├── 📋 MCP_SKILLS_完成总结.md                # MCP总结
├── 📋 DELIVERY_CHECKLIST.md                 # 交付清单
├── 📋 技术架构重构方案.md                    # 架构设计
│
└── 📂 artpm_agent/                          # 主应用目录
    │
    ├── 📄 app.py                            # Streamlit UI主入口
    ├── 📄 agent.py                          # Agent核心逻辑
    ├── 📄 config.py                         # 配置管理
    │
    ├── 🧪 test_mcp_simple.py               # MCP快速测试 ⭐ NEW
    ├── 🧪 test_mcp_enhanced.py             # MCP完整测试 ⭐ NEW
    ├── 🧪 quick_verify.py                  # 快速验证脚本 ⭐ NEW
    │
    ├── 📂 core/                             # 核心模块
    │   ├── llm_client.py                    # LLM客户端
    │   ├── token_monitor.py                 # Token监控
    │   ├── rag_system.py                    # RAG系统
    │   ├── mcp_client.py                    # 原MCP客户端
    │   └── mcp_client_enhanced.py          # 增强型MCP客户端 ⭐ NEW
    │
    ├── 📂 skills/                           # Skills模块
    │   ├── base_skill.py                    # Skill基类
    │   ├── skill_router.py                  # Skill路由(已更新) ⭐ UPDATED
    │   └── mcp_skills.py                    # MCP Skills实现 ⭐ NEW
    │       ├── FileReaderSkill              # 文件读取
    │       ├── FileSearchSkill              # 文件搜索
    │       ├── DataAnalyzerSkill            # 数据分析
    │       ├── TrendAnalyzerSkill           # 趋势分析
    │       └── ProjectEvaluatorSkill        # 项目评估
    │
    ├── 📂 memory/                           # 记忆管理
    │   ├── sqlite_manager.py                # SQLite管理
    │   ├── vector_store.py                  # 向量存储
    │   └── memory_manager.py                # 记忆管理器
    │
    ├── 📂 parsers/                          # 解析器
    │   ├── excel_parser.py                  # Excel解析
    │   └── ocr_parser.py                    # OCR解析
    │
    ├── 📂 database/                         # 数据库
    │   └── models.py                        # 数据模型
    │
    ├── 📂 utils/                            # 工具函数
    │   ├── file_utils.py                    # 文件工具
    │   ├── validators.py                    # 验证器
    │   └── llm_client.py                    # LLM客户端封装
    │
    └── 📂 data/                             # 数据目录
        ├── artpm.db                         # 项目数据库
        └── token_usage.db                   # Token使用数据
```

---

## 🎯 关键文件说明

### 新增文件 (⭐ NEW)

| 文件 | 说明 | 代码量 |
|------|------|--------|
| `core/mcp_client_enhanced.py` | 增强型MCP客户端,提供5个MCP工具 | ~380行 |
| `skills/mcp_skills.py` | 5个MCP Skills的完整实现 | ~450行 |
| `test_mcp_simple.py` | 简化测试脚本,快速验证功能 | ~120行 |
| `test_mcp_enhanced.py` | 完整测试脚本,覆盖所有场景 | ~280行 |
| `quick_verify.py` | 快速验证脚本,检查安装状态 | ~100行 |

### 更新文件 (⭐ UPDATED)

| 文件 | 更新内容 |
|------|---------|
| `skills/skill_router.py` | 集成MCP Skills,自动注册到路由器 |
| `README.md` | 添加MCP Skills说明和使用示例 |

---

## 📊 Skills架构

### 原有Skills (5个)

```
Core Business Skills
├── document_classifier_parser   (文档解析)
├── quote_calculator             (利润计算)
├── task_allocator              (任务分配)
├── progress_tracker            (进度跟踪)
└── reminder_bot                (催办提醒)
```

### 新增MCP Skills (5个)

```
MCP-based Skills
├── file_reader                 (文件读取)
│   └── MCP Tool: read_file
│
├── file_search                 (文件搜索)
│   ├── MCP Tool: search_files
│   └── MCP Tool: search_content
│
├── data_analyzer               (数据分析)
│   └── MCP Tool: analyze_data
│
├── trend_analyzer              (趋势分析)
│   └── MCP Tool: analyze_data
│
└── project_evaluator           (项目评估)
    ├── MCP Tool: read_file
    └── MCP Tool: analyze_data
```

---

## 🔄 数据流向

### 用户请求流程

```
用户输入
  ↓
Agent (agent.py)
  ├── Intent Detection (意图识别)
  ├── Context Building (上下文构建)
  └── Response Generation (响应生成)
  ↓
Skill Router (skill_router.py)
  ├── Route to Core Skills (原有5个)
  └── Route to MCP Skills (新增5个)
  ↓
Skill Execution
  ├── Core Skills → Direct execution
  └── MCP Skills → MCP Client → Tools
  ↓
Enhanced MCP Client (mcp_client_enhanced.py)
  ├── read_file
  ├── search_files
  ├── search_content
  ├── analyze_data
  └── execute_command
  ↓
Return Result
  ↓
Format Response
  ↓
显示给用户
```

### Skill执行流程

```
execute_skill(skill_name, inputs)
  ↓
[1] 检查Skill类型
  ├── Core Skill? → 直接执行
  └── MCP Skill? → 调用MCP Client
  ↓
[2] MCP Skill执行
  ├── 初始化MCP Client
  ├── 调用对应Tool
  ├── 处理返回结果
  └── 可选: LLM增强(摘要、建议等)
  ↓
[3] 返回结构化结果
  {
    "success": bool,
    "data": {...},
    "error": str (if failed)
  }
```

---

## 🚀 快速导航

### 开始使用

1. **快速验证**: `cd artpm_agent && python quick_verify.py`
2. **启动应用**: `.\start.bat`
3. **运行测试**: `cd artpm_agent && python test_mcp_simple.py`

### 查看文档

- **快速开始**: [MCP_SKILLS_QUICKSTART.md](MCP_SKILLS_QUICKSTART.md)
- **完整报告**: [MCP_SKILLS_COMPLETION_REPORT.md](MCP_SKILLS_COMPLETION_REPORT.md)
- **开发计划**: [MCP_SKILLS_DEVELOPMENT_PLAN.md](MCP_SKILLS_DEVELOPMENT_PLAN.md)
- **交付清单**: [DELIVERY_CHECKLIST.md](DELIVERY_CHECKLIST.md)

### 开发指南

- **核心文件**: `artpm_agent/skills/mcp_skills.py`
- **MCP客户端**: `artpm_agent/core/mcp_client_enhanced.py`
- **测试文件**: `artpm_agent/test_mcp_simple.py`

---

## 📈 版本历史

| 版本 | 日期 | 更新内容 | Skills数量 |
|------|------|---------|-----------|
| v1.0 | 2026-06 | 初始版本,5个基础Skills | 5 |
| v2.0 | 2026-07 | 新增5个MCP Skills,优化架构 | 10 |

**当前版本**: v2.0 (2026-07-10)

---

生成时间: 2026-07-10
