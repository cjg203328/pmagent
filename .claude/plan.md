# 优化开发实施计划

## 目标

自动化执行三大优化任务：
1. 清理嵌套重复包和孤儿表
2. 启动 Phase 3（抽取 run_turn() 应用服务）
3. 补充离线降级文档

## 任务分解

### 任务 1：清理嵌套重复包和孤儿表 ✅ 可直接执行

#### 1.1 清理嵌套重复包

**问题**：存在 `artpm_agent/artpm_agent/utils/` 嵌套目录（疑似打包/导入残留）

**方案**：
- 检查 `artpm_agent/artpm_agent/` 目录内容
- 确认没有被代码引用后删除整个嵌套目录
- 验证测试通过

**风险**：低（git 状态显示为未跟踪）

#### 1.2 处理孤儿表

**问题**：数据库有 6 张表未在 models.py 建模

| 表名 | 列结构 | 数据量 | 代码引用 |
|------|--------|--------|---------|
| staff | 8列（id/name/level/skills等） | 0行 | 有引用 |
| quotes | 9列（id/project_id/document_type等） | 0行 | 有引用 |
| task_assignments | 3列（id/task_id/staff_id等） | 0行 | 有引用 |
| reminders | 未详查 | 0行 | 有引用 |
| operation_logs | 未详查 | 0行 | 有引用 |
| progress_updates | 未详查 | 0行 | 有引用 |

**方案 A（推荐）**：创建完整模型定义
- 为 6 张表在 models.py 中添加 SQLAlchemy 模型类
- 创建 Alembic 迁移标记这些表（仅 stamp，不修改已存在的表）
- 更新代码引用使用新模型

**方案 B（保守）**：标记为遗留表
- 创建 Alembic 迁移文档说明这些表为遗留
- 在代码中添加注释说明这些表的状态
- 不删除（因为有代码引用）

**选择**：方案 A - 因为表结构已知且有代码引用，建模更规范

**实施步骤**：
1. 查询所有 6 张表的完整结构
2. 在 models.py 中添加对应的 SQLAlchemy 模型
3. 创建 Alembic 迁移 `0003_model_orphan_tables.py`
4. 运行迁移测试（仅 stamp head，不修改表）
5. 验证代码引用正常工作

### 任务 2：补充离线降级文档 ✅ 可直接执行

**现状**：已有 `OFFLINE_FALLBACK.md`（53行），内容较基础

**目标**：提升到生产级文档

**扩展内容**：
1. **实际使用示例**
   - 离线模式启动和验证
   - 各技能的离线调用示例
   - 从离线切换到在线的步骤

2. **故障排查指南**
   - 常见问题 FAQ
   - 诊断命令
   - 降级行为判断

3. **能力对比表**
   - 离线 vs 在线功能矩阵
   - 性能影响分析
   - 企业部署建议

4. **集成到主文档**
   - README.md 中添加离线模式章节
   - ARCHITECTURE_SUMMARY.md 更新技术债状态
   - 添加醒目的离线优先标识

### 任务 3：启动 Phase 3（抽取 run_turn() 应用服务）⚠️ 复杂重构

#### 3.1 当前编排逻辑分布

**chat.py (pages/)：**
- Profile 提案检测与创建（lines 222-266）
- 知识摄取检测与创建（lines 268-310）
- Artifact 处理（lines 350-375）
- 工作流审批渲染（通过 ui_helpers）

**agent.py：**
- chat() 方法（lines 1235-1384，150行）
  - 快速意图判断（模型查询/身份/问候/能力）
  - 附件解析
  - 意图路由（三层）
  - 技能执行
  - 模型回退
  - 结果格式化

#### 3.2 目标架构

创建 `artpm_agent/harness/` 模块：

```
artpm_agent/harness/
├── __init__.py
├── turn_service.py       # run_turn() 核心编排
├── profile_handler.py    # Profile 提案逻辑
├── knowledge_handler.py  # 知识摄取逻辑
├── artifact_handler.py   # Artifact 处理
├── skill_handler.py      # 技能路由与执行
└── response_builder.py   # 响应格式化
```

**run_turn() 签名草案**：

