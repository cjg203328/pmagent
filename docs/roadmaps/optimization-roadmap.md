# ArtPM Agent 优化路线图

**基于深度分析报告的缺点改进方案**  
**生成时间**: 2026-08-13  
**优先级排序**: P0 (阻塞) → P1 (高) → P2 (中) → P3 (低)

---

## 目录

1. [P0 紧急优化](#p0-紧急优化-本周执行)
2. [P1 高优先级](#p1-高优先级-2周内)
3. [P2 中优先级](#p2-中优先级-1-2月)
4. [P3 长期改进](#p3-长期改进-3-6月)
5. [实施检查清单](#实施检查清单)

---

## P0 紧急优化 (本周执行)

### 缺点 #1: 响应缓存历史键碰撞风险

**问题描述**:
- 旧 `response-v1` schema 可能仍在 Redis 中
- 新旧缓存混用导致跨工作区数据泄漏
- 图片临时路径键在重启后失效

**影响范围**: 🔴 高风险 - 多租户数据隔离

**解决方案**:

```python
# artpm_agent/providers/response_cache.py

# 1. 强制版本升级
CACHE_SCHEMA_VERSION = "v3"  # 每次部署递增
CACHE_TTL_SECONDS = 3600

def response_cache_namespace(context: TenantContext) -> str:
    """生成命名空间，包含版本标识"""
    if not context or not context.tenant_id:
        raise ValueError("tenant context required for cache namespace")
    
    # 版本号作为命名空间一部分，强制失效旧缓存
    return f"{CACHE_SCHEMA_VERSION}:{context.tenant_id}:{context.workspace_id}"

# 2. 缓存键标准化
def _cache_key(messages: List[Dict], model: str, context: TenantContext) -> str:
    """规范化缓存键，避免碰撞"""
    normalized = {
        "schema": CACHE_SCHEMA_VERSION,
        "model": model.strip().lower(),
        "messages": [
            {
                "role": m["role"],
                "content": _normalize_content(m.get("content", "")),
                # 图片使用 SHA256 而非路径
                "image_hash": _image_content_hash(m) if _has_image(m) else None,
            }
            for m in messages
        ],
    }
    
    # 使用 NFC 规范化 + 排序 JSON
    canonical = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def _image_content_hash(message: Dict) -> Optional[str]:
    """计算图片内容哈希，而非路径"""
    if "image_url" in message.get("content", {}):
        image_data = message["content"]["image_url"]["url"]
        if image_data.startswith("data:"):
            # base64 编码的图片
            return hashlib.sha256(image_data.encode()).hexdigest()[:16]
    return None
```

**部署脚本**:

```bash
# scripts/upgrade_cache_v3.sh

#!/bin/bash
set -e

echo "🔄 升级响应缓存到 v3..."

# 1. 备份现有缓存 (可选)
if [ "$BACKUP_CACHE" = "true" ]; then
    redis-cli --scan --pattern "response-v2:*" > cache_backup_$(date +%Y%m%d).txt
fi

# 2. 清空旧版本缓存
redis-cli EVAL "return redis.call('del', unpack(redis.call('keys', 'response-v*')))" 0

# 3. 更新环境变量
echo "CACHE_SCHEMA_VERSION=v3" >> .env

# 4. 重启服务
python start_with_checks.py

echo "✅ 缓存升级完成"
```

**验证方法**:

```python
# tests/integration/test_cache_isolation.py

def test_cache_namespace_includes_version():
    """缓存命名空间必须包含版本号"""
    ctx = TenantContext(tenant_id="t1", workspace_id="w1")
    namespace = response_cache_namespace(ctx)
    
    assert namespace.startswith("v3:")
    assert "t1:w1" in namespace

def test_image_cache_uses_content_hash():
    """图片缓存使用内容哈希，而非路径"""
    message = {
        "role": "user",
        "content": {
            "image_url": {"url": "data:image/png;base64,iVBORw0KG..."}
        }
    }
    
    hash1 = _image_content_hash(message)
    # 相同内容，不同调用
    hash2 = _image_content_hash(message)
    
    assert hash1 == hash2
    assert len(hash1) == 16  # 截断的 SHA256
```

**执行时间**: 1 天  
**风险等级**: 低 (幂等操作，可回滚)

---

### 缺点 #2: 历史大文件职责混杂 (当前最大约 3013 行)

**问题描述**:
- 单个类负责 16 个组件初始化
- 意图路由、技能分发、AgentLoop 调度、MinerU 注入、插件管理全在一个类
- 修改风险高，测试隔离困难

**影响范围**: 🟡 中风险 - 可维护性差

**解决方案 - Phase 1: 提取组件注册器**

```python
# artpm_agent/orchestration/component_registry.py

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

@dataclass
class ComponentRegistry:
    """组件生命周期管理"""
    
    _components: Dict[str, Any] = field(default_factory=dict)
    _lazy_builders: Dict[str, callable] = field(default_factory=dict)
    
    def register(self, name: str, component: Any) -> None:
        """注册已初始化的组件"""
        if name in self._components:
            raise ValueError(f"Component {name} already registered")
        self._components[name] = component
    
    def register_lazy(self, name: str, builder: callable) -> None:
        """注册惰性初始化的组件"""
        self._lazy_builders[name] = builder
    
    def get(self, name: str) -> Any:
        """获取组件，惰性初始化"""
        if name in self._components:
            return self._components[name]
        
        if name in self._lazy_builders:
            component = self._lazy_builders[name]()
            self._components[name] = component
            del self._lazy_builders[name]
            return component
        
        raise KeyError(f"Component {name} not found")
    
    def get_all(self) -> Dict[str, Any]:
        """获取所有已初始化的组件"""
        # 触发所有惰性初始化
        for name in list(self._lazy_builders.keys()):
            self.get(name)
        return self._components.copy()
```

```python
# artpm_agent/orchestration/component_factory.py

from artpm_agent.memory import MemoryManager, create_embedding_provider
from artpm_agent.database.models import DatabaseManager
from artpm_agent.skills import SkillRouter
from artpm_agent.providers import ModelGateway
from artpm_agent.plugins import build_plugin_manager_from_environment

class ComponentFactory:
    """组件工厂，集中管理依赖注入"""
    
    @staticmethod
    def build_memory(config: Config, llm_client: Any) -> MemoryManager:
        """构建记忆管理器"""
        db_path = config.get("database.memory_db_path")
        vector_db_path = config.get("database.vector_db_path")
        embedding_provider = create_embedding_provider(config.get("memory", {}))
        
        return MemoryManager(
            db_path,
            vector_db_path,
            llm_client,
            embedding_provider=embedding_provider,
        )
    
    @staticmethod
    def build_database(config: Config) -> DatabaseManager:
        """构建数据库管理器"""
        business_db_path = Path(config.get("database.db_path"))
        return DatabaseManager(f"sqlite:///{business_db_path.as_posix()}")
    
    @staticmethod
    def build_gateway(
        llm_config: Dict[str, Any],
        primary_client: Any,
    ) -> ModelGateway:
        """构建模型网关"""
        return ModelGateway(
            llm_config=llm_config,
            primary_client=primary_client,
            client_factory=create_llm_client,
        )
    
    @staticmethod
    def build_skill_router(context: Dict[str, Any]) -> SkillRouter:
        """构建技能路由器"""
        return SkillRouter(context)
    
    @staticmethod
    def build_plugin_manager() -> Any:
        """构建插件管理器"""
        try:
            return build_plugin_manager_from_environment()
        except PluginConfigurationError as error:
            logger.error(f"Plugin configuration rejected: {error}")
            from artpm_agent.plugins import PluginManager
            return PluginManager()
```

```python
# artpm_agent/agent.py (重构后，目标 < 300 行)

class ArtPMAgent:
    """
    ArtPM Agent - 轻量级编排 Facade
    
    职责:
    - 组件注册与生命周期管理 (委托给 ComponentRegistry)
    - 请求路由 (委托给 RequestOrchestrator)
    - 向后兼容的公开 API
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        # 配置加载
        self.config = self._load_config(config)
        
        # 组件注册器
        self.registry = ComponentRegistry()
        self.factory = ComponentFactory()
        
        # 核心组件立即初始化
        self._init_core_components()
        
        # 可选组件惰性初始化
        self._register_lazy_components()
        
        # 运行时与编排器
        self.runtime = AgentRuntime(max_messages=200)
        self.orchestrator = RequestOrchestrator(self.registry, self.runtime)
        
        logger.info(f"ArtPM Agent 初始化完成")
    
    def _init_core_components(self):
        """初始化核心组件"""
        llm_config = self.config.get_all()["llm"]
        
        # LLM 客户端 (可选)
        llm_client = None
        try:
            llm_client = create_llm_client(llm_config)
            logger.info("LLM客户端已就绪")
        except (ValueError, ImportError) as e:
            logger.warning(f"LLM不可用: {e}")
        
        # 注册核心组件
        self.registry.register("config", self.config)
        self.registry.register("llm_client", llm_client)
        self.registry.register(
            "gateway",
            self.factory.build_gateway(llm_config, llm_client)
        )
    
    def _register_lazy_components(self):
        """注册惰性初始化的组件"""
        # 记忆管理器 (首次使用时初始化)
        self.registry.register_lazy(
            "memory",
            lambda: self.factory.build_memory(
                self.config,
                self.registry.get("llm_client")
            )
        )
        
        # 数据库 (首次使用时初始化)
        self.registry.register_lazy(
            "database",
            lambda: self.factory.build_database(self.config)
        )
        
        # 插件管理器
        self.registry.register_lazy(
            "plugin_manager",
            lambda: self.factory.build_plugin_manager()
        )
        
        # 技能路由器 (依赖多个组件)
        self.registry.register_lazy(
            "skill_router",
            lambda: self.factory.build_skill_router(
                self._build_skill_context()
            )
        )
    
    def _build_skill_context(self) -> Dict[str, Any]:
        """构建技能执行上下文"""
        return {
            "llm_client": self.registry.get("llm_client"),
            "memory": self.registry.get("memory"),
            "database": self.registry.get("database"),
            "config": self.config.get_all(),
            "plugin_manager": self.registry.get("plugin_manager"),
        }
    
    # ── 向后兼容的公开 API ──
    
    @property
    def memory(self):
        return self.registry.get("memory")
    
    @property
    def database(self):
        return self.registry.get("database")
    
    @property
    def router(self):
        return self.registry.get("skill_router")
    
    def chat(self, message: str, **options) -> str:
        """主入口 - 委托给编排器"""
        return self.orchestrator.handle(message, **options)
```

```python
# artpm_agent/orchestration/request_orchestrator.py

class RequestOrchestrator:
    """请求编排器 - 处理三条路径分流"""
    
    def __init__(self, registry: ComponentRegistry, runtime: AgentRuntime):
        self.registry = registry
        self.runtime = runtime
    
    def handle(self, message: str, **options) -> str:
        """统一请求处理入口"""
        # 1. 意图分类
        intent = self._classify_intent(message)
        
        # 2. 路由到对应处理器
        if intent.is_fast_path:
            return self._handle_fast_path(message, intent)
        elif intent.is_skill:
            return self._handle_skill_path(message, intent, **options)
        else:
            return self._handle_llm_path(message, **options)
    
    def _classify_intent(self, message: str) -> Intent:
        """意图分类 - 委托给 IntentRouter"""
        router = self.registry.get("intent_router")
        return router.classify_intent(message)
    
    def _handle_fast_path(self, message: str, intent: Intent) -> str:
        """本地快速路径 - 无 API 调用"""
        if intent.type == "greeting":
            return "你好！我是 ArtPM 项目管理助手。"
        elif intent.type == "capability_query":
            return self._build_capability_response()
        # ... 其他本地响应
    
    def _handle_skill_path(self, message: str, intent: Intent, **options) -> str:
        """业务技能路径"""
        skill_router = self.registry.get("skill_router")
        result = skill_router.route(message, **options)
        return render_skill_result(result)
    
    def _handle_llm_path(self, message: str, **options) -> str:
        """LLM 推理路径"""
        gateway = self.registry.get("gateway")
        session = self._create_agent_session(gateway, **options)
        return session.run(message)
```

**迁移步骤**:

1. **Week 1**: 创建 `orchestration/` 模块，编写 `ComponentRegistry` + `ComponentFactory`
2. **Week 2**: 编写 `RequestOrchestrator`，保持原 `ArtPMAgent.chat()` 签名不变
3. **Week 3**: 逐步迁移 `agent.py` 方法到 orchestrator，运行完整测试套件
4. **Week 4**: 删除旧代码，更新文档

**风险控制**:
- 向后兼容: `@property` 装饰器保持原 API
- 渐进式迁移: 先拆分，后删除
- 测试覆盖: 每个阶段运行完整测试套件

**执行时间**: 4 周  
**风险等级**: 中 (大规模重构，需充分测试)

---

## P1 高优先级 (2周内)

### 缺点 #3: async/await 不一致

**问题描述**:
- 部分 Skill: `async def execute()`
- 部分 Skill: `def execute()` (同步)
- 调用方需要 `asyncio.run()` / `await` 适配

**影响范围**: 🟡 中风险 - 性能瓶颈 + 代码混乱

**解决方案 - Phase 1: 统一接口**

```python
# artpm_agent/skills/base_skill.py

from abc import ABC, abstractmethod
from typing import Any, Dict
import asyncio

class BaseSkill(ABC):
    """
    技能基类 - 统一 async 接口
    
    所有 Skill 必须实现 async execute_async()
    同步代码用 asyncio.to_thread() 包裹
    """
    
    @abstractmethod
    async def execute_async(self, inputs: Dict[str, Any]) -> Any:
        """
        异步执行入口 (子类必须实现)
        
        同步 I/O 操作请用:
            result = await asyncio.to_thread(self._sync_operation, ...)
        """
        pass
    
    def execute(self, inputs: Dict[str, Any]) -> Any:
        """
        同步执行入口 (向后兼容)
        
        内部调用 execute_async()，自动处理 event loop
        """
        try:
            loop = asyncio.get_running_loop()
            # 已在 async 上下文中
            return asyncio.create_task(self.execute_async(inputs))
        except RuntimeError:
            # 同步上下文，创建新 loop
            return asyncio.run(self.execute_async(inputs))
```

**迁移示例 - 同步 Skill**:

```python
# artpm_agent/skills/quote_scheduling_skill.py (迁移前)

class QuoteSchedulingSkill(BaseSkill):
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """同步执行"""
        session = self.database.session()  # 阻塞 I/O
        projects = session.query(Project).all()  # 阻塞 I/O
        # ... 业务逻辑
        return {"status": "success", "data": results}
```

```python
# artpm_agent/skills/quote_scheduling_skill.py (迁移后)

class QuoteSchedulingSkill(BaseSkill):
    async def execute_async(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """异步执行"""
        # 包裹阻塞 I/O
        projects = await asyncio.to_thread(
            self._query_projects_sync
        )
        # ... 业务逻辑 (纯计算，无需 await)
        return {"status": "success", "data": results}
    
    def _query_projects_sync(self) -> List[Project]:
        """同步数据库查询 (隔离到独立方法)"""
        session = self.database.session()
        return session.query(Project).all()
```

**迁移示例 - 已有 async Skill**:

```python
# artpm_agent/skills/progress_management_skill.py (迁移前)

class ProgressManagementSkill(BaseSkill):
    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """已经是 async"""
        # ... 现有逻辑
        return results
```

```python
# artpm_agent/skills/progress_management_skill.py (迁移后)

class ProgressManagementSkill(BaseSkill):
    async def execute_async(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """重命名为 execute_async"""
        # ... 逻辑不变
        return results
```

**自动化迁移脚本**:

```python
# scripts/migrate_skills_to_async.py

import ast
import os
from pathlib import Path

def migrate_skill_file(file_path: Path):
    """自动迁移单个 Skill 文件"""
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    
    tree = ast.parse(source)
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # 检查是否继承 BaseSkill
            if any(base.id == 'BaseSkill' for base in node.bases if hasattr(base, 'id')):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == 'execute':
                        # 重命名为 execute_async
                        item.name = 'execute_async'
                        
                        # 如果不是 async，添加 async 关键字
                        if not isinstance(item, ast.AsyncFunctionDef):
                            print(f"⚠️ {file_path}: execute() 是同步函数，需要手动迁移")
    
    # 写回文件
    new_source = ast.unparse(tree)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_source)

def main():
    skills_dir = Path("artpm_agent/skills")
    for skill_file in skills_dir.glob("*_skill.py"):
        print(f"迁移 {skill_file}...")
        migrate_skill_file(skill_file)

if __name__ == "__main__":
    main()
```

**Phase 2: SQLAlchemy 异步化** (中期优化)

```python
# artpm_agent/database/models.py

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

class DatabaseManager:
    def __init__(self, db_url: str):
        # 将 sqlite:/// 替换为 sqlite+aiosqlite:///
        async_url = db_url.replace("sqlite://", "sqlite+aiosqlite://")
        self.engine = create_async_engine(async_url, echo=False)
        
        self.async_session_maker = sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    
    async def get_session(self) -> AsyncSession:
        """获取异步 session"""
        async with self.async_session_maker() as session:
            yield session
```

```python
# artpm_agent/skills/quote_scheduling_skill.py (Phase 2)

class QuoteSchedulingSkill(BaseSkill):
    async def execute_async(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """原生异步执行"""
        async with self.database.get_session() as session:
            # 原生异步查询
            result = await session.execute(
                select(Project).where(Project.status == "active")
            )
            projects = result.scalars().all()
        
        return {"status": "success", "data": projects}
```

**执行时间**: 
- Phase 1: 2 周 (统一接口)
- Phase 2: 4 周 (SQLAlchemy 异步化)

**风险等级**: 中 (需要全量回归测试)

---

### 缺点 #4: 多租户隔离仅在应用层

**问题描述**:
- `TenantContext` 仅在应用层拦截
- 数据库表无 `tenant_id` 索引
- SQLite 不支持行级安全 (RLS)

**影响范围**: 🔴 高风险 - 数据隔离不彻底

**解决方案 - 迁移 PostgreSQL + RLS**

**Step 1: 数据库 schema 迁移**

```sql
-- alembic/versions/0003_add_tenant_isolation.py

"""
添加多租户隔离字段

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa

def upgrade():
    # 1. 添加 tenant_id 列
    op.add_column('projects', sa.Column('tenant_id', sa.String(128), nullable=True))
    op.add_column('tasks', sa.Column('tenant_id', sa.String(128), nullable=True))
    op.add_column('team_members', sa.Column('tenant_id', sa.String(128), nullable=True))
    
    # 2. 回填现有数据 (默认 'local' 租户)
    op.execute("UPDATE projects SET tenant_id = 'local'")
    op.execute("UPDATE tasks SET tenant_id = 'local'")
    op.execute("UPDATE team_members SET tenant_id = 'local'")
    
    # 3. 设置为非空
    op.alter_column('projects', 'tenant_id', nullable=False)
    op.alter_column('tasks', 'tenant_id', nullable=False)
    op.alter_column('team_members', 'tenant_id', nullable=False)
    
    # 4. 添加索引 (加速租户查询)
    op.create_index('idx_projects_tenant', 'projects', ['tenant_id'])
    op.create_index('idx_tasks_tenant', 'tasks', ['tenant_id'])
    op.create_index('idx_team_members_tenant', 'team_members', ['tenant_id'])

def downgrade():
    op.drop_index('idx_projects_tenant')
    op.drop_index('idx_tasks_tenant')
    op.drop_index('idx_team_members_tenant')
    
    op.drop_column('projects', 'tenant_id')
    op.drop_column('tasks', 'tenant_id')
    op.drop_column('team_members', 'tenant_id')
```

**Step 2: 启用 PostgreSQL RLS**

```sql
-- alembic/versions/0004_enable_rls.py

def upgrade():
    # 启用行级安全
    op.execute("ALTER TABLE projects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tasks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE team_members ENABLE ROW LEVEL SECURITY")
    
    # 创建 RLS 策略
    op.execute("""
        CREATE POLICY tenant_isolation_projects ON projects
        USING (tenant_id = current_setting('app.tenant_id', true))
    """)
    
    op.execute("""
        CREATE POLICY tenant_isolation_tasks ON tasks
        USING (tenant_id = current_setting('app.tenant_id', true))
    """)
    
    op.execute("""
        CREATE POLICY tenant_isolation_team_members ON team_members
        USING (tenant_id = current_setting('app.tenant_id', true))
    """)
```

**Step 3: 应用层设置租户上下文**

```python
# artpm_agent/database/models.py

from artpm_agent.tenancy import TenantContextManager

class DatabaseManager:
    async def get_session(self) -> AsyncSession:
        """获取带租户上下文的 session"""
        context = TenantContextManager.get_current()
        if not context:
            raise RuntimeError("TenantContext not set")
        
        async with self.async_session_maker() as session:
            # 设置 PostgreSQL session 变量
            await session.execute(
                text("SET app.tenant_id = :tenant_id"),
                {"tenant_id": context.tenant_id}
            )
            yield session
```

**Step 4: SQLite 降级方案 (单租户部署)**

```python
# artpm_agent/database/tenant_filter.py

from sqlalchemy import event
from sqlalchemy.orm import Session

def apply_tenant_filter_sqlite(session: Session, context: TenantContext):
    """
    SQLite 降级方案 - 在查询时自动添加 tenant_id 过滤
    
    注意: 这不是真正的 RLS，仅应用层防护
    """
    @event.listens_for(session, "after_attach")
    def receive_after_attach(session, instance):
        # 自动设置 tenant_id
        if hasattr(instance, 'tenant_id') and not instance.tenant_id:
            instance.tenant_id = context.tenant_id
    
    # 在所有查询添加过滤
    @event.listens_for(Session, "do_orm_execute")
    def receive_do_orm_execute(execute_state):
        if execute_state.is_select:
            # 添加 WHERE tenant_id = ?
            execute_state.statement = execute_state.statement.where(
                Model.tenant_id == context.tenant_id
            )
```

**部署配置**:

```env
# .env

# PostgreSQL (生产环境)
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/artpm
ENABLE_RLS=true

# SQLite (开发/单租户)
DATABASE_URL=sqlite+aiosqlite:///./data/artpm.db
ENABLE_RLS=false  # 使用应用层过滤
```

**执行时间**: 
- Step 1-2: 1 周 (schema 迁移)
- Step 3: 3 天 (应用层适配)
- Step 4: 2 天 (SQLite 降级)

**风险等级**: 高 (数据库迁移，需完整备份 + 回滚方案)

---

## P2 中优先级 (1-2月)

### 缺点 #5: 测试覆盖深度不均

**问题描述**:
- 核心层 85%+，UI 层仅 40%
- 缺少多租户隔离集成测试
- 缺少故障转移熔断恢复测试

**解决方案**:

```python
# tests/integration/test_tenant_isolation.py

import pytest
from artpm_agent.tenancy import TenantContext, TenantContextManager, WorkspaceAccessDenied

@pytest.mark.integration
async def test_cross_workspace_write_denied(agent):
    """跨工作区写入必须被拒绝"""
    ctx1 = TenantContext(tenant_id="t1", workspace_id="w1")
    ctx2 = TenantContext(tenant_id="t1", workspace_id="w2")
    
    # 在 w1 中创建记忆
    with TenantContextManager.use(ctx1):
        await agent.memory.add_memory("secret data", metadata={"sensitive": True})
    
    # 在 w2 中尝试访问 w1 的数据
    with TenantContextManager.use(ctx2):
        with pytest.raises(WorkspaceAccessDenied):
            # WorkspaceStoreGuard 应该拦截
            await agent.memory.search("secret", workspace_id="w1")

@pytest.mark.integration
async def test_response_cache_isolation(agent):
    """响应缓存必须按工作区隔离"""
    ctx1 = TenantContext(tenant_id="t1", workspace_id="w1")
    ctx2 = TenantContext(tenant_id="t1", workspace_id="w2")
    
    # w1 中的请求
    with TenantContextManager.use(ctx1):
        response1 = await agent.chat("计算 2+2")
        # 再次请求应该命中缓存
        response1_cached = await agent.chat("计算 2+2")
        assert response1 == response1_cached
    
    # w2 中的相同请求应该 miss 缓存 (不同工作区)
    with TenantContextManager.use(ctx2):
        # 模拟不同的 LLM 响应
        with patch.object(agent.gateway, 'generate') as mock_gen:
            mock_gen.return_value = "4 (from LLM)"
            response2 = await agent.chat("计算 2+2")
            
            # 应该调用了 LLM，而非缓存
            mock_gen.assert_called_once()

@pytest.mark.integration
async def test_database_rls_enforcement(db_manager):
    """数据库 RLS 必须强制隔离"""
    ctx1 = TenantContext(tenant_id="t1", workspace_id="w1")
    ctx2 = TenantContext(tenant_id="t2", workspace_id="w2")
    
    # t1 创建项目
    with TenantContextManager.use(ctx1):
        async with db_manager.get_session() as session:
            project = Project(name="Project A", tenant_id="t1")
            session.add(project)
            await session.commit()
    
    # t2 不应该看到 t1 的项目
    with TenantContextManager.use(ctx2):
        async with db_manager.get_session() as session:
            result = await session.execute(select(Project))
            projects = result.scalars().all()
            
            # RLS 应该过滤掉 t1 的数据
            assert len(projects) == 0
```

```python
# tests/unit/test_model_failover.py

@pytest.mark.unit
async def test_gateway_cooldown_recovery(mock_llm_clients):
    """测试熔断恢复"""
    gateway = ModelGateway(
        llm_config={"provider": "anthropic", "model": "claude-3"},
        primary_client=mock_llm_clients["primary"],
    )
    
    # 1. 主模型失败，触发熔断
    mock_llm_clients["primary"].generate.side_effect = APIError("503 Service Unavailable")
    
    with pytest.raises(ModelFailoverError):
        await gateway.generate("test", max_attempts=1)
    
    # 2. 验证熔断状态
    assert gateway._is_unavailable("claude-3")
    
    # 3. 等待冷却期
    await asyncio.sleep(gateway.MODEL_FAILOVER_COOLDOWN_SECONDS + 1)
    
    # 4. 主模型恢复
    mock_llm_clients["primary"].generate.side_effect = None
    mock_llm_clients["primary"].generate.return_value = "recovered"
    
    # 5. 应该重试主模型
    response = await gateway.generate("test")
    assert response == "recovered"
    assert not gateway._is_unavailable("claude-3")
```

**测试覆盖目标**:

| 模块 | 当前覆盖 | 目标覆盖 | 优先补充 |
|------|---------|---------|---------|
| agent.py | 85% | 90% | 组件初始化失败路径 |
| gateway.py | 82% | 90% | 熔断恢复、跨 provider 切换 |
| tenancy/ | 90% | 95% | 跨租户写入拦截 |
| views/ | 40% | 70% | UI 交互路径、错误提示 |
| workflows/ | 55% | 75% | LangGraph 任务图边界 |

**自动化覆盖报告**:

```bash
# scripts/coverage_report_by_module.sh

#!/bin/bash

pytest --cov=artpm_agent --cov-report=json

# 生成分模块报告
python -c "
import json
with open('coverage.json') as f:
    data = json.load(f)

modules = {}
for file, coverage in data['files'].items():
    module = file.split('/')[1] if '/' in file else 'root'
    if module not in modules:
        modules[module] = {'covered': 0, 'total': 0}
    
    stats = coverage['summary']
    modules[module]['covered'] += stats['covered_lines']
    modules[module]['total'] += stats['num_statements']

for module, stats in sorted(modules.items()):
    pct = 100 * stats['covered'] / stats['total'] if stats['total'] > 0 else 0
    print(f\"{module:20s} {pct:5.1f}%\")
"
```

**执行时间**: 4 周  
**风险等级**: 低

---

### 缺点 #6: FAISS 在 NFS 不稳定

**问题描述**:
- FAISS 使用 WAL 模式写 SQLite 元数据
- 网络文件系统对 WAL 支持不完整
- 多实例并发写可能损坏索引

**解决方案 - 迁移 Qdrant**

```python
# artpm_agent/memory/vector_store_qdrant.py

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from typing import List, Dict, Any, Optional

class QdrantVectorStore:
    """
    Qdrant 向量存储
    
    优势:
    - 网络原生，解决 NFS 问题
    - 分布式部署，水平扩展
    - 内置租户隔离 (collection per tenant)
    """
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        collection_prefix: str = "artpm",
    ):
        self.client = QdrantClient(host=host, port=port)
        self.collection_prefix = collection_prefix
    
    def _collection_name(self, tenant_id: str, workspace_id: str) -> str:
        """每个工作区独立 collection"""
        return f"{self.collection_prefix}_{tenant_id}_{workspace_id}"
    
    def create_collection(
        self,
        tenant_id: str,
        workspace_id: str,
        vector_size: int = 384,
    ):
        """创建工作区 collection"""
        collection_name = self._collection_name(tenant_id, workspace_id)
        
        self.client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=vector_size,
                distance=Distance.COSINE,
            ),
        )
    
    async def add(
        self,
        tenant_id: str,
        workspace_id: str,
        texts: List[str],
        embeddings: List[List[float]],
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        """添加向量"""
        collection_name = self._collection_name(tenant_id, workspace_id)
        
        # 确保 collection 存在
        if not self.client.collection_exists(collection_name):
            self.create_collection(tenant_id, workspace_id, len(embeddings[0]))
        
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "text": text,
                    **(metadata or {}),
                },
            )
            for text, embedding, metadata in zip(
                texts, embeddings, metadatas or [{}] * len(texts)
            )
        ]
        
        self.client.upsert(collection_name=collection_name, points=points)
        return [p.id for p in points]
    
    async def search(
        self,
        tenant_id: str,
        workspace_id: str,
        query_embedding: List[float],
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """语义搜索"""
        collection_name = self._collection_name(tenant_id, workspace_id)
        
        if not self.client.collection_exists(collection_name):
            return []
        
        results = self.client.search(
            collection_name=collection_name,
            query_vector=query_embedding,
            limit=top_k,
            query_filter=filters,  # Qdrant 原生过滤
        )
        
        return [
            {
                "id": hit.id,
                "text": hit.payload["text"],
                "score": hit.score,
                "metadata": {
                    k: v for k, v in hit.payload.items() if k != "text"
                },
            }
            for hit in results
        ]
```

```python
# artpm_agent/memory/memory_manager.py (适配层)

class MemoryManager:
    def __init__(self, db_path, vector_db_path, llm_client, embedding_provider):
        self.embedding_provider = embedding_provider
        
        # 根据配置选择向量存储
        vector_backend = os.getenv("VECTOR_BACKEND", "faiss")
        
        if vector_backend == "qdrant":
            self.vector_store = QdrantVectorStore(
                host=os.getenv("QDRANT_HOST", "localhost"),
                port=int(os.getenv("QDRANT_PORT", 6333)),
            )
        else:
            # Fallback to FAISS (本地模式)
            self.vector_store = FAISSVectorStore(vector_db_path)
```

**部署配置**:

```yaml
# docker-compose.yml

services:
  qdrant:
    image: qdrant/qdrant:v1.7.4
    ports:
      - "6333:6333"
    volumes:
      - ./data/qdrant:/qdrant/storage
    environment:
      - QDRANT_ENABLE_TELEMETRY=false
  
  artpm-agent:
    build: .
    environment:
      - VECTOR_BACKEND=qdrant
      - QDRANT_HOST=qdrant
      - QDRANT_PORT=6333
    depends_on:
      - qdrant
```

**迁移脚本**:

```python
# scripts/migrate_faiss_to_qdrant.py

async def migrate_workspace(
    tenant_id: str,
    workspace_id: str,
    faiss_store: FAISSVectorStore,
    qdrant_store: QdrantVectorStore,
):
    """迁移单个工作区的向量数据"""
    # 1. 从 FAISS 导出
    vectors = faiss_store.export_all(workspace_id)
    
    # 2. 批量写入 Qdrant
    batch_size = 100
    for i in range(0, len(vectors), batch_size):
        batch = vectors[i:i+batch_size]
        await qdrant_store.add(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            texts=[v["text"] for v in batch],
            embeddings=[v["embedding"] for v in batch],
            metadatas=[v["metadata"] for v in batch],
        )
    
    print(f"✅ 迁移完成: {tenant_id}/{workspace_id} ({len(vectors)} vectors)")

async def main():
    faiss_store = FAISSVectorStore("./data/vector_store")
    qdrant_store = QdrantVectorStore(host="localhost", port=6333)
    
    # 获取所有工作区
    workspaces = list_all_workspaces()  # 从数据库读取
    
    for tenant_id, workspace_id in workspaces:
        await migrate_workspace(tenant_id, workspace_id, faiss_store, qdrant_store)

if __name__ == "__main__":
    asyncio.run(main())
```

**执行时间**: 3 周  
**风险等级**: 中 (数据迁移，需备份)

---

## P3 长期改进 (3-6月)

### 缺点 #7: 观测性不足

**解决方案 - OpenTelemetry 集成**

```python
# artpm_agent/observability/tracing.py

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

def init_telemetry(service_name: str = "artpm-agent"):
    """初始化 OpenTelemetry"""
    # 1. 配置 Tracer Provider
    trace.set_tracer_provider(TracerProvider())
    tracer_provider = trace.get_tracer_provider()
    
    # 2. 配置 OTLP Exporter (导出到 Grafana Tempo / Jaeger)
    otlp_exporter = OTLPSpanExporter(
        endpoint=os.getenv("OTLP_ENDPOINT", "http://localhost:4317"),
        insecure=True,
    )
    
    span_processor = BatchSpanProcessor(otlp_exporter)
    tracer_provider.add_span_processor(span_processor)
    
    # 3. 自动 instrument SQL / HTTP
    SQLAlchemyInstrumentor().instrument()
    RequestsInstrumentor().instrument()
    
    logger.info(f"✅ OpenTelemetry initialized: {service_name}")

# 使用装饰器自动追踪
tracer = trace.get_tracer(__name__)

def traced(span_name: str):
    """函数追踪装饰器"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(span_name) as span:
                # 添加上下文属性
                context = TenantContextManager.get_current()
                if context:
                    span.set_attribute("tenant.id", context.tenant_id)
                    span.set_attribute("workspace.id", context.workspace_id)
                
                try:
                    result = await func(*args, **kwargs)
                    span.set_attribute("result.type", type(result).__name__)
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
        return wrapper
    return decorator
```

```python
# artpm_agent/agent.py (集成追踪)

from artpm_agent.observability.tracing import traced, tracer

class ArtPMAgent:
    @traced("agent.chat")
    async def chat(self, message: str, **options) -> str:
        """主入口 - 自动追踪"""
        span = trace.get_current_span()
        span.set_attribute("message.length", len(message))
        span.set_attribute("options", json.dumps(options))
        
        result = await self.orchestrator.handle(message, **options)
        
        span.set_attribute("response.length", len(result))
        return result
```

**Grafana 仪表板配置**:

```yaml
# grafana/dashboards/artpm-agent.json

{
  "dashboard": {
    "title": "ArtPM Agent - Performance",
    "panels": [
      {
        "title": "请求延迟 (P50/P95/P99)",
        "targets": [{
          "expr": "histogram_quantile(0.95, rate(artpm_agent_request_duration_seconds_bucket[5m]))"
        }]
      },
      {
        "title": "错误率",
        "targets": [{
          "expr": "rate(artpm_agent_errors_total[5m])"
        }]
      },
      {
        "title": "缓存命中率",
        "targets": [{
          "expr": "rate(artpm_agent_cache_hits_total[5m]) / rate(artpm_agent_cache_requests_total[5m])"
        }]
      }
    ]
  }
}
```

**执行时间**: 6 周  
**风险等级**: 低

---

## 实施检查清单

### Week 1-2: P0 紧急优化

- [ ] 响应缓存强制升级到 v3
  - [ ] 编写迁移脚本 `scripts/upgrade_cache_v3.sh`
  - [ ] 运行测试 `test_cache_namespace_includes_version`
  - [ ] 生产部署前 Redis FLUSHDB
  - [ ] 监控缓存命中率变化

- [ ] Agent 类解耦 - Phase 1
  - [ ] 创建 `orchestration/component_registry.py`
  - [ ] 创建 `orchestration/component_factory.py`
  - [ ] 创建 `orchestration/request_orchestrator.py`
  - [ ] 重构 `agent.py` (保持 API 兼容)
  - [ ] 运行完整测试套件 (1274 tests pass)

### Week 3-4: P1 高优先级

- [ ] async/await 统一 - Phase 1
  - [ ] 修改 `BaseSkill` 基类
  - [ ] 运行迁移脚本 `scripts/migrate_skills_to_async.py`
  - [ ] 手动修复同步 Skill (用 `asyncio.to_thread` 包裹)
  - [ ] 运行 Skill 测试套件

- [ ] 多租户隔离加强
  - [ ] 编写 Alembic 迁移 `0003_add_tenant_isolation.py`
  - [ ] PostgreSQL 环境测试 RLS 策略
  - [ ] SQLite 降级方案测试
  - [ ] 编写集成测试 `test_cross_workspace_write_denied`

### Week 5-8: P2 中优先级

- [ ] 测试覆盖补充
  - [ ] 多租户隔离集成测试 (10+ cases)
  - [ ] 故障转移熔断恢复测试
  - [ ] UI 层测试 (views/chat.py)
  - [ ] 覆盖率目标: 核心层 90%+, UI 层 70%+

- [ ] FAISS 迁移 Qdrant
  - [ ] 部署 Qdrant 容器
  - [ ] 编写适配层 `vector_store_qdrant.py`
  - [ ] 编写迁移脚本 `migrate_faiss_to_qdrant.py`
  - [ ] 灰度切换 (`VECTOR_BACKEND=qdrant`)

### Week 9-16: P3 长期改进

- [ ] OpenTelemetry 集成
  - [ ] 初始化 Tracer Provider
  - [ ] 添加追踪装饰器 `@traced`
  - [ ] 配置 Grafana 仪表板
  - [ ] 生产环境验证分布式追踪

- [ ] 插件沙箱化 (可选)
  - [ ] 设计 subprocess 沙箱方案
  - [ ] 编写 Plugin Runtime Wrapper
  - [ ] 迁移现有插件
  - [ ] 性能测试 (对比 overhead)

---

## 成功指标

**技术指标**:
- Agent 类行数: 1338 → 74 行 ✅（当前已是兼容 facade）
- 当前最大文件: `workspace_knowledge_store.py` 3013 行；`ui_helpers.py` 2394 行；`ui_style.py` 2312 行
- async/await 一致性: 100% Skill 统一接口 ✅
- 多租户隔离: 应用层 + 数据库 RLS 双重防护 ✅
- 测试覆盖: 核心边界 90%+；全项目 CI 过渡基线 20%；UI/大模块持续补测
- 响应缓存隔离: 零跨工作区泄漏 ✅

**业务指标**:
- P95 响应延迟: <2s (LLM 推理路径)
- 错误率: <0.1%
- 缓存命中率: >50% (语义缓存启用后)
- 多租户并发: >100 req/s (PostgreSQL 模式)

**可观测性**:
- 分布式追踪覆盖: 100% 关键路径
- 自动异常聚合: Sentry 集成
- 自定义仪表板: Grafana 5+ panels

---

## 风险缓解矩阵

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|-------|------|---------|
| 重构导致功能回归 | 中 | 高 | 完整测试套件 + 渐进式迁移 |
| 数据库迁移失败 | 低 | 高 | 备份 + 回滚脚本 + 灰度切换 |
| PostgreSQL 性能瓶颈 | 低 | 中 | 连接池 + 索引优化 + 读写分离 |
| Qdrant 网络故障 | 中 | 中 | FAISS fallback + 健康检查 |
| async 迁移兼容性 | 中 | 中 | 保留同步 API + 适配层 |

---

**优化路线图负责人**: [待指定]  
**审阅周期**: 每 2 周  
**最后更新**: 2026-08-13
