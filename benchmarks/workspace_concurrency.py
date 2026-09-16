"""Offline concurrency benchmark for workspace-scoped API chat locks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from time import perf_counter, sleep
from typing import Any

from artpm_agent.api.services import (
    ChatCommand,
    DefaultGatewayRuntime,
    RequestPrincipal,
)
from artpm_agent.runtime.factory import RuntimeFactory
from artpm_agent.runtime.storage_registry import StorageRegistry


@dataclass(frozen=True, slots=True)
class WorkspaceConcurrencyResult:
    workspaces: int
    delay_seconds: float
    elapsed_seconds: float
    serial_seconds: float
    speedup: float
    passed: bool


def run_workspace_concurrency_benchmark(
    *,
    work_dir: str | Path | None = None,
    workspaces: int = 8,
    delay_seconds: float = 0.05,
) -> WorkspaceConcurrencyResult:
    if workspaces < 2:
        raise ValueError("workspaces must be at least 2")
    if delay_seconds <= 0:
        raise ValueError("delay_seconds must be positive")

    temporary: TemporaryDirectory[str] | None = None
    if work_dir is None:
        temporary = TemporaryDirectory(prefix="artpm-concurrency-")
        root = Path(temporary.name)
    else:
        root = Path(work_dir)
        root.mkdir(parents=True, exist_ok=True)
    try:
        factory = RuntimeFactory(
            storage=StorageRegistry(
                db_path=root / "runtime.sqlite",
                vector_store_path=root / "vectors",
                enable_vector_search=False,
            ),
            agent_factory=lambda: object(),
        )
        runtime = object.__new__(DefaultGatewayRuntime)
        runtime.runtime_factory = factory
        barrier = Barrier(workspaces)

        def scoped(command: ChatCommand) -> str:
            barrier.wait(timeout=5)
            sleep(delay_seconds)
            return command.principal.workspace_id

        runtime._chat_scoped = scoped

        def command(index: int) -> ChatCommand:
            workspace_id = f"workspace-{index}"
            principal = RequestPrincipal(
                tenant_id="benchmark-tenant",
                workspace_id=workspace_id,
                actor_id="benchmark-user",
            )
            return ChatCommand(
                principal=principal,
                conversation_id=f"conversation-{index}",
                turn_id=f"turn-{index}",
                message="benchmark",
                tenant_context=principal.tenant_context(),
            )

        started = perf_counter()
        with ThreadPoolExecutor(max_workers=workspaces) as executor:
            results: list[Any] = list(
                executor.map(runtime.chat, map(command, range(workspaces)))
            )
        elapsed = perf_counter() - started
        assert results == [f"workspace-{index}" for index in range(workspaces)]
        serial = workspaces * delay_seconds
        speedup = serial / elapsed if elapsed else float("inf")
        return WorkspaceConcurrencyResult(
            workspaces=workspaces,
            delay_seconds=delay_seconds,
            elapsed_seconds=elapsed,
            serial_seconds=serial,
            speedup=speedup,
            passed=elapsed < serial * 0.6,
        )
    finally:
        if temporary is not None:
            temporary.cleanup()


def main() -> int:
    result = run_workspace_concurrency_benchmark()
    print(
        f"workspace_concurrency workspaces={result.workspaces} "
        f"elapsed={result.elapsed_seconds:.4f}s serial={result.serial_seconds:.4f}s "
        f"speedup={result.speedup:.2f}x status={'PASS' if result.passed else 'FAIL'}"
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
