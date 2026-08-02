"""多租户模块单元测试"""
import pytest
from uuid import uuid4
from artpm_agent.tenancy.context import TenantContext
from artpm_agent.tenancy.permissions import Permission, WorkspaceRole, ROLE_PERMISSIONS

def test_tenant_context():
    context = TenantContext(tenant_id=uuid4())
    assert context.tenant_id is not None

def test_permissions():
    owner_perms = ROLE_PERMISSIONS[WorkspaceRole.OWNER]
    assert Permission.PROJECT_CREATE in owner_perms

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
