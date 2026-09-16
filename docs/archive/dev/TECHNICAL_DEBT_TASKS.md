# ArtPM Agent - 技术债务清单与执行任务

## 2026-08-15 当前状态

- **P0-1 多租户隔离**：SQLAlchemy Session 已在 ORM 查询、更新和删除入口自动注入
  tenant/workspace 条件；PostgreSQL RLS 仍作为生产环境的第二道防线。跨租户 SQLite
  回归测试已加入 `tests/test_architecture_modernization.py`。
- **P0-2 权限检查**：`RequestOrchestrator` 默认挂载 `permission_preflight`，模型工具调用
  经过风险策略和一次性审批存储；裸 `AgentLoop` 仍要求宿主显式提供策略钩子，这是其
  provider-neutral 低级 API 的刻意边界。
- **P1-1 数据库生命周期**：`DatabaseManager` 已提供 `close()`/`dispose()`、上下文管理器、
  finalizer 和进程级最后回收；后续只需继续减少业务代码对隐式全局实例的依赖。
- **验证器**：`scripts/verify_optimization.py` 已改为检查当前分析报告/路线图，并将
  测试产生的 `__pycache__` 作为提示而非破坏性清理门禁。

## 1. 安全相关任务 (P0-P1)

### 1.1 多租户数据隔离 [P0-1]
**状态**: 需要修复
**优先级**: 立即
**影响**: 数据泄露风险

**任务分解**:
- [ ] 审计 `/artpm_agent/database/models.py` 中所有 SELECT 查询
- [ ] 检查缺失 tenant_id 过滤的查询 (预期: 5-10 处)
- [ ] 创建 `@with_tenant_scope` 装饰器
- [ ] 为以下表添加多租户单元测试:
  - [ ] Project
  - [ ] Asset
  - [ ] Task
  - [ ] KnowledgeBase
- [ ] 集成测试: 交叉租户查询应返回空

**文件清单**:
- 修改: `/artpm_agent/database/models.py`
- 创建: `/artpm_agent/security/tenant_scope.py` (装饰器)
- 创建: `/tests/test_tenant_isolation.py` (150+ LOC)

**验收标准**:
```python
# 测试用例
def test_project_isolation():
    """Project 查询必须包含 tenant_id 过滤"""
    tenant1_projects = db.query(Project).filter(
        (Project.tenant_id == 'tenant1') & ...
    ).all()

    # 直接查询 tenant2 不应返回 tenant1 数据
    assert all(p.tenant_id == 'tenant1' for p in tenant1_projects)
```

**工作量**: 3 天
**所有者**: 安全工程师

---

### 1.2 权限检查机制加固 [P0-2]
**状态**: 需要加强
**优先级**: 立即
**影响**: 权限绕过

**任务分解**:
- [ ] 在 `AgentLoop.execute_tool()` 添加权限检查 (不只是 API 层)
- [ ] 验证所有工具调用路径都受权限保护
- [ ] 创建权限检查测试套件

**代码位置**:
```python
# 修改: /artpm_agent/runtime/agent_loop.py:300-350
class ToolExecutor:
    def execute_tool(self, tool_call: ToolCall, context: Dict) -> ToolResult:
        # 新增: 权限验证
        if not self._check_permission(tool_call, context):
            return ToolResult(error="Permission denied", terminate=True)
        # ... 原有逻辑
```

**文件清单**:
- 修改: `/artpm_agent/runtime/agent_loop.py`
- 修改: `/artpm_agent/runtime/tools.py`
- 创建: `/tests/test_agent_loop_permissions.py`

**验收标准**:
```python
# 测试: 直接调用 RequestOrchestrator 仍需权限检查
with pytest.raises(PermissionError):
    orchestrator.chat("delete all files", context=unauthorized_context)
```

**工作量**: 2 天
**所有者**: 后端工程师

---

### 1.3 敏感数据脱敏 [P2-3]
**状态**: 部分实现
**优先级**: 本周
**影响**: 隐私泄露风险

**任务分解**:
- [ ] 创建 `sanitize_for_logging()` 函数
- [ ] 识别要脱敏的数据类型:
  - [ ] API Key (sk-*, DEEPSEEK_API_KEY 等)
  - [ ] 邮箱地址
  - [ ] 电话号码
  - [ ] 数据库连接字符串
- [ ] 在所有 logger.info/debug 调用前应用脱敏
- [ ] 集成到 structlog 配置