```python
@dataclass
class TurnContext:
    """单个回合的完整上下文"""
    turn_id: str
    conversation_id: str
    user_input: str
    attachments: List[Any]
    agent_profile: Any
    knowledge_context: str
    conversation_history: List[Dict]
    agent: ArtPMAgent
    
@dataclass  
class TurnResult:
    """回合执行结果"""
    response: str
    metadata: Dict[str, Any]
    awaiting_approval: bool = False
    artifacts: List[Dict] = field(default_factory=list)
    handled_by: Optional[str] = None  # 'profile'/'knowledge'/'skill'/'llm'

def run_turn(ctx: TurnContext) -> TurnResult:
    """
    Phase 3 核心：统一回合编排服务
    
    执行顺序：
    1. Profile 提案检测
    2. 知识摄取检测
    3. Artifact 匹配
    4. 工作流检测与审批
    5. 技能路由
    6. 模型回退
    7. 响应格式化
    """
    ...
```

#### 3.3 实施步骤（渐进式）

**阶段 1：基础设施**（本次实施）
1. 创建 `artpm_agent/harness/` 目录结构
2. 实现 `TurnContext` 和 `TurnResult` 数据类
3. 实现空的 `run_turn()` 框架
4. 添加单元测试骨架

**阶段 2：迁移 Profile 和 Knowledge**
1. 从 chat.py 提取 Profile 提案逻辑到 `profile_handler.py`
2. 从 chat.py 提取知识摄取逻辑到 `knowledge_handler.py`
3. 在 `run_turn()` 中调用这两个 handler
4. 保持 chat.py 原逻辑作为 fallback

**阶段 3：迁移 Skill 和 Artifact**
1. 从 agent.py 提取技能路由到 `skill_handler.py`
2. 从 chat.py 提取 Artifact 处理到 `artifact_handler.py`
3. 整合到 `run_turn()`

**阶段 4：统一入口**
1. chat.py 和 agent.py 调用 `run_turn()`
2. 删除重复逻辑
3. 更新所有测试

**本次只实施阶段 1**，建立架构基础。

#### 3.4 设计决策

**保持向后兼容**：
- agent.chat() 保持签名不变
- 内部逐步切换到 run_turn()
- 允许双轨运行（新/旧路径）

**测试策略**：
- 为每个 handler 添加单元测试
- 保持现有集成测试通过
- 新增 run_turn() 集成测试

**风险缓解**：
- 小步迭代，每步可回滚
- 保留原有代码路径
- 充分测试每个阶段

## 执行顺序

1. **任务 1.1** - 删除嵌套包（5分钟，低风险）
2. **任务 1.2** - 建模孤儿表（30分钟，中等风险）
3. **任务 2** - 补充离线文档（45分钟，无风险）
4. **任务 3.1** - Phase 3 阶段 1（60分钟，架构性工作）

总计：约 2.5 小时

## 验证检查清单

### 任务 1
- [ ] 嵌套目录已删除
- [ ] 6 张孤儿表已建模
- [ ] Alembic 迁移已创建并测试
- [ ] 所有测试通过（pytest）
- [ ] ruff 检查通过

### 任务 2
- [ ] OFFLINE_FALLBACK.md 扩展完成
- [ ] README.md 更新
- [ ] ARCHITECTURE_SUMMARY.md 技术债清单更新

### 任务 3
- [ ] harness/ 目录结构创建
- [ ] TurnContext/TurnResult 数据类实现
- [ ] run_turn() 框架实现
- [ ] 基础单元测试通过
- [ ] 文档更新（PI_ARCHITECTURE_ADOPTION.md）

## 潜在风险

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| 孤儿表建模错误影响现有功能 | 中 | 使用 Alembic stamp，不修改表结构 |
| Phase 3 重构破坏现有功能 | 高 | 只实施阶段 1（基础设施），保持双轨 |
| 测试失败导致进度延误 | 中 | 每步验证，问题隔离 |
| 文档更新不完整 | 低 | 按清单逐项检查 |

## 后续工作（不在本次范围）

- Phase 3 阶段 2-4（Profile/Knowledge/Skill 迁移）
- Phase 2：结构化模型适配器
- Phase 4：可重放会话
- Phase 5：提供商/扩展边界
