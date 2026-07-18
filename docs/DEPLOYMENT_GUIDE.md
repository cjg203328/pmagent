# ArtPM Agent 部署与使用指南

本指南涵盖从本地开发到生产部署的完整流程，以及如何启用新增的优化功能。

---

## 快速开始（本地使用）

### 方式1: 直接运行（最简单）

```bash
# 1. 安装依赖
python -m pip install -e .

# 2. 配置环境变量（可选）
cp .env.example .env
# 编辑 .env 填写 API Key 等配置

# 3. 启动应用
streamlit run artpm_agent/app.py --server.address 127.0.0.1

# 或使用 Windows 启动脚本
start.bat
```

**访问**: http://localhost:8501

**特点**:
- ✅ 零配置快速启动
- ✅ 离线模式可用（无需 API Key）
- ✅ 自动热重载（开发模式）

---

### 方式2: 启用优化功能

新增的优化功能**默认关闭**，需要手动启用：

#### Step 1: 修改 .env 文件

```env
# ========================================
# 基础配置（必填）
# ========================================
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=your_api_key_here

# ========================================
# 优化功能开关（可选，建议启用）
# ========================================

# Phase 1: 快赢优化
ENABLE_CONNECTION_POOLING=true        # 数据库连接池（推荐）
ENABLE_LAZY_SKILL_LOADING=true        # 技能懒加载（推荐）
ENABLE_STREAMING_PROGRESS=true        # 流式进度反馈
ENABLE_FRIENDLY_ERRORS=true           # 友好错误消息
ENABLE_ADAPTIVE_VECTORS=true          # 自适应向量检索

# Phase 2: 算法优化
ENABLE_HYBRID_INTENT_SCORING=true     # 混合意图打分
ENABLE_MODEL_HEALTH_TRACKING=true     # 模型健康追踪

# ========================================
# 连接池配置（启用连接池时生效）
# ========================================
DB_POOL_SIZE=5                        # 连接池大小
DB_POOL_MAX_OVERFLOW=10               # 最大溢出连接数
DB_POOL_TIMEOUT=30                    # 获取连接超时（秒）

# ========================================
# 向量存储配置
# ========================================
VECTOR_INDEX_TYPE=auto                # auto/flat/ivf/hnsw
VECTOR_IVF_NLIST=auto                 # IVF 簇数量

# ========================================
# 意图路由配置
# ========================================
INTENT_KEYWORD_WEIGHT=0.3             # 关键词权重
INTENT_EMBEDDING_WEIGHT=0.5           # 嵌入权重
INTENT_LLM_WEIGHT=0.2                 # LLM 权重
```

#### Step 2: 验证优化生效

```python
# 在 Python 中验证
from artpm_agent.bugfixes import get_optimization_registry

registry = get_optimization_registry()
status = registry.get_status()

for opt, enabled in status.items():
    print(f"{opt}: {'✅ 启用' if enabled else '❌ 禁用'}")
```

**输出示例**:
```
connection_pooling: ✅ 启用
lazy_skill_loading: ✅ 启用
streaming_progress: ✅ 启用
friendly_errors: ✅ 启用
adaptive_vectors: ✅ 启用
hybrid_intent_scoring: ✅ 启用
model_health_tracking: ✅ 启用
```

---

## 生产部署（推荐）

### Docker 部署（完整栈）

#### Step 1: 准备配置

```bash
# 1. 复制环境变量模板
cp .env.example .env

# 2. 编辑 .env，至少配置：
#    - ANTHROPIC_API_KEY（或其他 LLM Key）
#    - BASIC_AUTH_USER 和 BASIC_AUTH_HASH（生产必需）
```

#### Step 2: 生成认证凭据

```bash
# 生成 Basic Auth 密码哈希
docker run --rm caddy:2-alpine caddy hash-password
# 输入密码，复制输出的哈希值

# 在 .env 中配置
BASIC_AUTH_USER=admin
BASIC_AUTH_HASH=$2a$14$...  # 刚才生成的哈希
```

#### Step 3: 配置域名（可选）

```bash
# 本地测试
export SITE_ADDRESS=localhost

# 生产环境（需要域名解析到本机）
export SITE_ADDRESS=pmagent.yourdomain.com
```

#### Step 4: 启动服务

```bash
# 构建并启动（自动拉起 Caddy + Redis + ArtPM Agent）
docker compose up -d --build

# 查看日志
docker compose logs -f

# 查看服务状态
docker compose ps
```

**访问**:
- 本地: https://localhost （需信任自签名证书）
- 生产: https://pmagent.yourdomain.com

**包含服务**:
- ✅ ArtPM Agent (Streamlit)
- ✅ Caddy (反向代理 + 自动 HTTPS)
- ✅ Redis (缓存层)
- ✅ 全站 Basic Auth 保护

#### Step 5: 验证部署

