# 🚀 MCP Skills 快速启动指南

## 📦 新增功能

ArtPM Agent现在支持**10个Skills**(原5个 + 新增5个MCP Skills):

### 新增的MCP Skills:
1. **file_reader** - 读取项目文件
2. **file_search** - 搜索文件和内容
3. **data_analyzer** - 深度数据分析
4. **trend_analyzer** - 趋势分析和预测
5. **project_evaluator** - 项目可行性评估

---

## ⚡ 快速开始

### 1. 启动应用

```bash
# 进入项目目录
cd d:\桌面\pmagent

# 启动应用
.\start.bat

# 或使用Python直接启动
cd artpm_agent
streamlit run app.py
```

### 2. 在聊天界面使用

打开浏览器访问 http://localhost:8501,在聊天界面中自然交互:

#### 示例1: 搜索文件
```
你: "帮我找一下所有的Excel报价单"
AI: 🔍 自动调用 file_search skill
    找到3个Excel文件:
    - 腾讯_Q2报价单.xlsx
    - 网易_角色项目.xlsx
    - 米哈游_场景报价.xlsx
```

#### 示例2: 分析项目
```
你: "分析一下这个项目能不能接: 报价30万,成本20万"
AI: 🤖 自动调用 quote_calculator + project_evaluator

    📊 综合分析:
    • 利润率: 18.5%
    • 可行性评分: 82/100
    • 风险等级: 中
    • 建议: 可以接单,但需注意成本控制
```

#### 示例3: 数据趋势
```
你: "分析一下最近的项目利润趋势"
AI: 📈 自动调用 data_analyzer + trend_analyzer

    趋势分析:
    • 平均利润率: 17.8%
    • 趋势: 稳定上升
    • 预测Q3利润率: 19.2%
```

---

## 🔧 开发者使用

### 直接调用Skills

```python
from agent import ArtPMAgent

# 初始化Agent
agent = ArtPMAgent()

# 方式1: 通过chat自然语言调用
response = agent.chat("找一下所有的报价单")

# 方式2: 直接调用Skill
from skills.mcp_skills import get_mcp_skill

skill = get_mcp_skill("file_search", {})
result = await skill.execute({
    "pattern": "*.xlsx",
    "directory": "projects",
    "limit": 10
})

print(result)
```

### 自定义Skill

```python
from skills.base_skill import BaseSkill
from core.mcp_client_enhanced import get_enhanced_mcp_client

class MyCustomSkill(BaseSkill):
    skill_name = "my_custom_skill"
    description = "我的自定义Skill"
    version = "1.0.0"

    def __init__(self, context):
        super().__init__(context)
        self.mcp_client = get_enhanced_mcp_client()

    async def execute(self, inputs):
        # 使用MCP工具
        result = await self.mcp_client.call_tool("read_file", {
            "file_path": inputs["file_path"]
        })

        # 处理逻辑
        # ...

        return {"success": True, "data": result}
```

---

## 📚 Skills参考

### 1. FileSearchSkill

**功能**: 搜索项目文件

**输入**:
```python
{
    "pattern": "*.xlsx",        # 必需: glob pattern
    "directory": "projects",     # 可选: 搜索目录
    "recursive": True,           # 可选: 递归搜索
    "limit": 10,                 # 可选: 结果数量限制
    "content_search": "利润率"   # 可选: 内容搜索关键词
}
```

**输出**:
```python
{
    "success": True,
    "files": ["file1.xlsx", "file2.xlsx"],
    "count": 2,
    "content_matches": [...]  # 如果有content_search
}
```

### 2. FileReaderSkill

**功能**: 读取文件内容

**输入**:
```python
{
    "file_path": "README.md",   # 必需: 文件路径
    "encoding": "utf-8",         # 可选: 文件编码
    "lines_limit": 100,          # 可选: 行数限制
    "summary": True              # 可选: 生成摘要(需要LLM)
}
```

**输出**:
```python
{
    "success": True,
    "content": "文件内容...",
    "metadata": {
        "file_name": "README.md",
        "file_size": 1234,
        "lines": 56
    },
    "summary": "文件摘要..."  # 如果请求
}
```

### 3. DataAnalyzerSkill

**功能**: 分析数据

**输入**:
```python
{
    "data_source": "data.csv",      # 必需: 文件路径或数据对象
    "analysis_type": "descriptive", # 可选: 分析类型
    "metrics": ["col1", "col2"],    # 可选: 指定列
    "visualize": True                # 可选: 生成可视化建议
}
```

