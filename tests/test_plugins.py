"""Security and failure-isolation tests for external skill plugins."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path

from artpm_agent.plugins import PluginManager, PluginPolicy
from artpm_agent.skills.skill_router import SkillRouter
from artpm_agent.tenancy import TenantContext
from artpm_agent.workflows.designer import capability_allowlist_from_skill_metadata


GOOD_MODULE = """\
from artpm_agent.skills.base_skill import BaseSkill

class ExternalEchoSkill(BaseSkill):
    skill_name = "external_echo"
    description = "Echo a trusted workspace scope"
    version = "1.0.0"

    def execute(self, inputs):
        tenant_context = self.context.get("tenant_context")
        return {
            "echo": inputs.get("value"),
            "tenant_id": inputs.get("tenant_id"),
            "workspace_id": inputs.get("workspace_id"),
            "context_principal": getattr(tenant_context, "principal_id", None),
        }
"""


def _plugin(
    root: Path,
    directory: str,
    *,
    plugin_id: str,
    source: str = GOOD_MODULE,
    skill_name: str = "external_echo",
    class_name: str = "ExternalEchoSkill",
    read_only: bool = True,
    requires_approval: bool = False,
) -> Path:
    plugin_root = root / directory
    plugin_root.mkdir(parents=True)
    module_path = plugin_root / "plugin.py"
    module_path.write_text(source, encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "plugin_id": plugin_id,
        "version": "1.0.0",
        "module": "plugin.py",
        "module_sha256": sha256(module_path.read_bytes()).hexdigest(),
        "skills": [
            {
                "name": skill_name,
                "class": class_name,
                "description": "External test skill",
                "version": "1.0.0",
                "risk": "low",
                "read_only": read_only,
                "requires_approval": requires_approval,
                "capabilities": ["external.analyze"],
            }
        ],
    }
    (plugin_root / "artpm-plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return plugin_root


def _policy(root: Path, *plugin_ids: str, enabled: bool = True) -> PluginPolicy:
    return PluginPolicy(
        enabled=enabled,
        trusted_roots=(root.resolve(),),
        allowed_plugin_ids=frozenset(plugin_ids),
    )


def test_secure_default_does_not_discover_or_execute_plugin_code(tmp_path):
    marker = tmp_path / "executed.txt"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n"
    _plugin(
        tmp_path,
        "disabled",
        plugin_id="test.disabled",
        source=source,
    )

    report = PluginManager().load_skills()

    assert report.registrations == ()
    assert not marker.exists()


def test_allowlisted_plugin_loads_and_router_keeps_metadata_local(tmp_path):
    _plugin(tmp_path, "good", plugin_id="test.good")
    manager = PluginManager(_policy(tmp_path, "test.good"))
    tenant = TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
    )

    router = SkillRouter({"plugin_manager": manager, "tenant_context": tenant})

    result = router.execute_skill("external_echo", {"value": "hello"})
    metadata = router.get_skill_metadata("external_echo")
    assert result["success"] is True
    assert result["tenant_id"] == "tenant-a"
    assert result["workspace_id"] == "workspace-a"
    assert metadata["plugin_id"] == "test.good"
    assert metadata["is_plugin_skill"] is True
    assert metadata["capabilities"] == ("external.analyze",)
    listed = next(item for item in router.list_skills() if item["name"] == "external_echo")
    assert listed["capabilities"] == ["external.analyze"]
    workflow_allowlist = capability_allowlist_from_skill_metadata(
        router.list_skills()
    )
    assert workflow_allowlist["external_echo"] == {"external.analyze"}


def test_duplicate_builtin_skill_name_is_rejected_without_override(tmp_path):
    marker = tmp_path / "duplicate-executed.txt"
    source = (
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
        + GOOD_MODULE.replace("external_echo", "quote_calculator")
    )
    _plugin(
        tmp_path,
        "duplicate",
        plugin_id="test.duplicate",
        source=source,
        skill_name="quote_calculator",
    )
    manager = PluginManager(_policy(tmp_path, "test.duplicate"))

    report = manager.load_skills(reserved_names={"quote_calculator"})

    assert report.registrations == ()
    assert report.loaded_plugin_ids == ()
    assert not marker.exists()
    assert any("duplicate or reserved" in item.error for item in report.failures)


def test_bad_plugin_is_isolated_while_good_plugin_loads(tmp_path):
    _plugin(tmp_path, "a-good", plugin_id="test.good")
    _plugin(
        tmp_path,
        "b-bad",
        plugin_id="test.bad",
        source="raise RuntimeError('bad plugin import')\n",
    )
    manager = PluginManager(_policy(tmp_path, "test.good", "test.bad"))

    report = manager.load_skills()

    assert report.loaded_plugin_ids == ("test.good",)
    assert [item.name for item in report.registrations] == ["external_echo"]
    assert any(item.plugin_id == "test.bad" for item in report.failures)


def test_plugin_system_exit_is_isolated_from_host_process(tmp_path):
    _plugin(
        tmp_path,
        "exit",
        plugin_id="test.exit",
        source="raise SystemExit(7)\n",
    )
    manager = PluginManager(_policy(tmp_path, "test.exit"))

    report = manager.load_skills()

    assert report.registrations == ()
    assert any("SystemExit" in item.error for item in report.failures)


def test_hash_mismatch_blocks_code_before_import(tmp_path):
    plugin_root = _plugin(tmp_path, "changed", plugin_id="test.changed")
    marker = tmp_path / "tampered.txt"
    (plugin_root / "plugin.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    manager = PluginManager(_policy(tmp_path, "test.changed"))

    report = manager.load_skills()

    assert report.registrations == ()
    assert not marker.exists()
    assert any("SHA-256" in item.error for item in report.failures)


def test_unallowlisted_plugin_is_discovered_but_never_imported(tmp_path):
    marker = tmp_path / "untrusted.txt"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
    _plugin(
        tmp_path,
        "untrusted",
        plugin_id="test.untrusted",
        source=source,
    )
    manager = PluginManager(_policy(tmp_path, "test.other"))

    report = manager.load_skills()

    assert report.skipped_plugin_ids == ("test.untrusted",)
    assert not marker.exists()


def test_write_plugin_must_declare_approval_requirement(tmp_path):
    _plugin(
        tmp_path,
        "unsafe",
        plugin_id="test.unsafe",
        read_only=False,
        requires_approval=False,
    )
    manager = PluginManager(_policy(tmp_path, "test.unsafe"))

    report = manager.load_skills()

    assert report.registrations == ()
    assert any("must require approval" in item.error for item in report.failures)


def test_request_scoped_router_does_not_reuse_tenant_skill_context(tmp_path):
    _plugin(tmp_path, "good", plugin_id="test.scoped")
    router = SkillRouter(
        {"plugin_manager": PluginManager(_policy(tmp_path, "test.scoped"))}
    )
    tenant_a = TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
        request_id="request-a",
    )
    tenant_b = TenantContext(
        tenant_id="tenant-b",
        workspace_id="workspace-b",
        principal_id="user-b",
        request_id="request-b",
    )
    bound_a = router.for_tenant(tenant_a)
    bound_b = router.for_tenant(tenant_b)

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(bound_a.execute_skill, "external_echo", {"value": "a"})
        future_b = executor.submit(bound_b.execute_skill, "external_echo", {"value": "b"})
        result_a = future_a.result()
        result_b = future_b.result()

    assert result_a["workspace_id"] == "workspace-a"
    assert result_a["context_principal"] == "user-a"
    assert result_b["workspace_id"] == "workspace-b"
    assert result_b["context_principal"] == "user-b"
    assert router.tenant_context is None
