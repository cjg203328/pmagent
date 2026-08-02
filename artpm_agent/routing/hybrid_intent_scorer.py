"""Hybrid intent scoring and adaptive threshold adjustment.

Combines multiple signals (keywords, embeddings, LLM) for robust intent detection,
with dynamic threshold adjustment based on historical accuracy.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class IntentSignal:
    """Single intent detection signal."""
    intent: str | None
    score: float  # 0.0 to 1.0
    weight: float  # Signal importance
    source: str  # "keyword", "embedding", "llm"


@dataclass
class IntentScore:
    """Aggregated intent score with confidence."""
    intent: str
    confidence: float  # 0.0 to 1.0
    signals: list[IntentSignal]
    metadata: dict[str, Any] | None = None


class HybridIntentScorer:
    """Fuse multiple signals with weighted voting."""

    def __init__(
        self,
        *,
        keyword_weight: float = 0.3,
        embedding_weight: float = 0.5,
        llm_weight: float = 0.2
    ):
        """Initialize hybrid scorer.

        Args:
            keyword_weight: Weight for keyword matching
            embedding_weight: Weight for embedding similarity
            llm_weight: Weight for LLM classification
        """
        self.weights = {
            "keyword": keyword_weight,
            "embedding": embedding_weight,
            "llm": llm_weight
        }

        # Normalize weights
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v/total for k, v in self.weights.items()}

    def fuse(
        self,
        signals: list[tuple[str | None, float, str]]
    ) -> tuple[str | None, float, list[IntentSignal]]:
        """Fuse multiple signals into final intent and confidence.

        Args:
            signals: List of (intent, score, source) tuples

        Returns:
            (final_intent, confidence, signal_list)
        """
        if not signals:
            return None, 0.0, []

        # Build IntentSignal objects
        signal_objects = []
        for intent, score, source in signals:
            weight = self.weights.get(source, 0.0)
            signal_objects.append(IntentSignal(intent, score, weight, source))

        # Weighted voting
        votes: dict[str, float] = {}
        for signal in signal_objects:
            if signal.intent:
                votes[signal.intent] = (
                    votes.get(signal.intent, 0.0) + signal.score * signal.weight
                )

        if not votes:
            return None, 0.0, signal_objects

        # Winner takes all
        winner_intent, winner_score = max(votes.items(), key=lambda x: x[1])

        # Calculate confidence (normalized)
        total_possible = sum(s.weight for s in signal_objects if s.intent)
        confidence = winner_score / total_possible if total_possible > 0 else 0.0

        # Boost confidence if multiple signals agree
        agreement_count = sum(1 for s in signal_objects if s.intent == winner_intent)
        if agreement_count > 1:
            confidence = min(1.0, confidence * (1 + 0.1 * (agreement_count - 1)))

        return winner_intent, confidence, signal_objects

    def score(
        self,
        signals: list[tuple[str | None, float, str]]
    ) -> IntentScore | None:
        """Score and package result.

        Args:
            signals: List of (intent, score, source) tuples

        Returns:
            IntentScore or None if no valid intent
        """
        intent, confidence, signal_objects = self.fuse(signals)

        if intent is None:
            return None

        return IntentScore(
            intent=intent,
            confidence=confidence,
            signals=signal_objects,
            metadata={
                "signal_count": len(signal_objects),
                "agreement": sum(1 for s in signal_objects if s.intent == intent)
            }
        )


class AdaptiveThresholdAdjuster:
    """Adjust thresholds based on historical accuracy."""

    def __init__(
        self,
        *,
        default_threshold: float = 0.75,
        min_threshold: float = 0.6,
        max_threshold: float = 0.9,
        min_samples: int = 10,
        history_path: str | None = None
    ):
        """Initialize threshold adjuster.

        Args:
            default_threshold: Initial threshold
            min_threshold: Minimum allowed threshold
            max_threshold: Maximum allowed threshold
            min_samples: Minimum samples before adjusting
            history_path: Optional path to persist history
        """
        self.default_threshold = default_threshold
        self.min_threshold = min_threshold
        self.max_threshold = max_threshold
        self.min_samples = min_samples
        self.history_path = Path(history_path) if history_path else None

        # intent -> (correct_count, total_count)
        self.history: dict[str, list[int]] = {}
        self._lock = RLock()

        # Load persisted history
        self._load_history()

    def get_threshold(self, intent: str) -> float:
        """Get adaptive threshold for intent.

        Args:
            intent: Intent name

        Returns:
            Threshold value (0.0 to 1.0)
        """
        with self._lock:
            if intent not in self.history:
                return self.default_threshold

            correct, total = self.history[intent]

            if total < self.min_samples:
                return self.default_threshold

            # Calculate accuracy
            accuracy = correct / total

            # Inverse relationship: high accuracy → lower threshold (more aggressive)
            # Low accuracy → higher threshold (more conservative)
            threshold = self.max_threshold - accuracy * (
                self.max_threshold - self.min_threshold
            )

            return max(self.min_threshold, min(self.max_threshold, threshold))

    def update(self, intent: str, was_correct: bool) -> None:
        """Update history with feedback.

        Args:
            intent: Intent name
            was_correct: Whether prediction was correct
        """
        with self._lock:
            if intent not in self.history:
                self.history[intent] = [0, 0]

            self.history[intent][1] += 1  # total
            if was_correct:
                self.history[intent][0] += 1  # correct

            # Persist if configured
            if self.history_path:
                self._save_history()

    def get_accuracy(self, intent: str) -> float | None:
        """Get historical accuracy for intent.

        Args:
            intent: Intent name

        Returns:
            Accuracy (0.0 to 1.0) or None if no history
        """
        with self._lock:
            if intent not in self.history:
                return None

            correct, total = self.history[intent]
            if total == 0:
                return None

            return correct / total

    def get_stats(self) -> dict[str, dict[str, Any]]:
        """Get statistics for all intents.

        Returns:
            Dictionary mapping intent to stats
        """
        with self._lock:
            stats = {}
            for intent, (correct, total) in self.history.items():
                accuracy = correct / total if total > 0 else None
                stats[intent] = {
                    "correct": correct,
                    "total": total,
                    "accuracy": accuracy,
                    "threshold": self.get_threshold(intent)
                }
            return stats

    def _load_history(self) -> None:
        """Load persisted history from disk."""
        if not self.history_path or not self.history_path.exists():
            return

        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.history = {
                intent: [stats["correct"], stats["total"]]
                for intent, stats in data.items()
            }

            logger.info(f"Loaded threshold history: {len(self.history)} intents")

        except Exception as e:
            logger.warning(f"Failed to load threshold history: {e}")

    def _save_history(self) -> None:
        """Persist history to disk."""
        if not self.history_path:
            return

        try:
            # Ensure parent directory exists
            self.history_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                intent: {"correct": correct, "total": total}
                for intent, (correct, total) in self.history.items()
            }

            with open(self.history_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.warning(f"Failed to save threshold history: {e}")

    def reset(self, intent: str | None = None) -> None:
        """Reset history for intent or all intents.

        Args:
            intent: Intent to reset, or None for all
        """
        with self._lock:
            if intent is None:
                self.history.clear()
                logger.info("Reset all threshold history")
            elif intent in self.history:
                del self.history[intent]
                logger.info(f"Reset threshold history for: {intent}")

            if self.history_path:
                self._save_history()


class EnhancedIntentRouter:
    """Enhanced intent router with hybrid scoring and adaptive thresholds."""

    def __init__(
        self,
        *,
        keyword_detector: Callable[[str], tuple[str | None, float]] | None = None,
        embedding_detector: Callable[[str], tuple[str | None, float]] | None = None,
        llm_detector: Callable[[str], tuple[str | None, float]] | None = None,
        scorer: HybridIntentScorer | None = None,
        adjuster: AdaptiveThresholdAdjuster | None = None
    ):
        """Initialize enhanced intent router.

        Args:
            keyword_detector: Callable that returns (intent, score) from keywords
            embedding_detector: Callable that returns (intent, score) from embeddings
            llm_detector: Callable that returns (intent, score) from LLM
            scorer: Custom hybrid scorer
            adjuster: Custom threshold adjuster
        """
        self.keyword_detector = keyword_detector
        self.embedding_detector = embedding_detector
        self.llm_detector = llm_detector

        self.scorer = scorer or HybridIntentScorer()
        self.adjuster = adjuster or AdaptiveThresholdAdjuster()

        self._stats = {
            "total_detections": 0,
            "confident_detections": 0,
            "uncertain_detections": 0,
            "fallback_to_llm": 0
        }
        self._lock = RLock()

    def detect(
        self,
        user_input: str,
        *,
        use_llm_fallback: bool = True,
        return_score: bool = False
    ) -> str | IntentScore | None:
        """Detect intent with hybrid scoring.

        Args:
            user_input: User input text
            use_llm_fallback: Whether to use LLM as fallback
            return_score: Whether to return full IntentScore

        Returns:
            Intent name, IntentScore, or None
        """
        start_time = time.time()
        signals: list[tuple[str | None, float, str]] = []

        # Collect signals
        if self.keyword_detector:
            intent, score = self.keyword_detector(user_input)
            signals.append((intent, score, "keyword"))

        if self.embedding_detector:
            intent, score = self.embedding_detector(user_input)
            signals.append((intent, score, "embedding"))

        # Fuse signals
        intent_score = self.scorer.score(signals)

        # Check threshold
        if intent_score:
            threshold = self.adjuster.get_threshold(intent_score.intent)

            if intent_score.confidence >= threshold:
                # High confidence, accept
                with self._lock:
                    self._stats["total_detections"] += 1
                    self._stats["confident_detections"] += 1

                logger.debug(
                    f"Intent detected: {intent_score.intent} "
                    f"(confidence={intent_score.confidence:.2f}, "
                    f"threshold={threshold:.2f}, "
                    f"time={time.time()-start_time:.3f}s)"
                )

                return intent_score if return_score else intent_score.intent

        # Fallback to LLM if available and enabled
        if use_llm_fallback and self.llm_detector:
            llm_intent, llm_score = self.llm_detector(user_input)

            if llm_intent:
                with self._lock:
                    self._stats["total_detections"] += 1
                    self._stats["fallback_to_llm"] += 1

                logger.debug(
                    f"LLM fallback detected: {llm_intent} "
                    f"(score={llm_score:.2f}, time={time.time()-start_time:.3f}s)"
                )

                if return_score:
                    signals.append((llm_intent, llm_score, "llm"))
                    return self.scorer.score(signals)
                else:
                    return llm_intent

        # No confident detection
        with self._lock:
            self._stats["total_detections"] += 1
            self._stats["uncertain_detections"] += 1

        logger.debug(f"No confident intent detected (time={time.time()-start_time:.3f}s)")
        return None

    def provide_feedback(self, intent: str, was_correct: bool) -> None:
        """Provide feedback to improve threshold adjustment.

        Args:
            intent: Detected intent
            was_correct: Whether detection was correct
        """
        self.adjuster.update(intent, was_correct)

        logger.debug(
            f"Feedback recorded for {intent}: {'correct' if was_correct else 'incorrect'}"
        )

    def get_stats(self) -> dict[str, Any]:
        """Get router statistics.

        Returns:
            Dictionary with statistics
        """
        with self._lock:
            stats: dict[str, Any] = dict(self._stats)

        stats["threshold_history"] = self.adjuster.get_stats()

        return stats

    def reset_stats(self) -> None:
        """Reset statistics."""
        with self._lock:
            self._stats = {
                "total_detections": 0,
                "confident_detections": 0,
                "uncertain_detections": 0,
                "fallback_to_llm": 0
            }
