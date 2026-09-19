"""Vision model routing and configuration.

Handles dedicated vision/multimodal model pairing, allowing a non-vision
primary model to process image inputs via a separate vision-capable model.
"""

from typing import Any, Callable, Dict, Mapping, Optional

from artpm_agent.utils import get_logger

logger = get_logger(__name__)


class VisionModelRouter:
    """Routes vision requests to a dedicated multimodal model."""

    def __init__(
        self,
        llm_config: Mapping[str, Any],
        client_factory: Callable[[Dict[str, Any]], Any],
    ) -> None:
        self._llm_config = dict(llm_config)
        self._client_factory = client_factory
        self._vision_provider: Optional[str] = (
            str(self._llm_config.get("vision_provider", "") or "").strip().lower()
            or None
        )
        self._vision_model: Optional[str] = (
            str(self._llm_config.get("vision_model", "") or "").strip() or None
        )

    @property
    def has_vision_pairing(self) -> bool:
        """True when a dedicated vision model is configured."""
        return bool(self._vision_model)

    def has_vision_model(self) -> bool:
        """True when a dedicated vision model is configured."""
        return bool(self._vision_model)

    def cache_key(self) -> str:
        """Return cache key for the vision client."""
        return f"__vision__:{self._vision_provider or ''}:{self._vision_model or ''}"

    def vision_cache_key(self) -> str:
        """Return cache key for the vision client."""
        return f"__vision__:{self._vision_provider or ''}:{self._vision_model or ''}"

    def vision_client_config(self) -> Dict[str, Any]:
        """Assemble a client config for the dedicated vision model."""
        cfg = dict(self._llm_config)
        provider = self._vision_provider or str(cfg.get("provider", "")).strip().lower()
        cfg["provider"] = provider
        cfg["model"] = self._vision_model or ""

        if provider == "zhipu":
            key = cfg.get("vision_api_key") or cfg.get("zhipu_api_key")
            base = (
                cfg.get("vision_api_base")
                or cfg.get("zhipu_api_base")
                or "https://open.bigmodel.cn/api/paas/v4"
            )
        elif provider in {"openai", "custom"}:
            key = cfg.get("vision_api_key") or cfg.get("openai_api_key")
            base = cfg.get("vision_api_base") or cfg.get("openai_api_base")
        elif provider == "deepseek":
            key = cfg.get("vision_api_key") or cfg.get("deepseek_api_key")
            base = cfg.get("vision_api_base") or cfg.get("deepseek_api_base")
        else:
            key = cfg.get("vision_api_key")
            base = cfg.get("vision_api_base")

        if key:
            cfg[f"{provider}_api_key"] = key
        if base:
            cfg[f"{provider}_api_base"] = base

        # GLM-4V-Flash caps output at 1024 tokens; be safe for other models too.
        try:
            configured_max = int(cfg.get("max_tokens", 1200))
        except (TypeError, ValueError):
            configured_max = 1200
        cfg["max_tokens"] = min(configured_max, 1024)
        cfg["retry_max_attempts"] = 1
        return cfg

    def build_vision_client(self) -> Any:
        """Build a new vision client instance."""
        try:
            return self._client_factory(self.vision_client_config())
        except Exception:
            logger.exception("vision client failed to build")
            return None


__all__ = ["VisionModelRouter"]
