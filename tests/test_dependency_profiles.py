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
    assert not any(name.startswith("streamlit") for name in core)
    assert not any(name.startswith("faiss-cpu") for name in core)
    assert not any(name.startswith("langchain") for name in core)
    assert not any(name.startswith("openpyxl") for name in core)
    assert any(name.startswith("fastapi") for name in optional["api"])
    assert any(name.startswith("streamlit") for name in optional["ui"])
    assert any(name.startswith("faiss-cpu") for name in optional["vector-local"])
    assert any(name.startswith("langchain") for name in optional["llm-langchain"])
    assert any(name.startswith("openpyxl") for name in optional["documents"])
    assert any(name.startswith("mcp") for name in optional["mcp"])
    assert any(name.startswith("qdrant-client") for name in optional["production"])
    assert any(name.startswith("psycopg") for name in optional["production"])


def test_dev_profile_contains_only_quality_tooling():
    pyproject = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    dev = pyproject["project"]["optional-dependencies"]["dev"]
    forbidden = ("fastapi", "psycopg", "qdrant-client", "redis", "opentelemetry")
    assert not any(item.startswith(forbidden) for item in dev)


def test_docker_installs_the_production_profile():
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert '"${wheel}[production]"' in dockerfile
