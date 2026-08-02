# 多租户系统使用指南

**版本**: 2.0
**更新日期**: 2026-08-02

本文描述**已实现**的多租户能力。设计阶段的 RLS / 配额 / 审计日志方案见
`docs/MULTI_TENANT_ARCHITECTURE.md`（路线图，未实施）。

---

## 📚 目录

- [能力边界](#能力边界)
- [快速开始](#快速开始)
- [核心概念](#核心概念)
- [使用示例](#使用示例)
- [API 参考](#api-参考)
- [最佳实践](#最佳实践)
- [故障排查](#故障排查)

---

## 能力边界

已实现（`artpm_agent/tenancy/`，不依赖任何 Web 框架）：

| 能力 | 模块 |
| --- | --- |
| 请求级租户上下文 | `tenancy/context.py` |
| RBAC 角色与权限判定 | `tenancy/permissions.py` |
| store 层工作区隔离守卫 | `tenancy/scoped_store.py` |
| HTTP 入口身份解析 | `artpm_agent/api/app.py` |

**未实现**：租户/工作空间/用户的 ORM 模型与管理端点、资源配额、
审计日志、PostgreSQL Row-Level Security。`tenancy` 包不持久化租户信息，
它只消费由可信入口层构造好的上下文。

---

## 快速开始

多租户能力随包一起安装，无需额外依赖：

```bash
pip install -e .
```

### 基础使用

```python
from artpm_agent.tenancy import TenantContext, TenantContextManager

context = TenantContext(
    tenant_id="acme-corp",
    workspace_id="acme-engineering",
    principal_id="john-doe",
    roles=frozenset({"user"}),
    permissions=frozenset({"project:create", "project:view"}),
)

with TenantContextManager.use(context):
    current = TenantContextManager.get_current()
    print(f"当前租户: {current.tenant_id}")
```

本地单机运行可以直接用内置的 `local` 作用域：

```python
context = TenantContext.local()
# tenant_id="local", workspace_id="local-default", principal_id="local-user"
```

---

## 核心概念

### 1. 租户与工作空间 (tenant / workspace)

两者都是**字符串 ID**，不是数据库实体。格式受
`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$` 约束，非法值直接抛
`TenantContextError`。

`workspace_id` 是底层 store 的隔离键，必须**全局唯一**，且
`tenant -> workspace` 的归属关系由入口层校验完毕后才构造上下文。
`tenancy` 包本身不做归属查询。

### 2. 上下文只能由可信宿主创建

`TenantContext` 是 `frozen=True, slots=True` 的不可变 dataclass。
模型输出、请求体、客户端参数都不允许构造或替换它 —— 这是整个隔离
方案的信任根。

### 3. 角色 (WorkspaceRole)

- **OWNER** - 拥有全部权限
- **ADMIN** - 管理工作空间与成员
- **MEMBER** - 基础读写
- **VIEWER** - 只读

注意 `TenantContext.roles` 只接受 `user` / `admin` / `service` 三个
传输层角色，`WorkspaceRole` 是权限判定层的更细粒度概念，二者不同。

### 4. 权限 (Permission)

25 个细粒度权限枚举，见 `artpm_agent/tenancy/permissions.py`：

```python
from artpm_agent.tenancy.permissions import Permission

Permission.PROJECT_CREATE    # "project:create"
Permission.PROJECT_VIEW      # "project:view"
Permission.PROJECT_DELETE    # "project:delete"
Permission.USER_INVITE       # "user:invite"
Permission.DATA_EXPORT       # "data:export"
```

`ROLE_PERMISSIONS` 给出角色到权限集的默认映射。

---

## 使用示例

### 示例 1: 权限判定

```python
from artpm_agent.tenancy import TenantContext, TenantContextManager
from artpm_agent.tenancy.permissions import (
    Permission,
    PermissionChecker,
    ROLE_PERMISSIONS,
    WorkspaceRole,
)

context = TenantContext(
    tenant_id="acme-corp",
    workspace_id="acme-engineering",
    principal_id="alice",
    roles=frozenset({"admin"}),
    permissions=frozenset(
        item.value for item in ROLE_PERMISSIONS[WorkspaceRole.ADMIN]
    ),
)

with TenantContextManager.use(context):
    granted = PermissionChecker.has_permission(
        "alice", "acme-engineering", Permission.PROJECT_CREATE
    )
    print(f"可创建项目: {granted}")
```

`context.permissions` 为空时，判定回退到角色默认值：`admin` 角色
拿到 OWNER 权限集，其余拿到 MEMBER 权限集。

### 示例 2: 装饰器

```python
from artpm_agent.tenancy.permissions import (
    Permission,
    WorkspaceRole,
    require_permission,
    require_role,
    require_tenant_owner,
)

@require_permission(Permission.PROJECT_DELETE)
def delete_project(project_id: str) -> None:
    ...

@require_role(WorkspaceRole.ADMIN, WorkspaceRole.OWNER)
def invite_member(email: str) -> None:
    ...

@require_tenant_owner
def transfer_tenant(new_owner_id: str) -> None:
    ...
```

装饰器从 `contextvars` 读当前上下文，**不接收** `tenant_id` 参数，
因此调用方无法伪造作用域。缺上下文抛
`TenantAuthenticationRequired`，权限不足抛 `WorkspaceAccessDenied`。
HTTP 适配层负责把这两个异常翻译成 401 / 403。

### 示例 3: store 工作区守卫

`WorkspaceStoreGuard` 只允许调用签名里带 `workspace_id` 的方法，并
强制用上下文里的值覆盖调用方传入的值：

```python
from artpm_agent.tenancy import TenantContext, WorkspaceStoreGuard
from artpm_agent.security.permission_store import PermissionStore

store = PermissionStore("data/permissions.db")
scoped = WorkspaceStoreGuard(store, context)

# workspace_id 由守卫注入，跨工作区的 request_id 取不到数据
request = scoped.call("get", "req-123")
pending = scoped.call("list_pending")
```

即使调用方显式传入别的工作区，也会被上下文覆盖：

```python
scoped.call("get", "req-123", workspace_id="other-workspace")
# WorkspaceAccessDenied: workspace does not match the authenticated context
```

没有 `workspace_id` 边界的方法，以及私有方法，一律拒绝：

```python
scoped.call("list_workspaces")  # UnsafeStoreOperation：无工作区边界
scoped.call("_connect")         # UnsafeStoreOperation：私有方法
```

### 示例 4: 绑定 Skill 输入

`bind_inputs` 用服务端作用域覆盖模型给出的参数，并在模型试图改写
租户/工作空间/主体时抛错：

```python
with TenantContextManager.use(context):
    bound = context.bind_inputs({"name": "新项目", "workspace_id": ""})
    # bound["tenant_id"] / bound["workspace_id"] 已被服务端填充

    context.bind_inputs({"tenant_id": "other-corp"})  # WorkspaceAccessDenied
```

### 示例 5: FastAPI 网关

网关由可信反向代理注入身份请求头，`RequestPrincipal` 校验后生成
上下文，路由再从 `request.state.tenant_context` 取值：

```python
from artpm_agent.api import create_app

app = create_app(services=my_services, identity_resolver=my_resolver)
```

| Header | 含义 |
| --- | --- |
| `X-Tenant-ID` | 已认证租户，缺省 `local` |
| `X-Workspace-ID` | 全局唯一的规范工作区 ID |
| `X-Actor-ID` | 当前主体 ID |
| `X-Actor-Role` | `user` 或 `admin` |
| `X-Actor-Kind` | `human` 或 `service`，审批接口只接受 `human` |
| `X-Gateway-Token` | 启用共享密钥时的代理签名头 |

`ARTPM_ENV=production` 时强制要求 `ARTPM_GATEWAY_SHARED_SECRET` 与
`X-Gateway-Token`；不设密钥时身份头只接受 loopback 来源，避免远程调用方
自行指派租户或 `admin` 角色。详见 `docs/API_GATEWAY.md`。

---

## API 参考

### TenantContext

```python
@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: str
    workspace_id: str = ""
    principal_id: str = "local-user"
    roles: frozenset[str] = frozenset({"user"})
    request_id: str | None = None
    permissions: frozenset[str] = frozenset()

    @classmethod
    def local(cls) -> TenantContext: ...
    @property
    def user_id(self) -> str: ...            # principal_id 的兼容别名
    def require_workspace(self, requested=None) -> str: ...
    def bind_inputs(self, inputs: Mapping) -> dict: ...
```

### TenantContextManager

```python
class TenantContextManager:
    @staticmethod
    def get_current() -> TenantContext | None: ...
    @staticmethod
    def set_current(context: TenantContext) -> Token: ...
    @staticmethod
    def reset(token: Token) -> None: ...
    @staticmethod
    @contextmanager
    def use(context: TenantContext) -> Iterator[TenantContext]: ...
```

基于 `contextvars`，因此 asyncio 并发请求之间天然隔离。

### PermissionChecker

```python
class PermissionChecker:
    @staticmethod
    def current() -> TenantContext: ...
    @staticmethod
    def get_user_permissions(user_id, workspace_id, *, context=None) -> frozenset[Permission]: ...
    @staticmethod
    def has_permission(user_id, workspace_id, permission, *, context=None) -> bool: ...
    @staticmethod
    def has_role(user_id, workspace_id, role, *, context=None) -> bool: ...
```

### WorkspaceStoreGuard

```python
class WorkspaceStoreGuard:
    def __init__(self, store: Any, tenant_context: TenantContext): ...
    def call(self, method_name: str, /, *args, **kwargs) -> Any: ...
```

### 异常层级

```
ValueError
└── TenantContextError            # 上下文非法或被伪造
    ├── WorkspaceAccessDenied     # 越权访问其他工作区
    │   └── UnsafeStoreOperation  # store 方法无法证明隔离
    └── TenantAuthenticationRequired  # 缺少上下文
```

---

## 最佳实践

### 1. 上下文只在入口层创建一次

```python
# ✅ 入口层校验身份后创建，向下透传
with TenantContextManager.use(principal.tenant_context()):
    handle_request()

# ❌ 业务代码里自行拼装，等于绕过认证
TenantContext(tenant_id=request.json["tenant_id"], ...)
```

### 2. 走守卫访问 store，不要手写过滤

```python
# ✅ 工作区键由守卫注入
WorkspaceStoreGuard(store, context).call("list_conversations")

# ❌ 手写过滤，漏一处就是跨租户泄漏
store.list_conversations(workspace_id=request.json["workspace_id"])
```

### 3. 失败时保持 fail-closed

上下文非法或 store 不可用时应当拒绝操作，而不是放行。例如
`has_pending_permission_requests` 在这两种情况下都返回 `True`，
让输入框保持锁定状态。

### 4. 用 `bind_inputs` 处理所有模型产出的参数

模型给出的 `tenant_id` / `workspace_id` / `principal_id` 一律视为不可信，
交给 `bind_inputs` 覆盖或拒绝。

### 5. 不要把 `roles` 当业务角色用

`roles` 只有 `user` / `admin` / `service`，用于传输层判定。业务角色请用
`WorkspaceRole` 加 `permissions` 表达。

---

## 故障排查

### 问题 1: `TenantContextError: tenant_id has an invalid format`

ID 必须匹配 `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`。常见原因是传了
UUID 对象而非字符串、含空格，或以 `-` / `_` 开头。

```python
TenantContext(tenant_id=str(tenant_uuid), ...)
```

### 问题 2: `TenantAuthenticationRequired: tenant context is required`

装饰器或 `PermissionChecker.current()` 在上下文之外被调用。确认调用
包在 `TenantContextManager.use(...)` 里；注意上下文不跨线程传播，
用 `run_in_executor` 时需要在子线程内重新设置。

### 问题 3: `WorkspaceAccessDenied: workspace scope is required`

`workspace_id` 为空却调用了 `require_workspace()`。构造上下文时补上
工作区，或改用 `TenantContext.local()`。

### 问题 4: `UnsafeStoreOperation: store method has no workspace_id boundary`

被调方法签名里没有 `workspace_id`。给 store 方法加上显式的
`workspace_id` 参数，而不是绕过守卫直接调用。

### 问题 5: 网关返回 401 `invalid_tenant_context`

身份头缺失或格式非法。本地调试确认从 loopback 发起；生产环境确认
反向代理注入了 `X-Gateway-Token` 且与 `ARTPM_GATEWAY_SHARED_SECRET` 一致。

---

## 附录：权限与角色映射

| 权限 | Owner | Admin | Member | Viewer |
|------|-------|-------|--------|--------|
| `tenant:manage` / `tenant:view` / `tenant:settings` | ✅ | ❌ | ❌ | ❌ |
| `workspace:create` / `workspace:delete` / `workspace:manage` | ✅ | ❌ | ❌ | ❌ |
| `workspace:view` | ✅ | ✅ | ✅ | ✅ |
| `workspace:settings` | ✅ | ✅ | ❌ | ❌ |
| `project:create` | ✅ | ✅ | ❌ | ❌ |
| `project:update` | ✅ | ✅ | ✅ | ❌ |
| `project:archive` | ✅ | ✅ | ❌ | ❌ |
| `project:delete` | ✅ | ❌ | ❌ | ❌ |
| `project:view` | ✅ | ✅ | ✅ | ✅ |
| `user:invite` | ✅ | ✅ | ❌ | ❌ |
| `user:remove` / `user:manage` | ✅ | ❌ | ❌ | ❌ |
| `user:view` | ✅ | ✅ | ✅ | ✅ |
| `skill:execute` | ✅ | ✅ | ✅ | ❌ |
| `skill:manage` | ✅ | ✅ | ❌ | ❌ |
| `skill:view` | ✅ | ✅ | ✅ | ✅ |
| `data:read` | ✅ | ✅ | ✅ | ✅ |
| `data:write` | ✅ | ✅ | ✅ | ❌ |
| `data:delete` | ✅ | ❌ | ❌ | ❌ |
| `data:export` | ✅ | ✅ | ❌ | ❌ |
| `audit:view` | ✅ | ❌ | ❌ | ❌ |

以 `artpm_agent/tenancy/permissions.py` 的 `ROLE_PERMISSIONS` 为准。

---

**文档版本**: 2.0
**更新日期**: 2026-08-02
**维护者**: ArtPM Team
