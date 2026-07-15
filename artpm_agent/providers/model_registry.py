"""Model capability registry for task-aware deployment.

Owns the deployment-level "model recognition": it maps every configured model
to its capabilities (vision / tier / cost / latency) and selects the most
appropriate model for a given *task type* instead of always hitting the single
primary model.

This is what lets cheap classification ("routing") run on a small/fast model
while heavy reasoning uses a stronger one, without changing any caller except
to pass a ``task_type``. Everything here is read-only and additive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional

# Task types that drive model selection.
TASK_TYPES = ("routing", "chat", "reasoning", "vision")

# Capability tiers, ordered from cheapest to strongest.
TIERS = ("cheap", "general", "strong")

VISION_MARKERS = (
    "vision",
    "multimodal",
    "vl",
    "qwen-vl",
    "glm-4v",
    "4v",
    "pixtral",
)

REASONING_MARKERS = (
    "o1",
    "o3",
    "o4",
    "reasoning",
    "deepseek-r1",
    "r1",
    "qwq",
    "think",
)

CHEAP_MARKERS = (
    "mini",
    "nano",
    "lite",
    "haiku",
    "flash",
    "8b",
    "small",
    "light",
)


@dataclass(frozen=True)
class ModelCapability:
    """Describes what a single model id can do and roughly costs."""

    model_id: str
    provider: str = ""
    vision: bool = False
    tier: str = "general"
    cost_weight: float = 1.0
    latency_weight: float = 1.0

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            object.__setattr__(self, "tier", "general")
        object.__setattr__(self, "cost_weight", max(0.01, float(self.cost_weight)))
        object.__setattr__(self, "latency_weight", max(0.01, float(self.latency_weight)))


def _casefold(model_id: str) -> str:
    return str(model_id or "").strip().casefold()


def _has_marker(model_id: str, markers: Iterable[str]) -> bool:
    normalized = _casefold(model_id)
    return any(marker in normalized for marker in markers)


class ModelCapabilityRegistry:
    """Builds a capability table from the LLM config and answers selection."""

    def __init__(self, llm_config: Mapping[str, Any]) -> None:
        self._config = dict(llm_config or {})
        self._models: List[ModelCapability] = self._build()
        self._by_id = {m.model_id: m for m in self._models}

    # ── build ──

    def _build(self) -> List[ModelCapability]:
        primary = str(self._config.get("model", "") or "").strip()
        provider = str(self._config.get("provider", "") or "").strip().lower()
        available = self._config.get("available_models", []) or []
        vision_model = str(self._config.get("vision_model", "") or "").strip()
        multimodal_model = str(self._config.get("multimodal_model", "") or "").strip()

        ids: List[str] = []
        if primary:
            ids.append(primary)
        for m in available:
            m = str(m or "").strip()
            if m and m not in ids:
                ids.append(m)
        for m in (vision_model, multimodal_model):
            if m and m not in ids:
                ids.append(m)

        explicit_vision_ids = {
            mid for mid in (vision_model, multimodal_model) if mid
        }
        return [
            self._capability_for(
                mid,
                provider,
                explicit_vision=mid in explicit_vision_ids,
            )
            for mid in ids
        ]

    def _capability_for(
        self,
        model_id: str,
        provider: str,
        *,
        explicit_vision: bool = False,
    ) -> ModelCapability:
        vision = explicit_vision or _has_marker(model_id, VISION_MARKERS)
        tier = "general"
        if _has_marker(model_id, REASONING_MARKERS):
            tier = "strong"
        elif _has_marker(model_id, CHEAP_MARKERS):
            tier = "cheap"
        cost = {"cheap": 0.3, "general": 1.0, "strong": 2.5}[tier]
        lat = {"cheap": 0.5, "general": 1.0, "strong": 1.6}[tier]
        if vision:
            lat = max(lat, 1.2)
        return ModelCapability(
            model_id=model_id,
            provider=provider,
            vision=vision,
            tier=tier,
            cost_weight=cost,
            latency_weight=lat,
        )

    # ── query ──

    def capability(self, model_id: str) -> Optional[ModelCapability]:
        return self._by_id.get(model_id)

    def all(self) -> List[ModelCapability]:
        return list(self._models)

    def models_for_task(
        self, task_type: str, *, require_vision: bool = False
    ) -> List[ModelCapability]:
        if task_type not in TASK_TYPES:
            task_type = "chat"
        candidates = self._models
        if require_vision or task_type == "vision":
            candidates = [m for m in candidates if m.vision]
        if task_type == "vision":
            candidates.sort(key=lambda m: (m.tier != "strong", not m.vision))
        elif task_type == "routing":
            # cheap classification: prefer cheap then general, lowest cost first
            candidates.sort(key=lambda m: (TIERS.index(m.tier), m.cost_weight))
        elif task_type == "reasoning":
            candidates.sort(key=lambda m: (-TIERS.index(m.tier), m.cost_weight))
        else:  # chat
            # prefer a balanced model; avoid cheapest for general chat unless only option
            candidates.sort(
                key=lambda m: (m.tier == "cheap", TIERS.index(m.tier), m.cost_weight)
            )
        return candidates

    def select_model(
        self,
        task_type: str,
        primary_model: Optional[str],
        fallback_ids: Optional[List[str]] = None,
        *,
        require_vision: bool = False,
    ) -> Optional[str]:
        """Pick the best available model id for a task, or fall back to primary."""
        candidates = self.models_for_task(task_type, require_vision=require_vision)
        if not candidates:
            return primary_model
        # Reasoning / vision are capability-critical: take the strongest capable
        # model outright rather than biasing toward the primary's family.
        if task_type in ("reasoning", "vision"):
            return candidates[0].model_id

        preferred_family = None
        if primary_model:
            preferred_family = re.split(r"[-_:/.]", _casefold(primary_model), maxsplit=1)[0]

        # For chat / routing, prefer the best-tier candidate in the same family
        # as the primary (keeps response style and provider billing consistent).
        if preferred_family:
            for m in candidates:
                if (
                    re.split(r"[-_:/.]", _casefold(m.model_id), maxsplit=1)[0]
                    == preferred_family
                ):
                    return m.model_id
        return candidates[0].model_id
