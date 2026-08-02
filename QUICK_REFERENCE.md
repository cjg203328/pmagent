# ArtPM Agent 速查手册

## 🎯 快速定位

| 我想... | 去哪里看 |
|---------|---------|
| 了解项目核心价值 | [PROJECT_ANALYSIS_2026-07-22.md](PROJECT_ANALYSIS_2026-07-22.md) 第 1 节 |
| 理解架构设计 | [ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md) |
| 快速启动项目 | [QUICKSTART.md](QUICKSTART.md) |
| 排查配置问题 | `python -m artpm_agent.tools.check_config` |
| 查看运行成本 | Streamlit UI → 可观测面板 |
| 修改模型配置 | `.env` 文件 → `LLM_PROVIDER`/`LLM_MODEL` |
| 添加新技能 | 继承 `BaseSkill` → 注册到 `SKILL_METADATA` |
| 调试故障转移 | [artpm_agent/providers/gateway.py:24](artpm_agent/providers/gateway.py#L24) |
| 优化性能 | [PROJECT_ANALYSIS_2026-07-22.md](PROJECT_ANALYSIS_2026-07-22.md) 第 7.2 节 |
| 生产部署 | [PROJECT_ANALYSIS_2026-07-22.md](PROJECT_ANALYSIS_2026-07-22.md) 第 6 节 |

---

## 📁 目录结构速查

```
pmagent/
├── artpm_agent/              # 核心代码包
│   ├── agent.py              # 🔥 Agent 主类 (1300+ 行)
│   ├── app.py                # 🔥 Streamlit 入口 (70 行)
│   ├── skills/               # 🔥 业务技能实现
│   │   ├── skill_router.py   # 路由核心逻辑
│   │   ├── smart_task_allocator.py
│   │   └── smart_progress_tracker.py
│   ├── runtime/              # 🔥 运行时系统
│   │   ├── agent_loop.py     # 底层循环
│   │   ├── agent_runtime.py  # 高级运行时
│   │   └── telemetry.py      # 遥测记录
│   ├── providers/            # 🔥 模型网关
│   │   ├── gateway.py        # 故障转移核心
│   │   └── structured.py     # 结构化输出
│   ├── memory/               # 记忆系统
│   │   ├── memory_manager.py # 统一接口
│   │   └── vector_store.py   # FAISS 索引
│   ├── harness/              # Turn 服务层
│   │   └── turn_service.py   # 编排逻辑
│   ├── views/                # UI 页面
│   │   ├── chat.py           # 对话页
│   │   ├── settings.py       # 设置页
│   │   └── observability.py  # 可观测面板
│   ├── database/             # 数据模型
│   ├── parsers/              # 文档解析器
│   ├── core/                 # MCP 客户端
│   └── utils/                # 工具函数
├── data/                     # 数据目录
│   ├── artpm.db              # 业务数据库
│   ├── memory.db             # 记忆数据库
│   ├── telemetry.db          # 遥测数据库
│   └── vector_store/         # FAISS 索引
├── tests/                    # 测试代码
├── docs/                     # 文档
├── .env                      # 🔥 环境配置 (从 .env.example 复制)
├── requirements.txt          # Python 依赖
├── pyproject.toml            # 项目元数据
└── start.bat / start.sh      # 启动脚本
```

---

## ⚙️ 关键配置项

### 必需配置

```env
# 主模型配置
LLM_PROVIDER=anthropic          # anthropic | openai | zhipu
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=              # 留空 = 离线模式

# 数据路径
DB_PATH=./data/artpm.db
MEMORY_DB_PATH=./data/memory.db
VECTOR_DB_PATH=./data/vector_store
```

### 性能优化 (可选)

```env
# Phase 1 快赢优化
ENABLE_CONNECTION_POOLING=true       # 数据库连接池
ENABLE_LAZY_SKILL_LOADING=true       # 技能延迟加载
ENABLE_STREAMING_PROGRESS=true       # 流式进度提示
ENABLE_ADAPTIVE_VECTORS=true         # 自适应向量维度

# 响应缓存
ARTPM_RESPONSE_CACHE=true
ARTPM_RESPONSE_CACHE_TTL=1800
REDIS_URL=redis://localhost:6379/0   # 可选 Redis 加速
```

### 故障转移配置

```env
# ⚠️ 候选模型必须与主模型同 Provider
LLM_AVAILABLE_MODELS=["gpt-4o","gpt-4o-mini","gpt-3.5-turbo"]

# 故障转移参数
LLM_FAILOVER_MAX_ATTEMPTS=2          # 最多尝试 2 次候选
LLM_REQUEST_TIMEOUT_SECONDS=12       # 主模型超时
LLM_FAILOVER_REQUEST_TIMEOUT_SECONDS=8  # 候选模型超时
```

### MCP 集成 (可选)

```env
MCP_ENABLED=false                    # 默认关闭
MCP_TRANSPORT=stdio                  # stdio (推荐) | http
MCP_HEARTBEAT_INTERVAL=30            # 心跳间隔
SKILLS_FORGE_KEY=                    # Skills Forge API Key
MCP_ALLOW_COMMANDS=false             # 命令执行权限
MCP_COMMAND_ALLOWLIST=               # 白名单 (逗号分隔)
```

### 多模态文档 (可选)

```env
# MinerU 高级文档引擎
MINERU_ENABLED=true
MINERU_MODE=auto                     # auto | local | remote | disabled
MINERU_API_URL=                      # remote 模式 URL
MINERU_TIMEOUT_SECONDS=600

# PaddleOCR (图片 OCR)
# 安装: pip install -e ".[ocr]"
```

### 生产部署

```env
ARTPM_ENV=production
ARTPM_GATEWAY_SHARED_SECRET=<random-256-bit>  # 生产必需
BASIC_AUTH_USER=admin
BASIC_AUTH_HASH=$2a$14$...                    # bcrypt 哈希

# CORS 白名单
ARTPM_CORS_ORIGINS=https://your-domain.com

# 日志配置
ARTPM_LOG_LEVEL=INFO
ARTPM_LOG_ROTATION=time
ARTPM_LOG_BACKUP_COUNT=14
```

---

## 🚀 常用命令

### 启动与检查

```bash
# 推荐: 带配置检查的启动
python start_with_checks.py

# 或者: 快捷脚本
start.bat              # Windows
./start.sh             # Linux/Mac

# 手动启动
python -m streamlit run artpm_agent/app.py

# 配置检查 (单独运行)
python -m artpm_agent.tools.check_config

# 健康检查
python health_check.py
```

### 测试与质量

```bash
# 运行所有测试
pytest -q

# 测试覆盖率
pytest --cov=artpm_agent --cov-report=html

# 工作流模块覆盖率 (50%+ 门禁)
pytest tests/test_workflow_*.py \
  --cov=artpm_agent.workflows \
  --cov-fail-under=50

# 性能基准 (30 样本)
python -m benchmarks.core_performance \
  --samples 30 \
  --enforce  # 劣化时失败

# 代码风格检查
ruff check artpm_agent tests

# 类型检查
mypy artpm_agent

# 安全扫描
bandit -r artpm_agent
safety check
```

### 数据管理

```bash
# 备份所有数据
python scripts/backup_data.py

# 数据库迁移 (Alembic)
alembic upgrade head
alembic revision --autogenerate -m "description"
```

### API Gateway

```bash
# 启动 REST API (默认端口 8765)
python -m artpm_agent.api

# 自定义端口
ARTPM_API_PORT=9000 python -m artpm_agent.api
```

---

## 🔍 故障排查流程

### 问题: "服务繁忙" 或 "未收到有效回答"

**排查步骤**:

1. **检查配置**:
   ```bash
   python -m artpm_agent.tools.check_config
   ```
   常见问题:
   - ❌ 候选模型跨 Provider (如 OpenAI 主模型 + Deepseek 候选)
   - ❌ API Key 是占位符 (如 `sk-your-key-here`)
   - ❌ 主模型与 API Key 不匹配

2. **查看遥测数据**:
   - 打开 Streamlit → 左侧导航 → "可观测"
   - 检查"连接情况"部分:
     - 成功率是否低于 80%
     - 错误类型分布 (auth/timeout/rate_limit)
     - 端点健康度

3. **查看日志**:
   ```bash
   tail -f artpm_agent/logs/artpm.log
   ```
   关键词搜索:
   - `ERROR`
   - `Failover`
   - `authentication failed`
   - `timeout`

4. **测试网络连接**:
   ```python
   # 测试 Anthropic API
   curl -H "x-api-key: YOUR_KEY" \
     -H "anthropic-version: 2023-06-01" \
     https://api.anthropic.com/v1/messages

   # 测试 OpenAI API
   curl -H "Authorization: Bearer YOUR_KEY" \
     https://api.openai.com/v1/models
   ```

5. **清理缓存**:
   ```bash
   # 清理响应缓存
   rm -rf data/.cache/

   # 重启 Redis (如果使用)
   redis-cli FLUSHDB
   ```

### 问题: 启动慢 (>2 秒)

**优化检查**:

```bash
# 验证优化开关
grep "ENABLE_" .env

# 预期输出:
# ENABLE_CONNECTION_POOLING=true
# ENABLE_LAZY_SKILL_LOADING=true
# ENABLE_STREAMING_PROGRESS=true
# ENABLE_ADAPTIVE_VECTORS=true

# 测量启动时间
time python -c "from artpm_agent.agent import ArtPMAgent; ArtPMAgent()"
# 预期: < 0.5 秒
```

### 问题: 文档解析失败

**诊断步骤**:

1. **检查文件格式支持**:
   ```python
   from artpm_agent.skills.skill_router import _SUPPORTED_DOCUMENT_SUFFIXES
   print(_SUPPORTED_DOCUMENT_SUFFIXES)
   # {'.xlsx', '.xls', '.pdf', '.docx', '.png', '.jpg', ...}
   ```

2. **检查 MinerU 状态**:
   ```bash
   # 测试 local 模式
   mineru --version

   # 测试 remote 模式
   curl -X POST MINERU_API_URL/health
   ```

3. **查看解析日志**:
   ```bash
   grep "MinerU" artpm_agent/logs/artpm.log
   grep "fallback" artpm_agent/logs/artpm.log
   ```

### 问题: 向量检索不工作

**检查清单**:

```bash
# 1. 验证 FAISS 安装
python -c "import faiss; print(faiss.__version__)"

# 2. 检查向量库目录
ls -la data/vector_store/
# 预期文件: index.faiss, metadata.json

# 3. 检查嵌入配置
grep "EMBEDDING_" .env
# EMBEDDING_PROVIDER=local_feature_hash
# EMBEDDING_DIMENSION=1536

# 4. 重建索引 (如果损坏)
rm -rf data/vector_store/
# 重启应用自动重建
```

---

## 📊 性能基准参考

### 启动时间

| 配置 | 时间 | 说明 |
|------|------|------|
| 未优化 | ~2.0s | 所有优化关闭 |
| Phase 1 优化 | ~0.5s | 懒加载 + 连接池 |
| 目标 | <0.3s | 进一步优化空间 |

### 响应延迟

| 场景 | P50 | P95 | 说明 |
|------|-----|-----|------|
| 离线技能 (利润测算) | <50ms | <100ms | 纯计算 |
| 数据库查询 (任务分配) | <200ms | <500ms | SQLite 查询 |
| LLM 调用 (Claude) | ~2s | ~5s | 网络 + 模型推理 |
| 文档解析 (Excel) | ~100ms | ~300ms | 本地解析 |
| 文档解析 (MinerU) | ~3s | ~10s | 重量级引擎 |

### Token 成本估算

| 模型 | Input | Output | 1k 对话成本 |
|------|-------|--------|------------|
| gpt-4o | $0.0025/1k | $0.01/1k | ~$0.05 |
| gpt-4o-mini | $0.00015/1k | $0.0006/1k | ~$0.003 |
| claude-3-5-sonnet | $0.003/1k | $0.015/1k | ~$0.06 |
| 缓存命中 | $0 | $0 | $0 |

---

## 🛠️ 开发指南

### 添加新技能

1. **创建技能类**:
   ```python
   # artpm_agent/skills/my_new_skill.py
   from .base_skill import BaseSkill
   from .input_schemas import BUILTIN_SKILL_INPUT_SCHEMAS

   class MyNewSkill(BaseSkill):
       skill_name = "my_new_skill"
       description = "我的新技能描述"
       version = "1.0"
       input_schema = BUILTIN_SKILL_INPUT_SCHEMAS[skill_name]

       def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
           # 实现业务逻辑
           result = {"success": True, "data": {}}
           return result
   ```

2. **注册 Schema**:
   ```python
   # artpm_agent/skills/input_schemas.py
   BUILTIN_SKILL_INPUT_SCHEMAS["my_new_skill"] = {
       "type": "object",
       "properties": {
           "param1": {"type": "string"},
           "param2": {"type": "number"}
       },
       "required": ["param1"]
   }
   ```

3. **注册元数据**:
   ```python
   # artpm_agent/skills/skill_router.py
   SKILL_METADATA["my_new_skill"] = {
       "name": "my_new_skill",
       "display_name": "我的新技能",
       "description": "...",
       "keywords": ["关键词1", "关键词2"],
       "class": MyNewSkill
   }
   ```

### 自定义 Provider

```python
# artpm_agent/providers/custom_provider.py
from artpm_agent.utils import BaseLLMClient

class CustomProviderClient(BaseLLMClient):
    def chat(self, messages, **kwargs):
        # 实现自定义 Provider 调用
        response = self._call_custom_api(messages)
        return response

# 在 agent.py 中注册
from artpm_agent.providers.custom_provider import CustomProviderClient
client_factory = lambda config: CustomProviderClient(config)
agent = ArtPMAgent(config, client_factory=client_factory)
```

### 添加遥测指标

```python
from artpm_agent.runtime.telemetry import get_telemetry_recorder

recorder = get_telemetry_recorder()

# 记录自定义事件
recorder.record_custom_metric(
    metric_name="skill_execution_time",
    value=0.123,  # 秒
    labels={"skill": "my_skill"}
)
```

---

## 🔗 相关文档

- [完整项目分析](PROJECT_ANALYSIS_2026-07-22.md)
- [架构图谱](ARCHITECTURE_DIAGRAM.md)
- [快速启动指南](QUICKSTART.md)
- [故障排查指南](docs/TROUBLESHOOTING.md)
- [部署指南](docs/DEPLOYMENT_GUIDE.md)
- [实施报告](docs/COMPLETE_IMPLEMENTATION_REPORT.md)

---

**版本**: v1.0  
**适用项目版本**: v0.2.0  
**最后更新**: 2026-07-22
