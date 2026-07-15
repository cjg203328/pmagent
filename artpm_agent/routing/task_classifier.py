"""LLM task-type classifier for layered (hierarchical) model selection.

Wires ``task_type="routing"`` to a cheap LLM classifier so that:

1. The classifier itself runs on the **routing-tier (cheapest)** model via
   ``ModelGateway.best_model_for_task("routing")`` — classification is nearly
   free and never blocks the real response.
2. Its output (``chat`` / ``reasoning`` / ``vision``) is then fed back into
   ``best_model_for_task`` to pick the **actual** response model.

This realizes "分层模型" (layered model selection): a small model decides the
task type, and the task type selects the right-sized model for the answer.

Opt-in only: active when ``ARTPM_TASK_CLASSIFIER=true`` (or
``llm.task_classifier_enabled`` in config). When disabled, ``classify`` returns
``"chat"``, which is exactly today's behaviour — no caller change, no behaviour
change.
"""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from typing import Any, List, Mapping, Optional

from artpm_agent.providers.model_registry import TASK_TYPES
from artpm_agent.utils.logger import get_logger

logger = get_logger(__name__)

# The classifier only emits user-facing task types. "routing" is the *internal*
# tier used to run the classifier itself and is normalised to "chat".
CLASSIFIER_OUTPUTS = ("chat", "reasoning", "vision", "routing")

# Heuristic signals for the offline fallback when the LLM is unavailable.
_REASONING_MARKERS = (
    "分析", "推理", "为什么", "原因", "评估", "计算", "方案", "建议",
    "复杂", "对比", "总结", "归纳", "推导", "论证", "规划", "怎么算",
    "plan", "analyze", "why", "reason", "compare", "evaluate", "summarize",
    "reasoning",
)


class LLMTaskClassifier:
    """Classify a user message into a task type, using the routing-tier model."""

    def __init__(
        self,
        gateway: Any,
        *,
        enabled: bool = True,
        config: Optional[Mapping[str, Any]] = None,
        cache_max: int = 256,
    ) -> None:
        self._gateway = gateway
        self._enabled = bool(enabled)
        self._config = config or {}
        self._cache: "OrderedDict[str, str]" = OrderedDict()
        self._cache_lock = RLock()
        self._cache_max = cache_max

    # ── Public API ──

    def classify(
        self, user_input: str, image_paths: Optional[List[str]] = None
    ) -> str:
        """Return one of ``chat`` / ``reasoning`` / ``vision`` for model selection."""
        if not self._enabled:
            return "chat"
        text = (user_input or "").strip()
        if not text and not image_paths:
            return "chat"

        cache_key = text.lower()
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        result = self._classify_llm(text) or self._heuristic(text, image_paths)

        with self._cache_lock:
            self._cache[cache_key] = result
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)
        return result

    # ── Internals ──

    def _classify_llm(self, text: str) -> Optional[str]:
        """Run the classifier on the routing-tier (cheapest) model."""
        try:
            routing_model = self._gateway.best_model_for_task("routing")
            client = self._gateway.client_for_model(
                routing_model or self._gateway.primary_model_id()
            )
            if client is None:
                return None
            response = client.chat(self._build_prompt(text))
            if not isinstance(response, str) or not response.strip():
                return None
            label = response.strip().strip('"\'`').split()[0].lower()
            if label in CLASSIFIER_OUTPUTS:
                return "chat" if label == "routing" else label
            return None
        except Exception as error:  # noqa: BLE001 - degrade to heuristic
            logger.warning("[TaskClassifier] LLM classify failed: %s", error)
            return None

    @staticmethod
    def _heuristic(text: str, image_paths: Optional[List[str]]) -> str:
        if image_paths:
            return "vision"
        low = text.lower()
        if any(marker in low for marker in _REASONING_MARKERS):
            return "reasoning"
        return "chat"

    @staticmethod
    def _build_prompt(text: str) -> str:
        return (
            "你是一个任务类型分类器。判断用户消息应由哪类模型处理，"
            "只返回下列之一：chat / reasoning / vision。\n"
            "- chat：普通对话、问候、简单问答、信息查询\n"
            "- reasoning：需要分析、推理、计算、评估、对比、方案生成等复杂任务\n"
            "- vision：涉及图片或多模态理解（用户提供了图片或要求看图）\n\n"
            f"用户消息：{text}\n\n只返回类型名，不要任何解释。"
        )
