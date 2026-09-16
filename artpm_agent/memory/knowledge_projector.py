"""Background-friendly projector for the derived workspace vector index."""

from __future__ import annotations

from typing import Any


class KnowledgeVectorProjector:
    """Consume durable index work without owning authoritative knowledge writes."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def run_once(self, *, limit: int = 100) -> dict[str, Any]:
        return self.store.project_index_outbox(limit=limit)

    def run_until_idle(
        self,
        *,
        batch_size: int = 100,
        max_batches: int = 100,
    ) -> dict[str, Any]:
        processed = 0
        report: dict[str, Any] = {"processed": 0, "pending": 0}
        for _ in range(max_batches):
            report = self.run_once(limit=batch_size)
            processed += int(report.get("processed", 0))
            if not report.get("pending") or not report.get("processed"):
                break
        return {**report, "processed": processed}


__all__ = ["KnowledgeVectorProjector"]