**代码位置**:
```python
# 创建: /artpm_agent/utils/sanitization.py

def sanitize_for_logging(text: str) -> str:
    """Remove sensitive data from log output"""
    import re
    # API Key
    text = re.sub(r'sk-\w+|OPENAI_API_KEY=\w+', '[REDACTED_KEY]', text)
    # Email
    text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[EMAIL]', text)
    # Phone
    text = re.sub(r'\+?1?\d{10,}', '[PHONE]', text)
    # Connection string password
    text = re.sub(r'password=\w+', 'password=[REDACTED]', text)
    return text
```

**测试**:
```python
# /tests/test_sanitization.py
def test_api_key_redaction():
    text = "Using key sk-abc123xyz"
    assert sanitize_for_logging(text) == "Using key [REDACTED_KEY]"
```

**文件清单**:
- 创建: `/artpm_agent/utils/sanitization.py`
- 修改: `/artpm_agent/utils/logger.py` (集成脱敏)
- 创建: `/tests/test_sanitization.py`

**工作量**: 1 天
**所有者**: 安全工程师

---

## 2. 资源管理任务 (P1)

### 2.1 数据库连接生命周期 [P1-1]
**状态**: ResourceWarning 告警
**优先级**: 本周
**影响**: 连接泄露

**问题代码** (`/artpm_agent/database/models.py:15-40`):
```python
_ACTIVE_ENGINES: WeakSet = WeakSet()

def _dispose_engine(engine):
    # 手动调用,容易遗漏
    _ACTIVE_ENGINES.discard(engine)
    engine.dispose()

atexit.register(_dispose_all_engines)  # 不保证执行顺序
```

**任务分解**:
- [ ] 创建 `DatabaseManager` 上下文管理器
- [ ] 替换 atexit 机制
- [ ] 添加连接池监控

**新实现**:
```python
# 修改: /artpm_agent/database/connection_pool.py

class DatabaseManager(ContextManager):
    def __init__(self, url: str, **kwargs):
        self.engine = None
        self.session_factory = None

    def __enter__(self):
        self.engine = create_engine(self.url,
            pool_pre_ping=True,  # 验证连接
            pool_recycle=3600,   # 1小时回收
            echo_pool=True       # 调试信息
        )
        self.session_factory = sessionmaker(bind=self.engine)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.engine:
            self.engine.dispose()
```

**使用**:
```python
# 原
engine = create_engine(url)
# 可能泄露

# 新
with DatabaseManager(url) as db:
    results = db.session.query(Project).all()
# 自动清理
```

**文件清单**:
- 创建: `/artpm_agent/database/connection_pool.py` (改进)
- 修改: `/artpm_agent/request_orchestrator.py` (使用新 API)
- 修改: `/artpm_agent/database/models.py` (移除 atexit)
- 创建: `/tests/test_connection_lifecycle.py`

**验收标准**:
```bash
# 运行测试,无 ResourceWarning
python -W default -m pytest tests/test_connection_lifecycle.py
```

**工作量**: 2 天
**所有者**: 后端工程师

---

### 2.2 异常处理层级化 [P1-2]
**状态**: 使用 `except Exception` 过度
**优先级**: 本周
**影响**: 难以调试,掩盖错误

**任务分解**:
- [ ] 创建异常层级
- [ ] 审计现有异常捕获 (估计 50+ 处)
- [ ] 按类型替换宽泛的 except
- [ ] 添加日志和上报

**新异常体系**:
```python
# 创建: /artpm_agent/exceptions.py

class ArtPMException(Exception):
    """Base exception for all ArtPM errors"""
    pass

class PermissionError(ArtPMException):
    """Permission denied"""
    pass

class TenantError(ArtPMException):
    """Tenant/workspace related errors"""
    pass

class ToolError(ArtPMException):
    """Tool execution errors"""
    pass

class ValidationError(ArtPMException):
    """Input validation errors"""
    pass

class DatabaseError(ArtPMException):
    """Database operation errors"""
    pass
```

**迁移示例** (`/artpm_agent/api/app.py`):
```python
# 原
except Exception as error:  # noqa: BLE001
    logger.error("request failed")
    return {"error": "internal error"}

# 新
except PermissionError as error:
    logger.warning("permission denied", user_id=..., tool=...)
    return {"error": "permission denied"}
except ValidationError as error:
    logger.info("validation failed", field=...)
    return {"error": str(error)}
except TenantError as error:
    logger.error("tenant error", exc_info=error)
    raise
except ArtPMException as error:
    logger.error("application error", exc_info=error)
    sentry_sdk.capture_exception(error)
    raise
except Exception as error:  # 最后防线
    logger.critical("unexpected error", exc_info=error)
    sentry_sdk.capture_exception(error)
    raise
```

