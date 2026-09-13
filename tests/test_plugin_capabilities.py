"""Tests for the capability-extension of the plugin system.

Covers: manifests that declare tools/handlers/providers (instead of only
skills), loader collection of the three new registration kinds, the unified
CapabilityRegistry, and the create_llm_client provider extension point.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from artpm_agent.plugins import (
    PluginLoadReport,
    PluginManager,
    PluginPolicy,
    PluginProviderRegistration,
    PluginToolRegistration,
    get_capability_registry,
    load_plugin_manifest,
    reset_capability_registry,
)
from artpm_agent.utils.llm_client import create_llm_client

CAPABILITY_MODULE = """\
class EchoTool:
    name = "echo_tool"
    description = "Echo tool contributed by a plugin"

    def execute(self, inputs):
        return {"echo": inputs.get("value")}


class AuditHandler:
    name = "audit_handler"

    def handle(self, event):
        return {"handled": True}


def build_alt_client(config):
    class AltClient:
        provider = "alt"
        config = dict(config)

    return AltClient(config)
"""


def _write_plugin(root: Path, *, plugin_id: str, manifest_extra: dict) -> Path:
    plugin_root = root / plugin_id.replace(".", "_")
    plugin_root.mkdir(parents=True)
    module_path = plugin_root / "plugin.py"
    module_path.write_text(CAPABILITY_MODULE, encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "plugin_id": plugin_id,
        "version": "1.0.0",
        "module": "plugin.py",
        "module_sha256": sha256(module_path.read_bytes()).hexdigest(),
    }
    manifest.update(manifest_extra)
    (plugin_root / "artpm-plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return plugin_root


def _policy(root: Path, *plugin_ids: str) -> PluginPolicy:
    return PluginPolicy(
        enabled=True,
        trusted_roots=(root.resolve(),),
        allowed_plugin_ids=frozenset(plugin_ids),
    )


def _entry(name: str, class_name: str) -> dict:
    return {
        "name": name,
        "class": class_name,
        "description": "test entry",
        "version": "1.0.0",
        "risk": "low",
        "read_only": True,
        "requires_approval": False,
    }


# ── manifest ──


def test_manifest_accepts_tools_handlers_providers_without_skills(tmp_path):
    root = _write_plugin(
        tmp_path,
        plugin_id="test.cap",
        manifest_extra={
            "tools": [_entry("echo_tool", "EchoTool")],
            "handlers": [_entry("audit_handler", "AuditHandler")],
            "providers": [_entry("alt", "build_alt_client")],
        },
    )
    manifest = load_plugin_manifest(root / "artpm-plugin.json")
    assert manifest.skills == ()
    assert [entry.name for entry in manifest.tools] == ["echo_tool"]
    assert [entry.name for entry in manifest.handlers] == ["audit_handler"]
    assert [entry.name for entry in manifest.providers] == ["alt"]


def test_manifest_requires_at_least_one_capability(tmp_path):
    root = _write_plugin(tmp_path, plugin_id="test.empty", manifest_extra={})
    with pytest.raises(Exception, match="at least one skill, tool, handler, or provider"):
        load_plugin_manifest(root / "artpm-plugin.json")


def test_manifest_rejects_empty_tools_array(tmp_path):
    root = _write_plugin(
        tmp_path,
        plugin_id="test.empty.tools",
        manifest_extra={"tools": []},
    )
    with pytest.raises(Exception, match="tools must be a non-empty array"):
        load_plugin_manifest(root / "artpm-plugin.json")


def test_manifest_rejects_duplicate_tool_names(tmp_path):
    root = _write_plugin(
        tmp_path,
        plugin_id="test.dup",
        manifest_extra={
            "tools": [_entry("echo_tool", "EchoTool"), _entry("echo_tool", "OtherTool")]
        },
    )
    with pytest.raises(Exception, match="tools names must be unique"):
        load_plugin_manifest(root / "artpm-plugin.json")


# ── loader ──


def test_loader_collects_tool_handler_provider_registrations(tmp_path):
    _write_plugin(
        tmp_path,
        plugin_id="test.cap2",
        manifest_extra={
            "tools": [_entry("echo_tool", "EchoTool")],
            "handlers": [_entry("audit_handler", "AuditHandler")],
            "providers": [_entry("alt", "build_alt_client")],
        },
    )
    report = PluginManager(_policy(tmp_path, "test.cap2")).load_skills()

    assert report.loaded_plugin_ids == ("test.cap2",)
    assert len(report.tool_registrations) == 1
    assert report.tool_registrations[0].name == "echo_tool"
    assert report.tool_registrations[0].tool_class is not None
    assert len(report.handler_registrations) == 1
    assert report.handler_registrations[0].name == "audit_handler"
    assert len(report.provider_registrations) == 1
    assert report.provider_registrations[0].name == "alt"
    assert report.provider_registrations[0].metadata["kind"] == "provider"
    assert report.registrations == ()


def test_loader_rejects_non_callable_tool_export(tmp_path):
    source = 'TOOL_NOT_CALLABLE = "nope"\n'
    _write_plugin(
        tmp_path,
        plugin_id="test.badtool",
        manifest_extra={"tools": [_entry("echo_tool", "TOOL_NOT_CALLABLE")]},
    )
    plugin_root = tmp_path / "test_badtool"
    module_path = plugin_root / "plugin.py"
    module_path.write_text(source, encoding="utf-8")
    manifest_path = plugin_root / "artpm-plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["module_sha256"] = sha256(module_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = PluginManager(_policy(tmp_path, "test.badtool")).load_skills()

    assert report.loaded_plugin_ids == ()
    assert report.failures and report.failures[0].stage == "class"


# ── CapabilityRegistry ──


def test_registry_register_and_lookup(monkeypatch):
    reset_capability_registry()
    registry = get_capability_registry()
    registry.clear()

    registry.register_tool("tool_a", lambda: None, metadata={"m": 1})
    registry.register_provider("prov_a", lambda config: config, plugin_id="p1")
    registry.register_handler("handler_a", lambda: None)
    registry.register_skill("skill_a", object)

    assert registry.tool("tool_a").metadata["m"] == 1
    assert registry.provider("prov_a").plugin_id == "p1"
    assert registry.handler("handler_a") is not None
    assert registry.skill("skill_a") is not None
    assert registry.tool("missing") is None
    assert len(registry.all()) == 4


def test_registry_rejects_duplicate_names():
    reset_capability_registry()
    registry = get_capability_registry()
    registry.clear()
    registry.register_tool("dup", lambda: None)
    with pytest.raises(ValueError, match="duplicate tool registration"):
        registry.register_tool("dup", lambda: None)


def test_registry_merge_plugin_report():
    reset_capability_registry()
    registry = get_capability_registry()
    registry.clear()

    def _cls():
        pass

    report = PluginLoadReport(
        registrations=(
            type("SR", (), {"name": "s", "skill_class": _cls, "metadata": {}, "plugin_id": "p", "plugin_version": "1"})(),
        ),
        tool_registrations=(
            PluginToolRegistration(
                name="t", tool_class=_cls, metadata={"kind": "tool"}, plugin_id="p", plugin_version="1"
            ),
        ),
        provider_registrations=(
            PluginProviderRegistration(
                name="pv", provider_class=_cls, metadata={"kind": "provider"}, plugin_id="p", plugin_version="1"
            ),
        ),
    )
    merged = registry.merge_plugin_report(report)
    assert merged == 3
    assert registry.tool("t") is not None
    assert registry.provider("pv") is not None
    assert registry.skill("s") is not None


# ── create_llm_client extension point ──


def test_create_llm_client_uses_registered_provider(monkeypatch):
    reset_capability_registry()
    registry = get_capability_registry()
    registry.clear()

    marker = {}

    def alt_factory(config):
        marker["config"] = config
        return "alt-client"

    registry.register_provider("alt", alt_factory)

    client = create_llm_client({"provider": "alt", "model": "m"})
    assert client == "alt-client"
    assert marker["config"]["model"] == "m"


def test_create_llm_client_unknown_provider_still_raises(monkeypatch):
    reset_capability_registry()
    registry = get_capability_registry()
    registry.clear()
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_llm_client({"provider": "definitely-not-registered", "model": "m"})


def test_registry_clear_after_plugin_report(tmp_path):
    reset_capability_registry()
    registry = get_capability_registry()
    registry.clear()
    _write_plugin(
        tmp_path,
        plugin_id="test.cap3",
        manifest_extra={"providers": [_entry("alt", "build_alt_client")]},
    )
    report = PluginManager(_policy(tmp_path, "test.cap3")).load_skills()
    assert registry.merge_plugin_report(report) == 1
    assert registry.provider("alt") is not None
    assert registry.provider("alt").plugin_id == "test.cap3"