**输出**:
```python
{
    "success": True,
    "analysis": {
        "rows": 100,
        "columns": 5,
        "statistics": {...}
    },
    "insights": ["洞察1", "洞察2"],
    "visualizations": "建议..."  # 如果请求
}
```

### 4. TrendAnalyzerSkill

**功能**: 趋势分析

**输入**:
```python
{
    "data_series": "timeseries.csv",  # 必需: 时间序列数据
    "time_column": "date",             # 可选: 时间列名
    "value_column": "profit",          # 可选: 值列名
    "period": "monthly",               # 可选: 分析周期
    "forecast": True                   # 可选: 是否预测
}
```

**输出**:
```python
{
    "success": True,
    "trend": "上升/下降/稳定",
    "statistics": {...},
    "forecast": "预测结果...",  # 如果请求
    "insights": [...]
}
```

### 5. ProjectEvaluatorSkill

**功能**: 项目评估

**输入**:
```python
{
    "project_data": {
        "quote_amount": 300000,
        "cost": 200000,
        "deadline": "2026-08-01"
    },
    "historical_data": "projects.csv",  # 可选: 历史数据
    "constraints": {...}                 # 可选: 约束条件
}
```

**输出**:
```python
{
    "success": True,
    "feasibility_score": 85,     # 0-100
    "profit_rate": 0.185,
    "risk_level": "低/中/高",
    "risk_analysis": {...},
    "recommendations": ["建议1", "建议2"],
    "similar_projects": [...]    # 如果有历史数据
}
```

---

## 🧪 测试

### 运行测试

```bash
cd artpm_agent

# 简化测试(推荐)
python test_mcp_simple.py

# 完整测试
python test_mcp_enhanced.py
```

### 测试输出示例

```
============================================================
ArtPM Agent - MCP Skills Test
============================================================

[Test 1] Available MCP Skills:
  - file_reader: 读取和分析项目文件
  - file_search: 搜索项目目录中的文件
  - data_analyzer: 分析结构化数据
  - trend_analyzer: 分析项目利润、成本、进度等趋势
  - project_evaluator: 评估项目的可行性、风险和收益

[Test 2] MCP Client Tools:
  - read_file: 读取文件内容
  - search_files: 搜索文件
  - search_content: 搜索文件内容
  - analyze_data: 分析结构化数据
  - execute_command: 执行系统命令

[Test 3] Search Python files in core/:
  Found 7 files:
    core\chat_agent.py
    core\llm_client.py
    core\mcp_client.py
    core\mcp_client_enhanced.py
    ...

✅ All tests passed!
```

---

## ❓ 常见问题

### Q1: MCP Skills和原有Skills有什么区别?

**原有Skills**: 专注于业务逻辑(利润计算、任务分配等)
**MCP Skills**: 提供通用能力(文件操作、数据分析等)

两者互补,共同构建完整的智能系统。

### Q2: 需要额外配置吗?

不需要! MCP Skills基于Python标准库实现,无需额外依赖或API Key。

### Q3: 如何查看所有可用Skills?

```python
from agent import ArtPMAgent

agent = ArtPMAgent()
skills = agent.list_skills()

for skill in skills:
    print(f"{skill['name']}: {skill['description']}")
```

### Q4: Skills执行失败怎么办?

Skills会返回详细的错误信息:

```python
result = await skill.execute(inputs)
if not result["success"]:
    print(f"Error: {result['error']}")
```

### Q5: 可以在Jupyter Notebook中使用吗?

可以! 所有Skills都支持异步执行:

```python
import asyncio
from skills.mcp_skills import get_mcp_skill

# Jupyter中使用await
skill = get_mcp_skill("file_search", {})
result = await skill.execute({"pattern": "*.xlsx"})
```

---

## 📞 支持

- 📖 完整文档: `MCP_SKILLS_COMPLETION_REPORT.md`
- 🎯 开发计划: `MCP_SKILLS_DEVELOPMENT_PLAN.md`
- 💻 测试代码: `artpm_agent/test_mcp_simple.py`
- 🐛 问题反馈: 项目Issues

---

## 🎉 开始使用

```bash
# 1. 启动应用
.\start.bat

# 2. 打开浏览器
http://localhost:8501

# 3. 在聊天界面尝试:
"帮我找一下所有的报价单"
"分析这个项目: 报价30万,成本20万"
"查看最近项目的利润趋势"
```

**祝使用愉快!** 🚀

---

生成时间: 2026-07-10
