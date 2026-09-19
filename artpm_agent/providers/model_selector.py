"""Provider and model selection logic.

Handles model family detection, fallback candidate selection, and
vision model identification.
"""

import re
from typing import Any, List, Mapping, Optional, Set


class ProviderSelector:
    """Selects primary and fallback models based on configuration and health."""

    MODEL_FAILOVER_PROVIDERS = {"openai", "custom", "zhipu", "deepseek"}
    VISION_MODEL_MARKERS = (
        "vision",
        "multimodal",
        "vl",
        "gpt-4o",
        "gpt-4.1",
        "gpt-5",
        "gemini",
        "claude-3",
        "claude-4",
        "qwen-vl",
        "glm-4v",
        "pixtral",
    )

    def __init__(self, llm_config: Mapping[str, Any]) -> None:
        self._llm_config = dict(llm_config)

    @property
    def provider(self) -> Optional[str]:
        """Normalized primary provider for legacy selector callers."""
        value = str(self._llm_config.get("provider", "") or "").strip().lower()
        return value or None

    @property
    def model(self) -> Optional[str]:
        """Trimmed primary model ID while preserving its case."""
        return self.primary_model_id()

    @staticmethod
    def model_family(model_id: str) -> str:
        """Return a stable family prefix without assuming vendor naming rules."""
        normalized = str(model_id or "").strip().casefold()
        return re.split(r"[-_:/.]", normalized, maxsplit=1)[0]

    @staticmethod
    def is_likely_vision_model(model_id: str) -> bool:
        """Check if a model ID suggests vision/multimodal capability."""
        normalized = str(model_id or "").casefold()
        return any(
            marker in normalized for marker in ProviderSelector.VISION_MODEL_MARKERS
        )

    def primary_model_id(self) -> Optional[str]:
        """Return the primary model ID from configuration."""
        return str(self._llm_config.get("model", "") or "").strip() or None

    def supports_failover(self) -> bool:
        """Check if the current provider supports failover."""
        provider = str(self._llm_config.get("provider", "") or "").strip().lower()
        return provider in self.MODEL_FAILOVER_PROVIDERS

    def fallback_model_ids(self, primary_model: str, health_check: Any) -> List[str]:
        """Return available fallback models, prioritizing same family."""
        if not self.supports_failover():
            return []

        raw_models = self._llm_config.get("available_models", [])
        if not isinstance(raw_models, list):
            return []

        unique_models = []
        seen: Set[str] = {primary_model.casefold()}
        for item in raw_models:
            model_id = str(item or "").strip()
            key = model_id.casefold()
            if not model_id or key in seen:
                continue
            seen.add(key)
            unique_models.append(model_id)

        primary_family = self.model_family(primary_model)
        unique_models.sort(
            key=lambda model_id: (
                self.model_family(model_id) != primary_family,
                model_id.casefold(),
            )
        )
        return [
            model_id
            for model_id in unique_models
            if health_check.is_available(model_id)
        ]

    def vision_fallback_model_ids(
        self, primary_model: str, health_check: Any
    ) -> List[str]:
        """Prefer explicitly configured or recognizably multimodal candidates."""
        raw_models = self._llm_config.get("available_models", [])
        if not isinstance(raw_models, list):
            raw_models = []

        explicit = []
        for key in ("vision_model", "multimodal_model"):
            value = str(self._llm_config.get(key, "") or "").strip()
            if value:
                explicit.append(value)

        candidates = explicit + [str(item).strip() for item in raw_models]
        unique: List[str] = []
        seen: Set[str] = {primary_model.casefold()}
        for model_id in candidates:
            if not model_id or model_id.casefold() in seen:
                continue
            seen.add(model_id.casefold())
            if health_check.is_available(model_id):
                unique.append(model_id)

        likely = [
            model_id for model_id in unique if self.is_likely_vision_model(model_id)
        ]
        return likely or unique


__all__ = ["ProviderSelector"]
