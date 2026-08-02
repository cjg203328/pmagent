# 多租户架构设计与实施方案

**版本**: v1.0  
**日期**: 2026-07-22  
**状态**: 设计提案（未实施）

> ⚠️ **这是设计提案，不是当前 API 文档。**
>
> 本文中的 Row-Level Security、独立 Schema、资源配额、审计日志、
> 租户/工作空间管理 REST 端点均**尚未实现**，代码库里没有对应模块。
> 文中示例用了 Flask 与 PostgreSQL RLS，而本项目实际用 FastAPI + SQLite。
>
> 当前真正落地的能力见 `docs/MULTI_TENANT_USAGE_GUIDE.md`：
> 框架中立的 `artpm_agent/tenancy/`（请求级上下文、RBAC 判定、
> store 工作区守卫）加上 `artpm_agent/api/` 的 FastAPI 网关。
> 引用本文时请把它当作路线图，不要照抄代码。

---

## 📋 目录

1. [概述](#概述)
2. [架构设计](#架构设计)
3. [数据隔离方案](#数据隔离方案)
4. [权限管理系统](#权限管理系统)
5. [实施计划](#实施计划)
6. [API 设计](#api-设计)
7. [测试策略](#测试策略)

---

## 概述

### 目标
将 ArtPM Agent 升级为支持多租户的 SaaS 平台,实现:
- ✅ **数据完全隔离** - 租户间数据物理/逻辑隔离
- ✅ **细粒度权限** - 基于 RBAC 的权限控制
- ✅ **资源配额** - 租户级资源限制
- ✅ **审计日志** - 完整操作追踪

### 租户层级
```
Organization (组织)
  └── Workspaces (工作空间)
        └── Users (用户)
              └── Roles (角色)
                    └── Permissions (权限)
```

---

## 架构设计

### 多租户架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      API Gateway                             │
│  - 租户识别 (X-Tenant-ID / subdomain)                        │
│  - 认证验证 (JWT + Tenant Context)                          │
│  - 限流控制 (Per-Tenant Rate Limiting)                      │
└────────────────────┬────────────────────────────────────────┘
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
┌────────────────┐      ┌────────────────┐
│ Tenant Service │      │ Auth Service   │
│ - 租户管理     │      │ - 用户认证     │
│ - 配额控制     │      │ - 权限验证     │
└────────┬───────┘      └────────┬───────┘
         │                       │
         └───────────┬───────────┘
                     ▼
         ┌───────────────────────┐
         │   Tenant Context      │
         │   (Request Scoped)    │
         │   - tenant_id         │
         │   - workspace_id      │
         │   - user_id           │
         │   - roles             │
         │   - permissions       │
         └───────────┬───────────┘
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
┌────────────────┐      ┌────────────────┐
│ Business Layer │      │ Data Access    │
│ - Skills       │      │ - Row-Level    │
│ - Workflows    │      │   Security     │
│ - Analytics    │      │ - Tenant Filter│
└────────────────┘      └────────┬───────┘
                                 │
                     ┌───────────┴───────────┐
                     ▼                       ▼
         ┌──────────────────┐    ┌──────────────────┐
         │ PostgreSQL       │    │ Redis            │
         │ - 数据分区       │    │ - 租户缓存       │
         │ - RLS 策略       │    │ - Session Store  │
         └──────────────────┘    └──────────────────┘
```

---

## 数据隔离方案

### 方案对比

| 方案 | 隔离级别 | 性能 | 成本 | 可扩展性 | 推荐度 |
|------|---------|------|------|---------|--------|
| **独立数据库** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐☆☆ | ⭐⭐☆☆☆ | ⭐⭐⭐☆☆ | 企业版 |
| **独立 Schema** | ⭐⭐⭐⭐☆ | ⭐⭐⭐⭐☆ | ⭐⭐⭐☆☆ | ⭐⭐⭐⭐☆ | **推荐** |
| **共享表 + RLS** | ⭐⭐⭐☆☆ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 标准版 |

### 推荐方案: 混合模式

**标准版**: 共享表 + Row-Level Security (RLS)  
**企业版**: 独立 Schema (可选独立数据库)

---

### 实现 1: Row-Level Security (标准版)

#### 数据库结构
```sql
-- 租户表
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    subdomain VARCHAR(100) UNIQUE NOT NULL,
    plan_tier VARCHAR(50) DEFAULT 'free', -- free, pro, enterprise
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 工作空间表
CREATE TABLE workspaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(tenant_id, slug)
);

-- 用户表 (租户级)
CREATE TABLE tenant_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255),
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(tenant_id, email)
);

-- 工作空间成员
CREATE TABLE workspace_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES tenant_users(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL, -- owner, admin, member, viewer
    joined_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(workspace_id, user_id)
);

-- 业务数据示例: 项目表
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'active',
    created_by UUID NOT NULL REFERENCES tenant_users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 启用 RLS
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;

-- RLS 策略: 只能访问自己租户的数据
CREATE POLICY tenant_isolation ON projects
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- RLS 策略: 只能访问自己工作空间的数据
CREATE POLICY workspace_isolation ON projects
    USING (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members 
            WHERE user_id = current_setting('app.current_user_id')::UUID
        )
    );
```

#### Python 实现
```python
# artpm_agent/tenancy/context.py
from contextlib import contextmanager
from uuid import UUID
from typing import Optional
from sqlalchemy import event
from sqlalchemy.engine import Engine

class TenantContext:
    """租户上下文"""
    def __init__(
        self,
        tenant_id: UUID,
        workspace_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None
    ):
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        self.user_id = user_id

@contextmanager
def tenant_scope(db_session, tenant_id: UUID, user_id: Optional[UUID] = None):
    """设置租户作用域"""
    # 设置 PostgreSQL session 变量
    db_session.execute(
        f"SET LOCAL app.current_tenant_id = '{tenant_id}'"
    )
    if user_id:
        db_session.execute(
            f"SET LOCAL app.current_user_id = '{user_id}'"
        )
    
    try:
        yield
    finally:
        # 清理
        db_session.execute("RESET app.current_tenant_id")
        if user_id:
            db_session.execute("RESET app.current_user_id")
```

---

### 实现 2: 独立 Schema (企业版)

```sql
-- 为每个租户创建独立 schema
CREATE SCHEMA tenant_abc123;
CREATE SCHEMA tenant_def456;

-- 在租户 schema 中创建表
CREATE TABLE tenant_abc123.projects (
    id UUID PRIMARY KEY,
    name VARCHAR(255),
    ...
);

-- 动态切换 schema
SET search_path TO tenant_abc123, public;
```

```python
# artpm_agent/tenancy/schema_manager.py
class SchemaManager:
    """Schema 管理器"""
    
    @staticmethod
    def create_tenant_schema(tenant_id: UUID):
        """创建租户 Schema"""
        schema_name = f"tenant_{tenant_id.hex}"
        
        with engine.connect() as conn:
            # 创建 schema
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
            
            # 复制表结构
            conn.execute(f"""
                CREATE TABLE {schema_name}.projects (LIKE public.projects INCLUDING ALL)
            """)
    
    @staticmethod
    @contextmanager
    def tenant_schema_scope(db_session, tenant_id: UUID):
        """切换到租户 Schema"""
        schema_name = f"tenant_{tenant_id.hex}"
        
        db_session.execute(f"SET search_path TO {schema_name}, public")
        try:
            yield
        finally:
            db_session.execute("SET search_path TO public")
```

---

## 权限管理系统

### RBAC 模型

```
User (用户)
  └── has many → UserRole (用户角色)
                    └── Role (角色)
                          └── has many → RolePermission
                                           └── Permission (权限)
```

### 角色定义

```python
# artpm_agent/tenancy/roles.py
from enum import Enum

class SystemRole(str, Enum):
    """系统角色"""
    SUPER_ADMIN = "super_admin"      # 平台超级管理员
    TENANT_OWNER = "tenant_owner"    # 租户所有者
    TENANT_ADMIN = "tenant_admin"    # 租户管理员

class WorkspaceRole(str, Enum):
    """工作空间角色"""
    OWNER = "owner"       # 工作空间所有者
    ADMIN = "admin"       # 管理员
    MEMBER = "member"     # 普通成员
    VIEWER = "viewer"     # 只读访客

class Permission(str, Enum):
    """权限定义"""
    # 租户管理
    TENANT_MANAGE = "tenant:manage"
    TENANT_VIEW = "tenant:view"
    
    # 工作空间管理
    WORKSPACE_CREATE = "workspace:create"
    WORKSPACE_DELETE = "workspace:delete"
    WORKSPACE_MANAGE = "workspace:manage"
    WORKSPACE_VIEW = "workspace:view"
    
    # 项目管理
    PROJECT_CREATE = "project:create"
    PROJECT_UPDATE = "project:update"
    PROJECT_DELETE = "project:delete"
    PROJECT_VIEW = "project:view"
    
    # 用户管理
    USER_INVITE = "user:invite"
    USER_REMOVE = "user:remove"
    USER_MANAGE = "user:manage"
    
    # Skill 执行
    SKILL_EXECUTE = "skill:execute"
    SKILL_MANAGE = "skill:manage"
    
    # 数据访问
    DATA_READ = "data:read"
    DATA_WRITE = "data:write"
    DATA_DELETE = "data:delete"

# 角色权限映射
ROLE_PERMISSIONS = {
    WorkspaceRole.OWNER: [
        Permission.WORKSPACE_MANAGE,
        Permission.PROJECT_CREATE,
        Permission.PROJECT_UPDATE,
        Permission.PROJECT_DELETE,
        Permission.PROJECT_VIEW,
        Permission.USER_INVITE,
        Permission.USER_REMOVE,
        Permission.USER_MANAGE,
        Permission.SKILL_EXECUTE,
        Permission.SKILL_MANAGE,
        Permission.DATA_READ,
        Permission.DATA_WRITE,
        Permission.DATA_DELETE,
    ],
    WorkspaceRole.ADMIN: [
        Permission.PROJECT_CREATE,
        Permission.PROJECT_UPDATE,
        Permission.PROJECT_VIEW,
        Permission.USER_INVITE,
        Permission.SKILL_EXECUTE,
        Permission.DATA_READ,
        Permission.DATA_WRITE,
    ],
    WorkspaceRole.MEMBER: [
        Permission.PROJECT_VIEW,
        Permission.SKILL_EXECUTE,
        Permission.DATA_READ,
        Permission.DATA_WRITE,
    ],
    WorkspaceRole.VIEWER: [
        Permission.PROJECT_VIEW,
        Permission.DATA_READ,
    ],
}
```

### 权限检查装饰器

```python
# artpm_agent/tenancy/decorators.py
from functools import wraps
from flask import g, abort

def require_permission(*permissions: Permission):
    """权限检查装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 获取当前用户权限
            user_permissions = get_user_permissions(
                g.current_user.id,
                g.workspace_id
            )
            
            # 检查权限
            required = set(permissions)
            if not required.issubset(user_permissions):
                abort(403, "Permission denied")
            
            return func(*args, **kwargs)
        return wrapper
    return decorator

def require_role(*roles: WorkspaceRole):
    """角色检查装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user_role = get_user_role(g.current_user.id, g.workspace_id)
            
            if user_role not in roles:
                abort(403, "Role required")
            
            return func(*args, **kwargs)
        return wrapper
    return decorator

# 使用示例
@app.route('/api/v1/projects', methods=['POST'])
@require_permission(Permission.PROJECT_CREATE)
def create_project():
    """创建项目 (需要 project:create 权限)"""
    pass

@app.route('/api/v1/workspaces/<workspace_id>', methods=['DELETE'])
@require_role(WorkspaceRole.OWNER)
def delete_workspace(workspace_id):
    """删除工作空间 (仅 OWNER)"""
    pass
```

---

## 资源配额管理

```python
# artpm_agent/tenancy/quotas.py
from dataclasses import dataclass
from typing import Dict

@dataclass
class TenantQuota:
    """租户配额"""
    # 用户数限制
    max_users: int
    current_users: int
    
    # 工作空间限制
    max_workspaces: int
    current_workspaces: int
    
    # 项目限制
    max_projects: int
    current_projects: int
    
    # 存储限制 (MB)
    max_storage_mb: int
    current_storage_mb: float
    
    # API 请求限制 (每月)
    max_api_requests_monthly: int
    current_api_requests_monthly: int
    
    # LLM Token 限制 (每月)
    max_llm_tokens_monthly: int
    current_llm_tokens_monthly: int

# 套餐配额
PLAN_QUOTAS: Dict[str, TenantQuota] = {
    "free": TenantQuota(
        max_users=3,
        current_users=0,
        max_workspaces=1,
        current_workspaces=0,
        max_projects=5,
        current_projects=0,
        max_storage_mb=100,
        current_storage_mb=0,
        max_api_requests_monthly=1000,
        current_api_requests_monthly=0,
        max_llm_tokens_monthly=100_000,
        current_llm_tokens_monthly=0,
    ),
    "pro": TenantQuota(
        max_users=10,
        current_users=0,
        max_workspaces=5,
        current_workspaces=0,
        max_projects=50,
        current_projects=0,
        max_storage_mb=5000,
        current_storage_mb=0,
        max_api_requests_monthly=50_000,
        current_api_requests_monthly=0,
        max_llm_tokens_monthly=5_000_000,
        current_llm_tokens_monthly=0,
    ),
    "enterprise": TenantQuota(
        max_users=-1,  # 无限制
        current_users=0,
        max_workspaces=-1,
        current_workspaces=0,
        max_projects=-1,
        current_projects=0,
        max_storage_mb=-1,
        current_storage_mb=0,
        max_api_requests_monthly=-1,
        current_api_requests_monthly=0,
        max_llm_tokens_monthly=-1,
        current_llm_tokens_monthly=0,
    ),
}

class QuotaChecker:
    """配额检查器"""
    
    @staticmethod
    def check_quota(tenant_id: UUID, quota_type: str) -> bool:
        """检查配额是否超限"""
        tenant = get_tenant(tenant_id)
        quota = PLAN_QUOTAS[tenant.plan_tier]
        
        if quota_type == "users":
            if quota.max_users == -1:
                return True
            return quota.current_users < quota.max_users
        
        # ... 其他配额检查
        
    @staticmethod
    def enforce_quota(tenant_id: UUID, quota_type: str):
        """强制配额限制"""
        if not QuotaChecker.check_quota(tenant_id, quota_type):
            raise QuotaExceededError(
                f"{quota_type} quota exceeded for tenant {tenant_id}"
            )
```

---

## 审计日志

```python
# artpm_agent/tenancy/audit.py
from datetime import datetime
from uuid import UUID

class AuditLogger:
    """审计日志记录器"""
    
    @staticmethod
    def log_action(
        tenant_id: UUID,
        user_id: UUID,
        action: str,
        resource_type: str,
        resource_id: Optional[UUID] = None,
        metadata: Optional[Dict] = None
    ):
        """记录审计日志"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "action": action,  # create, update, delete, view
            "resource_type": resource_type,  # project, user, workspace
            "resource_id": str(resource_id) if resource_id else None,
            "metadata": metadata or {},
            "ip_address": request.remote_addr,
            "user_agent": request.user_agent.string,
        }
        
        # 写入数据库
        db.session.execute("""
            INSERT INTO audit_logs (
                tenant_id, user_id, action, resource_type, 
                resource_id, metadata, ip_address, user_agent
            ) VALUES (
                :tenant_id, :user_id, :action, :resource_type,
                :resource_id, :metadata, :ip_address, :user_agent
            )
        """, log_entry)

# 装饰器
def audit(action: str, resource_type: str):
    """审计装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            
            # 记录审计日志
            AuditLogger.log_action(
                tenant_id=g.tenant_id,
                user_id=g.current_user.id,
                action=action,
                resource_type=resource_type,
                resource_id=result.get('id') if isinstance(result, dict) else None
            )
            
            return result
        return wrapper
    return decorator

# 使用示例
@app.route('/api/v1/projects', methods=['POST'])
@require_permission(Permission.PROJECT_CREATE)
@audit(action="create", resource_type="project")
def create_project():
    """创建项目"""
    pass
```

---

## 实施计划

### Phase 1: 基础架构 (2 周)
- [ ] 数据库迁移脚本 (RLS 策略)
- [ ] TenantContext 实现
- [ ] 租户识别中间件
- [ ] 基础 RBAC 模型

### Phase 2: 权限系统 (2 周)
- [ ] 角色权限映射
- [ ] 权限检查装饰器
- [ ] 资源配额管理
- [ ] 审计日志系统

### Phase 3: API 改造 (2 周)
- [ ] 所有 API 添加租户过滤
- [ ] 权限检查集成
- [ ] 配额检查集成
- [ ] 错误处理

### Phase 4: 测试与优化 (1 周)
- [ ] 单元测试
- [ ] 集成测试
- [ ] 性能测试
- [ ] 安全审计

---

## API 设计

### 租户管理 API

```python
# POST /api/v1/tenants - 创建租户
{
  "name": "Acme Inc",
  "subdomain": "acme",
  "plan_tier": "pro",
  "owner_email": "admin@acme.com"
}

# GET /api/v1/tenants/{tenant_id} - 获取租户信息
# PUT /api/v1/tenants/{tenant_id} - 更新租户
# DELETE /api/v1/tenants/{tenant_id} - 删除租户
```

### 工作空间 API

```python
# POST /api/v1/workspaces - 创建工作空间
{
  "name": "Marketing Team",
  "slug": "marketing"
}

# GET /api/v1/workspaces - 列出工作空间
# GET /api/v1/workspaces/{workspace_id} - 获取工作空间
# PUT /api/v1/workspaces/{workspace_id} - 更新工作空间
# DELETE /api/v1/workspaces/{workspace_id} - 删除工作空间
```

### 成员管理 API

```python
# POST /api/v1/workspaces/{workspace_id}/members - 邀请成员
{
  "email": "user@example.com",
  "role": "member"
}

# GET /api/v1/workspaces/{workspace_id}/members - 列出成员
# PUT /api/v1/workspaces/{workspace_id}/members/{user_id} - 更新角色
# DELETE /api/v1/workspaces/{workspace_id}/members/{user_id} - 移除成员
```

---

## 测试策略

### 单元测试
```python
# tests/test_tenancy.py
def test_tenant_isolation():
    """测试租户隔离"""
    tenant_a = create_tenant("tenant_a")
    tenant_b = create_tenant("tenant_b")
    
    project_a = create_project(tenant_a.id, "Project A")
    
    # tenant_b 不应该能访问 tenant_a 的项目
    with tenant_scope(db.session, tenant_b.id):
        projects = Project.query.all()
        assert project_a not in projects

def test_permission_check():
    """测试权限检查"""
    user = create_user(role=WorkspaceRole.VIEWER)
    
    # Viewer 不应该能创建项目
    with pytest.raises(PermissionError):
        with user_context(user):
            create_project("Test Project")
```

### 性能测试
```python
def test_rls_performance():
    """测试 RLS 性能影响"""
    # 创建 10000 条记录
    for i in range(10000):
        create_project(tenant_id, f"Project {i}")
    
    # 测试查询性能
    start = time.time()
    projects = Project.query.filter_by(tenant_id=tenant_id).all()
    elapsed = time.time() - start
    
    # 应该 < 100ms
    assert elapsed < 0.1
```

---

**下一步**: 实现基础架构代码

**预计完成时间**: 7 周  
**优先级**: 高  
**风险**: 中 (需要大量测试验证数据隔离)