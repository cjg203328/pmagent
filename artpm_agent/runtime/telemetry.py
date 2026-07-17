"""Agent telemetry: latency / fallback / cache / cost / token / connection signals.

Lightweight per-turn telemetry that (a) gives operators visibility into model
performance and (b) feeds the evolution loop — ``ReflectionJob`` can mine
performance regressions (rising latency, climbing fallback rate, cache misses)
the same way it mines failure episodes.

Backed by a dedicated sqlite database so it never touches business tables and
stays decoupled from the conversation/episode stores.

The schema is extended in a **backward-compatible** way: new columns are added
with idempotent ``ALTER TABLE`` statements (guarded by ``PRAGMA table_info``),
and a separate ``connection_events`` table records per-attempt connectivity so
the turn-level row stays small. All recording paths are best-effort and never
raise into the caller's hot path.
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
    from artpm_agent.config import resolve_state_path

    return str(resolve_state_path("telemetry.db", "ARTPM_TELEMETRY_DB"))


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
    # ── observability extensions (token + connection) ──
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    provider: str = ""
    endpoint: str = ""
    attempt: int = 0
    error_type: str = ""
    http_status: Optional[int] = None
    error: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


# New turn_telemetry columns added on top of the original 10 (idempotent ALTER).
_NEW_TURN_COLUMNS = [
    ("prompt_tokens", "INTEGER DEFAULT 0"),
    ("completion_tokens", "INTEGER DEFAULT 0"),
    ("cached_tokens", "INTEGER DEFAULT 0"),
    ("total_tokens", "INTEGER DEFAULT 0"),
    ("cost_usd", "REAL DEFAULT 0.0"),
    ("provider", "TEXT"),
    ("endpoint", "TEXT"),
    ("attempt", "INTEGER DEFAULT 0"),
    ("error_type", "TEXT"),
    ("http_status", "INTEGER"),
]


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
            self._add_turn_columns(conn)
            self._ensure_connection_table(conn)

    def _add_turn_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(turn_telemetry)")}
        for name, typedef in _NEW_TURN_COLUMNS:
            if name not in existing:
                conn.execute(
                    f"ALTER TABLE turn_telemetry ADD COLUMN {name} {typedef}"
                )
        conn.commit()

    def _ensure_connection_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connection_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                provider TEXT,
                model TEXT,
                endpoint TEXT,
                attempt INTEGER,
                ok INTEGER,
                error_type TEXT,
                http_status INTEGER,
                latency_ms REAL,
                task_type TEXT
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
        t = TurnTelemetry(**{k: v for k, v in kwargs.items() if k in valid})
        try:
            with self._lock, self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO turn_telemetry
                    (turn_id, task_type, model_used, latency_ms, fallback,
                     cache_hit, success, tokens_est,
                     prompt_tokens, completion_tokens, cached_tokens,
                     total_tokens, cost_usd, provider, endpoint, attempt,
                     error_type, http_status, error, timestamp)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                        t.prompt_tokens,
                        t.completion_tokens,
                        t.cached_tokens,
                        t.total_tokens,
                        t.cost_usd,
                        t.provider,
                        t.endpoint,
                        t.attempt,
                        t.error_type,
                        t.http_status,
                        t.error,
                        t.timestamp,
                    ),
                )
                conn.commit()
        except Exception as error:  # noqa: BLE001
            logger.debug("Telemetry record failed: %s", error)

    def record_connection(
        self,
        *,
        provider: str = "",
        model: str = "",
        endpoint: str = "",
        attempt: int = 0,
        ok: bool = True,
        error_type: str = "",
        http_status: Optional[int] = None,
        latency_ms: float = 0.0,
        task_type: str = "chat",
    ) -> None:
        """Record a single connectivity attempt (one failover candidate try)."""
        if not self.enabled:
            return
        try:
            with self._lock, self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO connection_events
                    (timestamp, provider, model, endpoint, attempt, ok,
                     error_type, http_status, latency_ms, task_type)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        provider,
                        model,
                        endpoint,
                        int(attempt),
                        int(ok),
                        error_type or "",
                        int(http_status) if http_status is not None else None,
                        float(latency_ms),
                        task_type or "chat",
                    ),
                )
                conn.commit()
        except Exception as error:  # noqa: BLE001
            logger.debug("Telemetry connection record failed: %s", error)

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

    def recent_connections(self, limit: int = 200) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT * FROM connection_events ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                rows.reverse()  # chronological for trend charts
                return rows
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
            "total_tokens": sum(int(r.get("total_tokens") or 0) for r in rows),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in rows), 4),
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
            "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in rows),
            "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in rows),
            "total_tokens": sum(int(r.get("total_tokens") or 0) for r in rows),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in rows), 4),
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
                "total_tokens": int(r.get("total_tokens") or 0),
                "cost_usd": float(r.get("cost_usd") or 0.0),
            }
            for r in rows
        ]
        trend.reverse()  # recent() returns DESC; chart wants chronological
        return trend

    # ── Observability: token + connection aggregations ──

    def token_summary(self, window: int = 200) -> Dict[str, Any]:
        """Token + cost rollup for the observability panel."""
        rows = self.recent(window)
        if not rows:
            return {"samples": 0}
        return {
            "samples": len(rows),
            "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in rows),
            "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in rows),
            "cached_tokens": sum(int(r.get("cached_tokens") or 0) for r in rows),
            "total_tokens": sum(int(r.get("total_tokens") or 0) for r in rows),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in rows), 4),
            "cache_hit_tokens": sum(
                int(r.get("cached_tokens") or 0) for r in rows if r.get("cache_hit")
            ),
        }

    def connection_summary(self, window: int = 200) -> Dict[str, Any]:
        """Connectivity rollup: success rate, error breakdown, latency."""
        rows = self.recent_connections(window)
        if not rows:
            return {"samples": 0}
        n = len(rows)
        ok = sum(1 for r in rows if r["ok"])
        lat = sorted(r["latency_ms"] for r in rows if r["latency_ms"] is not None)
        errors: Dict[str, int] = {}
        for r in rows:
            et = r.get("error_type") or "other"
            if not r["ok"]:
                errors[et] = errors.get(et, 0) + 1
        avg_lat = round(sum(lat) / len(lat), 1) if lat else 0.0
        return {
            "samples": n,
            "ok": ok,
            "failed": n - ok,
            "success_rate": round(ok / n, 3),
            "avg_latency_ms": avg_lat,
            "error_breakdown": errors,
        }

    def by_provider(self, window: int = 200) -> Dict[str, Dict[str, Any]]:
        """Group token/cost/connection signals by provider."""
        turns = self.recent(window)
        conns = self.recent_connections(window)
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in turns:
            groups.setdefault(r.get("provider") or "(unknown)", []).append(r)
        out: Dict[str, Dict[str, Any]] = {}
        for prov, rows in groups.items():
            out[prov] = {
                "total_tokens": sum(int(r.get("total_tokens") or 0) for r in rows),
                "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in rows), 4),
                "turns": len(rows),
            }
        # attach connection stats
        conn_groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in conns:
            conn_groups.setdefault(r.get("provider") or "(unknown)", []).append(r)
        for prov, rows in conn_groups.items():
            bucket = out.setdefault(prov, {"total_tokens": 0, "cost_usd": 0.0, "turns": 0})
            bucket["attempts"] = len(rows)
            bucket["conn_ok"] = sum(1 for r in rows if r["ok"])
            bucket["conn_success_rate"] = round(
                sum(1 for r in rows if r["ok"]) / len(rows), 3
            )
        return out

    def endpoint_health(
        self, window: int = 500, gateway: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """Per-(provider, model, endpoint) health derived from connection events.

        When a live ``gateway`` is supplied, its circuit-breaker cooldown state
        is folded in via ``gateway.is_model_available(model)``.
        """
        rows = self.recent_connections(window)
        if not rows:
            return []
        buckets: Dict[tuple, Dict[str, Any]] = {}
        for r in rows:
            key = (
                r.get("provider") or "(unknown)",
                r.get("model") or "(unknown)",
                r.get("endpoint") or "",
            )
            b = buckets.setdefault(
                key,
                {
                    "provider": key[0],
                    "model": key[1],
                    "endpoint": key[2],
                    "attempts": 0,
                    "ok": 0,
                    "last_ok": None,
                    "last_error": None,
                    "last_seen": None,
                    "error_types": {},
                },
            )
            b["attempts"] += 1
            b["last_seen"] = r.get("timestamp")
            if r["ok"]:
                b["ok"] += 1
                b["last_ok"] = r.get("timestamp")
            else:
                b["last_error"] = r.get("error_type") or "other"
                et = r.get("error_type") or "other"
                b["error_types"][et] = b["error_types"].get(et, 0) + 1
        health = []
        for b in buckets.values():
            success_rate = round(b["ok"] / b["attempts"], 3) if b["attempts"] else 0.0
            unavailable = None
            if gateway is not None and hasattr(gateway, "is_model_available"):
                try:
                    unavailable = not gateway.is_model_available(b["model"])
                except Exception:  # noqa: BLE001
                    unavailable = None
            health.append(
                {
                    "provider": b["provider"],
                    "model": b["model"],
                    "endpoint": b["endpoint"],
                    "attempts": b["attempts"],
                    "success_rate": success_rate,
                    "last_ok": b["last_ok"],
                    "last_error": b["last_error"],
                    "last_seen": b["last_seen"],
                    "error_types": b["error_types"],
                    "unavailable": unavailable,
                }
            )
        health.sort(key=lambda h: (h["provider"], h["model"], h["endpoint"]))
        return health

    def connection_trend(self, window: int = 200) -> List[Dict[str, Any]]:
        """Chronological connection outcomes for a sparkline."""
        return self.recent_connections(window)
