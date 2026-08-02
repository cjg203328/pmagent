# Phase 2 实施计划: API 集成与测试

**开始日期**: 2026-07-22  
**预计周期**: 1 周  
**状态**: 🚀 启动中

---

## 📋 Phase 2 目标

将 Phase 1 的多租户基础架构集成到现有 API 中,实现:
- ✅ 所有 API 端点租户隔离
- ✅ 自动权限检查
- ✅ 自动配额限制
- ✅ 完整审计日志
- ✅ 端到端集成测试

---

## 🎯 核心任务

### 任务 1: 租户管理 API (2 小时)
创建租户、工作空间、用户管理的完整 REST API

**端点**:
- POST   /api/v1/tenants - 创建租户
- GET    /api/v1/tenants/{id} - 获取租户
- PUT    /api/v1/tenants/{id} - 更新租户
- DELETE /api/v1/tenants/{id} - 删除租户
- POST   /api/v1/workspaces - 创建工作空间
- GET    /api/v1/workspaces - 列出工作空间
- POST   /api/v1/workspaces/{id}/members - 添加成员
- GET    /api/v1/tenants/{id}/quota - 获取配额状态

### 任务 2: 现有 API 改造 (3 小时)
为现有 API 添加租户隔离和权限检查

**需要改造的 API**:
- 项目管理 API
- 技能执行 API
- 工作流 API
- 对话 API

### 任务 3: 集成测试 (2 小时)
端到端测试验证完整流程

**测试场景**:
- 多租户隔离测试
- 权限控制测试
- 配额限制测试
- 并发访问测试

### 任务 4: 性能优化 (1 小时)
查询优化和缓存策略

---

## 📝 详细实施步骤

### Step 1: 创建租户管理 API

#### 1.1 租户 CRUD API
```python
# artpm_agent/api/tenant_api.py
from flask import Blueprint, request, jsonify, g
from artpm_agent.tenancy import (
    Tenant,
    require_permission,
    Permission,
    audit,
)

tenant_bp = Blueprint('tenant', __name__)

@tenant_bp.route('/tenants', methods=['POST'])
@require_permission(Permission.TENANT_MANAGE)
@audit(action="create", resource_type="tenant")
def create_tenant():
    """创建租户"""
    pass

@tenant_bp.route('/tenants/<tenant_id>', methods=['GET'])
@require_permission(Permission.TENANT_VIEW)
def get_tenant(tenant_id):
    """获取租户信息"""
    pass
```

#### 1.2 工作空间 API
```python
@tenant_bp.route('/workspaces', methods=['POST'])
@require_permission(Permission.WORKSPACE_CREATE)
@require_quota('workspaces')
@audit(action="create", resource_type="workspace")
def create_workspace():
    """创建工作空间"""
    pass
```

### Step 2: 改造现有 API

#### 2.1 项目 API 改造
```python
# 改造前
@app.route('/api/v1/projects', methods=['GET'])
def list_projects():
    projects = Project.query.all()  # ❌ 没有租户过滤
    return jsonify(projects)

# 改造后
@app.route('/api/v1/projects', methods=['GET'])
@tenant_required
@workspace_required
@require_permission(Permission.PROJECT_VIEW)
def list_projects():
    projects = Project.query.filter_by(
        tenant_id=g.tenant_id,
        workspace_id=g.workspace_id
    ).all()  # ✅ 自动租户隔离
    return jsonify(projects)
```

### Step 3: 集成测试

#### 3.1 端到端测试框架
```python
# tests/integration/test_multi_tenant_api.py
class TestMultiTenantAPI:
    def test_tenant_isolation(self):
        """测试租户隔离"""
        # 创建两个租户
        tenant1 = create_tenant("tenant1")
        tenant2 = create_tenant("tenant2")
        
        # 租户1创建项目
        project1 = create_project(tenant1, "Project 1")
        
        # 租户2不应该看到租户1的项目
        projects = list_projects(tenant2)
        assert project1 not in projects
```

---

## 🔧 技术实现

### 1. Flask Blueprint 结构

```
artpm_agent/api/
├── __init__.py
├── tenant_api.py      # 租户管理 API
├── workspace_api.py   # 工作空间 API
├── user_api.py        # 用户管理 API
└── quota_api.py       # 配额管理 API
```

### 2. 中间件集成

```python
# artpm_agent/app.py
from flask import Flask
from artpm_agent.tenancy import TenantMiddleware
from artpm_agent.api import tenant_bp, workspace_bp

app = Flask(__name__)

# 注册中间件
TenantMiddleware(app)

# 注册 Blueprint
app.register_blueprint(tenant_bp, url_prefix='/api/v1')
app.register_blueprint(workspace_bp, url_prefix='/api/v1')
```

### 3. 数据库查询模式

```python
# 模式 1: 手动过滤 (简单场景)
projects = Project.query.filter_by(
    tenant_id=g.tenant_id,
    workspace_id=g.workspace_id
).all()

# 模式 2: RLS 自动过滤 (复杂场景)
with tenant_db_scope(db.session, g.tenant_id, g.current_user.id):
    projects = db.session.query(Project).all()
    # PostgreSQL RLS 自动过滤
```

---

## 📊 实施进度跟踪

| 任务 | 预计时间 | 实际时间 | 状态 |
|------|---------|---------|------|
| 租户管理 API | 2h | - | 待开始 |
| 工作空间 API | 1h | - | 待开始 |
| 现有 API 改造 | 3h | - | 待开始 |
| 集成测试 | 2h | - | 待开始 |
| 性能优化 | 1h | - | 待开始 |
| 文档更新 | 1h | - | 待开始 |
| **总计** | **10h** | **0h** | **0%** |

---

## 🎯 成功标准

### 功能完整性
- [ ] 所有租户管理 API 实现
- [ ] 所有现有 API 添加租户过滤
- [ ] 权限检查 100% 覆盖
- [ ] 配额检查关键端点覆盖

### 测试覆盖
- [ ] 单元测试覆盖率 > 80%
- [ ] 集成测试 20+ 场景
- [ ] 性能测试通过

### 文档
- [ ] API 文档更新
- [ ] 使用示例完整
- [ ] 迁移指南

---

## 🚀 开始执行

**当前状态**: Phase 2 计划已完成  
**下一步**: 创建租户管理 API

---

**创建时间**: 2026-07-22  
**预计完成**: 2026-07-23  
**负责人**: Claude (Fable 5)
