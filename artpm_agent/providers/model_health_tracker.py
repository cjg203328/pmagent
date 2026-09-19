"""Model health tracker with sliding window metrics.

Tracks success rate, latency, and recency-weighted health scores
to guide intelligent failover decisions.
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import RLock
from typing import Deque

logger = logging.getLogger(__name__)


@dataclass
class HealthRecord:
    """Single health record."""
    success: bool
    latency: float  # seconds
    timestamp: float
    error_type: str | None = None


@dataclass
class HealthMetrics:
    """Aggregated health metrics."""
    success_rate: float  # 0.0 to 1.0
    avg_latency: float  # seconds
    p95_latency: float  # seconds
    latency_score: float  # 0.0 to 1.0, normalized latency
    recency_score: float  # 0.0 to 1.0, time-decay weighted
    health_score: float  # 0.0 to 1.0, composite score
    sample_count: int
    error_distribution: dict[str, int] | None = None


class ModelHealthTracker:
    """Track model health with sliding window."""

    def __init__(
        self,
        *,
        window_size: int = 100,
        recency_half_life: float = 300.0,  # 5 minutes
        health_weights: dict[str, float] | None = None,
        persistence_path: str | None = None
    ):
        """Initialize health tracker.

        Args:
            window_size: Number of recent records to keep
            recency_half_life: Half-life for time decay (seconds)
            health_weights: Custom weights for health score components
            persistence_path: Optional path to persist metrics
        """
        self.window_size = window_size
        self.recency_half_life = recency_half_life
        self.persistence_path = Path(persistence_path) if persistence_path else None

        # Default weights: success=60%, latency=30%, recency=10%
        self.health_weights = health_weights or {
            "success_rate": 0.6,
            "latency_score": 0.3,
            "recency_score": 0.1
        }

        # model_id -> deque of HealthRecord
        self.records: dict[str, Deque[HealthRecord]] = {}
        self._lock = RLock()

        # Load persisted data
        self._load_persistence()

    def record_success(
        self,
        model_id: str,
        latency: float
    ) -> None:
        """Record successful model call.

        Args:
            model_id: Model identifier
            latency: Response latency in seconds
        """
        self._add_record(
            model_id,
            HealthRecord(
                success=True,
                latency=latency,
                timestamp=time.time()
            )
        )

    def record_failure(
        self,
        model_id: str,
        error_type: str,
        latency: float = 0.0
    ) -> None:
        """Record failed model call.

        Args:
            model_id: Model identifier
            error_type: Type of error (timeout, auth, server, etc.)
            latency: Time before failure (seconds)
        """
        self._add_record(
            model_id,
            HealthRecord(
                success=False,
                latency=latency,
                timestamp=time.time(),
                error_type=error_type
            )
        )

    def _add_record(self, model_id: str, record: HealthRecord) -> None:
        """Add record to sliding window.

        Args:
            model_id: Model identifier
            record: Health record
        """
        with self._lock:
            if model_id not in self.records:
                self.records[model_id] = deque(maxlen=self.window_size)

            self.records[model_id].append(record)

            # Periodic persistence
            if len(self.records[model_id]) % 10 == 0:
                self._save_persistence()

    def get_health_score(self, model_id: str) -> float:
        """Get composite health score.

        Args:
            model_id: Model identifier

        Returns:
            Health score (0.0 to 1.0), 1.0 = healthy
        """
        metrics = self.get_metrics(model_id)

        if metrics.sample_count == 0:
            return 1.0  # Unknown model assumed healthy

        # Composite score
        score = (
            metrics.success_rate * self.health_weights["success_rate"]
            + metrics.latency_score * self.health_weights["latency_score"]
            + metrics.recency_score * self.health_weights["recency_score"]
        )

        return max(0.0, min(1.0, score))

    def get_metrics(self, model_id: str) -> HealthMetrics:
        """Get detailed health metrics.

        Args:
            model_id: Model identifier

        Returns:
            HealthMetrics
        """
        with self._lock:
            if model_id not in self.records or len(self.records[model_id]) == 0:
                return HealthMetrics(
                    success_rate=1.0,
                    avg_latency=0.0,
                    p95_latency=0.0,
                    latency_score=1.0,
                    recency_score=1.0,
                    health_score=1.0,
                    sample_count=0
                )

            records = list(self.records[model_id])

        # Success rate
        successes = sum(1 for r in records if r.success)
        success_rate = successes / len(records)

        # Latency metrics (successful calls only)
        successful_latencies = [r.latency for r in records if r.success]

        if successful_latencies:
            avg_latency = sum(successful_latencies) / len(successful_latencies)
            sorted_latencies = sorted(successful_latencies)
            p95_idx = int(len(sorted_latencies) * 0.95)
            p95_latency = sorted_latencies[p95_idx] if sorted_latencies else 0.0

            # Latency score: normalized to [0, 1]
            # Assume 2s = excellent, 10s = poor
            latency_score = max(0.0, 1.0 - (avg_latency - 2.0) / 8.0)
        else:
            avg_latency = 0.0
            p95_latency = 0.0
            latency_score = 0.0

        # Recency score: time-decay weighted success rate
        now = time.time()
        weighted_successes = 0.0
        total_weights = 0.0

        for record in records:
            age = now - record.timestamp
            weight = math.exp(-age * math.log(2) / self.recency_half_life)
            total_weights += weight
            if record.success:
                weighted_successes += weight

        recency_score = weighted_successes / total_weights if total_weights > 0 else 0.5

        # Error distribution
        error_types = [r.error_type for r in records if not r.success and r.error_type]
        error_distribution: dict[str, int] = {}
        for error_type in error_types:
            error_distribution[error_type] = error_distribution.get(error_type, 0) + 1

        # Composite health score
        health_score = (
            success_rate * self.health_weights["success_rate"]
            + latency_score * self.health_weights["latency_score"]
            + recency_score * self.health_weights["recency_score"]
        )

        return HealthMetrics(
            success_rate=success_rate,
            avg_latency=avg_latency,
            p95_latency=p95_latency,
            latency_score=latency_score,
            recency_score=recency_score,
            health_score=health_score,
            sample_count=len(records),
            error_distribution=error_distribution if error_distribution else None
        )

    def is_healthy(
        self,
        model_id: str,
        *,
        threshold: float = 0.5
    ) -> bool:
        """Check if model is healthy.

        Args:
            model_id: Model identifier
            threshold: Minimum health score to be considered healthy

        Returns:
            True if healthy
        """
        return self.get_health_score(model_id) >= threshold

    def get_all_metrics(self) -> dict[str, HealthMetrics]:
        """Get metrics for all tracked models.

        Returns:
            Dictionary mapping model_id to HealthMetrics
        """
        with self._lock:
            model_ids = list(self.records.keys())

        return {
            model_id: self.get_metrics(model_id)
            for model_id in model_ids
        }

    def compare_models(
        self,
        model_ids: list[str]
    ) -> list[tuple[str, float]]:
        """Compare health scores of multiple models.

        Args:
            model_ids: List of model identifiers

        Returns:
            List of (model_id, health_score) sorted by score descending
        """
        scores = [
            (model_id, self.get_health_score(model_id))
            for model_id in model_ids
        ]

        return sorted(scores, key=lambda x: x[1], reverse=True)

    def reset(self, model_id: str | None = None) -> None:
        """Reset tracking for model or all models.

        Args:
            model_id: Model to reset, or None for all
        """
        with self._lock:
            if model_id is None:
                self.records.clear()
                logger.info("Reset all model health tracking")
            elif model_id in self.records:
                del self.records[model_id]
                logger.info(f"Reset health tracking for: {model_id}")

            if self.persistence_path:
                self._save_persistence()

    def _load_persistence(self) -> None:
        """Load persisted health data."""
        if not self.persistence_path or not self.persistence_path.exists():
            return

        try:
            with open(self.persistence_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for model_id, record_list in data.items():
                self.records[model_id] = deque(
                    [
                        HealthRecord(
                            success=r["success"],
                            latency=r["latency"],
                            timestamp=r["timestamp"],
                            error_type=r.get("error_type")
                        )
                        for r in record_list
                    ],
                    maxlen=self.window_size
                )

            logger.info(
                f"Loaded health tracking for {len(self.records)} models"
            )

        except Exception as e:
            logger.warning(f"Failed to load health persistence: {e}")

    def _save_persistence(self) -> None:
        """Persist health data to disk."""
        if not self.persistence_path:
            return

        try:
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)

            with self._lock:
                data = {
                    model_id: [asdict(r) for r in records]
                    for model_id, records in self.records.items()
                }

            with open(self.persistence_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.warning(f"Failed to save health persistence: {e}")


class SmartRetryPolicy:
    """Differentiated retry policy based on error type."""

    # Error-specific retry limits
    MAX_RETRIES: dict[str, int] = {
        "rate_limit": 2,
        "timeout": 3,
        "network": 3,
        "server": 2,
        "auth": 0,  # Don't retry auth errors
        "unknown": 1
    }

    # Error-specific cooldown times (seconds)
    COOLDOWN_TIMES: dict[str, float] = {
        "rate_limit": 120,
        "timeout": 30,
        "network": 10,
        "server": 60,
        "auth": 0,
        "unknown": 60
    }

    @classmethod
    def should_retry(
        cls,
        error_type: str,
        attempt_number: int
    ) -> bool:
        """Determine if error should be retried.

        Args:
            error_type: Type of error
            attempt_number: Current attempt number (0-indexed)

        Returns:
            True if should retry
        """
        max_retries = cls.MAX_RETRIES.get(error_type, 1)
        return attempt_number < max_retries

    @classmethod
    def get_cooldown(cls, error_type: str) -> float:
        """Get cooldown time for error type.

        Args:
            error_type: Type of error

        Returns:
            Cooldown time in seconds
        """
        return cls.COOLDOWN_TIMES.get(error_type, 60.0)

    @classmethod
    def get_backoff_time(
        cls,
        error_type: str,
        attempt_number: int,
        *,
        base_cooldown: float | None = None
    ) -> float:
        """Get exponential backoff time.

        Args:
            error_type: Type of error
            attempt_number: Current attempt number (0-indexed)
            base_cooldown: Optional base cooldown (defaults to error-specific)

        Returns:
            Backoff time in seconds
        """
        if base_cooldown is None:
            base_cooldown = cls.get_cooldown(error_type)

        # Exponential backoff: base * 2^attempt
        return base_cooldown * (2 ** attempt_number)
