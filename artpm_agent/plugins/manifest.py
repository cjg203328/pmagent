"""Validated manifest contract for operator-installed ArtPM skill plugins."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping


MANIFEST_FILENAME = "artpm-plugin.json"
MANIFEST_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 128 * 1024
MAX_MODULE_BYTES = 4 * 1024 * 1024

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CLASS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_RISKS = frozenset({"low", "medium", "high", "critical", "untrusted"})


class PluginManifestError(ValueError):
    """Raised when a plugin manifest does not satisfy the public contract."""


def _required_text(
    value: Any,
    field: str,
    *,
    pattern: re.Pattern[str] | None = None,
    max_length: int = 2_000,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PluginManifestError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise PluginManifestError(f"{field} exceeds {max_length} characters")
    if pattern is not None and pattern.fullmatch(normalized) is None:
        raise PluginManifestError(f"{field} has an invalid format")
    return normalized


def _required_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise PluginManifestError(f"{field} must be a boolean")
    return value


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _is_linklike(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


@dataclass(frozen=True, slots=True)
class PluginSkillManifest:
    """One skill class exported by a plugin module."""

    name: str
    class_name: str
    description: str
    version: str
    risk: str
    read_only: bool
    requires_approval: bool
    required_role: str = "user"
    capabilities: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Any, *, index: int) -> "PluginSkillManifest":
        if not isinstance(value, Mapping):
            raise PluginManifestError(f"skills[{index}] must be an object")
        prefix = f"skills[{index}]"
        name = _required_text(value.get("name"), f"{prefix}.name", pattern=_IDENTIFIER)
        class_name = _required_text(
            value.get("class"), f"{prefix}.class", pattern=_CLASS_NAME
        )
        description = _required_text(
            value.get("description"), f"{prefix}.description"
        )
        version = _required_text(
            value.get("version"), f"{prefix}.version", pattern=_VERSION
        )
        risk = _required_text(value.get("risk"), f"{prefix}.risk").casefold()
        if risk not in _RISKS:
            raise PluginManifestError(f"{prefix}.risk is unsupported")
        read_only = _required_bool(value.get("read_only"), f"{prefix}.read_only")
        requires_approval = _required_bool(
            value.get("requires_approval"), f"{prefix}.requires_approval"
        )
        if not read_only and not requires_approval:
            raise PluginManifestError(
                f"{prefix} is write-capable and must require approval"
            )
        required_role = str(value.get("required_role") or "user").strip().casefold()
        if required_role not in {"user", "admin"}:
            raise PluginManifestError(f"{prefix}.required_role is unsupported")
        capability_values = value.get("capabilities", [])
        if not isinstance(capability_values, list) or len(capability_values) > 32:
            raise PluginManifestError(
                f"{prefix}.capabilities must be an array of at most 32 identifiers"
            )
        capabilities = tuple(
            _required_text(
                item,
                f"{prefix}.capabilities[{capability_index}]",
                pattern=_IDENTIFIER,
            )
            for capability_index, item in enumerate(capability_values)
        )
        if len(capabilities) != len(set(capabilities)):
            raise PluginManifestError(f"{prefix}.capabilities must be unique")
        return cls(
            name=name,
            class_name=class_name,
            description=description,
            version=version,
            risk=risk,
            read_only=read_only,
            requires_approval=requires_approval,
            required_role=required_role,
            capabilities=capabilities,
        )

    def capability_metadata(self, plugin_id: str) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "description": self.description,
                "version": self.version,
                "requires_llm": False,
                "risk": self.risk,
                "read_only": self.read_only,
                "requires_approval": self.requires_approval,
                "required_role": self.required_role,
                "capabilities": self.capabilities,
                "plugin_id": plugin_id,
                "is_plugin_skill": True,
            }
        )


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Fully validated plugin manifest and immutable module binding."""

    plugin_id: str
    version: str
    root: Path
    manifest_path: Path
    module_path: Path
    module_sha256: str | None
    skills: tuple[PluginSkillManifest, ...]

    def read_verified_module(self, *, require_hash: bool = True) -> bytes:
        """Read the exact source bytes that will be compiled and verify its digest."""

        try:
            module_size = self.module_path.stat().st_size
        except OSError as error:
            raise PluginManifestError(f"plugin module is unavailable: {error}") from None
        if module_size <= 0 or module_size > MAX_MODULE_BYTES:
            raise PluginManifestError(
                f"plugin module size must be between 1 and {MAX_MODULE_BYTES} bytes"
            )
        try:
            source = self.module_path.read_bytes()
        except OSError as error:
            raise PluginManifestError(f"plugin module cannot be read: {error}") from None
        if not source or len(source) > MAX_MODULE_BYTES:
            raise PluginManifestError(
                f"plugin module size must be between 1 and {MAX_MODULE_BYTES} bytes"
            )
        digest = sha256(source).hexdigest()
        if require_hash and self.module_sha256 is None:
            raise PluginManifestError("module_sha256 is required by plugin policy")
        if self.module_sha256 is not None and digest != self.module_sha256:
            raise PluginManifestError("plugin module SHA-256 does not match the manifest")
        return source


