from pathlib import Path

from artpm_agent.tools.audit_langchain import (
    RUNTIME_DEPENDENCIES,
    RuntimeDependency,
    _declared_requirements,
    audit_runtime_dependencies,
    build_audit_report,
)


def test_langchain_runtime_dependencies_are_direct_and_importable():
    results = audit_runtime_dependencies()

    assert {item.distribution for item in results} == {
        item.distribution for item in RUNTIME_DEPENDENCIES
    }
    assert all(item.ok for item in results)


def test_langchain_declarations_are_read_from_source_pyproject(tmp_path):
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text(
        '[project]\nname = "fixture"\n'
        'dependencies = ["langchain>=1", "langgraph>=1"]\n',
        encoding="utf-8",
    )

    assert _declared_requirements(project_file=project_file) == {
        "langchain",
        "langgraph",
    }


def test_langchain_audit_reports_broken_public_symbol():
    result = audit_runtime_dependencies(
        (
            RuntimeDependency(
                "langchain-core",
                "langchain_core.messages",
                "DefinitelyMissingSymbol",
                "test boundary",
            ),
        )
    )[0]

    assert result.declared is True
    assert result.importable is False
    assert "DefinitelyMissingSymbol" in (result.error or "")


def test_langchain_audit_report_can_skip_pip_subprocess():
    report = build_audit_report(include_pip_check=False)

    assert report["status"] == "ok"
    assert report["pip_check"] == {
        "ok": True,
        "skipped": True,
        "output": "not requested",
    }


def test_requirements_compatibility_entries_are_ascii_and_use_pyproject():
    """Verify root-level requirements shims are ASCII and point to pyproject."""
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "requirements.txt").read_bytes().decode("ascii")
    development = (root / "requirements-dev.txt").read_bytes().decode("ascii")

    assert "-e ." in runtime
    assert '-e ".[production,dev]"' in development
