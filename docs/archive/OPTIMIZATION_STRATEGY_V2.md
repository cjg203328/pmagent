# ArtPM Agent 优化策略执行方案

> 基于深度分析报告，这是可立即执行的优化方案

**执行优先级**: P0（立即） > P1（本月） > P2（季度） > P3（年度）

---

## 🔥 P0 优化（本周执行）

### 策略 1: 测试覆盖率突击 (19.24% → 34%+)

#### 目标模块与预期提升

| 模块 | 当前覆盖率 | 目标覆盖率 | 新增测试 | 优先级 |
|------|-----------|-----------|---------|--------|
| `views/chat.py` | 5% | 35% | ~50 | 🔥 P0.1 |
| `agent.py` | 未知 | 50% | ~80 | 🔥 P0.2 |
| `skills/skill_router.py` | 未知 | 45% | ~60 | 🔥 P0.3 |
| `workflows/engine.py` | 16% | 50% | ~40 | 🟡 P0.4 |

#### 执行步骤

##### Step 1: views/chat.py 测试（2天）

**创建**: `tests/test_views_chat_enhanced.py`

**测试重点**:
```python
class TestChatPage:
    """聊天页面核心测试"""
    
    # 1. 页面渲染
    def test_page_initialization()
    def test_message_display()
    def test_attachment_upload_ui()
    
    # 2. 消息处理
    def test_send_message_basic()
    def test_send_message_with_attachment()
    def test_message_history_persistence()
    
    # 3. 错误处理
    def test_api_error_handling()
    def test_timeout_handling()
    def test_rate_limit_handling()
    
    # 4. 用户交互
    def test_regenerate_response()
    def test_clear_conversation()
    def test_export_conversation()
```

**执行命令**:
```bash
# 开发测试
python -m pytest tests/test_views_chat_enhanced.py -v

# 覆盖率检查
python -m pytest tests/test_views_chat_enhanced.py --cov=artpm_agent.views.chat --cov-report=term-missing
```

##### Step 2: agent.py 核心测试（2天）

**创建**: `tests/test_agent_core.py`

**测试重点**:
```python
class TestArtPMAgentCore:
    """Agent 核心功能测试"""
    
    # 1. 初始化
    def test_agent_initialization_with_config()
    def test_agent_initialization_without_api_key()
    def test_agent_offline_mode()
    
    # 2. 对话处理
    def test_handle_user_message()
    def test_skill_routing()
    def test_model_failover()
    
    # 3. 工具调用
    def test_tool_invocation()
    def test_tool_permission_check()
    def test_tool_error_handling()
    
    # 4. 状态管理
    def test_session_state_persistence()
    def test_context_window_management()
```

##### Step 3: skill_router.py 测试（1天）

**创建**: `tests/test_skill_router_enhanced.py`

**测试重点**:
```python
class TestSkillRouter:
    """Skill 路由测试"""
    
    # 1. 路由决策
    def test_route_to_correct_skill()
    def test_multi_skill_coordination()
    def test_fallback_handling()
    
    # 2. 输入验证
    def test_input_schema_validation()
    def test_required_fields_check()
    
    # 3. 执行
    def test_skill_execution_success()
    def test_skill_execution_error()
```

---

### 策略 2: 超大文件重构（单一职责原则）

#### 重构优先级

| 文件 | 行数 | 复杂度 | 拆分方案 | 优先级 |
|------|------|--------|---------|--------|
| `workspace_knowledge_store.py` | 2,407 | 🔴 极高 | 4个文件 | 🔥 P0.5 |
| `ui_helpers.py` | 1,954 | 🔴 高 | 4个文件 | 🟡 P0.6 |
| `ui_style.py` | 1,560 | 🟡 中 | 3个文件 | 🟢 P0.7 |
| `agent.py` | 1,389 | 🟡 中 | 4个文件 | 🟢 P0.8 |

#### 重构模板

##### A. workspace_knowledge_store.py (2,407 行 → 4 个文件)

**拆分计划**:
```
artpm_agent/memory/
├── knowledge_store.py          # 600行 - 核心存储 API
├── knowledge_indexer.py        # 550行 - 索引和检索
├── knowledge_models.py         # 400行 - 数据模型
└── knowledge_utils.py          # 500行 - 工具函数
```