def load_plugin_manifest(
    manifest_path: Path,
    *,
    reject_symlinks: bool = True,
) -> PluginManifest:
    """Load one manifest without importing or executing plugin code."""

    manifest_path = Path(manifest_path)
    if manifest_path.name != MANIFEST_FILENAME:
        raise PluginManifestError(f"manifest must be named {MANIFEST_FILENAME}")
    if reject_symlinks and _is_linklike(manifest_path):
        raise PluginManifestError("symbolic-link manifests are not allowed")
    try:
        size = manifest_path.stat().st_size
    except OSError as error:
        raise PluginManifestError(f"manifest is unavailable: {error}") from None
    if size <= 0 or size > MAX_MANIFEST_BYTES:
        raise PluginManifestError(
            f"manifest size must be between 1 and {MAX_MANIFEST_BYTES} bytes"
        )
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PluginManifestError(f"manifest is not valid UTF-8 JSON: {error}") from None
    if not isinstance(raw, Mapping):
        raise PluginManifestError("manifest root must be an object")
    schema_version = raw.get("schema_version")
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise PluginManifestError(
            f"schema_version must be {MANIFEST_SCHEMA_VERSION}"
        )
    plugin_id = _required_text(raw.get("plugin_id"), "plugin_id", pattern=_IDENTIFIER)
    version = _required_text(raw.get("version"), "version", pattern=_VERSION)
    module_value = _required_text(raw.get("module"), "module", max_length=512)
    module_relative = Path(module_value)
    if module_relative.is_absolute() or ".." in module_relative.parts:
        raise PluginManifestError("module must be a relative path inside the plugin")
    if module_relative.suffix.casefold() != ".py":
        raise PluginManifestError("module must reference a Python source file")

    root = manifest_path.parent.resolve()
    module_path = (root / module_relative).resolve()
    if not _inside(root, module_path):
        raise PluginManifestError("module resolves outside the plugin directory")
    if not module_path.is_file():
        raise PluginManifestError("plugin module does not exist")
    if reject_symlinks:
        current = root
        for part in module_relative.parts:
            current = current / part
            if _is_linklike(current):
                raise PluginManifestError("symbolic-link plugin modules are not allowed")

    digest_value = raw.get("module_sha256")
    module_sha256 = None
    if digest_value is not None:
        module_sha256 = _required_text(
            digest_value,
            "module_sha256",
            pattern=_SHA256,
            max_length=64,
        )
    skill_values = raw.get("skills")
    if not isinstance(skill_values, list) or not skill_values:
        raise PluginManifestError("skills must be a non-empty array")
    if len(skill_values) > 64:
        raise PluginManifestError("a plugin cannot export more than 64 skills")
    skills = tuple(
        PluginSkillManifest.from_mapping(value, index=index)
        for index, value in enumerate(skill_values)
    )
    names = [skill.name for skill in skills]
    if len(names) != len(set(names)):
        raise PluginManifestError("skill names must be unique within a plugin")
    return PluginManifest(
        plugin_id=plugin_id,
        version=version,
        root=root,
        manifest_path=manifest_path.resolve(),
        module_path=module_path,
        module_sha256=module_sha256,
        skills=skills,
    )
