"""Persistent store for one-sentence-edit feedback (the learning loop seed).

Every edit attempt is recorded. When the user rejects an AI edit or supplies a
manual correction, that sample is captured too - this is what lets the system
adapt over time (see ``reflection.py``).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EditFeedbackStore:
    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = Path(__file__).resolve().parent / "feedback.jsonl"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        doc_type: str,
        instruction: str,
        plan: dict[str, Any],
        accepted: bool,
        correction: str | None = None,
        doc_name: str | None = None,
    ) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "doc_type": doc_type,
            "doc_name": doc_name,
            "instruction": instruction,
            "ops": [o.get("op") for o in plan.get("ops", [])],
            "accepted": bool(accepted),
            "correction": correction,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def recent(self, n: int = 20) -> list[dict[str, Any]]:
        return self.all()[-n:]

    def stats(self) -> dict[str, Any]:
        rows = self.all()
        total = len(rows)
        accepted = sum(1 for r in rows if r.get("accepted"))
        op_counter: Counter[str] = Counter()
        for r in rows:
            for op in r.get("ops", []):
                op_counter[op] += 1
        return {
            "total": total,
            "accepted": accepted,
            "rejected": total - accepted,
            "accept_rate": (accepted / total) if total else None,
            "op_counts": dict(op_counter),
        }
