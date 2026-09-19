"""Provider-neutral model gateway.

Owns model selection, cooldown bookkeeping, and failover orchestration so the
agent class no longer manages provider retries. The gateway depends only on a
plain LLM config mapping and a client factory, which means its input/output can
be tested without initializing Streamlit, the database, or any Skill.

Provider capability differences (what counts as a retryable error, what counts
as a vision-capability error) live here rather than leaking into business Skills.
"""

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from threading import RLock, local
from typing import Any, Callable, Dict, List, Mapping, Optional

from artpm_agent.providers.contracts import ModelResponseMetadata
from artpm_agent.providers.gateway_cache import BoundedClientCache
from artpm_agent.providers.health_tracker import ProviderHealthTracker
from artpm_agent.providers.model_selector import ProviderSelector
from artpm_agent.providers.request_limiter import ModelRequestLimiter
from artpm_agent.providers.vision_router import VisionModelRouter
from artpm_agent.runtime.performance import (
    begin_first_model_call,
    finish_first_model_call,
)
from artpm_agent.utils import create_llm_client, get_logger

logger = get_logger(__name__)
ModelAttempt = tuple[str, Any, bool]


class ModelGateway:
    """Selects models, tracks circuit-breaker cooldowns, and fails over.

    The gateway never touches Streamlit, SQLAlchemy, or Skill routing. It speaks
    only to an LLM client (or a factory that builds one) and a config dict.
    """

    MODEL_FAILOVER_COOLDOWN_SECONDS = 60
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

    def __init__(
        self,
        llm_config: Mapping[str, Any],
        primary_client: Any,
        client_factory: Callable[[Dict[str, Any]], Any] = create_llm_client,
        telemetry: Any = None,
        response_cache: Any = None,
    ) -> None:
        self._llm_config = dict(llm_config)
        self._client_factory = client_factory
        self._primary_client = primary_client
        self._state_lock = RLock()
        self._response_state = local()

        # Initialize component modules
        self._health_tracker = ProviderHealthTracker(
            cooldown_seconds=self.MODEL_FAILOVER_COOLDOWN_SECONDS,
            max_entries=self._read_bounded_int(
                "health_cache_max_entries", 64, minimum=1, maximum=512
            ),
        )
        self._model_selector = ProviderSelector(llm_config)
        self._request_limiter = ModelRequestLimiter(
            max_concurrent=self._read_bounded_int(
                "max_concurrent_requests", 8, minimum=1, maximum=64
            ),
            timeout_seconds=self._read_bounded_float(
                "request_queue_timeout_seconds", 30.0, minimum=0.1, maximum=300.0
            ),
        )
        self._vision_router = VisionModelRouter(llm_config, client_factory)
        # Keep the historical attributes available to integrations that read
        # the configured "eyes" model directly.  Routing itself remains owned
        # by VisionModelRouter so the compatibility values cannot affect
        # request selection.
        self._vision_model = self._vision_router._vision_model
        self._vision_provider = self._vision_router._vision_provider

        # Client cache
        self._client_cache_max_entries = self._read_bounded_int(
            "client_cache_max_entries", 8, minimum=1, maximum=64
        )
        self._client_cache_ttl_seconds = self._read_bounded_float(
            "client_cache_ttl_seconds", 900.0, minimum=1.0, maximum=86400.0
        )
        self._clients: BoundedClientCache[Any] = BoundedClientCache(
            max_entries=self._client_cache_max_entries,
            ttl_seconds=self._client_cache_ttl_seconds,
            close_client=self._close_provider_client,
        )

        # Legacy state for backward compatibility
        self._unavailable_until: Dict[str, float] = {}
        self._health_max_entries = self._read_bounded_int(
            "health_cache_max_entries", 64, minimum=1, maximum=512
        )
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
        self._failover_max_attempts = self._read_failover_max_attempts()
        primary_model = str(self._llm_config.get("model", "") or "").strip() or None
        self._default_response_metadata = ModelResponseMetadata(primary_model)
        self._primary_client_model: Optional[str] = (
            primary_model if primary_client is not None else None
        )

        # Vision client state
        self._vision_client: Optional[Any] = None
        self._vision_client_built = False

    def _read_bounded_int(
        self,
        key: str,
        default: int,
        *,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            value = int(self._llm_config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    def _read_bounded_float(
        self,
        key: str,
        default: float,
        *,
        minimum: float,
        maximum: float,
    ) -> float:
        try:
            value = float(self._llm_config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    @staticmethod
    def _close_provider_client(client: Any) -> None:
        close = getattr(client, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception:
            logger.warning("Failed to close model client", exc_info=True)

    @property
    def response_metadata(self) -> ModelResponseMetadata:
        """Return model metadata isolated to the current request thread."""

        return getattr(
            self._response_state,
            "metadata",
            self._default_response_metadata,
        )

    @property
    def last_response_model(self) -> Optional[str]:
        return self.response_metadata.model_id

    @last_response_model.setter
    def last_response_model(self, value: Optional[str]) -> None:
        current = self.response_metadata
        self._response_state.metadata = ModelResponseMetadata(
            str(value).strip() if value else None,
            current.fallback_from,
        )

    @property
    def last_model_fallback_from(self) -> Optional[str]:
        return self.response_metadata.fallback_from

    @last_model_fallback_from.setter
    def last_model_fallback_from(self, value: Optional[str]) -> None:
        current = self.response_metadata
        self._response_state.metadata = ModelResponseMetadata(
            current.model_id,
            str(value).strip() if value else None,
        )

    @contextmanager
    def _model_request_slot(self):
        with self._request_limiter.acquire_slot():
            yield

    def cleanup_clients(self) -> int:
        """Close expired idle model clients."""

        return self._clients.prune()

    def client_cache_snapshot(self) -> dict[str, object]:
        snapshot = self._clients.snapshot()
        snapshot["max_concurrent_requests"] = self._request_limiter.max_concurrent
        return snapshot

    # ── Vision pairing ("eyes model") ──

    def _vision_client_config(self) -> Dict[str, Any]:
        """Assemble a client config for the dedicated vision model."""
        return self._vision_router.vision_client_config()

    def vision_client(self) -> Any:
        """Lazily build the dedicated vision client under the bounded cache."""

        cache_key = self._vision_cache_key()
        try:
            client = self._clients.get_or_create(
                cache_key,
                lambda: self._client_factory(self._vision_client_config()),
            )
        except Exception:
            logger.exception("vision client failed to build")
            client = None
        with self._state_lock:
            self._vision_client_built = True
            self._vision_client = client
        return client

    def _vision_cache_key(self) -> str:
        return self._vision_router.cache_key()

    @contextmanager
    def _vision_client_lease(self, candidate: Any = None):
        with self._clients.lease(
            self._vision_cache_key(),
            lambda: self._client_factory(self._vision_client_config()),
            candidate=candidate,
        ) as client:
            yield client

    def _leased_vision_stream(self, candidate: Any, build_stream: Callable):
        with self._vision_client_lease(candidate) as client:
            with self._model_request_slot():
                yield from build_stream(client)

    @property
    def has_vision_pairing(self) -> bool:
        """True when a dedicated vision model is configured."""
        return self._vision_router.has_vision_model()

    def _chat_with_vision(
        self,
        prompt: str,
        system_prompt: Optional[str],
        history: Any,
        image_paths: List[str],
        *,
        cache_scope: str = "local:default",
    ) -> str:
        """Single-attempt image request through the dedicated vision client.

        When a vision pairing is configured, image requests no longer fall
        back to the text primary model (which may reject images outright);
        they go straight to the vision model (e.g. GLM-4V-Flash).
        """
        if not self._vision_router.has_vision_model():
            raise RuntimeError("视觉模型未配置或不可用，无法识别图片")

        model_id = self._vision_router._vision_model or ""
        client = self.vision_client()
        if client is None:
            raise RuntimeError("视觉模型未配置或不可用，无法识别图片")
        cached, reservation = self._cache_acquire(
            model_id,
            system_prompt or "",
            prompt,
            history,
            image_paths,
            cache_scope,
        )
        if cached is not None:
            self.record_success(model_id, None)
            if reservation:
                self._cache_release(reservation)
            return cached
        try:
            with self._vision_client_lease(client) as active_client:
                with self._model_request_slot():
                    response = active_client.chat_with_images(
                        prompt,
                        image_paths,
                        system_prompt=system_prompt,
                        history=history,
                    )
        except Exception:
            if reservation:
                self._cache_release(reservation)
            raise
        if reservation:
            self._cache_release(reservation)
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError("视觉模型未返回有效回答")
        answer = response.strip()
        self.record_success(model_id, None)
        self._cache_put(
            model_id,
            system_prompt or "",
            prompt,
            history,
            image_paths,
            answer,
            cache_scope,
        )
        return answer

    def _stream_with_vision(
        self,
        user_input: str,
        system_prompt: Optional[str],
        history: Any,
        image_paths: List[str],
        *,
        cache_scope: str = "local:default",
    ) -> Any:
        """Stream an image request through the dedicated vision client."""
        if not self._vision_router.has_vision_model():
            raise RuntimeError("视觉模型未配置或不可用，无法识别图片")

        model_id = self._vision_router._vision_model or ""
        client = self.vision_client()
        if client is None:
            raise RuntimeError("视觉模型未配置或不可用，无法识别图片")
        cached, reservation = self._cache_acquire(
            model_id,
            system_prompt or "",
            user_input,
            history,
            image_paths,
            cache_scope,
        )
        if cached is not None:
            self.record_success(model_id, None)
            if reservation:
                self._cache_release(reservation)
            yield cached
            return
        parts: list[str] = []
        yielded = False
        chunks = None
        try:

            def build_stream(active_client):
                stream_fn = getattr(
                    active_client,
                    "stream_chat_with_images",
                    None,
                )
                if stream_fn is not None:
                    return stream_fn(
                        user_input,
                        image_paths,
                        system_prompt=system_prompt,
                        history=history,
                    )
                return iter(
                    [
                        active_client.chat_with_images(
                            user_input,
                            image_paths,
                            system_prompt=system_prompt,
                            history=history,
                        )
                    ]
                )

            chunks = self._leased_vision_stream(client, build_stream)
            for chunk in chunks:
                if not isinstance(chunk, str) or not chunk:
                    continue
                if not yielded:
                    self.record_success(model_id, None)
                yielded = True
                parts.append(chunk)
                yield chunk
        finally:
            close = getattr(chunks, "close", None)
            if callable(close):
                close()
            if reservation:
                self._cache_release(reservation)
        if parts:
            try:
                self._cache_put(
                    model_id,
                    system_prompt or "",
                    user_input,
                    history,
                    image_paths,
                    "".join(parts),
                    cache_scope,
                )
            except Exception:
                logger.warning("vision stream cache put failed", exc_info=True)

    @staticmethod
    def _model_family(model_id: str) -> str:
        """Return a stable family prefix without assuming vendor naming rules."""
        normalized = str(model_id or "").strip().casefold()
        return re.split(r"[-_:/.]", normalized, maxsplit=1)[0]

    @staticmethod
    def _error_chain_text(error: BaseException) -> str:
        parts: list[str] = []
        current: BaseException | None = error
        seen: set[int] = set()
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
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            raw_status = getattr(current, "status_code", None)
            try:
                status_code = int(raw_status) if raw_status is not None else None
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
        now = time.monotonic()
        with self._state_lock:
            expires_at = self._unavailable_until.get(model_id, 0)
            if expires_at <= now:
                self._unavailable_until.pop(model_id, None)
                return True
            return False

    def mark_model_unavailable(self, model_id: str) -> None:
        if not model_id:
            return
        with self._state_lock:
            now = time.monotonic()
            self._unavailable_until = {
                key: value
                for key, value in self._unavailable_until.items()
                if value > now
            }
            self._unavailable_until[model_id] = (
                now + self.MODEL_FAILOVER_COOLDOWN_SECONDS
            )
            while len(self._unavailable_until) > self._health_max_entries:
                victim = min(
                    self._unavailable_until,
                    key=self._unavailable_until.__getitem__,
                )
                self._unavailable_until.pop(victim, None)

    def mark_model_healthy(self, model_id: str) -> None:
        with self._state_lock:
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
        if model_id == primary_model and model_id == self._primary_client_model:
            return self._primary_client
        return self._clients.get_or_create(
            model_id,
            lambda: self._build_client_for_model(model_id, primary_model),
        )

    def _build_client_for_model(
        self,
        model_id: str,
        primary_model: Optional[str] = None,
    ) -> Any:
        config = dict(self._llm_config)
        config["model"] = model_id
        if model_id != primary_model:
            try:
                primary_timeout = float(config.get("request_timeout_seconds", 12))
                fallback_timeout = float(
                    config.get("failover_request_timeout_seconds", 8)
                )
                config["request_timeout_seconds"] = min(
                    primary_timeout,
                    fallback_timeout,
                )
            except (TypeError, ValueError):
                config["request_timeout_seconds"] = 8
        return self._client_factory(config)

    @contextmanager
    def _model_client_lease(self, model_id: str, candidate: Any = None):
        primary_model = self.primary_model_id()
        if (
            candidate is None
            and model_id == primary_model
            and model_id == self._primary_client_model
        ):
            candidate = self._primary_client
        if candidate is self._primary_client and candidate is not None:
            yield candidate
            return
        cache_key = model_id or "__primary__"
        with self._clients.lease(
            cache_key,
            lambda: self._build_client_for_model(model_id, primary_model),
            candidate=candidate,
        ) as client:
            yield client

    def _leased_stream(self, model_id: str, candidate: Any, build_stream: Callable):
        """Hold both the client lease and concurrency slot while streaming."""

        with self._model_client_lease(model_id, candidate) as client:
            with self._model_request_slot():
                yield from build_stream(client)

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

    def _read_failover_max_attempts(self) -> int:
        """Bound total provider attempts so one turn cannot time out N times."""

        raw_value = os.getenv("LLM_FAILOVER_MAX_ATTEMPTS", "") or self._llm_config.get(
            "failover_max_attempts", 2
        )
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = 2
        return max(1, min(value, 8))

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
            result = classifier.classify(user_input, image_paths)
            return str(result or "chat")
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
        selected = registry.select_model(
            task_type,
            self.primary_model_id(),
            self.fallback_model_ids(self.primary_model_id() or ""),
            require_vision=require_vision,
        )
        return str(selected) if selected else None

    def _attempts_with_preference(
        self, preferred: Optional[str], require_vision: bool
    ) -> list[ModelAttempt]:
        attempts = self.model_attempts(require_vision=require_vision)
        if preferred and attempts:
            reordered = [(m, c, f) for (m, c, f) in attempts if m == preferred]
            reordered += [(m, c, f) for (m, c, f) in attempts if m != preferred]
            attempts = reordered
        return attempts[: self._failover_max_attempts]

    def _record_telemetry(
        self,
        task_type: Optional[str],
        model_used: Optional[str],
        latency_ms: float,
        fallback: bool,
        cache_hit: bool,
        success: bool,
        error: Optional[str] = None,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
        cost_usd: float = 0.0,
        attempt: int = 0,
        error_type: str = "",
        http_status: Optional[int] = None,
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
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cost_usd=cost_usd,
                provider=self._provider(),
                endpoint=self._endpoint(),
                attempt=attempt,
                error_type=error_type,
                http_status=http_status,
            )
        except Exception:  # noqa: BLE001 - telemetry must never break a turn
            pass

    # ── Observability helpers (token + connection) ──

    def _provider(self) -> str:
        return str(self._llm_config.get("provider", "") or "").strip().lower()

    def _endpoint(self) -> str:
        provider = self._provider()
        for key in (f"{provider}_api_base", "base_url", "api_base", "openai_api_base"):
            val = str(self._llm_config.get(key, "") or "").strip()
            if val:
                return val
        return ""

    def _cache_model_contract(self, model_id: Optional[str]) -> str:
        """Fingerprint response-affecting settings without exposing credentials."""

        contract = {
            "model": str(model_id or ""),
            "provider": self._provider(),
            "endpoint": self._endpoint(),
            "framework": self._llm_config.get("framework"),
            "temperature": self._llm_config.get("temperature"),
            "max_tokens": self._llm_config.get("max_tokens"),
            "top_p": self._llm_config.get("top_p"),
            "seed": self._llm_config.get("seed"),
            "response_format": self._llm_config.get("response_format"),
        }
        raw = json.dumps(contract, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"{model_id or ''}:{digest}"

    def _cache_get(
        self,
        model_id: Optional[str],
        system_prompt: str,
        prompt: str,
        history: Any,
        image_paths: Optional[List[str]],
        cache_scope: str,
    ) -> Optional[str]:
        cache = self._response_cache
        if cache is None or not callable(getattr(cache, "get", None)):
            return None
        args = (
            self._cache_model_contract(model_id),
            system_prompt,
            prompt,
            history,
            image_paths,
        )
        try:
            result = cache.get(*args, cache_scope)
            return result if isinstance(result, str) else None
        except TypeError:
            # Compatibility with injected cache implementations using the v1 API.
            try:
                result = cache.get(*args)
                return result if isinstance(result, str) else None
            except Exception:
                logger.debug("Response cache read failed", exc_info=True)
                return None
        except Exception:
            logger.debug("Response cache read failed", exc_info=True)
            return None

    def _cache_put(
        self,
        model_id: Optional[str],
        system_prompt: str,
        prompt: str,
        history: Any,
        image_paths: Optional[List[str]],
        value: str,
        cache_scope: str,
    ) -> None:
        cache = self._response_cache
        if cache is None or not callable(getattr(cache, "put", None)):
            return
        args = (
            self._cache_model_contract(model_id),
            system_prompt,
            prompt,
            history,
            image_paths,
            value,
        )
        try:
            cache.put(*args, cache_scope)
        except TypeError:
            try:
                cache.put(*args)
            except Exception:
                logger.debug("Response cache write failed", exc_info=True)
        except Exception:
            logger.debug("Response cache write failed", exc_info=True)

    def _cache_acquire(
        self,
        model_id: Optional[str],
        system_prompt: str,
        prompt: str,
        history: Any,
        image_paths: Optional[List[str]],
        cache_scope: str,
    ) -> tuple[Optional[str], Optional[str]]:
        cache = self._response_cache
        acquire = getattr(cache, "acquire", None)
        if not callable(acquire):
            return (
                self._cache_get(
                    model_id,
                    system_prompt,
                    prompt,
                    history,
                    image_paths,
                    cache_scope,
                ),
                None,
            )
        try:
            result = acquire(
                self._cache_model_contract(model_id),
                system_prompt,
                prompt,
                history,
                image_paths,
                cache_scope,
            )
            if isinstance(result, tuple) and len(result) == 2:
                return result
        except Exception:
            logger.debug("Response cache reservation failed", exc_info=True)
        return None, None

    def _cache_release(self, reservation: Optional[str]) -> None:
        if not reservation:
            return
        release = getattr(self._response_cache, "release", None)
        if callable(release):
            try:
                release(reservation)
            except Exception:
                logger.debug("Response cache reservation release failed", exc_info=True)

    def response_cache_stats(self) -> Dict[str, Any]:
        cache = self._response_cache
        stats = getattr(cache, "stats", None)
        if callable(stats):
            try:
                result = stats()
                return dict(result) if isinstance(result, Mapping) else {}
            except Exception:
                return {}
        return {"enabled": bool(getattr(cache, "enabled", False))}

    @staticmethod
    def _http_status(error: BaseException) -> Optional[int]:
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            raw_status = getattr(current, "status_code", None)
            try:
                status_code = int(raw_status) if raw_status is not None else None
            except (TypeError, ValueError):
                status_code = None
            if status_code is not None:
                return status_code
            current = current.__cause__ or current.__context__
        return None

    def _classify_error(self, error: BaseException) -> str:
        status = self._http_status(error)
        if status is not None:
            if status in (401, 403):
                return "auth"
            if status == 404:
                return "not_found"
            if status == 429:
                return "rate_limit"
            if status >= 500:
                return "server_error"
        text = self._error_chain_text(error)
        if any(
            m in text
            for m in ("invalid api key", "authentication", "unauthorized", "forbidden")
        ):
            return "auth"
        if self._is_vision_capability_error(error):
            return "vision_unsupported"
        if any(m in text for m in ("rate limit", "too many requests", "429")):
            return "rate_limit"
        if any(
            m in text
            for m in (
                "timeout",
                "timed out",
                "连接失败",
                "超时",
                "connection",
                "connecterror",
                "reset",
            )
        ):
            return "timeout"
        if any(m in text for m in ("model not found", "unsupported model", "404")):
            return "not_found"
        if any(
            m in text
            for m in (
                "500",
                "503",
                "service unavailable",
                "服务繁忙",
                "overloaded",
                "capacity",
            )
        ):
            return "server_error"
        return "other"

    @staticmethod
    def _extract_real_usage(client: Any) -> Optional[Dict[str, int]]:
        usage = getattr(client, "last_usage", None)
        if not isinstance(usage, dict):
            return None

        def _int(v: Any, key: str) -> int:
            try:
                return int(usage.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0

        pt = _int(usage, "prompt_tokens")
        ct = _int(usage, "completion_tokens")
        cached = max(
            _int(usage, "cached_tokens"),
            _int(usage, "prompt_cache_hit_tokens"),
            _int(usage, "cache_read_input_tokens"),
        )
        if pt or ct:
            return {
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "cached_tokens": cached,
            }
        return None

    def _estimate_usage(
        self,
        *,
        system_prompt: str,
        prompt: str,
        history: Any,
        output_text: str,
        model: Optional[str],
        client: Any = None,
        cache_hit: bool = False,
    ) -> Dict[str, Any]:
        """Token + cost accounting for one request.

        Prefers a real ``client.last_usage`` dict when the client exposes one;
        otherwise falls back to a text-length heuristic. Never raises.
        """
        real = self._extract_real_usage(client) if client is not None else None
        try:
            if real is not None:
                prompt_tokens = real["prompt_tokens"]
                completion_tokens = real["completion_tokens"]
            else:
                from artpm_agent.harness.token_budget import estimate_tokens

                input_text = "{sys}\n{usr}\n{hist}".format(
                    sys=str(system_prompt or ""),
                    usr=str(prompt or ""),
                    hist=str(history if history is not None else ""),
                )
                prompt_tokens = estimate_tokens(input_text, model=model or "")
                completion_tokens = estimate_tokens(output_text, model=model or "")
        except Exception:  # noqa: BLE001 - estimation must never break a turn
            prompt_tokens = 0
            completion_tokens = 0
        try:
            from artpm_agent.runtime.pricing import estimate_cost

            cost = (
                0.0
                if cache_hit
                else estimate_cost(
                    model or "",
                    prompt_tokens,
                    completion_tokens,
                    provider=self._provider(),
                )
            )
        except Exception:  # noqa: BLE001
            cost = 0.0
        cached_tokens = (
            prompt_tokens + completion_tokens
            if cache_hit
            else int((real or {}).get("cached_tokens", 0) or 0)
        )
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": cost,
        }

    def _record_connection_attempt(
        self,
        *,
        model_id: Optional[str],
        attempt_index: int,
        ok: bool,
        error: Optional[BaseException],
        latency_ms: float,
        task_type: Optional[str],
    ) -> None:
        tel = self._telemetry
        if tel is None:
            return
        try:
            tel.record_connection(
                provider=self._provider(),
                model=model_id or "",
                endpoint=self._endpoint(),
                attempt=attempt_index,
                ok=ok,
                error_type=self._classify_error(error) if error is not None else "",
                http_status=self._http_status(error) if error is not None else None,
                latency_ms=latency_ms,
                task_type=task_type or "chat",
            )
        except Exception:  # noqa: BLE001
            pass

    def _build_response_cache(self) -> Optional[Any]:
        try:
            from artpm_agent.providers.response_cache import ResponseCache

            env_on = os.getenv("ARTPM_RESPONSE_CACHE", "")
            enabled = (
                env_on.strip().lower() in {"1", "true", "yes", "on"}
                if env_on.strip()
                else bool(self._llm_config.get("response_cache_enabled", False))
            )

            def _bounded_int(
                env_name: str,
                config_name: str,
                default: int,
                limit: int,
            ) -> int:
                raw = os.getenv(env_name, "") or self._llm_config.get(
                    config_name, default
                )
                try:
                    value = int(raw)
                except (TypeError, ValueError):
                    value = default
                return max(0, min(value, limit))

            ttl = _bounded_int(
                "ARTPM_RESPONSE_CACHE_TTL",
                "response_cache_ttl",
                1800,
                86_400,
            )
            max_size = max(
                1,
                _bounded_int(
                    "ARTPM_RESPONSE_CACHE_MAX_SIZE",
                    "response_cache_max_size",
                    512,
                    20_000,
                ),
            )
            redis_env = os.getenv("ARTPM_RESPONSE_CACHE_REDIS", "")
            redis_enabled = (
                redis_env.strip().lower() in {"1", "true", "yes", "on"}
                if redis_env.strip()
                else bool(self._llm_config.get("response_cache_redis_enabled", True))
            )
            return ResponseCache(
                enabled=enabled,
                ttl=ttl,
                max_size=max_size,
                redis_enabled=redis_enabled,
            )
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

    def model_attempts(self, *, require_vision: bool = False) -> list[ModelAttempt]:
        primary_model = self.primary_model_id()
        if self._primary_client is None:
            return []
        # Some offline integrations create a minimal agent without a persisted
        # model ID. Preserve their original one-client behavior instead of
        # requiring a catalog before an attachment can be analyzed.
        if not primary_model:
            return [("", self._primary_client, False)]

        attempts: list[ModelAttempt] = []
        if self.is_model_available(primary_model):
            primary_client = (
                self._primary_client
                if primary_model == self._primary_client_model
                else self._clients.get(primary_model)
            )
            attempts.append((primary_model, primary_client, False))
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
        self._response_state.metadata = ModelResponseMetadata(
            model_id or None,
            fallback_from,
        )
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
        cache_scope: str = "local:default",
    ) -> str:
        primary_model = self.primary_model_id()
        vision_paths = image_paths or []
        requires_vision = bool(vision_paths)

        # "Eyes model" pairing: when a dedicated vision model is configured,
        # image requests bypass the text primary/failover path entirely and
        # go straight to the vision client (e.g. GLM-4V-Flash).
        if requires_vision and self.has_vision_pairing:
            return self._chat_with_vision(
                prompt,
                system_prompt,
                history,
                vision_paths,
                cache_scope=cache_scope,
            )

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
        cache_hit = False
        attempts = self._attempts_with_preference(preferred, requires_vision)
        if not attempts:
            if primary_model:
                raise RuntimeError("模型当前服务繁忙，请稍后重试")
            raise RuntimeError("模型请求失败")

        start = time.monotonic()
        last_error = None
        for i, (model_id, client, is_fallback) in enumerate(attempts):
            attempt_start = time.monotonic()
            reservation: Optional[str] = None
            try:
                cached, reservation = self._cache_acquire(
                    model_id,
                    system_prompt,
                    prompt,
                    history,
                    image_paths,
                    cache_scope,
                )
                if cached is not None:
                    cache_hit = True
                    self.record_success(
                        model_id,
                        primary_model if is_fallback else None,
                    )
                    latency = (time.monotonic() - attempt_start) * 1000
                    usage = self._estimate_usage(
                        system_prompt=system_prompt,
                        prompt=prompt,
                        history=history,
                        output_text=cached,
                        model=model_id,
                        cache_hit=True,
                    )
                    self._record_telemetry(
                        task_type,
                        model_id,
                        latency,
                        bool(is_fallback),
                        True,
                        True,
                        prompt_tokens=usage["prompt_tokens"],
                        completion_tokens=usage["completion_tokens"],
                        cached_tokens=usage["cached_tokens"],
                        cost_usd=usage["cost_usd"],
                        attempt=i,
                    )
                    if is_fallback and primary_model:
                        return self.fallback_notice(primary_model, model_id) + cached
                    return cached

                if model_id and not self.is_model_available(model_id):
                    continue
                with self._model_client_lease(model_id, client) as leased_client:
                    client = leased_client
                    first_call = begin_first_model_call()
                    try:
                        with self._model_request_slot():
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
                    except BaseException:
                        finish_first_model_call(first_call, success=False)
                        raise
                    finish_first_model_call(first_call, success=True)
                if not isinstance(response, str) or not response.strip():
                    raise RuntimeError("模型服务未返回有效回答")
                self.record_success(
                    model_id,
                    primary_model if is_fallback else None,
                )
                answer = response.strip()
                self._cache_put(
                    model_id,
                    system_prompt,
                    prompt,
                    history,
                    image_paths,
                    answer,
                    cache_scope,
                )
                latency = (time.monotonic() - attempt_start) * 1000
                self._record_connection_attempt(
                    model_id=model_id,
                    attempt_index=i,
                    ok=True,
                    error=None,
                    latency_ms=latency,
                    task_type=task_type,
                )
                usage = self._estimate_usage(
                    system_prompt=system_prompt,
                    prompt=prompt,
                    history=history,
                    output_text=answer,
                    model=model_id,
                    client=client,
                    cache_hit=False,
                )
                self._record_telemetry(
                    task_type,
                    model_id,
                    latency,
                    bool(is_fallback),
                    cache_hit,
                    True,
                    prompt_tokens=usage["prompt_tokens"],
                    completion_tokens=usage["completion_tokens"],
                    cached_tokens=usage["cached_tokens"],
                    cost_usd=usage["cost_usd"],
                    attempt=i,
                )
                if is_fallback and primary_model:
                    return self.fallback_notice(primary_model, model_id) + answer
                return answer
            except Exception as error:
                last_error = error
                attempt_latency = (time.monotonic() - attempt_start) * 1000
                self._record_connection_attempt(
                    model_id=model_id,
                    attempt_index=i,
                    ok=False,
                    error=error,
                    latency_ms=attempt_latency,
                    task_type=task_type,
                )
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
            finally:
                self._cache_release(reservation)

        latency = (time.monotonic() - start) * 1000
        error_type = self._classify_error(last_error) if last_error is not None else ""
        http_status = self._http_status(last_error) if last_error is not None else None
        self._record_telemetry(
            task_type,
            primary_model,
            latency,
            False,
            cache_hit,
            False,
            error=str(last_error) if last_error else None,
            error_type=error_type,
            http_status=http_status,
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
        cache_scope: str = "local:default",
    ):
        primary_model = self.primary_model_id()
        vision_paths = image_paths or []
        requires_vision = bool(vision_paths)

        # "Eyes model" pairing: image streams go straight to the vision model.
        if requires_vision and self.has_vision_pairing:
            yield from self._stream_with_vision(
                user_input,
                system_prompt,
                history,
                vision_paths,
                cache_scope=cache_scope,
            )
            return

        if task_type is None and self._task_classification_enabled:
            task_type = self.classify_task(user_input, image_paths)
        preferred = (
            self.best_model_for_task(task_type, require_vision=requires_vision)
            if task_type
            else None
        )
        cache_hit = False
        attempts = self._attempts_with_preference(preferred, requires_vision)
        if not attempts:
            if primary_model:
                raise RuntimeError("模型当前服务繁忙，请稍后重试")
            raise RuntimeError("模型请求失败")

        start = time.monotonic()
        last_error = None
        for i, (model_id, client, is_fallback) in enumerate(attempts):
            yielded = False
            attempt_start = time.monotonic()
            reservation: Optional[str] = None
            try:
                cached, reservation = self._cache_acquire(
                    model_id,
                    system_prompt,
                    user_input,
                    history,
                    image_paths,
                    cache_scope,
                )
                if cached is not None:
                    cache_hit = True
                    self.record_success(
                        model_id,
                        primary_model if is_fallback else None,
                    )
                    latency = (time.monotonic() - attempt_start) * 1000
                    usage = self._estimate_usage(
                        system_prompt=system_prompt,
                        prompt=user_input,
                        history=history,
                        output_text=cached,
                        model=model_id,
                        cache_hit=True,
                    )
                    self._record_telemetry(
                        task_type,
                        model_id,
                        latency,
                        bool(is_fallback),
                        True,
                        True,
                        prompt_tokens=usage["prompt_tokens"],
                        completion_tokens=usage["completion_tokens"],
                        cached_tokens=usage["cached_tokens"],
                        cost_usd=usage["cost_usd"],
                        attempt=i,
                    )
                    if is_fallback and primary_model:
                        yield self.fallback_notice(primary_model, model_id)
                    yield cached
                    return

                if model_id and not self.is_model_available(model_id):
                    continue
                first_call = begin_first_model_call()
                leased_client: list[Any] = []

                def build_stream(active_client):
                    leased_client.append(active_client)
                    stream_fn = getattr(
                        active_client,
                        "stream_chat_with_images",
                        None,
                    )
                    if image_paths and stream_fn is not None:
                        return stream_fn(
                            user_input,
                            image_paths,
                            system_prompt=system_prompt,
                            history=history,
                        )
                    return active_client.stream_chat(
                        user_input,
                        system_prompt=system_prompt,
                        history=history,
                    )

                chunks = self._leased_stream(model_id, client, build_stream)
                parts = []
                chunks = iter(chunks)
                try:
                    for chunk in chunks:
                        if not isinstance(chunk, str) or not chunk:
                            continue
                        if not yielded:
                            finish_first_model_call(first_call, success=True)
                            first_call = None
                            self.record_success(
                                model_id,
                                primary_model if is_fallback else None,
                            )
                            if is_fallback and primary_model:
                                yield self.fallback_notice(primary_model, model_id)
                        yielded = True
                        parts.append(chunk)
                        yield chunk
                finally:
                    if first_call is not None:
                        finish_first_model_call(first_call, success=False)
                    close = getattr(chunks, "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            logger.warning(
                                "Failed to close model response stream",
                                exc_info=True,
                            )
                if not yielded:
                    raise RuntimeError("模型服务未返回有效回答")
                answer_text = "".join(parts)
                self._cache_put(
                    model_id,
                    system_prompt,
                    user_input,
                    history,
                    image_paths,
                    answer_text,
                    cache_scope,
                )
                latency = (time.monotonic() - attempt_start) * 1000
                self._record_connection_attempt(
                    model_id=model_id,
                    attempt_index=i,
                    ok=True,
                    error=None,
                    latency_ms=latency,
                    task_type=task_type,
                )
                if leased_client:
                    client = leased_client[-1]
                usage = self._estimate_usage(
                    system_prompt=system_prompt,
                    prompt=user_input,
                    history=history,
                    output_text=answer_text,
                    model=model_id,
                    client=client,
                    cache_hit=False,
                )
                self._record_telemetry(
                    task_type,
                    model_id,
                    latency,
                    bool(is_fallback),
                    cache_hit,
                    True,
                    prompt_tokens=usage["prompt_tokens"],
                    completion_tokens=usage["completion_tokens"],
                    cached_tokens=usage["cached_tokens"],
                    cost_usd=usage["cost_usd"],
                    attempt=i,
                )
                return
            except Exception as error:
                last_error = error
                attempt_latency = (time.monotonic() - attempt_start) * 1000
                self._record_connection_attempt(
                    model_id=model_id,
                    attempt_index=i,
                    ok=False,
                    error=error,
                    latency_ms=attempt_latency,
                    task_type=task_type,
                )
                if yielded:
                    self.mark_model_unavailable(model_id)
                    logger.warning("模型 %s 在返回部分内容后中断", model_id)
                    raise RuntimeError("模型请求失败") from error
                if not self._is_retryable_model_error(error):
                    break
                self.mark_model_unavailable(model_id)
                logger.warning("模型 %s 流式请求失败，尝试候选模型", model_id)
            finally:
                self._cache_release(reservation)

        latency = (time.monotonic() - start) * 1000
        error_type = self._classify_error(last_error) if last_error is not None else ""
        http_status = self._http_status(last_error) if last_error is not None else None
        self._record_telemetry(
            task_type,
            primary_model,
            latency,
            False,
            cache_hit,
            False,
            error=str(last_error) if last_error else None,
            error_type=error_type,
            http_status=http_status,
        )
        raise RuntimeError("模型请求失败") from last_error

    def close(self) -> None:
        """Close every distinct cached provider client exactly once."""
        cached_clients = list(self._clients.values())
        cached_ids = {id(client) for client in cached_clients}
        primary_client = self._primary_client
        self._clients.close_all()
        self._primary_client = None
        self._primary_client_model = None
        self._vision_client = None
        if primary_client is not None and id(primary_client) not in cached_ids:
            self._close_provider_client(primary_client)
