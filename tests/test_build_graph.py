from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_graph.py"


def _run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def test_dependency_graph_builds_and_queries_python_imports(tmp_path: Path) -> None:
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from .target import Target\n", encoding="utf-8"
    )
    (package / "caller.py").write_text("from sample import target\n", encoding="utf-8")
    (package / "target.py").write_text("class Target:\n    pass\n", encoding="utf-8")

    built = _run(tmp_path, "--rebuild")
    assert built.returncode == 0, built.stderr
    queried = _run(tmp_path, "--query", "sample/target.py", "--direction", "up")
    assert queried.returncode == 0, queried.stderr
    result = json.loads(queried.stdout)

    assert set(result["nodes"]) == {
        "sample/__init__.py",
        "sample/caller.py",
        "sample/target.py",
    }
    assert result["cycle"] is False
    symbols = json.loads(
        (tmp_path / ".ai-memory" / "knowledge-graph" / "symbols.json").read_text(
            encoding="utf-8"
        )
    )
    assert symbols["Target"][0]["file"] == "sample/target.py"


def test_dependency_graph_rebuild_removes_deleted_nodes(tmp_path: Path) -> None:
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    caller = package / "caller.py"
    caller.write_text("from sample import target\n", encoding="utf-8")
    target = package / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    assert _run(tmp_path, "--rebuild").returncode == 0

    target.unlink()
    queried = _run(tmp_path, "--query", "sample/caller.py", "--direction", "down")
    assert queried.returncode == 0, queried.stderr
    assert "[GRAPH-STALE]" in queried.stderr
    result = json.loads(queried.stdout)
    assert "sample/target.py" not in result["nodes"]


def test_dependency_graph_selftest_isolated_from_workspace() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--selftest"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "graph selftest passed" in completed.stdout
