"""Secure, opt-in plugin infrastructure for external ArtPM skills."""

from .discovery import (
    PluginDiscovery,
    PluginDiscoveryReport,
    PluginFailure,
    PluginPolicy,
)
from .environment import (
    PLUGIN_ALLOWLIST_ENV,
    PLUGIN_ENABLED_ENV,
    PLUGIN_ROOTS_ENV,
    PluginConfigurationError,
    build_plugin_manager_from_environment,
)
from .loader import PluginLoadReport, PluginManager, PluginSkillRegistration
from .manifest import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    PluginManifest,
    PluginManifestError,
    PluginSkillManifest,
    load_plugin_manifest,
)

__all__ = [
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "PLUGIN_ALLOWLIST_ENV",
    "PLUGIN_ENABLED_ENV",
    "PLUGIN_ROOTS_ENV",
    "PluginConfigurationError",
    "PluginDiscovery",
    "PluginDiscoveryReport",
    "PluginFailure",
    "PluginLoadReport",
    "PluginManager",
    "PluginManifest",
    "PluginManifestError",
    "PluginPolicy",
    "PluginSkillManifest",
    "PluginSkillRegistration",
    "build_plugin_manager_from_environment",
    "load_plugin_manifest",
]