```bash
# 健康检查
curl -u admin:your_password https://localhost/healthz

# 查看优化状态
docker compose exec artpm-agent python -c "
from artpm_agent.bugfixes import get_optimization_registry
print(get_optimization_registry().get_status())
"
```

---

## 性能验证

### 1. 启动时间测试

```bash
# 测试启动时间
time python -c "
from artpm_agent.agent import ArtPMAgent
agent = ArtPMAgent()
print('Agent initialized')
"
```

**预期结果**:
- 关闭懒加载: ~2.0s
- 开启懒加载: ~0.5s

### 2. 连接池性能测试

```python
from artpm_agent.database.connection_pool import get_db_manager
import time

manager = get_db_manager()

# 测试 100 次连接
start = time.time()
for _ in range(100):
    session = manager.get_session("data/artpm.db")
    session.close()
duration = time.time() - start

print(f"100 connections: {duration:.2f}s")
print(f"Avg per connection: {duration*10:.2f}ms")
```

**预期结果**:
- 无连接池: ~5s (50ms/conn)
- 有连接池: <1s (5ms/conn)

### 3. 向量检索性能测试

```python
import pytest

# 运行性能基准测试
pytest tests/test_phase1_optimizations.py::test_vector_store_performance_comparison -v -s
```

**预期输出**:
```
Flat search: 98.5ms
IVF search: 8.2ms
Speedup: 12.0x
```

### 4. 端到端测试

```bash
# 运行完整测试套件
pytest tests/ -v --tb=short

# 只运行优化相关测试
pytest tests/test_phase1_optimizations.py -v
pytest tests/integration/test_turn_flow.py -v
```

---

## 分阶段部署建议

### 阶段 1: 金丝雀（10% 流量，3-7 天）

**启用优化**:
```env
ENABLE_CONNECTION_POOLING=true
ENABLE_FRIENDLY_ERRORS=true
```

**监控指标**:
- 数据库连接数
- 错误率
- 用户反馈

**回滚条件**:
- 错误率增加 >5%
- 用户投诉增加

---

### 阶段 2: 灰度（50% 流量，1-2 周）

**新增启用**:
```env
ENABLE_LAZY_SKILL_LOADING=true
ENABLE_ADAPTIVE_VECTORS=true
```

**监控指标**:
- 启动时间
- 向量检索延迟
- 内存占用
- CPU 使用率

**回滚条件**:
- 启动时间增加（不应该发生）
- 内存泄漏

---

### 阶段 3: 全量（100% 流量）

**全部启用**:
```env
ENABLE_STREAMING_PROGRESS=true
ENABLE_HYBRID_INTENT_SCORING=true
ENABLE_MODEL_HEALTH_TRACKING=true
```

**持续监控**:
- 所有性能指标
- 用户满意度
- 意图识别准确率

---

## 快速回滚

### 方式 1: 禁用单个优化

```env
# 在 .env 中设置为 false
ENABLE_CONNECTION_POOLING=false
```

```bash
# 重启服务
docker compose restart artpm-agent
# 或本地
streamlit run artpm_agent/app.py
```

### 方式 2: 禁用所有优化

```python
# 在代码中临时禁用
from artpm_agent.bugfixes import get_optimization_registry

registry = get_optimization_registry()
for opt in registry.get_status():
    registry.disable(opt)
```

### 方式 3: 回滚到优化前版本

```bash
# 回退到优化前的提交
git checkout 66efbc7  # v0.2 版本

# 重新部署
docker compose up -d --build
```

---

## 故障排查

### 问题 1: 优化没有生效

**症状**: 启动时间仍然很慢

**排查**:
```python
# 检查优化状态
from artpm_agent.bugfixes import get_optimization_registry
print(get_optimization_registry().get_status())

# 检查环境变量
import os
print(os.getenv('ENABLE_LAZY_SKILL_LOADING'))
```

**解决**:
- 确认 .env 文件路径正确
- 确认环境变量已加载（重启服务）

---

### 问题 2: 连接池导致数据库锁

**症状**: `database is locked` 错误

**原因**: SQLite WAL 模式不支持某些网络文件系统

**解决**:
```env
# 禁用连接池
ENABLE_CONNECTION_POOLING=false

# 或使用本地磁盘存储数据库
DB_PATH=./local_data/artpm.db
```

---

### 问题 3: 向量检索变慢

**症状**: 升级到 IVF 索引后反而变慢

**原因**: 数据量不足，IVF 未训练

**解决**:
```env
# 强制使用 Flat 索引
VECTOR_INDEX_TYPE=flat

# 或等待数据量达到 1000+ 后自动升级
```

---

## 监控与可观测

### 1. 访问可观测面板

```
http://localhost:8501
→ 点击左侧导航「可观测」
```

**查看内容**:
- Token 消耗统计
- 连接状态分布
- 模型健康度
- 错误类型分布

### 2. 导出遥测报告

```bash
python -m artpm_agent.runtime.telemetry_dashboard \
  --out telemetry_report.html \
  --window 500
```

### 3. 查看连接池状态

