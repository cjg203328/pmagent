"""Deployment environment contract for opt-in external plugins."""

from __future__ import annotations

import json

import pytest

from artpm_agent.plugins import (
    PluginConfigurationError,
    PluginManager,
    build_plugin_manager_from_environment,
)


def test_plugin_environment_is_disabled_by_default():
    manager = build_plugin_manager_from_environment({})

    assert isinstance(manager, PluginManager)
    assert manager.policy.enabled is False
    assert manager.load_skills().registrations == ()


def test_enabled_environment_requires_absolute_roots_and_allowlist(tmp_path):
    manager = build_plugin_manager_from_environment(
        {
            "ARTPM_PLUGINS_ENABLED": "true",
            "ARTPM_PLUGIN_ROOTS": json.dumps([str(tmp_path.resolve())]),
            "ARTPM_PLUGIN_ALLOWLIST": json.dumps(["studio.asset-review"]),
        }
    )

    assert manager.policy.enabled is True
    assert manager.policy.trusted_roots == (tmp_path.resolve(),)
    assert manager.policy.allowed_plugin_ids == {"studio.asset-review"}
    assert manager.policy.require_hash is True
    assert manager.policy.reject_symlinks is True


@pytest.mark.parametrize(
    "environment",
    [
        {"ARTPM_PLUGINS_ENABLED": "sometimes"},
        {
            "ARTPM_PLUGINS_ENABLED": "true",
            "ARTPM_PLUGIN_ROOTS": "[]",
            "ARTPM_PLUGIN_ALLOWLIST": '["studio.asset-review"]',
        },
        {
            "ARTPM_PLUGINS_ENABLED": "true",
            "ARTPM_PLUGIN_ROOTS": '["relative/plugins"]',
            "ARTPM_PLUGIN_ALLOWLIST": '["studio.asset-review"]',
        },
        {
            "ARTPM_PLUGINS_ENABLED": "true",
            "ARTPM_PLUGIN_ROOTS": '["/plugins"]',
            "ARTPM_PLUGIN_ALLOWLIST": "not-json",
        },
    ],
)
def test_unsafe_or_malformed_plugin_environment_fails_closed(environment):
    with pytest.raises(PluginConfigurationError):
        build_plugin_manager_from_environment(environment)


def test_artpm_agent_injects_deployment_plugin_manager(monkeypatch, tmp_path):
    import artpm_agent.agent as agent_module

    manager = PluginManager()
    monkeypatch.setattr(
        agent_module,
        "build_plugin_manager_from_environment",
        lambda: manager,
    )
    monkeypatch.setenv("MCP_ENABLED", "false")
    agent = agent_module.ArtPMAgent(
        {
            "database.db_path": str(tmp_path / "business.db"),
            "database.memory_db_path": str(tmp_path / "memory.db"),
            "database.conversation_db_path": str(tmp_path / "conversations.db"),
            "database.vector_db_path": str(tmp_path / "vectors"),
            "unlimited_ocr.enabled": False,
            "mineru.enabled": False,
        }
    )

    assert agent.plugin_manager is manager
    assert agent.context["plugin_manager"] is manager
    assert agent.router.plugin_load_report is not None
