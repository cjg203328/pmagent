# ArtPM Agent - MCP Skills 开发优化方案

## 📋 当前状态

### ✅ 已完成
1. MCP 客户端基础架构 (`core/mcp_client.py`)
2. Agent 集成 MCP (`agent.py`)
3. 5个业务Skills:
   - `quote_calculator` - 利润计算
   - `task_allocator` - 任务分配
   - `progress_tracker` - 进度跟踪
   - `reminder_bot` - 催办提醒
   - `document_classifier_parser` - 文档解析

### 🔸 待优化
1. 连接真实的Claude Code MCP能力
2. 开发实用的项目管理Skills
3. 集成文件操作、代码分析等能力
4. 构建智能决策系统

---

## 🎯 优化方案

### Phase 1: 核心MCP Skills开发 (优先级⭐⭐⭐⭐⭐)

#### 1.1 文件操作 Skills
```python
# artpm_agent/skills/file_skills.py

class FileReaderSkill(BaseSkill):
    """文件读取Skill - 读取项目文件、报价单等"""
    
    skill_name = "file_reader"
    description = "读取和分析项目文件(Excel, PDF, TXT, CSV等)"
    version = "1.0.0"
    
    async def execute(self, context: Dict) -> Dict:
        """
        执行文件读取
        
        输入:
        - file_path: 文件路径
        - read_type: 读取类型 (full/partial/summary)
        - encoding: 文件编码
        
        输出:
        - content: 文件内容
        - metadata: 文件元数据
        - preview: 内容预览
        """
        pass

class FileSearchSkill(BaseSkill):
    """文件搜索Skill - 在项目中搜索文件"""
    
    skill_name = "file_search"
    description = "搜索项目目录中的文件和文档"
    
    async def execute(self, context: Dict) -> Dict:
        """
        搜索文件
        
        输入:
        - pattern: 搜索模式 (glob pattern)
        - directory: 搜索目录
        - content_search: 是否搜索文件内容
        
        输出:
        - files: 匹配的文件列表
        - summary: 搜索摘要
        """
        pass
```

#### 1.2 数据分析 Skills
```python
# artpm_agent/skills/analysis_skills.py

class DataAnalyzerSkill(BaseSkill):
    """数据分析Skill - 分析项目数据、报表等"""
    
    skill_name = "data_analyzer"
    description = "分析结构化数据(Excel, CSV, JSON)"
    
    async def execute(self, context: Dict) -> Dict:
        """
        数据分析
        
        输入:
        - data_source: 数据源(文件路径或数据对象)
        - analysis_type: 分析类型(descriptive/comparative/trend)
        - metrics: 要分析的指标
        
        输出:
        - statistics: 统计结果
        - insights: 分析洞察
        - visualization: 可视化建议
        """
        pass

class TrendAnalyzerSkill(BaseSkill):
    """趋势分析Skill - 分析项目趋势"""
    
    skill_name = "trend_analyzer"
    description = "分析项目利润、成本、进度等趋势"
    
    async def execute(self, context: Dict) -> Dict:
        """
        趋势分析
        
        输入:
        - data_series: 时间序列数据
        - period: 分析周期(daily/weekly/monthly)
        - metrics: 分析指标
        
        输出:
        - trend: 趋势方向(上升/下降/稳定)
        - forecast: 预测值
        - recommendations: 建议
        """
        pass
```

