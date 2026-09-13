from __future__ import annotations

from pathlib import Path
import tomllib


def test_remote_runtime_dependencies_are_profiled():
    pyproject = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    core = set(pyproject["project"]["dependencies"])
    optional = pyproject["project"]["optional-dependencies"]

    assert not any(name.startswith("fastapi") for name in core)
    assert not any(name.startswith("qdrant-client") for name in core)
    assert not any(name.startswith("psycopg") for name in core)
    assert any(name.startswith("fastapi") for name in optional["api"])
    assert any(name.startswith("qdrant-client") for name in optional["production"])
    assert any(name.startswith("psycopg") for name in optional["production"])


def test_docker_installs_the_production_profile():
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert '"${wheel}[production]"' in dockerfile
