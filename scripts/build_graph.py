"""Build and query a bounded source dependency graph.

The graph is an agent-facing development aid. It is deliberately rebuildable,
stored outside the source tree, and never replaces grep or executable tests.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.0.0"
MAX_NODES = 80
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".php", ".go", ".java", ".css"}
EXCLUDED_DIRS = {
    ".ai-memory",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "data",
    "dist",
    "logs",
    "node_modules",
    "site-packages",
    "venv",
}
GENERIC_IMPORT_PATTERNS = (
    ("import", re.compile(r"(?:from\s+|require\s*\(\s*)['\"]([^'\"]+)['\"]")),
    ("cssimport", re.compile(r"@import\s+(?:url\()?['\"]([^'\"]+)['\"]")),
    ("include", re.compile(r"\b(?:include|include_once|require|require_once)\s*\(?\s*['\"]([^'\"]+)['\"]")),
)


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        relative_parts = path.relative_to(root).parts
        if any(part in EXCLUDED_DIRS or part.endswith(".egg-info") for part in relative_parts):
            continue
        files.append(path)
    return sorted(files, key=lambda item: _relative(item, root))


def _module_name(path: Path, root: Path) -> str | None:
    if path.suffix.lower() != ".py":
        return None
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


def _module_index(files: Iterable[Path], root: Path) -> dict[str, str]:
    modules: dict[str, str] = {}
    for path in files:
        module = _module_name(path, root)
        if module:
            modules[module] = _relative(path, root)
    return modules


def _resolve_python_module(name: str, modules: dict[str, str]) -> str | None:
    candidate = name
    while candidate:
        target = modules.get(candidate)
        if target is not None:
            return target
        candidate = candidate.rpartition(".")[0]
    return None


def _absolute_from_module(
    current_package: str | None,
    imported_module: str | None,
    level: int,
) -> str:
    if level == 0:
        return imported_module or ""
    current_parts = (current_package or "").split(".")
    keep = max(0, len(current_parts) - (level - 1))
    base = current_parts[:keep]
    if imported_module:
        base.extend(imported_module.split("."))
    return ".".join(part for part in base if part)


def _python_details(
    path: Path,
    root: Path,
    modules: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    source = _relative(path, root)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=source)
    except (OSError, SyntaxError, UnicodeError):
        return [], [], []

    edges: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    current_module = _module_name(path, root)
    current_package = (
        current_module
        if path.name == "__init__.py"
        else (current_module or "").rpartition(".")[0]
    )
    seen_targets: set[tuple[str, int]] = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({"name": node.name, "line": node.lineno})
        elif isinstance(node, ast.ClassDef):
            classes.append({"name": node.name, "line": node.lineno})

    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute_from_module(current_package, node.module, node.level)
            for alias in node.names:
                child = f"{base}.{alias.name}" if base else alias.name
                candidates.append(child if child in modules else base)
        else:
            continue
        for candidate in candidates:
            target = _resolve_python_module(candidate, modules)
            key = (target or "", node.lineno)
            if target is None or target == source or key in seen_targets:
                continue
            seen_targets.add(key)
            edges.append(
                {"source": source, "target": target, "type": "import", "line": node.lineno}
            )
    return edges, classes, functions


def _resolve_relative_asset(reference: str, source_path: Path, root: Path) -> str | None:
    if not reference.startswith((".", "/")):
        return None
    candidate = (root / reference.lstrip("/")) if reference.startswith("/") else source_path.parent / reference
    variants = [candidate]
    if not candidate.suffix:
        variants.extend(candidate.with_suffix(suffix) for suffix in SOURCE_SUFFIXES)
        variants.extend((candidate / f"index{suffix}") for suffix in SOURCE_SUFFIXES)
    for variant in variants:
        try:
            resolved = variant.resolve(strict=True)
            resolved.relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        if resolved.is_file() and resolved.suffix.lower() in SOURCE_SUFFIXES:
            return _relative(resolved, root)
    return None


def _generic_edges(path: Path, root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return [], []
    source = _relative(path, root)
    edges: list[dict[str, Any]] = []
    dangling: list[dict[str, Any]] = []
    for edge_type, pattern in GENERIC_IMPORT_PATTERNS:
        for match in pattern.finditer(text):
            reference = match.group(1)
            line = text.count("\n", 0, match.start()) + 1
            target = _resolve_relative_asset(reference, path, root)
            if target is None:
                if reference.startswith((".", "/")):
                    dangling.append(
                        {"source": source, "line": line, "value": reference, "base": source}
                    )
                continue
            if target != source:
                edges.append(
                    {"source": source, "target": target, "type": edge_type, "line": line}
                )
    return edges, dangling


def _detect_lsp() -> dict[str, bool]:
    return {
        "php": shutil.which("intelephense") is not None,
        "js": shutil.which("typescript-language-server") is not None,
        "python": shutil.which("pyright-langserver") is not None,
        "java": shutil.which("jdtls") is not None,
        "go": shutil.which("gopls") is not None,
    }


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _graph_dir(root: Path) -> Path:
    return root / ".ai-memory" / "knowledge-graph"


def build_graph(root: Path) -> dict[str, Any]:
    root = root.resolve()
    files = _source_files(root)
    modules = _module_index(files, root)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    dangling: list[dict[str, Any]] = []
    symbols: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for path in files:
        relative = _relative(path, root)
        classes: list[dict[str, Any]] = []
        functions: list[dict[str, Any]] = []
        if path.suffix.lower() == ".py":
            file_edges, classes, functions = _python_details(path, root, modules)
            edges.extend(file_edges)
        else:
            file_edges, file_dangling = _generic_edges(path, root)
            edges.extend(file_edges)
            dangling.extend(file_dangling)
        nodes[relative] = {"file": relative, "classes": classes, "functions": functions}
        module = relative.split("/", 1)[0]
        for kind, definitions in (("class", classes), ("function", functions)):
            for definition in definitions:
                symbols[definition["name"]].append(
                    {"file": relative, "line": definition["line"], "kind": kind, "module": module}
                )

    edge_keys: set[tuple[str, str, str, int]] = set()
    valid_edges: list[dict[str, Any]] = []
    for edge in sorted(edges, key=lambda item: (item["source"], item["target"], item["line"], item["type"])):
        key = (edge["source"], edge["target"], edge["type"], edge["line"])
        if edge["target"] not in nodes:
            dangling.append(
                {"source": edge["source"], "line": edge["line"], "value": edge["target"], "base": edge["source"]}
            )
        elif key not in edge_keys:
            edge_keys.add(key)
            valid_edges.append(edge)

    output_dir = _graph_dir(root)
    graph = {"nodes": nodes, "edges": valid_edges}
    lsp = _detect_lsp()
    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "tool_version": TOOL_VERSION,
        "root": str(root),
        "files": {
            _relative(path, root): {"sha256": _sha256(path), "mtime_ns": path.stat().st_mtime_ns}
            for path in files
        },
        "lsp_available": lsp,
        "accuracy_report": {
            "edges_total": len(valid_edges) + len(dangling),
            "edges_valid": len(valid_edges),
            "edges_dangling": len(dangling),
            "symbols_total": sum(len(items) for items in symbols.values()),
            "static_coverage": 1.0 if not edges and not dangling else len(valid_edges) / (len(valid_edges) + len(dangling)),
            "lsp_available": lsp,
        },
        "dangling_edges": dangling,
        "script_hash": _sha256(Path(__file__)),
    }
    _atomic_json(output_dir / "graph.json", graph)
    _atomic_json(output_dir / "meta.json", meta)
    _atomic_json(output_dir / "symbols.json", dict(sorted(symbols.items())))
    lines = ["# Project dependency graph (human-readable fallback)", ""]
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in valid_edges:
        outgoing[edge["source"]].append(f"{edge['target']} ({edge['type']}:{edge['line']})")
    for node in nodes:
        targets = ", ".join(outgoing[node]) or "-"
        lines.append(f"- {node} -> {targets}")
    _atomic_text(output_dir / "graph.md", "\n".join(lines) + "\n")
    report = meta["accuracy_report"]
    print(
        "[GRAPH-ACCURACY] "
        f"valid {report['edges_valid']} / dangling {report['edges_dangling']} (excluded) / "
        f"coverage {report['static_coverage']:.1%}",
        file=sys.stderr,
    )
    return graph


def _stale_files(root: Path) -> list[str]:
    meta_path = _graph_dir(root) / "meta.json"
    graph_path = _graph_dir(root) / "graph.json"
    symbols_path = _graph_dir(root) / "symbols.json"
    if not meta_path.is_file() or not graph_path.is_file() or not symbols_path.is_file():
        return ["graph artifacts missing"]
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        previous: dict[str, dict[str, Any]] = meta["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return ["invalid graph metadata"]
    current = {_relative(path, root): path for path in _source_files(root)}
    stale = sorted(set(previous) ^ set(current))
    for name in sorted(set(previous) & set(current)):
        path = current[name]
        expected = previous[name]
        if path.stat().st_mtime_ns == expected.get("mtime_ns"):
            continue
        if _sha256(path) != expected.get("sha256"):
            stale.append(name)
    return stale


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _query_entries(query: str, nodes: dict[str, Any], symbols: dict[str, Any]) -> list[str]:
    entries: list[str] = []
    for raw in query.split(","):
        candidate = raw.strip().replace("\\", "/").lstrip("./")
        if not candidate:
            continue
        if candidate in nodes:
            entries.append(candidate)
            continue
        matches = symbols.get(raw.strip(), [])
        entries.extend(item["file"] for item in matches if item["file"] in nodes)
    return list(dict.fromkeys(entries))


def _has_cycle(node_names: set[str], edges: list[dict[str, Any]]) -> bool:
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge["source"] in node_names and edge["target"] in node_names:
            outgoing[edge["source"]].append(edge["target"])
    visited: set[str] = set()
    active: set[str] = set()

    def visit(node: str) -> bool:
        if node in active:
            return True
        if node in visited:
            return False
        visited.add(node)
        active.add(node)
        found = any(visit(target) for target in outgoing[node])
        active.remove(node)
        return found

    return any(visit(node) for node in node_names if node not in visited)


def query_graph(root: Path, query: str, direction: str, depth: int) -> dict[str, Any]:
    output_dir = _graph_dir(root)
    graph = _load_json(output_dir / "graph.json")
    symbols = _load_json(output_dir / "symbols.json")
    nodes: dict[str, Any] = graph["nodes"]
    edges: list[dict[str, Any]] = graph["edges"]
    entries = _query_entries(query, nodes, symbols)
    if not entries:
        return {"nodes": {}, "edges": [], "cycle": False, "truncated": False, "unresolved": query}

    upstream: dict[str, list[str]] = defaultdict(list)
    downstream: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        downstream[edge["source"]].append(edge["target"])
        upstream[edge["target"]].append(edge["source"])

    selected = set(entries)
    queue: deque[tuple[str, int]] = deque((entry, 0) for entry in entries)
    truncated = False
    while queue:
        node, distance = queue.popleft()
        if distance >= depth:
            continue
        neighbours: list[str] = []
        if direction in {"down", "both"}:
            neighbours.extend(downstream[node])
        if direction in {"up", "both"}:
            neighbours.extend(upstream[node])
        for neighbour in neighbours:
            if neighbour in selected:
                continue
            if len(selected) >= MAX_NODES:
                truncated = True
                queue.clear()
                break
            selected.add(neighbour)
            queue.append((neighbour, distance + 1))

    selected_edges = [
        edge for edge in edges if edge["source"] in selected and edge["target"] in selected
    ]
    cycle = _has_cycle(selected, selected_edges)
    if cycle:
        print("[GRAPH-CYCLE] dependency cycle detected", file=sys.stderr)
    return {
        "nodes": {name: nodes[name] for name in sorted(selected)},
        "edges": selected_edges,
        "cycle": cycle,
        "truncated": truncated,
        "entries": entries,
    }


def _selftest() -> int:
    with tempfile.TemporaryDirectory(prefix="artpm-graph-") as temp:
        root = Path(temp)
        package = root / "sample"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "a.py").write_text("from sample import b\n", encoding="utf-8")
        target = package / "b.py"
        target.write_text("def value():\n    return 1\n", encoding="utf-8")
        build_graph(root)
        first = query_graph(root, "sample/b.py", "up", 2)
        if "sample/a.py" not in first["nodes"]:
            print("graph selftest failed: upstream import missing", file=sys.stderr)
            return 1
        target.unlink()
        stale = _stale_files(root)
        if "sample/b.py" not in stale:
            print("graph selftest failed: deletion not detected", file=sys.stderr)
            return 1
        rebuilt = build_graph(root)
        if "sample/b.py" in rebuilt["nodes"]:
            print("graph selftest failed: deleted node retained", file=sys.stderr)
            return 1
    print("graph selftest passed")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--query")
    parser.add_argument("--direction", choices=("up", "down", "both"), default="both")
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.selftest:
        return _selftest()
    root = args.root.resolve()
    if not root.is_dir():
        print(f"project root does not exist: {root}", file=sys.stderr)
        return 2
    if args.depth < 0:
        print("depth must be non-negative", file=sys.stderr)
        return 2

    stale = _stale_files(root)
    if args.rebuild or (stale and not args.no_rebuild):
        if stale and not args.rebuild:
            print(f"[GRAPH-STALE] {len(stale)} source changes detected; rebuilding", file=sys.stderr)
        build_graph(root)
    elif stale:
        print(f"[GRAPH-STALE] {len(stale)} source changes detected; --no-rebuild refused stale query", file=sys.stderr)
        return 2
    elif not args.query:
        build_graph(root)

    if args.query:
        result = query_graph(root, args.query, args.direction, args.depth)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
