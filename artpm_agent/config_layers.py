"""Layered configuration: profile patches applied over a base config dict.

Mirrors the deepseek-harness profile/bundle layering model in Python terms:

- A profile is a named directory whose ``config.patch.json`` is applied over
  the packaged base config (``default_config.json``).
- Patch semantics follow dsh: a patch locates a key and replaces its *whole*
  value (no deep merge); unknown keys are inserted. Dot paths (``llm.model``)
  locate nested entries.
- Layers apply in order, later layers win: base -> profile patch -> home patch
  (``~/.artpm/config.patch.json``) -> extra ``--patch`` overlays. Environment
  overrides remain the highest-precedence deployment layer (applied later by
  ``Config``), which matches the project's existing "env is authoritative"
  contract.

The home patch is optional (absent file is skipped); profile and overlay
patches are required when referenced (a typo must fail loudly, not silently
fall back to defaults).
"""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

#: Packaged profile roots (deployments may override with ARTPM_PROFILE_DIR).
DEFAULT_PROFILE_DIR = Path(__file__).resolve().parent / "config_profiles"
HOME_PATCH_RELATIVE = Path(".artpm") / "config.patch.json"
PATCH_FILENAME = "config.patch.json"

PROFILE_ENV = "ARTPM_PROFILE"
PROFILE_DIR_ENV = "ARTPM_PROFILE_DIR"
MAX_PATCH_BYTES = 512 * 1024
MAX_LAYERS = 32


class ConfigLayerError(ValueError):
    """Raised when a config layer cannot be parsed or applied."""


