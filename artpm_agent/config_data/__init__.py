"""Packaged default configuration resources."""

from importlib.resources import files
import json
from typing import Any


def load_default_config() -> dict[str, Any]:
    """Load the bundled default configuration from source or an installed wheel."""
    resource = files(__package__).joinpath("default_config.json")
    with resource.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    if not isinstance(config, dict):
        raise TypeError("default configuration must be a JSON object")
    return config


__all__ = ["load_default_config"]
