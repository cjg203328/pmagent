"""Provider-neutral model gateway.

Owns model selection, cooldown bookkeeping, and failover orchestration so the
agent class no longer manages provider retries. The gateway depends only on a
plain LLM config mapping and a client factory, which means its input/output can
be tested without initializing Streamlit, the database, or any Skill.

Provider capability differences (what counts as a retryable error, what counts
as a vision-capability error) live here rather than leaking into business Skills.
"""

import os
import re
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from artpm_agent.utils import create_llm_client, get_logger

logger = get_logger(__name__)


class ModelGateway:
    """Selects models, tracks circuit-breaker cooldowns, and fails over.

    The gateway never touches Streamlit, SQLAlchemy, or Skill routing. It speaks
    only to an LLM client (or a factory that builds one) and a config dict.
    """

    MODEL_FAILOVER_COOLDOWN_SECONDS = 60
    MODEL_FAILOVER_PROVIDERS = {"openai", "custom", "zhipu"}
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

    def __init__(
        self,
        llm_config: Mapping[str, Any],
        primary_client: Any,
        client_factory: Callable[[Mapping[str, Any]], Any] = create_llm_client,
        telemetry: Any = None,
        response_cache: Any = None,
    ) -> None:
        self._llm_config = dict(llm_config)
        self._client_factory = client_factory
        self._primary_client = primary_client
        self._clients: Dict[str, Any] = {}
        self._unavailable_until: Dict[str, float] = {}
        self._capability_registry: Optional[Any] = None
        self._response_cache: Optional[Any] = (
            response_cache
            if response_cache is not None
            else self._build_response_cache()
        )
        self._telemetry: Optional[Any] = (
            telemetry if telemetry is not None else self._build_telemetry()
        )
        # Layered model selection: a cheap LLM classifier (opt-in) that decides
        # the task type so the gateway can pick the right-sized model. Disabled
        # by default → classify_task() falls back to "chat" (today's behaviour).
        self._task_classifier: Optional[Any] = None
        self._task_classification_enabled = self._read_task_classifier_flag()
        self.last_response_model: Optional[str] = (
            str(self._llm_config.get("model", "") or "").strip() or None
        )
        self.last_model_fallback_from: Optional[str] = None
        if primary_client is not None and self.last_response_model:
            self._clients[self.last_response_model] = primary_client

    # ── Provider-neutral helpers (no Streamlit / DB / Skill) ──

    @staticmethod
    def _model_family(model_id: str) -> str:
        """Return a stable family prefix without assuming vendor naming rules."""
        normalized = str(model_id or "").strip().casefold()
        return re.split(r"[-_:/.]", normalized, maxsplit=1)[0]

    @staticmethod
    def _error_chain_text(error: BaseException) -> str:
        parts = []
        current = error
        seen = set()
        while current is not None and id(current) not in seen and len(parts) < 6:
            seen.add(id(current))
            parts.append(f"{type(current).__name__}: {current}".casefold())
            current = current.__cause__ or current.__context__
        return " ".join(parts)

    @staticmethod
    def _is_likely_vision_model(model_id: str) -> bool:
        normalized = str(model_id or "").casefold()
        return any(marker in normalized for marker in ModelGateway.VISION_MODEL_MARKERS)

    @staticmethod
    def _is_vision_capability_error(error: BaseException) -> bool:
        text = ModelGateway._error_chain_text(error)
        markers = (
            "does not support image",
            "image input is not supported",
            "unsupported modality",
            "image_url is not supported",
            "multimodal input is not supported",
            "vision is not supported",
            "not support vision",
            "模型客户端不支持图片",
            "当前模型客户端不支持图片",
            "不支持图片",
            "不支持多模态",
            "图片理解",
        )
        return any(marker.casefold() in text for marker in markers)

    def _is_retryable_model_error(self, error: BaseException) -> bool:
        """Only fail over for transient provider or transport failures."""
        current = error
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            status_code = getattr(current, "status_code", None)
            try:
                status_code = int(status_code)
            except (TypeError, ValueError):
                status_code = None
            if status_code is not None:
                if status_code in {400, 401, 403, 422}:
                    return False
                if status_code in {404, 408, 409, 425, 429} or status_code >= 500:
                    return True
            current = current.__cause__ or current.__context__

        text = self._error_chain_text(error)
        non_retryable_markers = (
            "invalid api key",
            "authentication",
            "unauthorized",
            "forbidden",
            "invalid request",
            "bad request",
        )
        if any(marker in text for marker in non_retryable_markers):
            return False
        retryable_markers = (
            "timeout",
            "timed out",
            "connection",
            "connecterror",
            "resourceexhausted",
            "resource exhausted",
            "rate limit",
            "too many requests",
            "worker local total request limit",
            "overloaded",
            "capacity",
            "unsupported model",
            "model not found",
            "模型服务未返回有效回答",
            "no valid response",
            "empty response",
            "服务繁忙",
            "超时",
            "连接失败",
        )
        return any(marker in text for marker in retryable_markers)

    def is_retryable_model_error(self, error: BaseException) -> bool:
        """Public compatibility boundary for agent and structured adapters."""
        return self._is_retryable_model_error(error)

    # ── Public gateway API ──

    def primary_model_id(self) -> Optional[str]:
        model_id = str(self._llm_config.get("model", "") or "").strip()
        return model_id or None

    def is_model_available(self, model_id: str) -> bool:
        return self._unavailable_until.get(model_id, 0) <= time.monotonic()

    def mark_model_unavailable(self, model_id: str) -> None:
        self._unavailable_until[model_id] = (
            time.monotonic() + self.MODEL_FAILOVER_COOLDOWN_SECONDS
        )

    def mark_model_healthy(self, model_id: str) -> None:
        self._unavailable_until.pop(model_id, None)

    def fallback_model_ids(self, primary_model: str) -> List[str]:
        provider = str(self._llm_config.get("provider", "") or "").strip().lower()
        if provider not in self.MODEL_FAILOVER_PROVIDERS:
            return []

        raw_models = self._llm_config.get("available_models", [])
        if not isinstance(raw_models, list):
            return []
        unique_models = []
        seen = {primary_model.casefold()}
        for item in raw_models:
            model_id = str(item or "").strip()
            key = model_id.casefold()
            if not model_id or key in seen:
                continue
            seen.add(key)
            unique_models.append(model_id)

        primary_family = self._model_family(primary_model)
        unique_models.sort(
            key=lambda model_id: (
                self._model_family(model_id) != primary_family,
                model_id.casefold(),
            )
        )
        return [
            model_id for model_id in unique_models if self.is_model_available(model_id)
        ]

    def vision_fallback_model_ids(self, primary_model: str) -> List[str]:
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
        seen = {primary_model.casefold()}
        for model_id in candidates:
            if not model_id or model_id.casefold() in seen:
                continue
            seen.add(model_id.casefold())
            if self.is_model_available(model_id):
                unique.append(model_id)
        likely = [
            model_id for model_id in unique if self._is_likely_vision_model(model_id)
        ]
        return likely or unique

    def client_for_model(self, model_id: str):
        primary_model = self.primary_model_id()
        if model_id == primary_model:
            return self._primary_client
        client = self._clients.get(model_id)
        if client is not None:
            return client
        config = dict(self._llm_config)
        config["model"] = model_id
        client = self._client_factory(config)
        self._clients[model_id] = client
        return client

    # ── Task-aware model selection (deployment-level "model recognition") ──

    def _get_capability_registry(self) -> Optional[Any]:
        if self._capability_registry is None:
            try:
                from artpm_agent.providers.model_registry import (
                    ModelCapabilityRegistry,
                )

                self._capability_registry = ModelCapabilityRegistry(self._llm_config)
            except Exception:  # noqa: BLE001 - selection is best-effort
                return None
        return self._capability_registry

    # ── Layered model selection: LLM task classifier ──

    def _read_task_classifier_flag(self) -> bool:
        env = os.getenv("ARTPM_TASK_CLASSIFIER", "")
        if env.strip().lower() in {"1", "true", "yes", "on"}:
            return True
        return bool(self._llm_config.get("task_classifier_enabled", False))

    def _get_task_classifier(self) -> Optional[Any]:
        if self._task_classifier is None and self._task_classification_enabled:
            try:
                from artpm_agent.routing.task_classifier import LLMTaskClassifier

                self._task_classifier = LLMTaskClassifier(
                    self, enabled=True, config=self._llm_config
                )
            except Exception:  # noqa: BLE001 - classifier is best-effort
                return None
        return self._task_classifier

    def classify_task(
        self, user_input: str, image_paths: Optional[List[str]] = None
    ) -> str:
        """Classify ``user_input`` into a task type for model selection.

        Returns ``"chat"`` when the classifier is disabled or errors, so callers
        that omit ``task_type`` keep today's behaviour.
        """
        classifier = self._get_task_classifier()
        if classifier is None:
            return "chat"
        try:
            return classifier.classify(user_input, image_paths)
        except Exception:  # noqa: BLE001 - never break a turn over classification
            return "chat"

    def best_model_for_task(
        self, task_type: str, *, require_vision: bool = False
    ) -> Optional[str]:
        """Return the most appropriate configured model for a task type.

        Returns ``None`` when no capability table is available, which callers
        treat as "use the default primary model". This keeps the method a safe,
        no-op-compatible enhancement.
        """
        registry = self._get_capability_registry()
        if registry is None:
            return None
        return registry.select_model(
            task_type,
            self.primary_model_id(),
            self.fallback_model_ids(self.primary_model_id() or ""),
            require_vision=require_vision,
        )

    def _attempts_with_preference(
        self, preferred: Optional[str], require_vision: bool
    ) -> List:
        attempts = self.model_attempts(require_vision=require_vision)
        if not preferred or not attempts:
            return attempts
        reordered = [(m, c, f) for (m, c, f) in attempts if m == preferred]
        reordered += [(m, c, f) for (m, c, f) in attempts if m != preferred]
        return reordered

    def _record_telemetry(
        self,
        task_type: Optional[str],
        model_used: Optional[str],
        latency_ms: float,
        fallback: bool,
        cache_hit: bool,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        tel = self._telemetry
        if tel is None:
            return
        try:
            tel.record(
                task_type=task_type or "chat",
                model_used=model_used,
                latency_ms=latency_ms,
                fallback=fallback,
                cache_hit=cache_hit,
                success=success,
                error=error,
            )
        except Exception:  # noqa: BLE001 - telemetry must never break a turn
            pass

    def _build_response_cache(self) -> Optional[Any]:
        try:
            from artpm_agent.providers.response_cache import ResponseCache

            env_on = os.getenv("ARTPM_RESPONSE_CACHE", "")
            enabled = env_on.strip().lower() in {"1", "true", "yes", "on"} or bool(
                self._llm_config.get("response_cache_enabled", False)
            )
            ttl = int(
                os.getenv("ARTPM_RESPONSE_CACHE_TTL", "")
                or self._llm_config.get("response_cache_ttl", 3600)
            )
            return ResponseCache(enabled=enabled, ttl=ttl)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _build_telemetry() -> Optional[Any]:
        try:
            from artpm_agent.runtime.telemetry import AgentTelemetry

            env = os.getenv("ARTPM_TELEMETRY", "")
            if env.strip().lower() in {"0", "false", "no", "off"}:
                return AgentTelemetry(enabled=False)
            return AgentTelemetry(enabled=True)
        except Exception:  # noqa: BLE001
            return None

    def model_attempts(self, *, require_vision: bool = False):
        primary_model = self.primary_model_id()
        if self._primary_client is None:
            return []
        # Some offline integrations create a minimal agent without a persisted
        # model ID. Preserve their original one-client behavior instead of
        # requiring a catalog before an attachment can be analyzed.
        if not primary_model:
            return [("", self._primary_client, False)]

        attempts = []
        if self.is_model_available(primary_model):
            attempts.append((primary_model, self._primary_client, False))
        fallback_ids = (
            self.vision_fallback_model_ids(primary_model)
            if require_vision
            else self.fallback_model_ids(primary_model)
        )
        attempts.extend((model_id, None, True) for model_id in fallback_ids)
        return attempts

    def record_success(
        self, model_id: str, fallback_from: Optional[str] = None
    ) -> None:
        self.last_response_model = model_id or None
        self.last_model_fallback_from = fallback_from
        if model_id:
            self.mark_model_healthy(model_id)

    @staticmethod
    def fallback_notice(primary_model: str, fallback_model: str) -> str:
        return f"已切换备用模型：`{fallback_model}`。\n\n"

    def chat_with_failover(
        self,
        prompt: str,
        system_prompt: str,
        history: Any,
        image_paths: Optional[List[str]] = None,
        *,
        task_type: Optional[str] = None,
    ) -> str:
        primary_model = self.primary_model_id()
        requires_vision = bool(image_paths)

        # Task-aware model selection (deployment-level recognition). When the
        # caller omits task_type AND the classifier is enabled, lazily classify
        # it (opt-in LLM classifier runs on the cheap routing-tier model). When
        # the classifier is disabled (default) we deliberately leave task_type
        # as None so no model-preference reordering happens — this preserves the
        # exact pre-classifier failover ordering (primary first, then fallbacks).
        if task_type is None and self._task_classification_enabled:
            task_type = self.classify_task(prompt, image_paths)
        preferred = (
            self.best_model_for_task(task_type, require_vision=requires_vision)
            if task_type
            else None
        )
        cache_model = preferred or primary_model

        # Response cache: skip the API call for identical requests.
        cache = self._response_cache
        cache_hit = False
        if cache is not None:
            cached = cache.get(cache_model or "", system_prompt, prompt, history, image_paths)
            if cached is not None:
                cache_hit = True
                self._record_telemetry(task_type, cache_model, 0.0, False, True, True)
                return cached

        attempts = self._attempts_with_preference(preferred, requires_vision)
        if not attempts:
            if primary_model:
                raise RuntimeError("模型当前服务繁忙，请稍后重试")
            raise RuntimeError("模型请求失败")

        start = time.monotonic()
        last_error = None
        for model_id, client, is_fallback in attempts:
            try:
                client = client or self.client_for_model(model_id)
                if image_paths:
                    response = client.chat_with_images(
                        prompt,
                        image_paths,
                        system_prompt=system_prompt,
                        history=history,
                    )
                else:
                    response = client.chat(
                        prompt,
                        system_prompt=system_prompt,
                        history=history,
                    )
                if not isinstance(response, str) or not response.strip():
                    raise RuntimeError("模型服务未返回有效回答")
                self.record_success(
                    model_id,
                    primary_model if is_fallback else None,
                )
                answer = response.strip()
                if cache is not None:
                    cache.put(cache_model or "", system_prompt, prompt, history, image_paths, answer)
                latency = (time.monotonic() - start) * 1000
                self._record_telemetry(
                    task_type, model_id, latency, bool(is_fallback), cache_hit, True
                )
                if is_fallback and primary_model:
                    return self.fallback_notice(primary_model, model_id) + answer
                return answer
            except Exception as error:
                last_error = error
                if requires_vision and self._is_vision_capability_error(error):
                    logger.warning(
                        "模型 %s 不支持多模态输入，尝试候选模型",
                        model_id or "当前模型",
                    )
                    continue
                if not self._is_retryable_model_error(error):
                    break
                self.mark_model_unavailable(model_id)
                logger.warning("模型 %s 暂时不可用，尝试候选模型", model_id)

        latency = (time.monotonic() - start) * 1000
        self._record_telemetry(
            task_type, primary_model, latency, False, cache_hit, False,
            error=str(last_error) if last_error else None,
        )
        raise RuntimeError("模型请求失败") from last_error

    def stream_with_failover(
        self,
        user_input,
        system_prompt,
        history,
        image_paths: Optional[List[str]] = None,
        *,
        task_type: Optional[str] = None,
    ):
        primary_model = self.primary_model_id()
        requires_vision = bool(image_paths)

        if task_type is None and self._task_classification_enabled:
            task_type = self.classify_task(user_input)
        preferred = (
            self.best_model_for_task(task_type, require_vision=requires_vision)
            if task_type
            else None
        )
        cache_model = preferred or primary_model

        cache = self._response_cache
        cache_hit = False
        if cache is not None:
            cached = cache.get(cache_model or "", system_prompt, user_input, history, image_paths)
            if cached is not None:
                cache_hit = True
                self._record_telemetry(task_type, cache_model, 0.0, False, True, True)
                yield cached
                return

        attempts = self._attempts_with_preference(preferred, requires_vision)
        if not attempts:
            if primary_model:
                raise RuntimeError("模型当前服务繁忙，请稍后重试")
            raise RuntimeError("模型请求失败")

        start = time.monotonic()
        last_error = None
        for model_id, client, is_fallback in attempts:
            yielded = False
            try:
                client = client or self.client_for_model(model_id)
                stream_fn = getattr(client, "stream_chat_with_images", None)
                if image_paths and stream_fn is not None:
                    chunks = stream_fn(
                        user_input,
                        image_paths,
                        system_prompt=system_prompt,
                        history=history,
                    )
                else:
                    chunks = client.stream_chat(
                        user_input,
                        system_prompt=system_prompt,
                        history=history,
                    )
                for chunk in chunks:
                    if not isinstance(chunk, str) or not chunk:
                        continue
                    if not yielded:
                        self.record_success(
                            model_id,
                            primary_model if is_fallback else None,
                        )
                        if is_fallback and primary_model:
                            yield self.fallback_notice(primary_model, model_id)
                    yielded = True
                    yield chunk
                if not yielded:
                    raise RuntimeError("模型服务未返回有效回答")
                latency = (time.monotonic() - start) * 1000
                self._record_telemetry(
                    task_type, model_id, latency, bool(is_fallback), cache_hit, True
                )
                return
            except Exception as error:
                last_error = error
                if yielded:
                    self.mark_model_unavailable(model_id)
                    logger.warning("模型 %s 在返回部分内容后中断", model_id)
                    raise RuntimeError("模型请求失败") from error
                if not self._is_retryable_model_error(error):
                    break
                self.mark_model_unavailable(model_id)
                logger.warning("模型 %s 流式请求失败，尝试候选模型", model_id)

        latency = (time.monotonic() - start) * 1000
        self._record_telemetry(
            task_type, primary_model, latency, False, cache_hit, False,
            error=str(last_error) if last_error else None,
        )
        raise RuntimeError("模型请求失败") from last_error