def _read_json_file(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ConfigLayerError(f"config layer does not exist: {path}")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ConfigLayerError(f"config layer is unavailable: {error}") from None
    if size <= 0 or size > MAX_PATCH_BYTES:
        raise ConfigLayerError(
            f"config layer size must be between 1 and {MAX_PATCH_BYTES} bytes"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigLayerError(f"config layer is not valid UTF-8 JSON: {error}") from None
    if not isinstance(raw, Mapping):
        raise ConfigLayerError(f"config layer root must be an object: {path}")
    return raw


def _split_dot_path(key: str) -> tuple[str, ...]:
    parts = tuple(str(key).strip().split("."))
    if not parts or any(not part for part in parts):
        raise ConfigLayerError(f"invalid patch key: {key!r}")
    return parts


def apply_patch(
    base: Mapping[str, Any],
    patch: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a new dict with one patch layer applied.

    Each patch key replaces the whole target value (dot paths walk into nested
    dicts, creating intermediates as needed). Values are deep-copied so later
    mutation of the caller's dict never leaks into the merged config.
    """
    result = deepcopy(dict(base))
    for key, value in patch.items():
        parts = _split_dot_path(key)
        target = result
        for part in parts[:-1]:
            child = target.get(part)
            if not isinstance(child, dict):
                child = {}
                target[part] = child
            target = child
        target[parts[-1]] = deepcopy(value)
    return result


@dataclass(frozen=True, slots=True)
class ConfigLayer:
    """One named patch layer with its provenance."""

    name: str
    source: str
    patch: Mapping[str, Any] = field(compare=False)


class LayeredConfig:
    """Ordered patch layers applied over a base config dict."""

    def __init__(self, base: Mapping[str, Any]):
        self.base: dict[str, Any] = deepcopy(dict(base))
        self.layers: list[ConfigLayer] = []

    def add_patch(
        self,
        name: str,
        patch: Mapping[str, Any],
        *,
        source: str = "<inline>",
    ) -> "LayeredConfig":
        if len(self.layers) >= MAX_LAYERS:
            raise ConfigLayerError(f"cannot exceed {MAX_LAYERS} config layers")
        self.layers.append(ConfigLayer(name=name, source=source, patch=dict(patch)))
        return self

    def add_json_file(
        self,
        name: str,
        path: Path | str,
        *,
        optional: bool = False,
    ) -> "LayeredConfig":
        resolved = Path(path).expanduser()
        if optional and not resolved.is_file():
            return self
        patch = _read_json_file(resolved)
        self.add_patch(name, patch, source=str(resolved))
        return self

    def merged(self) -> dict[str, Any]:
        """Apply every layer in order over the base; later layers win."""
        result = deepcopy(self.base)
        for layer in self.layers:
            result = apply_patch(result, layer.patch)
        return result

    def dump(
        self,
        *,
        annotate: bool = False,
    ) -> dict[str, Any]:
        """Return the merged config; with ``annotate``, every leaf records its
        winning layer (``{value, layer, source}``) for ``--dump-config``."""
        merged = self.merged()
        if not annotate:
            return merged
        annotated: dict[str, Any] = {}
        for key, value in merged.items():
            winner = next(
                (
                    layer
                    for layer in reversed(self.layers)
                    if any(
                        patch_key == key or patch_key.startswith(key + ".")
                        for patch_key in layer.patch
                    )
                ),
                None,
            )
            if winner is not None:
                annotated[key] = {
                    "value": value,
                    "layer": winner.name,
                    "source": winner.source,
                }
            else:
                annotated[key] = {"value": value, "layer": "base", "source": "packaged"}
        return annotated


def home_patch_path() -> Path:
    home = Path(os.path.expanduser("~"))
    return home / HOME_PATCH_RELATIVE


def load_layered_config(
    base: Mapping[str, Any],
    *,
    profile: Optional[str] = None,
    profile_dir: Optional[Path | str] = None,
    overlays: Iterable[Path | str] = (),
    include_home_patch: bool = True,
) -> LayeredConfig:
    """Assemble the dsh-style layer stack for a profile.

    Order (later wins): base -> profile patch -> home patch -> overlays.
    """
    layered = LayeredConfig(base)
    if profile:
        root = Path(profile_dir or os.getenv(PROFILE_DIR_ENV) or DEFAULT_PROFILE_DIR)
        profile_root = root / profile
        layered.add_json_file(
            f"profile:{profile}",
            profile_root / PATCH_FILENAME,
        )
    if include_home_patch:
        layered.add_json_file("home", home_patch_path(), optional=True)
    for index, overlay in enumerate(overlays):
        layered.add_json_file(f"overlay:{index + 1}", overlay)
    return layered


def profile_names(profile_dir: Optional[Path | str] = None) -> tuple[str, ...]:
    """Return available profile names (directories with a config.patch.json)."""
    root = Path(profile_dir or os.getenv(PROFILE_DIR_ENV) or DEFAULT_PROFILE_DIR)
    if not root.is_dir():
        return ()
    names = sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and (entry / PATCH_FILENAME).is_file()
    )
    return tuple(names)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: inspect the layered configuration (dsh ``--dump-config`` style)."""
    import argparse

    from .config_data import load_default_config

    parser = argparse.ArgumentParser(
        prog="artpm-config-layers",
        description="Inspect layered ArtPM configuration (dsh profile style).",
    )
    parser.add_argument("--profile", default=None, help="active profile name")
    parser.add_argument(
        "--profile-dir", default=None, help="override the profile root directory"
    )
    parser.add_argument(
        "--overlay", action="append", default=[], help="extra patch file (repeatable)"
    )
    parser.add_argument("--no-home", action="store_true", help="skip the home patch")
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="list available profiles and exit",
    )
    parser.add_argument(
        "--dump-config",
        action="store_true",
        help="print the merged config as JSON (annotated with layer provenance)",
    )
    args = parser.parse_args(argv)

    if args.list_profiles:
        for name in profile_names(args.profile_dir):
            print(name)
        return 0

    if not args.dump_config:
        parser.print_help()
        return 0

    base = load_default_config()
    try:
        layered = load_layered_config(
            base,
            profile=args.profile,
            profile_dir=args.profile_dir,
            overlays=args.overlay,
            include_home_patch=not args.no_home,
        )
    except ConfigLayerError as error:
        print(f"config error: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            layered.dump(annotate=True),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