**文件清单**:
- 创建: `/artpm_agent/exceptions.py` (70 LOC)
- 修改: `/artpm_agent/api/app.py` (40+ 处)
- 修改: `/artpm_agent/runtime/agent_loop.py` (10+ 处)
- 修改: `/artpm_agent/harness/` (各模块)
- 创建: `/tests/test_exceptions.py`

**工作量**: 3 天
**所有者**: 后端工程师

---

## 3. 性能优化任务 (P1-P2)

### 3.1 数据库 N+1 查询修复 [P1-3]
**状态**: 部分优化,有改进空间
**优先级**: 本周
**影响**: 查询延迟,数据库负载

**发现的问题** (`/artpm_agent/database/models.py`):
```python
# N+1 风险
for project in session.query(Project).all():
    tasks = session.query(Task).filter(Task.project_id == project.id).all()
    # 每个 project 一个查询!
```

**任务分解**:
- [ ] 审计所有 ORM 查询 (预期: 20-30 处)
- [ ] 添加 selectinload/joinedload
- [ ] 实现查询超时保护
- [ ] 添加查询日志

**修复示例**:
```python
# 原
projects = session.query(Project).all()

# 新 - 一次查询加载所有关联
projects = session.query(Project).options(
    selectinload(Project.tasks),
    selectinload(Project.assets),
    selectinload(Project.team_members)
).all()
```

**查询超时保护**:
```python
# 创建: /artpm_agent/database/query_protection.py

@contextmanager
def query_timeout(session, seconds: int = 5):
    """Protect queries with timeout"""
    if db_type == 'postgresql':
        session.execute(text(f"SET statement_timeout = {seconds * 1000}"))
    try:
        yield
    finally:
        if db_type == 'postgresql':
            session.execute(text("SET statement_timeout = 0"))
```

**查询日志**:
```python
# 修改: /artpm_agent/database/models.py
from sqlalchemy import event

@event.listens_for(Engine, "after_cursor_execute")
def receive_after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if "SELECT" in statement:
        logger.debug(f"Query: {statement[:100]}...",
            extra={"duration_ms": cursor.rownumber})
```

**文件清单**:
- 审计清单: `database-query-audit.txt` (50+ 行)
- 修改: `/artpm_agent/database/models.py` (所有查询)
- 创建: `/artpm_agent/database/query_protection.py`
- 创建: `/tests/test_query_optimization.py`

**验收标准**:
```python
# 性能测试
def test_project_queries_optimized():
    """Projects with 100 tasks should use 1 query, not 101"""
    with assert_query_count(1):
        projects = session.query(Project).options(
            selectinload(Project.tasks)
        ).all()
```

**工作量**: 2-3 天
**所有者**: 后端工程师

---

### 3.2 缓存预热与监控 [P2-2]
**状态**: 基础缓存存在,无预热
**优先级**: 第 2 周
**影响**: 冷启动缓存命中率

**任务分解**:
- [ ] 实现启动时缓存预热
- [ ] 添加缓存命中率指标
- [ ] 创建热点数据识别

**预热逻辑**:
```python
# 创建: /artpm_agent/providers/cache_warmer.py

class CacheWarmer:
    def __init__(self, cache: ResponseCache, agent: ArtPMAgent):
        self.cache = cache
        self.agent = agent

    def warm_on_startup(self):
        """Load frequent queries into cache"""
        # 预热常见技能
        common_queries = [
            "project status?",
            "cost analysis",
            "team availability",
        ]

        for query in common_queries:
            try:
                result = self.agent.chat(query)
                # 自动缓存
            except Exception:
                logger.debug(f"Cache warmup failed for {query}")

    def warm_periodic(self):
        """Refresh hot cache entries"""
        # 每 6 小时更新一次热点数据
```

**缓存指标**:
```python
# 修改: /artpm_agent/providers/response_cache.py

@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

# 集成到 OpenTelemetry
def record_cache_metric(cache_stats: CacheStats):
    meter = metrics.get_meter("artpm")
    meter.create_gauge(
        "cache.hit_rate",
        callbacks=[lambda: cache_stats.hit_rate]
    )
```

