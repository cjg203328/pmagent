"""Audit the installed LangChain/LangGraph runtime used by ArtPM.

Run locally or in CI with::

    python -m artpm_agent.tools.audit_langchain

The audit is deliberately offline. Package freshness is a release-management
decision; this command verifies declarations, imports, required public symbols,
and the installed dependency graph without contacting package indexes.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from importlib import import_module, metadata
import json
from pathlib import Path
import subprocess
import sys
import tomllib
from typing import Any, Sequence

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


PROJECT_DISTRIBUTION = "artpm-agent"
PROJECT_FILE = Path(__file__).resolve().parents[2] / "pyproject.toml"


@dataclass(frozen=True)
class RuntimeDependency:
    """One distribution and the public runtime symbol ArtPM relies on."""

    distribution: str
    module: str
    symbol: str | None
    usage: str


RUNTIME_DEPENDENCIES: tuple[RuntimeDependency, ...] = (
    RuntimeDependency(
        "langchain",
        "langchain",
        None,
        "LangChain v1 release family and compatibility boundary",
    ),
    RuntimeDependency(
        "langchain-core",
        "langchain_core.messages",
        "AIMessage",
        "structured tool-call message conversion",
    ),
    RuntimeDependency(
        "langchain-openai",
        "langchain_openai",
        "ChatOpenAI",
        "OpenAI-compatible model adapter",
    ),
    RuntimeDependency(
        "langchain-anthropic",
        "langchain_anthropic",
        "ChatAnthropic",
        "Anthropic model adapter",
    ),
    RuntimeDependency(
        "langgraph",
        "langgraph.graph",
        "StateGraph",
        "collaborative task orchestration",
    ),
)


@dataclass(frozen=True)
class DependencyResult:
    distribution: str
    version: str | None
    declared: bool
    importable: bool
    usage: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.declared and self.importable and self.version is not None


def _normalized_requirements(raw_requirements: Sequence[str]) -> set[str]:
    declared: set[str] = set()
    for raw in raw_requirements:
        requirement = Requirement(raw)
        if requirement.marker is None or requirement.marker.evaluate():
            declared.add(canonicalize_name(requirement.name))
    return declared


def _declared_requirements(
    project: str = PROJECT_DISTRIBUTION,
    project_file: Path = PROJECT_FILE,
) -> set[str]:
    """Return direct requirements from source or installed package metadata."""

    if project_file.is_file():
        project_data = tomllib.loads(project_file.read_text(encoding="utf-8"))
        raw_requirements = project_data.get("project", {}).get("dependencies", ())
        if not isinstance(raw_requirements, list):
            raise ValueError("pyproject project.dependencies must be a list")
        return _normalized_requirements(raw_requirements)

    try:
        raw_requirements = metadata.requires(project) or ()
    except metadata.PackageNotFoundError:
        return set()
    return _normalized_requirements(raw_requirements)


def _import_runtime_symbol(dependency: RuntimeDependency) -> None:
    module = import_module(dependency.module)
    if dependency.symbol is not None and not hasattr(module, dependency.symbol):
        raise AttributeError(
            f"{dependency.module} does not expose {dependency.symbol}"
        )


def audit_runtime_dependencies(
    dependencies: Sequence[RuntimeDependency] = RUNTIME_DEPENDENCIES,
) -> list[DependencyResult]:
    """Inspect direct declarations and the imports exercised by ArtPM."""

    declared = _declared_requirements()
    results: list[DependencyResult] = []
    for dependency in dependencies:
        normalized = canonicalize_name(dependency.distribution)
        try:
            installed_version = metadata.version(dependency.distribution)
        except metadata.PackageNotFoundError:
            installed_version = None

        error: str | None = None
        try:
            _import_runtime_symbol(dependency)
            importable = True
        except Exception as exc:  # Import failures include broken transitive deps.
            importable = False
            error = f"{type(exc).__name__}: {exc}"

        results.append(
            DependencyResult(
                distribution=dependency.distribution,
                version=installed_version,
                declared=normalized in declared,
                importable=importable,
                usage=dependency.usage,
                error=error,
            )
        )
    return results


def run_pip_check() -> tuple[bool, str]:
    """Ask pip to validate all installed requirement specifiers."""

    completed = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (completed.stdout or completed.stderr).strip()
    return completed.returncode == 0, output


def build_audit_report(*, include_pip_check: bool = True) -> dict[str, Any]:
    results = audit_runtime_dependencies()
    pip_ok, pip_output = (True, "not requested")
    if include_pip_check:
        pip_ok, pip_output = run_pip_check()
    return {
        "status": "ok" if all(item.ok for item in results) and pip_ok else "error",
        "dependencies": [asdict(item) | {"ok": item.ok} for item in results],
        "pip_check": {
            "ok": pip_ok,
            "skipped": not include_pip_check,
            "output": pip_output,
        },
    }


def _print_report(report: dict[str, Any]) -> None:
    print("ArtPM LangChain dependency audit")
    for item in report["dependencies"]:
        marker = "OK" if item["ok"] else "ERROR"
        version = item["version"] or "not installed"
        print(
            f"[{marker}] {item['distribution']} {version}: {item['usage']}"
        )
        if item["error"]:
            print(f"        {item['error']}")
        if not item["declared"]:
            print("        missing from direct project dependencies")
    pip_result = report["pip_check"]
    pip_marker = (
        "SKIP"
        if pip_result["skipped"]
        else "OK"
        if pip_result["ok"]
        else "ERROR"
    )
    print(f"[{pip_marker}] pip check")
    if pip_result["output"]:
        print(f"        {pip_result['output']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON for CI")
    parser.add_argument(
        "--skip-pip-check",
        action="store_true",
        help="only validate project declarations and runtime imports",
    )
    args = parser.parse_args(argv)
    report = build_audit_report(include_pip_check=not args.skip_pip_check)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
