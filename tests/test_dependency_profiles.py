from __future__ import annotations

from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


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


def test_onnxruntime_is_profiled_and_not_in_core():
    """The onnx embedding provider imports onnxruntime lazily.

    A lazily imported optional dependency still has to be declared somewhere, or
    the documented install path (`.[embedding-onnx]`) cannot work and users hit
    the ImportError with no way to resolve it.
    """
    pyproject = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    core = set(pyproject["project"]["dependencies"])
    optional = pyproject["project"]["optional-dependencies"]

    # Core stays offline-installable without a neural runtime.
    assert not any(name.startswith("onnxruntime") for name in core)
    assert any(
        name.startswith("onnxruntime")
        for name in optional["embedding-onnx"]
    )


def test_embedding_provider_settings_are_documented():
    """The env vars the code reads must appear in the operator docs."""
    repo = Path(__file__).parents[1]
    docs = (repo / "docs" / "operations" / "DEPENDENCY_PROFILES.md").read_text(
        encoding="utf-8"
    )
    example = (repo / ".env.example").read_text(encoding="utf-8")

    for variable in (
        "EMBEDDING_PROVIDER",
        "EMBEDDING_DIMENSION",
        "EMBEDDING_MODEL_PATH",
        "EMBEDDING_MODEL_NAME",
        "VECTOR_SHARD_BY_SCOPE",
    ):
        assert variable in docs, f"{variable} missing from DEPENDENCY_PROFILES.md"
        assert variable in example, f"{variable} missing from .env.example"
