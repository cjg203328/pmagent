"""Secure, opt-in plugin infrastructure for external ArtPM capabilities."""

from .capabilities import (
    CapabilityEntry,
    CapabilityRegistry,
    get_capability_registry,
    reset_capability_registry,
)
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
from .loader import (
    PluginHandlerRegistration,
    PluginLoadReport,
    PluginManager,
    PluginProviderRegistration,
    PluginSkillRegistration,
    PluginToolRegistration,
)
from .manifest import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    PluginEntryManifest,
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
    "PluginEntryManifest",
    "PluginFailure",
    "PluginHandlerRegistration",
    "PluginLoadReport",
    "PluginManager",
    "PluginManifest",
    "PluginManifestError",
    "PluginPolicy",
    "PluginProviderRegistration",
    "PluginSkillManifest",
    "PluginSkillRegistration",
    "PluginToolRegistration",
    "CapabilityEntry",
    "CapabilityRegistry",
    "build_plugin_manager_from_environment",
    "get_capability_registry",
    "load_plugin_manifest",
    "reset_capability_registry",
]