**重构步骤**:
```bash
# 1. 创建新文件结构
cd artpm_agent/memory
touch knowledge_store.py knowledge_indexer.py knowledge_models.py knowledge_utils.py

# 2. 分析依赖关系
grep -n "^class\|^def" workspace_knowledge_store.py > structure.txt

# 3. 逐步迁移（Git提交粒度：每个文件一次提交）
# Step 1: 迁移数据模型
# Step 2: 迁移工具函数
# Step 3: 迁移索引器
# Step 4: 迁移核心存储
# Step 5: 更新导入

# 4. 测试验证
python -m pytest tests/test_knowledge_store.py -v

# 5. 删除旧文件
git mv workspace_knowledge_store.py workspace_knowledge_store.py.old
```

##### B. ui_helpers.py (1,954 行 → 4 个文件)

**拆分计划**:
```
artpm_agent/ui/
├── components.py              # 550行 - 可复用组件
├── metrics.py                 # 400行 - 指标显示
├── forms.py                   # 500行 - 表单处理
└── charts.py                  # 450行 - 图表渲染
```

##### C. ui_style.py (1,560 行 → 3 个文件)

**拆分计划**:
```
artpm_agent/ui/
├── theme.py                   # 600行 - 主题定义
├── layout.py                  # 500行 - 布局样式
└── widgets.py                 # 450行 - 组件样式
```

---

### 策略 3: LangChain 依赖优化

#### 审计流程

```bash
# 1. 运行依赖审计工具
python -m artpm_agent.tools.audit_langchain

# 2. 查看实际使用情况
grep -r "from langchain" artpm_agent --include="*.py" -n

# 3. 分析输出
# - 使用了哪些 LangChain 功能？
# - 是否可以用原生 SDK 替代？
# - 是否可以设为可选依赖？
```

#### 优化方案（基于审计结果）

**方案 A**: 完全移除（如果使用 < 5%）
```toml
# pyproject.toml
[project.optional-dependencies]
langchain = [
    "langchain>=1.3.14,<1.4.0",
    "langchain-openai>=1.3.5,<1.4.0",
    "langchain-anthropic>=1.4.8,<1.5.0",
]
```

**方案 B**: 延迟加载（如果使用 5-20%）
```python
# artpm_agent/utils/langchain_loader.py
def get_langchain_client():
    """延迟加载 LangChain 客户端"""
    try:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(...)
    except ImportError:
        raise ImportError("请安装: pip install artpm-agent[langchain]")
```

**方案 C**: 保留但优化导入（如果使用 > 20%）
```python
# 避免全量导入
# ❌ from langchain import *
# ✅ from langchain.chains import LLMChain
```

---

## 🚀 P1 优化（本月执行）

### 策略 4: 遗留代码清理

#### 清理清单

```bash
# 1. 识别遗留代码
find artpm_agent -type f -name "*.py" -exec grep -l "deprecated\|obsolete\|legacy" {} \;

# 2. 分析影响范围
for file in $(find artpm_agent -name "*legacy*" -o -name "*old*"); do
    echo "=== $file ==="
    git log --oneline $file | head -5
done

# 3. 制定删除计划
# - _legacy_backup/ 目录（整体删除）
# - deprecated 函数（逐个删除并更新调用）
# - 兼容性配置（标记为即将废弃）
```

#### 执行步骤

```bash
# Step 1: 删除 _legacy_backup/
rm -rf _legacy_backup/
git add -A
git commit -m "chore: remove legacy backup directory"

# Step 2: 删除 deprecated 函数（示例）
# 1. 找到所有调用
grep -r "deprecated_function" artpm_agent --include="*.py"
# 2. 替换为新函数
# 3. 删除旧函数
# 4. 运行测试
python -m pytest -x

# Step 3: 清理配置
# 编辑 config.py 移除旧配置项
# 更新 .env.example 移除说明
```

---

### 策略 5: 日志系统优化

#### 当前问题
- ❌ 日志体积过大（8.4MB）
- ❌ 无轮转策略
- ❌ 日志分散（3个目录）

#### 优化方案

##### 实现日志轮转

**编辑**: `artpm_agent/utils/logger.py`

```python
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def setup_logging(
    level: int = logging.INFO,
    log_dir: str = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 7,  # 保留 7 个备份
):
    """配置日志系统（带轮转）"""
    
    if log_dir is None:
        log_dir = Path(__file__).parent.parent / "logs"
    else:
        log_dir = Path(log_dir)
    
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 主日志文件（轮转）
    main_handler = RotatingFileHandler(
        log_dir / "artpm.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    main_handler.setLevel(level)
    
    # 错误日志文件（单独轮转）
    error_handler = RotatingFileHandler(
        log_dir / "error.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    
    # 格式化
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    main_handler.setFormatter(formatter)
    error_handler.setFormatter(formatter)
    
    # 配置根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(main_handler)
    root_logger.addHandler(error_handler)
    
    return root_logger
```