#### 1.3 智能决策 Skills
```python
# artpm_agent/skills/decision_skills.py

class ProjectEvaluatorSkill(BaseSkill):
    """项目评估Skill - 评估项目可行性"""
    
    skill_name = "project_evaluator"
    description = "评估项目的可行性、风险和收益"
    
    async def execute(self, context: Dict) -> Dict:
        """
        项目评估
        
        输入:
        - project_data: 项目数据(报价、成本、周期等)
        - historical_data: 历史项目数据
        - constraints: 约束条件
        
        输出:
        - feasibility_score: 可行性评分(0-100)
        - risk_analysis: 风险分析
        - recommendations: 决策建议
        - similar_projects: 相似历史项目
        """
        pass

class ResourceOptimizerSkill(BaseSkill):
    """资源优化Skill - 优化人员和资源分配"""
    
    skill_name = "resource_optimizer"
    description = "优化团队资源分配和排期"
    
    async def execute(self, context: Dict) -> Dict:
        """
        资源优化
        
        输入:
        - tasks: 任务列表
        - team_members: 团队成员列表
        - constraints: 约束条件(技能匹配、工作量等)
        
        输出:
        - optimized_allocation: 优化的分配方案
        - workload_balance: 工作量平衡分析
        - timeline: 优化后的时间线
        """
        pass
```

---

### Phase 2: MCP集成架构

#### 2.1 增强型MCP客户端
```python
# artpm_agent/core/mcp_client_enhanced.py

class EnhancedMCPClient:
    """增强型MCP客户端 - 连接Claude Code能力"""
    
    def __init__(self):
        # 集成Claude Code的工具能力
        self.available_tools = {
            "read": self._read_file,
            "write": self._write_file,
            "edit": self._edit_file,
            "glob": self._glob_files,
            "grep": self._grep_content,
            "bash": self._execute_bash,
            "agent": self._spawn_agent,
        }
    
    async def _read_file(self, file_path: str) -> str:
        """读取文件"""
        # 调用 Read tool
        pass
    
    async def _glob_files(self, pattern: str, path: str = None) -> List[str]:
        """搜索文件"""
        # 调用 Glob tool
        pass
    
    async def _grep_content(self, pattern: str, path: str = None) -> Dict:
        """搜索内容"""
        # 调用 Grep tool
        pass
    
    async def _spawn_agent(self, task: str, context: Dict) -> str:
        """生成子Agent处理复杂任务"""
        # 调用 Agent tool
        pass
```

#### 2.2 Skill注册和路由增强
```python
# artpm_agent/skills/skill_router_enhanced.py

class EnhancedSkillRouter:
    """增强型Skill路由器 - 支持MCP Skills"""
    
    def __init__(self, context: Dict):
        self.context = context
        self.mcp_client = EnhancedMCPClient()
        
        # 注册本地Skills
        self.local_skills = self._load_local_skills()
        
        # 注册MCP Skills
        self.mcp_skills = self._load_mcp_skills()
    
    async def execute_skill(self, skill_name: str, inputs: Dict) -> Dict:
        """
        执行Skill
        
        优先级:
        1. 本地Skills (直接执行,低延迟)
        2. MCP Skills (通过MCP调用)
        3. AI增强 (使用LLM辅助)
        """
        # 检查是否是本地Skill
        if skill_name in self.local_skills:
            return await self.local_skills[skill_name].execute(inputs)
        
        # 检查是否是MCP Skill
        if skill_name in self.mcp_skills:
            return await self.mcp_client.call_skill(skill_name, inputs)
        
        # 使用AI生成执行计划
        return await self._ai_enhanced_execution(skill_name, inputs)
    
    async def _ai_enhanced_execution(self, skill_name: str, inputs: Dict) -> Dict:
        """
        AI增强执行 - 当没有精确匹配的Skill时
        
        使用LLM分析任务,生成执行计划,组合多个Skills
        """
        pass
```

---

### Phase 3: 实际应用场景

#### 场景1: 智能报价单分析
```python
# 用户: "帮我分析这个报价单,看看能不能接"

# Agent执行流程:
1. document_classifier_parser (现有)
   - 解析Excel报价单
   - 提取项目信息、资产列表、金额

2. file_reader (新增MCP Skill)
   - 读取文件详细内容
   - 识别特殊条款、备注

3. quote_calculator (现有)
   - 计算利润率
   - 成本分析

4. project_evaluator (新增Skill)
   - 评估项目可行性
   - 对比历史相似项目
   - 风险分析

5. trend_analyzer (新增Skill)
   - 分析客户历史项目趋势
   - 价格趋势分析

# 输出:
- 利润率: 18.5% (良好)
- 可行性评分: 82/100
- 风险: 中等 (工期紧张)
- 建议: 可以接,但需要优化排期
- 相似项目: 找到3个类似项目,平均利润率17.2%
```

