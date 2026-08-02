# ArtPM Agent 优化计划

## 项目现状

### 代码指标
- **代码规模**: 52,731 行 Python 代码（150个文件）
- **测试**: 883 个测试用例，94 个测试文件
- **测试覆盖率**: 18.41% ❌（目标 50%）
- **代码质量**: Ruff 检查通过 ✅

### 发现的问题

#### 1. 测试覆盖率严重不足（高优先级）
- `observability.py`: 0%
- `settings.py`: 6%  
- `chat.py`: 5%
- 工作流模块: 12-22%
- **影响**: 代码变更风险高，难以重构

#### 2. 启动脚本冗余（中优先级）
- 7 个启动脚本功能重叠:
  - `start.bat` / `start.sh`
  - `start_app.bat`
  - `start_optimized.bat` / `start_optimized.sh`
  - `start_with_checks.bat` / `start_with_checks.py`
- **影响**: 维护成本高，用户困惑

#### 3. 日志和缓存污染（中优先级）
- 27 个日志文件分散在 3 个位置
- 175 个 __pycache__ / .pyc 文件
- **影响**: 仓库体积增大，查找困难

#### 4. 依赖复杂度（低优先级）
- LangChain 全家桶已声明但使用为 0
- 多个重量级可选依赖
- **影响**: 安装时间长，包体积大

## 优化方案

### 阶段一：清理优化（立即见效）⚡

#### 1.1 清理缓存和日志
```bash
# 删除 Python 缓存
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type f -name "*.pyc" -delete

# 清理旧日志（保留最近3天）
find artpm_agent/logs -name "*.log" -mtime +3 -delete
find .cache -name "*.log" -delete
rm -f streamlit*.log
```

#### 1.2 统一启动脚本
**保留**:
- `start_with_checks.py` - 推荐方式（Python跨平台）
- `start.bat` - Windows 简化版
- `start.sh` - Linux/Mac 简化版

**删除**:
- `start_app.bat` ❌
- `start_optimized.bat` ❌  
- `start_optimized.sh` ❌
- `start_with_checks.bat` ❌

#### 1.3 优化 .gitignore
添加忽略规则：
```
# Python
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/
.ruff_cache/
.coverage
coverage.xml
htmlcov/

# 项目特定
*.log
.cache/
data/*.db
data/*.db-wal
data/*.db-shm
artpm_agent.egg-info/
build/
dist/

# IDE
.vscode/
.idea/
*.swp
```

### 阶段二：测试覆盖率提升（核心优化）🎯

#### 2.1 视图层测试（优先）
**目标**: 覆盖率从 0-6% 提升至 40%+

创建测试文件：
- `tests/test_views_observability.py`
- `tests/test_views_settings.py`  
- `tests/test_views_chat.py`

重点测试：
- 页面渲染逻辑
- 表单验证
- 状态管理
- 错误处理

#### 2.2 工作流模块测试
**目标**: 从 12-22% 提升至 50%+

- `tests/test_workflows_coordinator.py`
- `tests/test_workflows_engine.py`
- `tests/test_workflows_task_graph.py`

#### 2.3 集成测试增强
添加端到端测试：
- 完整对话流程
- 文件上传和解析
- MCP 工具调用
- 数据库操作

### 阶段三：代码优化（质量提升）📈

#### 3.1 依赖审计
- 确认 LangChain 使用情况（当前导入为0）
- 评估是否可移除或延迟加载
- 优化可选依赖说明

#### 3.2 代码重构机会
- 提取重复的视图层逻辑
- 统一错误处理模式
- 简化配置加载

#### 3.3 性能优化
- 数据库查询优化（添加索引）
- 缓存策略调整
- 日志轮转配置

### 阶段四：文档和配置（体验优化）📚

#### 4.1 简化配置
减少必需环境变量：
- 合并相关配置
- 提供更好的默认值
- 添加配置验证

#### 4.2 改进文档
- 更新快速启动指南
- 添加架构图
- 补充故障排查指南

## 实施计划

### Week 1: 清理和基础优化
- [ ] 执行清理脚本
- [ ] 统一启动脚本
- [ ] 更新 .gitignore
- [ ] 清理 Git 历史中的大文件

### Week 2-3: 测试覆盖率提升
- [ ] 视图层测试（目标 +200 个测试）
- [ ] 工作流模块测试（目标 +150 个测试）
- [ ] 集成测试增强（目标 +50 个测试）

### Week 4: 代码优化和文档
- [ ] 依赖审计和优化
- [ ] 代码重构
- [ ] 文档更新

## 预期效果

### 代码质量
- ✅ 测试覆盖率: 18.41% → 50%+
- ✅ 启动脚本: 7 个 → 3 个
- ✅ 代码维护性: 提升 40%

### 仓库体积
- ✅ 清理缓存: -175 文件
- ✅ 清理日志: -24 文件
- ✅ 优化依赖: -50MB+ 安装体积

### 开发体验
- ✅ 快速启动时间: 减少 30%
- ✅ CI/CD 时间: 减少 20%
- ✅ 新手上手时间: 减少 50%

## 风险评估

### 低风险
- 清理缓存和日志
- 更新 .gitignore
- 文档改进

### 中风险
- 删除启动脚本（需要用户迁移）
- 依赖优化（需要充分测试）

### 高风险
- 大规模代码重构（需要高测试覆盖率支撑）

## 回滚策略

- Git 标签: 在每个阶段前打标签
- 分支策略: feature 分支开发，测试通过后合并
- 文档: 保留旧版本启动方式的文档

---

**创建时间**: 2026-07-19
**创建者**: Claude Fable 5
**项目版本**: v0.2.0
