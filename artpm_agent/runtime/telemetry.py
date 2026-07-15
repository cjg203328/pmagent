"""Agent telemetry: latency / fallback / cache / cost signals.

Lightweight per-turn telemetry that (a) gives operators visibility into model
performance and (b) feeds the evolution loop — ``ReflectionJob`` can mine
performance regressions (rising latency, climbing fallback rate, cache misses)
the same way it mines failure episodes.

Backed by a dedicated sqlite database so it never touches business tables and
stays decoupled from the conversation/episode stores.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from artpm_agent.utils.logger import get_logger

logger = get_logger(__name__)


def default_telemetry_db_path() -> str:
    env = os.getenv("ARTPM_TELEMETRY_DB")
    if env:
        return env
    root = Path(__file__).resolve().parent.parent.parent
    return str(root / "data" / "telemetry.db")


@dataclass
class TurnTelemetry:
    turn_id: str = ""
    task_type: str = "chat"
    model_used: Optional[str] = None
    latency_ms: float = 0.0
    fallback: bool = False
    cache_hit: bool = False
    success: bool = True
    tokens_est: int = 0
    error: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


class AgentTelemetry:
    """Append-only telemetry store with a small in-process summary API."""

    def __init__(self, db_path: Optional[str] = None, *, enabled: bool = True) -> None:
        self.enabled = bool(enabled)
        self._db_path = db_path or default_telemetry_db_path()
        self._lock = threading.RLock()
        if self.enabled:
            try:
                self._ensure_schema()
            except Exception as error:  # noqa: BLE001 - telemetry must never crash a turn
                logger.warning("Telemetry schema init failed: %s", error)
                self.enabled = False

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS turn_telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    turn_id TEXT,
                    task_type TEXT,
                    model_used TEXT,
                    latency_ms REAL,
                    fallback INTEGER,
                    cache_hit INTEGER,
                    success INTEGER,
                    tokens_est INTEGER,
                    error TEXT,
                    timestamp TEXT
                )
                """
            )
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def record(self, **kwargs: Any) -> None:
        if not self.enabled:
            return
        valid = TurnTelemetry.__dataclass_fields__
        t = TurnTelemetry(
            **{k: v for k, v in kwargs.items() if k in valid}
        )
        try:
            with self._lock, self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO turn_telemetry
                    (turn_id, task_type, model_used, latency_ms, fallback,
                     cache_hit, success, tokens_est, error, timestamp)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        t.turn_id,
                        t.task_type,
                        t.model_used,
                        t.latency_ms,
                        int(t.fallback),
                        int(t.cache_hit),
                        int(t.success),
                        t.tokens_est,
                        t.error,
                        t.timestamp,
                    ),
                )
                conn.commit()
        except Exception as error:  # noqa: BLE001
            logger.debug("Telemetry record failed: %s", error)

    def recent(self, limit: int = 200) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT * FROM turn_telemetry ORDER BY id DESC LIMIT ?", (int(limit),)
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:  # noqa: BLE001
            return []

    def summarize(self, window: int = 200) -> Dict[str, Any]:
        """Aggregate recent samples for dashboards and the evolution loop."""
        rows = self.recent(window)
        if not rows:
            return {"samples": 0}
        n = len(rows)
        lat = sorted(r["latency_ms"] for r in rows)
        fallbacks = sum(1 for r in rows if r["fallback"])
        hits = sum(1 for r in rows if r["cache_hit"])
        fails = sum(1 for r in rows if not r["success"])
        return {
            "samples": n,
            "avg_latency_ms": round(sum(lat) / n, 1),
            "p95_latency_ms": round(lat[min(n - 1, int(n * 0.95))], 1),
            "fallback_rate": round(fallbacks / n, 3),
            "cache_hit_rate": round(hits / n, 3),
            "failure_rate": round(fails / n, 3),
        }

    # ── Dashboard-facing aggregations (additive; never break older callers) ──

    @staticmethod
    def _aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(rows)
        if n == 0:
            return {"samples": 0}
        lat = sorted(r["latency_ms"] for r in rows)
        fallbacks = sum(1 for r in rows if r["fallback"])
        hits = sum(1 for r in rows if r["cache_hit"])
        fails = sum(1 for r in rows if not r["success"])
        return {
            "samples": n,
            "avg_latency_ms": round(sum(lat) / n, 1),
            "p95_latency_ms": round(lat[min(n - 1, int(n * 0.95))], 1),
            "fallback_rate": round(fallbacks / n, 3),
            "cache_hit_rate": round(hits / n, 3),
            "failure_rate": round(fails / n, 3),
        }

    def by_task_type(self, window: int = 200) -> Dict[str, Dict[str, Any]]:
        """Per-task-type breakdown for the operations dashboard."""
        rows = self.recent(window)
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault(r.get("task_type") or "chat", []).append(r)
        return {k: self._aggregate(v) for k, v in groups.items()}

    def by_model(self, window: int = 200) -> Dict[str, Dict[str, Any]]:
        """Per-model breakdown for the operations dashboard."""
        rows = self.recent(window)
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault(r.get("model_used") or "(none)", []).append(r)
        return {k: self._aggregate(v) for k, v in groups.items()}

    def latency_trend(self, window: int = 200) -> List[Dict[str, Any]]:
        """Chronological latency samples (id ASC) for a trend sparkline."""
        rows = self.recent(window)
        trend = [
            {
                "timestamp": r.get("timestamp"),
                "latency_ms": r.get("latency_ms") or 0,
                "task_type": r.get("task_type") or "chat",
                "model_used": r.get("model_used"),
                "success": bool(r.get("success")),
                "cache_hit": bool(r.get("cache_hit")),
                "fallback": bool(r.get("fallback")),
            }
            for r in rows
        ]
        trend.reverse()  # recent() returns DESC; chart wants chronological
        return trend