#### 场景2: 智能任务分配
```python
# 用户: "这个项目有20个角色模型,帮我分配给团队"

# Agent执行流程:
1. data_analyzer (新增Skill)
   - 分析任务列表
   - 统计资产类型、数量、复杂度

2. file_search (新增MCP Skill)
   - 搜索团队成员技能文档
   - 搜索历史项目分配记录

3. resource_optimizer (新增Skill)
   - 根据技能匹配分配任务
   - 平衡工作量
   - 优化时间线

4. task_allocator (现有,增强)
   - 生成分配方案
   - 创建排期表

# 输出:
- 张三: 高精度角色 × 5 (预计30h)
- 李四: 中精度角色 × 8 (预计32h)
- 王五: 低精度角色 × 7 (预计28h)
- 工作量平衡度: 95%
- 预计完成时间: 2周
```

#### 场景3: 项目进度预警
```python
# 用户: "检查一下项目进度,有没有风险"

# Agent执行流程:
1. progress_tracker (现有)
   - 检查所有项目状态
   - 识别延期风险

2. data_analyzer (新增Skill)
   - 分析进度数据
   - 计算完成率

3. bash (MCP Skill)
   - 查询数据库获取详细数据
   - 生成进度报告

4. trend_analyzer (新增Skill)
   - 分析进度趋势
   - 预测完成时间

5. reminder_bot (现有)
   - 生成催办消息
   - 发送提醒

# 输出:
- ⚠️ 高风险: 项目A (延期2天,完成率65%)
- ⚠️ 中风险: 项目B (明天截止,完成率88%)
- ✅ 正常: 项目C、D、E
- 自动发送催办消息给负责人
```

---

## 🚀 实施步骤

### Week 1: 基础MCP Skills
- [ ] 开发 `file_reader` Skill
- [ ] 开发 `file_search` Skill
- [ ] 集成 Bash 执行能力
- [ ] 测试文件操作

### Week 2: 数据分析Skills
- [ ] 开发 `data_analyzer` Skill
- [ ] 开发 `trend_analyzer` Skill
- [ ] 集成历史数据分析
- [ ] 可视化支持

### Week 3: 智能决策Skills
- [ ] 开发 `project_evaluator` Skill
- [ ] 开发 `resource_optimizer` Skill
- [ ] 集成RAG检索历史项目
- [ ] AI增强决策

### Week 4: 集成和优化
- [ ] 增强MCP客户端
- [ ] Skill联动优化
- [ ] 性能优化
- [ ] 完整场景测试

---

## 📊 预期效果

### 功能提升
- ✅ 文件操作能力 → 自动读取和分析项目文档
- ✅ 数据分析能力 → 深度分析项目数据和趋势
- ✅ 智能决策能力 → AI辅助项目决策和资源优化
- ✅ Skill联动 → 多Skill协作完成复杂任务

### 用户体验提升
- 🚀 自动化程度提升 70%
- 🚀 决策准确度提升 50%
- 🚀 响应速度提升 40%
- 🚀 用户操作减少 60%

---

## 🔧 技术栈

```yaml
Skills开发:
  - 基础: Python async/await
  - 数据处理: pandas, openpyxl
  - 分析: scipy, scikit-learn
  - 可视化: plotly, matplotlib

MCP集成:
  - 文件操作: Read, Write, Edit, Glob
  - 搜索: Grep
  - 执行: Bash
  - 协作: Agent

AI能力:
  - LLM: Claude 3.5 Sonnet / GPT-4
  - RAG: FAISS向量检索
  - Prompt Engineering: Few-shot, Chain-of-Thought
```

---

下一步: 选择从哪个Phase开始实现?
