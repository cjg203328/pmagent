"""Failure-isolated loader for explicitly trusted external skill plugins."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import logging
import sys
from threading import RLock
from types import MappingProxyType, ModuleType
from typing import TYPE_CHECKING, Any, Mapping

from .discovery import PluginDiscovery, PluginFailure, PluginPolicy
from .manifest import PluginManifest

if TYPE_CHECKING:
    from artpm_agent.skills.base_skill import BaseSkill


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PluginSkillRegistration:
    name: str
    skill_class: type[BaseSkill]
    metadata: Mapping[str, Any]
    plugin_id: str
    plugin_version: str


@dataclass(frozen=True, slots=True)
class PluginToolRegistration:
    """A tool class exported by a plugin (AgentTool subclass)."""

    name: str
    tool_class: type
    metadata: Mapping[str, Any]
    plugin_id: str
    plugin_version: str


@dataclass(frozen=True, slots=True)
class PluginHandlerRegistration:
    """A harness handler class exported by a plugin (duck-typed)."""

    name: str
    handler_class: type
    metadata: Mapping[str, Any]
    plugin_id: str
    plugin_version: str


@dataclass(frozen=True, slots=True)
class PluginProviderRegistration:
    """An LLM provider factory exported by a plugin.

    ``provider_class`` is expected to be a callable ``(config) -> BaseLLMClient``
    or a class with that constructor shape; type identity is validated only
    against the callable contract so providers can be factories.
    """

    name: str
    provider_class: type
    metadata: Mapping[str, Any]
    plugin_id: str
    plugin_version: str


@dataclass(frozen=True, slots=True)
class PluginLoadReport:
    registrations: tuple[PluginSkillRegistration, ...] = ()
    tool_registrations: tuple[PluginToolRegistration, ...] = ()
    handler_registrations: tuple[PluginHandlerRegistration, ...] = ()
    provider_registrations: tuple[PluginProviderRegistration, ...] = ()
    loaded_plugin_ids: tuple[str, ...] = ()
    skipped_plugin_ids: tuple[str, ...] = ()
    failures: tuple[PluginFailure, ...] = ()


class PluginManager:
    """Load trusted plugins without mutating the process-global skill registry.

    Plugins are trusted operator code, not sandboxed code. Loading requires all
    of: an enabled policy, an absolute trusted root, an allowlisted plugin ID,
    and (by default) a matching SHA-256 digest.
    """

    def __init__(self, policy: PluginPolicy | None = None):
        self.policy = policy or PluginPolicy()
        self._lock = RLock()
        self.last_report = PluginLoadReport()

    @staticmethod
    def _module_name(manifest: PluginManifest) -> str:
        identity = (
            f"{manifest.plugin_id}:{manifest.version}:"
            f"{manifest.module_sha256 or manifest.module_path}"
        )
        return f"_artpm_plugin_{sha256(identity.encode('utf-8')).hexdigest()}"

    def _load_module(self, manifest: PluginManifest) -> ModuleType:
        source = manifest.read_verified_module(require_hash=self.policy.require_hash)
        module_name = self._module_name(manifest)
        cached = sys.modules.get(module_name)
        if isinstance(cached, ModuleType):
            return cached
        module = ModuleType(module_name)
        module.__file__ = str(manifest.module_path)
        module.__package__ = ""
        module.__loader__ = self
        sys.modules[module_name] = module
        try:
            code = compile(source, str(manifest.module_path), "exec", dont_inherit=True)
            exec(code, module.__dict__)
        except BaseException as error:
            sys.modules.pop(module_name, None)
            raise RuntimeError(
                f"plugin import raised {type(error).__name__}: {error}"
            ) from None
        return module

    @staticmethod
    def _registrations(
        manifest: PluginManifest,
        module: ModuleType,
        *,
        reserved_names: set[str],
    ) -> tuple[PluginSkillRegistration, ...]:
        # Import lazily so ``artpm_agent.plugins`` remains independently
        # importable while the skills package is still initializing.
        from artpm_agent.skills.base_skill import BaseSkill

        local_names: set[str] = set()
        registrations: list[PluginSkillRegistration] = []
        for declaration in manifest.skills:
            if declaration.name in reserved_names or declaration.name in local_names:
                raise ValueError(f"duplicate or reserved skill name: {declaration.name}")
            candidate = getattr(module, declaration.class_name, None)
            if not isinstance(candidate, type) or not issubclass(candidate, BaseSkill):
                raise TypeError(
                    f"{declaration.class_name} must be a BaseSkill subclass"
                )
            if candidate is BaseSkill:
                raise TypeError("BaseSkill itself cannot be registered")
            if candidate.skill_name != declaration.name:
                raise ValueError(
                    f"class skill_name does not match manifest: {declaration.name}"
                )
            metadata = dict(declaration.capability_metadata(manifest.plugin_id))
            metadata["plugin_version"] = manifest.version
            registrations.append(
                PluginSkillRegistration(
                    name=declaration.name,
                    skill_class=candidate,
                    metadata=MappingProxyType(metadata),
                    plugin_id=manifest.plugin_id,
                    plugin_version=manifest.version,
                )
            )
            local_names.add(declaration.name)
        return tuple(registrations)

    @staticmethod
    def _claim(
        declaration: Any,
        local_names: set[str],
        reserved_names: set[str] | frozenset[str],
    ) -> None:
        if declaration.name in reserved_names or declaration.name in local_names:
            raise ValueError(f"duplicate or reserved capability name: {declaration.name}")
        local_names.add(declaration.name)

    def _capability_registrations(
        self,
        manifest: PluginManifest,
        module: ModuleType,
        *,
        reserved_names: set[str] | frozenset[str],
    ) -> tuple[
        tuple[PluginToolRegistration, ...],
        tuple[PluginHandlerRegistration, ...],
        tuple[PluginProviderRegistration, ...],
    ]:
        """Collect non-skill capability exports (tools/handlers/providers).

        Each export must be callable (a class or factory function). Tools and
        handlers are expected to be host-integrated by the operator before use;
        the loader only validates and reports, it never mutates a global
        registry — mirroring the skill loader's isolation contract.
        """
        local_names: set[str] = set()

        def _build(
            declaration: Any,
            registration_type: type,
            kind: str,
            expected: set[str] | None,
        ) -> Any:
            self._claim(declaration, local_names, reserved_names)
            candidate = getattr(module, declaration.class_name, None)
            if not callable(candidate):
                raise TypeError(
                    f"{kind} {declaration.name} must reference a callable "
                    f"class or factory: {declaration.class_name}"
                )
            if expected and declaration.name in expected:
                raise TypeError(f"{kind} name collides with a reserved capability")
            metadata = dict(declaration.capability_metadata(manifest.plugin_id))
            metadata["plugin_version"] = manifest.version
            metadata["kind"] = kind
            return registration_type(
                name=declaration.name,
                **(
                    {"tool_class": candidate}
                    if registration_type is PluginToolRegistration
                    else (
                        {"handler_class": candidate}
                        if registration_type is PluginHandlerRegistration
                        else {"provider_class": candidate}
                    )
                ),
                metadata=MappingProxyType(metadata),
                plugin_id=manifest.plugin_id,
                plugin_version=manifest.version,
            )

        tool_registrations = tuple(
            _build(declaration, PluginToolRegistration, "tool", None)
            for declaration in manifest.tools
        )
        handler_registrations = tuple(
            _build(declaration, PluginHandlerRegistration, "handler", None)
            for declaration in manifest.handlers
        )
        provider_registrations = tuple(
            _build(declaration, PluginProviderRegistration, "provider", None)
            for declaration in manifest.providers
        )
        return tool_registrations, handler_registrations, provider_registrations

    def load_skills(
        self,
        *,
        reserved_names: set[str] | frozenset[str] = frozenset(),
    ) -> PluginLoadReport:
        """Discover and atomically register each valid plugin in isolation."""

        with self._lock:
            discovered = PluginDiscovery(self.policy).discover()
            failures = list(discovered.failures)
            registrations: list[PluginSkillRegistration] = []
            all_registrations: list[Any] = []
            loaded: list[str] = []
            claimed_names = set(reserved_names)
            seen_plugin_ids: set[str] = set()
            for manifest in discovered.manifests:
                if manifest.plugin_id in seen_plugin_ids:
                    failures.append(
                        PluginFailure(
                            manifest.plugin_id,
                            str(manifest.manifest_path),
                            "policy",
                            "duplicate plugin ID",
                        )
                    )
                    continue
                seen_plugin_ids.add(manifest.plugin_id)
                conflicts = sorted(
                    declaration.name
                    for declaration in (*manifest.skills, *manifest.capabilities)
                    if declaration.name in claimed_names
                )
                if conflicts:
                    failures.append(
                        PluginFailure(
                            manifest.plugin_id,
                            str(manifest.manifest_path),
                            "policy",
                            "duplicate or reserved capability name: "
                            + ", ".join(conflicts),
                        )
                    )
                    continue
                try:
                    module = self._load_module(manifest)
                except Exception as error:
                    logger.warning("Plugin %s was isolated: %s", manifest.plugin_id, error)
                    failures.append(
                        PluginFailure(
                            manifest.plugin_id,
                            str(manifest.module_path),
                            "import",
                            str(error),
                        )
                    )
                    continue
                try:
                    plugin_registrations = self._registrations(
                        manifest,
                        module,
                        reserved_names=claimed_names,
                    )
                    (
                        tool_registrations,
                        handler_registrations,
                        provider_registrations,
                    ) = self._capability_registrations(
                        manifest,
                        module,
                        reserved_names=claimed_names,
                    )
                except Exception as error:
                    logger.warning("Plugin %s was isolated: %s", manifest.plugin_id, error)
                    failures.append(
                        PluginFailure(
                            manifest.plugin_id,
                            str(manifest.module_path),
                            "class",
                            str(error),
                        )
                    )
                    continue
                registrations.extend(plugin_registrations)
                all_registrations.extend(tool_registrations)
                all_registrations.extend(handler_registrations)
                all_registrations.extend(provider_registrations)
                claimed_names.update(item.name for item in plugin_registrations)
                claimed_names.update(item.name for item in tool_registrations)
                claimed_names.update(item.name for item in handler_registrations)
                claimed_names.update(item.name for item in provider_registrations)
                loaded.append(manifest.plugin_id)
            report = PluginLoadReport(
                registrations=tuple(registrations),
                tool_registrations=tuple(
                    item
                    for item in all_registrations
                    if isinstance(item, PluginToolRegistration)
                ),
                handler_registrations=tuple(
                    item
                    for item in all_registrations
                    if isinstance(item, PluginHandlerRegistration)
                ),
                provider_registrations=tuple(
                    item
                    for item in all_registrations
                    if isinstance(item, PluginProviderRegistration)
                ),
                loaded_plugin_ids=tuple(loaded),
                skipped_plugin_ids=discovered.skipped_plugin_ids,
                failures=tuple(failures),
            )
            self.last_report = report
            return report