**文件清单**:
- 创建: `/artpm_agent/providers/cache_warmer.py`
- 修改: `/artpm_agent/providers/response_cache.py` (统计)
- 修改: `/artpm_agent/request_orchestrator.py` (启动时预热)
- 创建: `/tests/test_cache_warmer.py`

**工作量**: 1-2 天
**所有者**: 后端工程师

---

## 4. 代码质量任务 (P2-P3)

### 4.1 类型注解完整化 [P2-1]
**状态**: 部分覆盖
**优先级**: 第 2 周
**影响**: IDE 支持,类型安全

**任务分解**:
- [ ] 定义 TypedDict 用于配置
- [ ] 添加所有公共 API 的类型
- [ ] 提升 mypy 严格性设置

**TypedDict 定义**:
```python
# 创建: /artpm_agent/config_types.py

class LLMConfig(TypedDict, total=False):
    provider: str  # "openai", "anthropic", "deepseek"
    model: str
    api_key: str
    vision_model: Optional[str]
    request_timeout_seconds: float

class MemoryConfig(TypedDict, total=False):
    backend: str  # "faiss", "qdrant", "sqlite"
    dimension: int
    top_k: int

class ArtPMConfig(TypedDict):
    llm: LLMConfig
    memory: MemoryConfig
    database: Dict[str, Any]
    skills: List[str]
```

**mypy 配置** (`pyproject.toml`):
```toml
[tool.mypy]
python_version = "3.10"
warn_return_any = true
warn_unused_configs = true
warn_no_return = true
check_untyped_defs = false          # Week 8: 改为 true
disallow_untyped_defs = false       # Week 8: 改为 true
disallow_incomplete_defs = false    # Week 8: 改为 true
```

**迁移计划**:
- Week 7: 添加 TypedDict 定义
- Week 8: 修复所有类型错误
- 目标: 0 mypy 错误

**文件清单**:
- 创建: `/artpm_agent/config_types.py` (100+ LOC)
- 修改: `pyproject.toml`
- 修改: `/artpm_agent/config.py` (使用 TypedDict)
- 修改: `/artpm_agent/request_orchestrator.py`

**验收标准**:
```bash
mypy artpm_agent/ --strict
# 0 errors
```

**工作量**: 3-4 天
**所有者**: 后端工程师

---

### 4.2 代码去重 [P3-1]
**状态**: 多个 _utc_now, _normalize_text 实现
**优先级**: 第 3 周
**影响**: 维护成本

**任务分解**:
- [ ] 提取 `_utc_now()` (4 处)
- [ ] 提取 `_normalize_text()` (2 处)
- [ ] 创建 VectorStore 基类
- [ ] 统一命名规范

**统一工具库**:
```python
# 创建: /artpm_agent/utils/datetime.py

def utc_now() -> str:
    """Get current UTC time as ISO 8601 string"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

# 创建: /artpm_agent/utils/text.py

def normalize_text(text: str, strip: bool = True) -> str:
    """Normalize text: NFC, CRLF→LF, trim"""
    import unicodedata
    normalized = unicodedata.normalize("NFC", str(text))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.strip() if strip else normalized

# 创建: /artpm_agent/memory/vector_store_base.py

class VectorStoreBase(ABC):
    """Abstract vector store interface"""

    @abstractmethod
    def index(self, vectors: np.ndarray, ids: List[str]) -> None:
        pass

    @abstractmethod
    def search(self, query_vector: np.ndarray, top_k: int) -> List[Tuple[str, float]]:
        pass
```

**文件清单**:
- 创建: `/artpm_agent/utils/datetime.py`
- 创建: `/artpm_agent/utils/text.py`
- 创建: `/artpm_agent/memory/vector_store_base.py`
- 修改: `/artpm_agent/memory/faiss_vector_store.py` (继承基类)
- 修改: `/artpm_agent/memory/qdrant_vector_store.py` (继承基类)
- 修改: 所有使用旧函数的模块

**工作量**: 1-2 天
**所有者**: 后端工程师

---

## 5. 测试与文档任务 (P2)

### 5.1 集成测试扩展 [P2-4]
**状态**: 仅 5 个集成测试文件
**优先级**: 第 3 周
**影响**: 回归风险

**任务分解**:
- [ ] 端到端工作流测试 (技能→LLM→结果)
- [ ] 多租户隔离场景测试
- [ ] 并发工具执行测试
- [ ] 故障转移测试