##### 统一日志目录

```bash
# 1. 移动所有日志到 artpm_agent/logs/
mkdir -p artpm_agent/logs
mv streamlit*.log artpm_agent/logs/ 2>/dev/null || true
mv .cache/*.log artpm_agent/logs/ 2>/dev/null || true

# 2. 更新 .gitignore
echo "artpm_agent/logs/*.log" >> .gitignore
echo "artpm_agent/logs/*.log.*" >> .gitignore

# 3. 清理旧日志
find artpm_agent/logs -name "*.log" -mtime +7 -delete
```

---

### 策略 6: 数据库整合评估

#### 评估方法

```python
# scripts/analyze_databases.py
import sqlite3
from pathlib import Path

def analyze_database(db_path: Path):
    """分析数据库结构和大小"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 获取表列表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    
    print(f"\n=== {db_path.name} ===")
    print(f"Size: {db_path.stat().st_size / 1024:.1f} KB")
    print(f"Tables: {len(tables)}")
    
    for (table_name,) in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"  - {table_name}: {count} rows")
    
    conn.close()

# 运行分析
data_dir = Path("data")
for db_file in data_dir.glob("*.db"):
    analyze_database(db_file)
```

#### 整合建议（基于分析结果）

**可能的整合方案**:
```
合并前:
- conversations.db
- episodes.db
- feedback.db
- reflection.db

合并后:
- conversations_unified.db (包含所有对话相关数据)
```

---

## 📊 执行检查清单

### Week 1 检查点
- [ ] 创建 `test_views_chat_enhanced.py`（+50 测试）
- [ ] 创建 `test_agent_core.py`（+80 测试）
- [ ] 创建 `test_skill_router_enhanced.py`（+60 测试）
- [ ] 运行覆盖率报告：预期 34%+
- [ ] Code Review 通过

### Week 2 检查点
- [ ] 重构 `workspace_knowledge_store.py`（→ 4 文件）
- [ ] 重构 `ui_helpers.py`（→ 4 文件）
- [ ] 重构 `ui_style.py`（→ 3 文件）
- [ ] 重构 `agent.py`（→ 4 文件）
- [ ] 所有测试通过（1,028+）

### Week 3 检查点
- [ ] 运行 LangChain 审计
- [ ] 实施优化方案（A/B/C）
- [ ] 验证功能完整性
- [ ] 测量安装时间改善

### Week 4 检查点
- [ ] 清理遗留代码（-18 文件）
- [ ] 实现日志轮转
- [ ] 完成数据库评估
- [ ] 更新文档

---

## 🎯 成功标准

| 指标 | 基线 | Week 1 | Week 2 | Week 3 | Week 4 |
|------|------|--------|--------|--------|--------|
| **测试覆盖率** | 19% | 25% | 30% | 32% | 34% |
| **最大文件行数** | 2,407 | 2,407 | 800 | 800 | 700 |
| **遗留代码文件** | 18 | 18 | 18 | 15 | 10 |
| **日志体积** | 8.4MB | 5MB | 3MB | 2MB | <2MB |
| **安装时间** | 基线 | 基线 | 基线 | -20% | -25% |

---

## 🚀 快速开始

```bash
# 1. 创建优化分支
git checkout -b optimize/p0-coverage-and-refactor

# 2. Week 1: 测试突击
cd tests
cp test_views_settings.py test_views_chat_enhanced.py
# 编辑并实现测试...
python -m pytest test_views_chat_enhanced.py -v

# 3. Week 2: 文件重构
cd ../artpm_agent/memory
# 按重构模板执行...

# 4. Week 3: 依赖优化
python -m artpm_agent.tools.audit_langchain
# 根据输出决定方案...

# 5. Week 4: 清理与文档
rm -rf _legacy_backup/
python scripts/clean.py
# 更新文档...

# 6. 合并到主分支
git merge optimize/p0-coverage-and-refactor
```

---

**创建时间**: 2026-07-19  
**负责人**: 开发团队  
**预计完成**: 2026-08-16（4周）  
**下次审查**: 每周五 EOD
