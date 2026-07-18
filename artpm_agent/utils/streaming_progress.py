"""Streaming response with progress feedback.

Provides user-friendly progress indicators during long-running operations,
improving perceived responsiveness.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger(__name__)


class TurnStage(str, Enum):
    """Stages of turn processing."""
    INIT = "init"
    INTENT_DETECTION = "intent_detection"
    MEMORY_RETRIEVAL = "memory_retrieval"
    SKILL_EXECUTION = "skill_execution"
    MODEL_GENERATION = "model_generation"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class ProgressEvent:
    """Progress update event."""
    stage: TurnStage
    message: str
    progress: float  # 0.0 to 1.0
    metadata: dict[str, Any] | None = None
    timestamp: float | None = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


class ProgressTracker:
    """Tracks and emits progress events during turn processing."""

    # Stage weights for progress calculation
    STAGE_WEIGHTS = {
        TurnStage.INIT: 0.05,
        TurnStage.INTENT_DETECTION: 0.10,
        TurnStage.MEMORY_RETRIEVAL: 0.15,
        TurnStage.SKILL_EXECUTION: 0.30,
        TurnStage.MODEL_GENERATION: 0.35,
        TurnStage.COMPLETE: 1.0,
    }

    # User-friendly stage descriptions
    STAGE_MESSAGES = {
        TurnStage.INIT: "🚀 初始化处理...",
        TurnStage.INTENT_DETECTION: "🔍 正在理解您的需求...",
        TurnStage.MEMORY_RETRIEVAL: "🧠 检索相关记忆...",
        TurnStage.SKILL_EXECUTION: "⚙️ 执行业务逻辑...",
        TurnStage.MODEL_GENERATION: "💬 正在生成回答...",
        TurnStage.COMPLETE: "✅ 完成",
        TurnStage.ERROR: "❌ 出现错误",
    }

    def __init__(self):
        self._current_stage: TurnStage = TurnStage.INIT
        self._start_time: float = time.time()
        self._stage_start_times: dict[TurnStage, float] = {}

    def update_stage(
        self,
        stage: TurnStage,
        custom_message: str | None = None,
        metadata: dict[str, Any] | None = None
    ) -> ProgressEvent:
        """Update current stage and emit progress event.

        Args:
            stage: New stage
            custom_message: Optional custom message (overrides default)
            metadata: Optional metadata

        Returns:
            ProgressEvent
        """
        self._current_stage = stage
        self._stage_start_times[stage] = time.time()

        progress = self.STAGE_WEIGHTS.get(stage, 0.0)
        message = custom_message or self.STAGE_MESSAGES.get(
            stage, f"处理阶段: {stage.value}"
        )

        event = ProgressEvent(
            stage=stage,
            message=message,
            progress=progress,
            metadata=metadata
        )

        logger.debug(
            f"Progress: {stage.value} ({progress:.0%}) - {message}"
        )

        return event

    def get_stage_duration(self, stage: TurnStage) -> float | None:
        """Get duration of a stage in seconds.

        Args:
            stage: Stage to query

        Returns:
            Duration in seconds, or None if stage not started
        """
        if stage not in self._stage_start_times:
            return None

        start = self._stage_start_times[stage]
        return time.time() - start

    def get_total_duration(self) -> float:
        """Get total duration since tracker creation.

        Returns:
            Duration in seconds
        """
        return time.time() - self._start_time


def stream_with_progress(
    agent: Any,
    user_input: str,
    context: dict[str, Any],
    *,
    emit_progress: Callable[[ProgressEvent], None] | None = None
) -> Iterator[str]:
    """Stream response with progress feedback.

    Args:
        agent: Agent instance
        user_input: User input
        context: Turn context
        emit_progress: Optional callback for progress events

    Yields:
        Response chunks
    """
    tracker = ProgressTracker()

    def emit(stage: TurnStage, custom_message: str | None = None, metadata: dict | None = None):
        """Helper to emit progress if callback provided."""
        event = tracker.update_stage(stage, custom_message, metadata)
        if emit_progress:
            try:
                emit_progress(event)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

    try:
        # Stage 1: Intent detection
        emit(TurnStage.INTENT_DETECTION)
        time.sleep(0.01)  # Brief pause for UI update

        # Stage 2: Memory retrieval (if applicable)
        if context.get("knowledge_context") or hasattr(agent, "memory"):
            emit(TurnStage.MEMORY_RETRIEVAL)
            time.sleep(0.01)

        # Stage 3: Check if skill execution
        has_attachments = bool(context.get("file_path") or context.get("file_paths"))
        if not has_attachments and hasattr(agent, "_detect_intent"):
            intent = agent._detect_intent(user_input)
            if intent and hasattr(agent, "router") and intent in agent.router.skills:
                emit(
                    TurnStage.SKILL_EXECUTION,
                    custom_message=f"⚙️ 执行技能: {intent}...",
                    metadata={"skill": intent}
                )
                time.sleep(0.01)

        # Stage 4: Model generation (streaming)
        emit(TurnStage.MODEL_GENERATION)

        # Stream response chunks
        chunk_count = 0
        for chunk in agent.stream_chat(user_input, context):
            yield chunk
            chunk_count += 1

            # Update progress periodically (every 10 chunks)
            if chunk_count % 10 == 0:
                current_progress = (
                    tracker.STAGE_WEIGHTS[TurnStage.MODEL_GENERATION]
                    + (tracker.STAGE_WEIGHTS[TurnStage.COMPLETE] -
                       tracker.STAGE_WEIGHTS[TurnStage.MODEL_GENERATION]) * 0.5
                )
                if emit_progress:
                    emit_progress(ProgressEvent(
                        stage=TurnStage.MODEL_GENERATION,
                        message=f"💬 正在生成回答... ({chunk_count} 字符)",
                        progress=min(current_progress, 0.95),
                        metadata={"chunk_count": chunk_count}
                    ))

        # Stage 5: Complete
        emit(
            TurnStage.COMPLETE,
            custom_message="✅ 完成",
            metadata={
                "chunk_count": chunk_count,
                "duration_ms": tracker.get_total_duration() * 1000
            }
        )

    except Exception as e:
        emit(
            TurnStage.ERROR,
            custom_message=f"❌ 处理失败: {str(e)}",
            metadata={"error": str(e), "error_type": type(e).__name__}
        )
        raise


@contextmanager
def progress_context(
    stage: TurnStage,
    message: str | None = None,
    *,
    emit_progress: Callable[[ProgressEvent], None] | None = None
):
    """Context manager for progress tracking.

    Usage:
        with progress_context(TurnStage.SKILL_EXECUTION, emit_progress=callback):
            result = execute_skill(...)

    Args:
        stage: Current stage
        message: Optional custom message
        emit_progress: Optional progress callback

    Yields:
        ProgressTracker instance
    """
    tracker = ProgressTracker()
    event = tracker.update_stage(stage, message)

    if emit_progress:
        try:
            emit_progress(event)
        except Exception as e:
            logger.warning(f"Progress callback failed: {e}")

    try:
        yield tracker
    finally:
        # Emit completion or error
        final_stage = TurnStage.COMPLETE if not hasattr(tracker, "_error") else TurnStage.ERROR
        final_event = tracker.update_stage(final_stage)
        if emit_progress:
            try:
                emit_progress(final_event)
            except Exception as e:
                logger.warning(f"Final progress callback failed: {e}")
