"""
多租户核心功能简化测试
"""

import pytest

from artpm_agent.tenancy.context import (
    TenantContext,
    TenantContextManager,
    TenantContextError,
    WorkspaceAccessDenied,
)


class TestTenantContext:
    """测试租户上下文"""

    def test_create_basic_context(self):
        """测试创建基础上下文"""
        context = TenantContext(
            tenant_id="test-tenant",
            workspace_id="test-workspace",
            principal_id="test-user"
        )

        assert context.tenant_id == "test-tenant"
        assert context.workspace_id == "test-workspace"
        assert context.principal_id == "test-user"
        assert context.user_id == "test-user"  # 别名

    def test_local_context(self):
        """测试本地上下文"""
        context = TenantContext.local()

        assert context.tenant_id == "local"
        assert context.workspace_id == "local-default"
        assert context.principal_id == "local-user"
        assert "user" in context.roles

    def test_context_immutable(self):
        """测试上下文不可变"""
        context = TenantContext(tenant_id="tenant1")

        # 不能修改属性
        with pytest.raises(AttributeError):
            context.tenant_id = "new-tenant"

    def test_require_workspace_success(self):
        """测试工作空间验证成功"""
        context = TenantContext(
            tenant_id="t1",
            workspace_id="w1"
        )

        # 匹配的工作空间
        assert context.require_workspace("w1") == "w1"
        assert context.require_workspace() == "w1"  # 无参数返回当前

    def test_require_workspace_fail(self):
        """测试工作空间验证失败"""
        context = TenantContext(
            tenant_id="t1",
            workspace_id="w1"
        )

        # 不匹配的工作空间
        with pytest.raises(WorkspaceAccessDenied):
            context.require_workspace("w2")

    def test_require_workspace_missing(self):
        """测试缺少工作空间"""
        context = TenantContext(
            tenant_id="t1",
            workspace_id=""
        )

        with pytest.raises(WorkspaceAccessDenied):
            context.require_workspace()

    def test_invalid_tenant_id(self):
        """测试无效租户 ID"""
        # 空 ID
        with pytest.raises(TenantContextError):
            TenantContext(tenant_id="")

        # 非法字符
        with pytest.raises(TenantContextError):
            TenantContext(tenant_id="invalid tenant!")

    def test_roles_validation(self):
        """测试角色验证"""
        # 有效角色
        context = TenantContext(
            tenant_id="t1",
            roles=frozenset({"user"})
        )
        assert "user" in context.roles

        # 无效角色
        with pytest.raises(TenantContextError):
            TenantContext(
                tenant_id="t1",
                roles=frozenset({"invalid-role"})
            )

    def test_permissions(self):
        """测试权限"""
        context = TenantContext(
            tenant_id="t1",
            permissions=frozenset({"project:create", "project:view"})
        )

        assert "project:create" in context.permissions
        assert "project:view" in context.permissions
        assert len(context.permissions) == 2

    def test_bind_inputs(self):
        """测试绑定输入"""
        context = TenantContext(
            tenant_id="t1",
            workspace_id="w1"
        )

        # 正常绑定
        inputs = {"name": "test", "tenant_id": "t1", "workspace_id": "w1"}
        bound = context.bind_inputs(inputs)

        assert bound["tenant_id"] == "t1"
        assert bound["workspace_id"] == "w1"
        assert bound["name"] == "test"

    def test_bind_inputs_conflict(self):
        """测试绑定输入冲突"""
        context = TenantContext(
            tenant_id="t1",
            workspace_id="w1"
        )

        # 租户不匹配
        with pytest.raises(WorkspaceAccessDenied):
            context.bind_inputs({"tenant_id": "t2"})

        # 工作空间不匹配
        with pytest.raises(WorkspaceAccessDenied):
            context.bind_inputs({"workspace_id": "w2"})


class TestTenantContextManager:
    """测试租户上下文管理器"""

    def test_get_set_current(self):
        """测试获取设置当前上下文"""
        context = TenantContext.local()

        # 初始为 None
        assert TenantContextManager.get_current() is None

        # 设置
        token = TenantContextManager.set_current(context)
        assert TenantContextManager.get_current() == context

        # 重置
        TenantContextManager.reset(token)
        assert TenantContextManager.get_current() is None

    def test_use_context_manager(self):
        """测试 use 上下文管理器"""
        context = TenantContext(tenant_id="t1", workspace_id="w1")

        # 使用 with 语句
        with TenantContextManager.use(context) as ctx:
            assert ctx == context
            assert TenantContextManager.get_current() == context

        # 退出后清空
        assert TenantContextManager.get_current() is None

    def test_nested_contexts(self):
        """测试嵌套上下文"""
        ctx1 = TenantContext(tenant_id="t1")
        ctx2 = TenantContext(tenant_id="t2")

        with TenantContextManager.use(ctx1):
            assert TenantContextManager.get_current().tenant_id == "t1"

            with TenantContextManager.use(ctx2):
                assert TenantContextManager.get_current().tenant_id == "t2"

            # 恢复外层
            assert TenantContextManager.get_current().tenant_id == "t1"

        # 全部清空
        assert TenantContextManager.get_current() is None

    def test_invalid_context(self):
        """测试无效上下文"""
        with pytest.raises(TenantContextError):
            TenantContextManager.set_current("not a context")


class TestIntegration:
    """集成测试"""

    def test_full_workflow(self):
        """测试完整工作流"""
        # 1. 创建上下文
        context = TenantContext(
            tenant_id="acme-corp",
            workspace_id="engineering",
            principal_id="john-doe",
            roles=frozenset({"user", "admin"}),
            permissions=frozenset({"project:create", "project:view"})
        )

        # 2. 使用上下文
        with TenantContextManager.use(context):
            current = TenantContextManager.get_current()

            # 3. 验证
            assert current.tenant_id == "acme-corp"
            assert current.workspace_id == "engineering"
            assert current.user_id == "john-doe"

            # 4. 工作空间验证
            current.require_workspace("engineering")

            # 5. 角色和权限
            assert "admin" in current.roles
            assert "project:create" in current.permissions

            # 6. 绑定输入
            inputs = {"name": "New Project"}
            bound = current.bind_inputs(inputs)
            assert bound["tenant_id"] == "acme-corp"
            assert bound["workspace_id"] == "engineering"

    def test_multi_tenant_isolation(self):
        """测试多租户隔离"""
        tenant1 = TenantContext(tenant_id="t1", workspace_id="w1")
        tenant2 = TenantContext(tenant_id="t2", workspace_id="w2")

        # 租户 1
        with TenantContextManager.use(tenant1):
            current = TenantContextManager.get_current()
            assert current.tenant_id == "t1"

            # 不能访问租户 2 的工作空间
            with pytest.raises(WorkspaceAccessDenied):
                current.require_workspace("w2")

        # 租户 2
        with TenantContextManager.use(tenant2):
            current = TenantContextManager.get_current()
            assert current.tenant_id == "t2"

            # 不能访问租户 1 的工作空间
            with pytest.raises(WorkspaceAccessDenied):
                current.require_workspace("w1")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
