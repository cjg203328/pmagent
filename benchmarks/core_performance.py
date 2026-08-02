"""Repeatable, offline performance benchmarks for core agent paths.

The benchmark suite deliberately excludes network and model calls. Setup is
performed before timing starts, and every sample is normalized to one logical
operation so results remain comparable when batch sizes change.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gc
import itertools
import json
import math
from pathlib import Path
import platform
import statistics
import tempfile
import time
from typing import Any

from artpm_agent.harness.memory_retrieval import retrieve_memory_context
from artpm_agent.memory.sqlite_manager import SQLiteManager
from artpm_agent.routing.service import IntentRouter
from artpm_agent.workflows.task_graph import LangGraphTaskOrchestrator


@dataclass(frozen=True)
class BenchmarkSpec:
    """One prepared benchmark workload."""

    name: str
    operation: Callable[[], None]
    operations_per_sample: int
    p95_limit_ms: float
    description: str


@dataclass(frozen=True)
class BenchmarkResult:
    """Serializable latency summary for one workload."""

    name: str
    description: str
    samples: int
    operations_per_sample: int
    median_ms: float
    p95_ms: float
    max_ms: float
    operations_per_second: float
    p95_limit_ms: float
    passed: bool


class _MemoryBackend:
    def __init__(self) -> None:
        self.records = [
            {
                "id": f"memory-{index}",
                "score": 0.93 - index / 100,
                "data": {
                    "source": f"project-{index}",
                    "raw_text": (
                        "Project delivery preference and reusable estimate "
                        f"constraint number {index}. " * 4
                    ),
                },
            }
            for index in range(12)
        ]

    def retrieve(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        if not query:
            raise AssertionError("memory benchmark query must not be empty")
        return self.records[:top_k]


class _KnowledgeBackend:
    DEFAULT_WORKSPACE_ID = "local-default"

    def __init__(self) -> None:
        self.records = [
            {
                "id": f"knowledge-{index}",
                "confidence": 0.96 - index / 100,
                "title": f"Delivery rule {index}",
                "text": (
                    "Workspace quality gate and acceptance checklist "
                    f"revision {index}. " * 4
                ),
            }
            for index in range(12)
        ]

    def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        if kwargs.get("workspace_id") != "benchmark-workspace":
            raise AssertionError("workspace isolation was not propagated")
        return self.records[: int(kwargs["limit"])]


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("at least one sample is required")
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in the interval (0, 1]")
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def measure(spec: BenchmarkSpec, *, samples: int, warmups: int) -> BenchmarkResult:
    """Measure a prepared workload and return normalized per-operation data."""

    if samples < 1:
        raise ValueError("samples must be at least 1")
    if warmups < 0:
        raise ValueError("warmups must not be negative")
    if spec.operations_per_sample < 1:
        raise ValueError("operations_per_sample must be at least 1")

    for _ in range(warmups):
        spec.operation()

    timings_ms: list[float] = []
    gc_was_enabled = gc.isenabled()
    try:
        gc.disable()
        for _ in range(samples):
            started = time.perf_counter_ns()
            spec.operation()
            elapsed_ns = time.perf_counter_ns() - started
            timings_ms.append(
                elapsed_ns / 1_000_000 / spec.operations_per_sample
            )
    finally:
        if gc_was_enabled:
            gc.enable()

    median_ms = statistics.median(timings_ms)
    p95_ms = _nearest_rank_percentile(timings_ms, 0.95)
    return BenchmarkResult(
        name=spec.name,
        description=spec.description,
        samples=samples,
        operations_per_sample=spec.operations_per_sample,
        median_ms=median_ms,
        p95_ms=p95_ms,
        max_ms=max(timings_ms),
        operations_per_second=1000 / median_ms if median_ms > 0 else math.inf,
        p95_limit_ms=spec.p95_limit_ms,
        passed=p95_ms <= spec.p95_limit_ms,
    )


def build_specs(work_dir: Path) -> list[BenchmarkSpec]:
    """Prepare all offline workloads outside the timed region."""

    work_dir.mkdir(parents=True, exist_ok=True)

    memory_backend = _MemoryBackend()
    knowledge_backend = _KnowledgeBackend()
    memory_batch_size = 100

    def retrieve_memory_batch() -> None:
        for _ in range(memory_batch_size):
            context = retrieve_memory_context(
                "reuse the delivery quality gate and estimate preference",
                [
                    {"role": "user", "content": "prepare the project estimate"},
                    {"role": "assistant", "content": "draft prepared"},
                ],
                memory_manager=memory_backend,
                knowledge_store=knowledge_backend,
                workspace_id="benchmark-workspace",
                top_k=8,
                max_total_chars=2400,
            )
            if not context or len(context) > 2400:
                raise AssertionError("memory retrieval returned an invalid context")

    router = IntentRouter(lambda text: [float(len(text) % 7)] * 8, lambda: None, {})
    route_query = "search file benchmark fixture"
    if router.detect(route_query) != "file_search":
        raise AssertionError("routing benchmark did not prime the expected route")
    routing_batch_size = 1000

    def route_cached_batch() -> None:
        for _ in range(routing_batch_size):
            if router.detect(route_query) != "file_search":
                raise AssertionError("cached route changed during benchmark")

    database = SQLiteManager(str(work_dir / "benchmark-memory.db"))
    persistence_namespace = time.perf_counter_ns()
    document_ids = itertools.count()
    persistence_batch_size = 5

    def persistence_roundtrip_batch() -> None:
        for _ in range(persistence_batch_size):
            index = next(document_ids)
            document_id = f"benchmark-doc-{persistence_namespace}-{index}"
            database.insert(
                "documents",
                {
                    "id": document_id,
                    "document_type": "benchmark",
                    "source": "offline",
                    "raw_text": "persistent memory benchmark record",
                    "confidence": 1.0,
                },
            )
            record = database.get_by_id("documents", document_id)
            if record is None or record["id"] != document_id:
                raise AssertionError("SQLite persistence roundtrip lost a record")

    def run_task(task: Any, context: Any) -> dict[str, Any]:
        return {
            "task": task.id,
            "dependency_count": len(context.get("results", {})),
        }

    task_graph = LangGraphTaskOrchestrator(
        run_task,
        max_parallelism=2,
        max_retries=0,
    )
    graph_run_ids = itertools.count()
    task_specs = [
        {"id": "research"},
        {"id": "budget"},
        {"id": "estimate", "depends_on": ["research", "budget"]},
        {"id": "review", "depends_on": ["estimate"]},
    ]

    def task_graph_invoke() -> None:
        index = next(graph_run_ids)
        state = task_graph.invoke(
            "prepare an offline project estimate",
            task_specs,
            thread_id=f"benchmark-graph-{index}",
        )
        if state.get("status") != "succeeded" or len(state.get("completed", [])) != 4:
            raise AssertionError("task graph benchmark did not complete")

    return [
        BenchmarkSpec(
            name="memory_retrieval",
            operation=retrieve_memory_batch,
            operations_per_sample=memory_batch_size,
            p95_limit_ms=5.0,
            description="Merge, deduplicate, and budget two memory sources",
        ),
        BenchmarkSpec(
            name="routing_cache",
            operation=route_cached_batch,
            operations_per_sample=routing_batch_size,
            p95_limit_ms=0.10,
            description="Resolve an already cached deterministic intent",
        ),
        BenchmarkSpec(
            name="sqlite_roundtrip",
            operation=persistence_roundtrip_batch,
            operations_per_sample=persistence_batch_size,
            p95_limit_ms=100.0,
            description="Commit and read one persistent memory record",
        ),
        BenchmarkSpec(
            name="task_graph",
            operation=task_graph_invoke,
            operations_per_sample=1,
            p95_limit_ms=500.0,
            description="Execute a four-task dependency graph with local workers",
        ),
    ]


def run_benchmarks(
    *,
    work_dir: Path,
    samples: int = 20,
    warmups: int = 3,
    only: Iterable[str] = (),
) -> list[BenchmarkResult]:
    """Run selected benchmarks and return their summaries."""

    selected = set(only)
    specs = build_specs(work_dir)
    unknown = selected - {spec.name for spec in specs}
    if unknown:
        raise ValueError(f"unknown benchmark(s): {', '.join(sorted(unknown))}")
    if selected:
        specs = [spec for spec in specs if spec.name in selected]
    return [measure(spec, samples=samples, warmups=warmups) for spec in specs]


def report_payload(results: Sequence[BenchmarkResult]) -> dict[str, Any]:
    """Build a machine-readable report with environment metadata."""

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "passed": all(result.passed for result in results),
        "benchmarks": [asdict(result) for result in results],
    }


def print_report(results: Sequence[BenchmarkResult]) -> None:
    """Print a compact report suitable for CI logs."""

    headers = ("benchmark", "median ms", "p95 ms", "ops/s", "p95 limit", "status")
    rows = [
        (
            result.name,
            f"{result.median_ms:.3f}",
            f"{result.p95_ms:.3f}",
            f"{result.operations_per_second:,.1f}",
            f"{result.p95_limit_ms:.3f}",
            "PASS" if result.passed else "FAIL",
        )
        for result in results
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def render(row: Sequence[str]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    print(render(headers))
    print(render(tuple("-" * width for width in widths)))
    for row in rows:
        print(render(row))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="NAME",
        help="run one benchmark; repeat to select multiple",
    )
    parser.add_argument("--output", type=Path, help="write the JSON report here")
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="exit non-zero when a P95 threshold is exceeded",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="artpm-benchmark-") as temp_dir:
        try:
            results = run_benchmarks(
                work_dir=Path(temp_dir),
                samples=args.samples,
                warmups=args.warmups,
                only=args.only,
            )
        except ValueError as error:
            _parser().error(str(error))

    print_report(results)
    payload = report_payload(results)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        print(f"JSON report: {args.output}")
    return 1 if args.enforce and not payload["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