**新测试套件**:
```python
# 创建: /tests/integration/test_e2e_workflows.py

class TestE2EWorkflows:
    """End-to-end workflow tests"""

    def test_full_conversation_with_tools(self):
        """User → Intent Router → Skill/LLM → Tool Execution → Result"""
        agent = ArtPMAgent()
        result = agent.chat("What's the status of project X?")
        assert "status" in result.lower()

    def test_multi_turn_conversation(self):
        """Maintain context across multiple turns"""
        agent = ArtPMAgent()
        r1 = agent.chat("Show me active projects")
        r2 = agent.chat("How many team members?")  # Should maintain context
        assert len(agent.runtime.messages) >= 4  # 2 user + 2 assistant

# 创建: /tests/integration/test_multi_tenant.py

class TestMultiTenant:
    """Multi-tenant isolation tests"""

    def test_tenant_data_isolation(self):
        """Data from tenant A should not be visible to tenant B"""
        tenant_a_context = {"tenant_id": "a", "workspace_id": "default"}
        tenant_b_context = {"tenant_id": "b", "workspace_id": "default"}

        agent_a = ArtPMAgent()
        agent_b = ArtPMAgent()

        result_a = agent_a.chat("List projects", tenant_a_context)
        result_b = agent_b.chat("List projects", tenant_b_context)

        # 结果应该不同
        assert result_a != result_b

# 创建: /tests/integration/test_concurrent_tools.py

class TestConcurrentTools:
    """Test concurrent tool execution"""

    def test_parallel_tool_execution(self):
        """Multiple tools should execute in parallel"""
        from concurrent.futures import ThreadPoolExecutor

        agent = ArtPMAgent()

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(agent.chat, f"Query {i}")
                for i in range(5)
            ]
            results = [f.result() for f in futures]

        assert all(r for r in results)
```

**文件清单**:
- 创建: `/tests/integration/test_e2e_workflows.py` (100+ LOC)
- 创建: `/tests/integration/test_multi_tenant.py` (80+ LOC)
- 创建: `/tests/integration/test_concurrent_tools.py` (60+ LOC)
- 修改: `/tests/conftest.py` (fixture 支持)

**验收标准**:
```bash
pytest tests/integration/ -v
# 所有集成测试通过
pytest tests/ --cov=artpm_agent --cov-report=html
# 覆盖 > 85%
```

**工作量**: 2-3 天
**所有者**: 测试工程师

---

## 执行跟踪表

| 任务 ID | 任务名 | P | 状态 | 所有者 | 预计完成 | 备注 |
|--------|-------|---|------|--------|--------|------|
| P0-1 | 多租户隔离 | P0 | 待处理 | 安全 | W1 | 关键安全修复 |
| P0-2 | 权限检查 | P0 | 待处理 | 后端 | W1 | 需下移到运行时 |
| P1-1 | DB 连接生命周期 | P1 | 待处理 | 后端 | W1-2 | 替换 atexit |
| P1-2 | 异常处理 | P1 | 待处理 | 后端 | W1-2 | 分层异常 |
| P1-3 | N+1 查询 | P1 | 待处理 | 后端 | W2 | selectinload + 超时 |
| P2-1 | 类型注解 | P2 | 待处理 | 后端 | W2-3 | TypedDict + 严格 mypy |
| P2-2 | 缓存预热 | P2 | 待处理 | 后端 | W2 | 启动优化 |
| P2-3 | 数据脱敏 | P2 | 待处理 | 安全 | W1 | 日志隐私 |
| P2-4 | 集成测试 | P2 | 待处理 | QA | W2-3 | e2e + 多租户 + 并发 |
| P3-1 | 代码去重 | P3 | 待处理 | 后端 | W3 | 工具函数统一 |

---

## 工作流建议

### 提交 PR 时的检查清单

- [ ] 是否包含测试?
- [ ] 代码是否通过 mypy 检查?
- [ ] 是否有 except Exception (需证明理由)?
- [ ] 新的多租户访问是否有 tenant_id 过滤?
- [ ] 是否有隐式资源泄露 (文件、连接)?
- [ ] 提交信息是否包含相关任务 ID?

### CI 质量门禁

```yaml
# .github/workflows/quality.yml
- name: Type checking
  run: mypy artpm_agent/

- name: Security audit
  run: |
    bandit -r artpm_agent/
    safety check

- name: Coverage
  run: pytest --cov=artpm_agent --cov-fail-under=80

- name: Performance regression
  run: pytest tests/performance/ --benchmark-compare
```

---

**最后更新**: 2026-08-15
**所有任务完成预期**: Week 24 (6 个月)
**关键路径**: P0 → P1 → P2