```python
from artpm_agent.database.connection_pool import get_db_manager

manager = get_db_manager()
status = manager.get_pool_status()

for db_path, stats in status.items():
    print(f"{db_path}:")
    print(f"  Size: {stats['size']}")
    print(f"  Checked in: {stats['checked_in']}")
    print(f"  Checked out: {stats['checked_out']}")
```

### 4. 查看向量存储统计

```python
from artpm_agent.memory.adaptive_vector_store import AdaptiveVectorStore

store = AdaptiveVectorStore("data/vector_store")
stats = store.get_stats()

print(f"Index type: {stats['index_type']}")
print(f"Vector count: {stats['count']}")
print(f"Dimension: {stats['dimension']}")
```

---

## 打包分发

### 方式 1: Docker 镜像

```bash
# 构建镜像
docker build -t artpm-agent:v0.3.0 .

# 导出镜像
docker save artpm-agent:v0.3.0 | gzip > artpm-agent-v0.3.0.tar.gz

# 在其他机器导入
docker load < artpm-agent-v0.3.0.tar.gz

# 运行
docker run -p 8501:8501 \
  -v $(pwd)/data:/app/data \
  -e ENABLE_CONNECTION_POOLING=true \
  artpm-agent:v0.3.0
```

### 方式 2: Python 包

```bash
# 构建 wheel
python -m build

# 生成文件在 dist/
ls dist/
# artpm_agent-0.3.0-py3-none-any.whl
# artpm_agent-0.3.0.tar.gz

# 在其他机器安装
pip install artpm_agent-0.3.0-py3-none-any.whl

# 运行
python -m artpm_agent.main
```

### 方式 3: 源码压缩包

```bash
# 打包源码
git archive --format=zip --output=artpm-agent-v0.3.0.zip HEAD

# 解压后使用
unzip artpm-agent-v0.3.0.zip
cd artpm-agent-v0.3.0
pip install -e .
streamlit run artpm_agent/app.py
```

---

## 配置推荐

### 开发环境

```env
# 快速启动，保留日志
ENABLE_CONNECTION_POOLING=true
ENABLE_LAZY_SKILL_LOADING=true
ENABLE_STREAMING_PROGRESS=true
ENABLE_FRIENDLY_ERRORS=true

# 禁用缓存，便于调试
ENABLE_ADAPTIVE_VECTORS=false
ENABLE_HYBRID_INTENT_SCORING=false
```

### 测试环境

```env
# 全部启用，验证功能
ENABLE_CONNECTION_POOLING=true
ENABLE_LAZY_SKILL_LOADING=true
ENABLE_STREAMING_PROGRESS=true
ENABLE_FRIENDLY_ERRORS=true
ENABLE_ADAPTIVE_VECTORS=true
ENABLE_HYBRID_INTENT_SCORING=true
ENABLE_MODEL_HEALTH_TRACKING=true
```

### 生产环境

```env
# 全部启用，最优性能
ENABLE_CONNECTION_POOLING=true
ENABLE_LAZY_SKILL_LOADING=true
ENABLE_STREAMING_PROGRESS=true
ENABLE_FRIENDLY_ERRORS=true
ENABLE_ADAPTIVE_VECTORS=true
ENABLE_HYBRID_INTENT_SCORING=true
ENABLE_MODEL_HEALTH_TRACKING=true

# 连接池配置
DB_POOL_SIZE=10
DB_POOL_MAX_OVERFLOW=20

# Redis 缓存（推荐）
REDIS_URL=redis://redis:6379/0
```

---

## 常见问题

### Q: 新功能会影响现有功能吗？

A: 不会。所有优化都是**可选的**，默认关闭。现有功能完全不受影响。

### Q: 启用优化后能回滚吗？

A: 可以。只需将对应的环境变量设为 `false` 并重启服务即可。

### Q: 优化需要重新训练模型吗？

A: 不需要。所有优化都是**架构层面**的，不涉及模型训练。

### Q: Docker 部署必须用域名吗？

A: 不必须。本地测试用 `localhost` 即可。生产环境建议用域名以获取正式 SSL 证书。

### Q: Redis 是必需的吗？

A: 不是。Redis 是**可选的加速层**，不影响核心功能。未配置时自动降级到 SQLite。

---

## 下一步

1. ✅ 本地测试所有优化
2. ✅ 金丝雀部署（10% 流量）
3. ⏳ 监控 3-7 天
4. ⏳ 灰度扩大（50% 流量）
5. ⏳ 全量部署（100% 流量）

**推荐阅读**:
- [完整实施报告](COMPLETE_IMPLEMENTATION_REPORT.md)
- [Phase 1 优化报告](PHASE1_OPTIMIZATION_REPORT.md)
- [优化策略文档](OPTIMIZATION_STRATEGY_MULTI_PERSPECTIVE.md)

---

**维护者**: ArtPM Agent Team  
**最后更新**: 2026-07-18  
**版本**: v0.3.0
