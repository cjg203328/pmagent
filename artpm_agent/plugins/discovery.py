"""Policy-bound plugin discovery that never accepts request-controlled paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .manifest import MANIFEST_FILENAME, PluginManifest, load_plugin_manifest


PluginFailureStage = Literal["policy", "discovery", "manifest", "import", "class"]


def _is_linklike(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


@dataclass(frozen=True, slots=True)
class PluginFailure:
    plugin_id: str | None
    path: str
    stage: PluginFailureStage
    error: str


@dataclass(frozen=True, slots=True)
class PluginPolicy:
    """Deployment-owned trust policy. The secure default loads no code."""

    enabled: bool = False
    trusted_roots: tuple[Path, ...] = field(default_factory=tuple)
    allowed_plugin_ids: frozenset[str] = field(default_factory=frozenset)
    require_hash: bool = True
    reject_symlinks: bool = True
    max_plugins: int = 64

    def __post_init__(self) -> None:
        if isinstance(self.max_plugins, bool) or not 1 <= self.max_plugins <= 256:
            raise ValueError("max_plugins must be between 1 and 256")
        normalized_roots: list[Path] = []
        for value in self.trusted_roots:
            root = Path(value).expanduser()
            if not root.is_absolute():
                raise ValueError("trusted plugin roots must be absolute paths")
            if self.reject_symlinks and _is_linklike(root):
                raise ValueError("symbolic-link or junction plugin roots are not allowed")
            normalized_roots.append(root.resolve())
        object.__setattr__(self, "trusted_roots", tuple(normalized_roots))
        object.__setattr__(
            self,
            "allowed_plugin_ids",
            frozenset(str(item).strip() for item in self.allowed_plugin_ids if str(item).strip()),
        )


@dataclass(frozen=True, slots=True)
class PluginDiscoveryReport:
    manifests: tuple[PluginManifest, ...] = ()
    failures: tuple[PluginFailure, ...] = ()
    skipped_plugin_ids: tuple[str, ...] = ()


class PluginDiscovery:
    """Discover immediate plugin directories under explicitly trusted roots."""

    def __init__(self, policy: PluginPolicy | None = None):
        self.policy = policy or PluginPolicy()

    def discover(self) -> PluginDiscoveryReport:
        if not self.policy.enabled:
            return PluginDiscoveryReport()

        manifests: list[PluginManifest] = []
        failures: list[PluginFailure] = []
        skipped: list[str] = []
        candidates: list[Path] = []
        for root in self.policy.trusted_roots:
            if not root.is_dir():
                failures.append(
                    PluginFailure(None, str(root), "discovery", "trusted root is unavailable")
                )
                continue
            if self.policy.reject_symlinks and _is_linklike(root):
                failures.append(
                    PluginFailure(None, str(root), "policy", "symbolic-link roots are not allowed")
                )
                continue
            try:
                children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
            except OSError as error:
                failures.append(PluginFailure(None, str(root), "discovery", str(error)))
                continue
            for child in children:
                if not child.is_dir():
                    continue
                if self.policy.reject_symlinks and _is_linklike(child):
                    failures.append(
                        PluginFailure(None, str(child), "policy", "symbolic-link plugins are not allowed")
                    )
                    continue
                manifest_path = child / MANIFEST_FILENAME
                if manifest_path.is_file():
                    candidates.append(manifest_path)

        for manifest_path in candidates[: self.policy.max_plugins]:
            try:
                manifest = load_plugin_manifest(
                    manifest_path,
                    reject_symlinks=self.policy.reject_symlinks,
                )
            except Exception as error:
                failures.append(
                    PluginFailure(None, str(manifest_path), "manifest", str(error))
                )
                continue
            if manifest.plugin_id not in self.policy.allowed_plugin_ids:
                skipped.append(manifest.plugin_id)
                continue
            manifests.append(manifest)
        if len(candidates) > self.policy.max_plugins:
            failures.append(
                PluginFailure(
                    None,
                    "",
                    "policy",
                    f"plugin candidate limit exceeded ({self.policy.max_plugins})",
                )
            )
        return PluginDiscoveryReport(
            manifests=tuple(manifests),
            failures=tuple(failures),
            skipped_plugin_ids=tuple(sorted(set(skipped))),
        )
