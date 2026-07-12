"""Optional client for an Unlimited-OCR OpenAI-compatible service.

The adapter deliberately contains no model runtime dependencies. It validates local
images with the application's shared image boundary and sends only data URIs to the
configured service. Remote image URLs are opt-in and reject non-public destinations.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import socket
from time import monotonic
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

from .image_validation import MAX_IMAGE_FILE_SIZE, load_validated_image


DEFAULT_MODEL = "Unlimited-OCR"
DEFAULT_PROMPT = "document parsing."
DEFAULT_MULTI_PAGE_PROMPT = "Multi page parsing."
MAX_IMAGES = 32
MAX_TOTAL_IMAGE_BYTES = 64 * 1024 * 1024
MAX_PROCESSOR_CHARS = 512 * 1024


class UnlimitedOCRError(RuntimeError):
    """Base error raised by strict Unlimited-OCR operations."""


class UnlimitedOCRConfigurationError(UnlimitedOCRError):
    """The adapter configuration is incomplete or unsafe."""


class UnlimitedOCRRequestError(UnlimitedOCRError):
    """The configured service could not complete the request."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def normalize_unlimited_ocr_base_url(value: str) -> str:
    """Validate a configured service root and preserve an optional ``/v1`` path."""
    candidate = (value or "").strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnlimitedOCRConfigurationError(
            "Unlimited-OCR Base URL must be a complete http(s) URL"
        )
    if parsed.username or parsed.password:
        raise UnlimitedOCRConfigurationError(
            "Unlimited-OCR Base URL cannot contain credentials"
        )
    if parsed.query or parsed.fragment:
        raise UnlimitedOCRConfigurationError(
            "Unlimited-OCR Base URL cannot contain a query or fragment"
        )
    try:
        port = parsed.port
    except ValueError as error:
        raise UnlimitedOCRConfigurationError(
            "Unlimited-OCR Base URL has an invalid port"
        ) from error

    host = parsed.hostname.rstrip(".").lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_unspecified
        or address.is_multicast
        or address.is_link_local
        or address.is_reserved
    ):
        raise UnlimitedOCRConfigurationError(
            "Unlimited-OCR Base URL uses an unsafe network address"
        )

    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def validate_custom_logit_processor(value: str) -> str:
    """Validate, but never deserialize, a trusted SGLang processor payload."""
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    if len(candidate) > MAX_PROCESSOR_CHARS:
        raise UnlimitedOCRConfigurationError(
            "custom logit processor exceeds the size limit"
        )
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise UnlimitedOCRConfigurationError(
            "custom logit processor must be valid JSON"
        ) from error
    if not isinstance(payload, dict) or set(payload) != {"callable"}:
        raise UnlimitedOCRConfigurationError(
            "custom logit processor must contain only callable"
        )
    serialized = payload.get("callable")
    if (
        not isinstance(serialized, str)
        or not serialized
        or len(serialized) % 2
        or any(char not in "0123456789abcdefABCDEF" for char in serialized)
    ):
        raise UnlimitedOCRConfigurationError(
            "custom logit processor callable must be a hex string"
        )
    return candidate


def validate_remote_image_url(
    value: str,
    *,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> str:
    """Reject remote image URLs that may target local or special networks."""
    candidate = (value or "").strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnlimitedOCRConfigurationError(
            "Remote image must use a complete http(s) URL"
        )
    if parsed.username or parsed.password:
        raise UnlimitedOCRConfigurationError(
            "Remote image URL cannot contain credentials"
        )
    if parsed.fragment:
        raise UnlimitedOCRConfigurationError(
            "Remote image URL cannot contain a fragment"
        )
    try:
        port = parsed.port
    except ValueError as error:
        raise UnlimitedOCRConfigurationError(
            "Remote image URL has an invalid port"
        ) from error

    host = parsed.hostname.rstrip(".").lower()
    if (
        host == "localhost"
        or host.endswith((".localhost", ".local"))
        or "." not in host
    ):
        raise UnlimitedOCRConfigurationError("Remote image URL must use a public host")

    try:
        literal = ipaddress.ip_address(host)
        addresses = {literal}
    except ValueError:
        try:
            resolved = resolver(host, port or (443 if parsed.scheme == "https" else 80))
        except OSError as error:
            raise UnlimitedOCRConfigurationError(
                "Remote image host could not be resolved safely"
            ) from error
        addresses = set()
        for item in resolved:
            try:
                addresses.add(ipaddress.ip_address(item[4][0]))
            except (IndexError, TypeError, ValueError):
                continue

    if not addresses or any(not address.is_global for address in addresses):
        raise UnlimitedOCRConfigurationError(
            "Remote image URL resolves to a non-public network"
        )
    return candidate


@dataclass(frozen=True)
class UnlimitedOCRConfig:
    """Configuration for an independently deployed Unlimited-OCR service."""

    enabled: bool = False
    base_url: str = ""
    model: str = DEFAULT_MODEL
    timeout: float = 120.0
    max_output: int = 32768
    allow_remote: bool = False
    api_key: str = ""
    # These values mirror the official Unlimited-OCR examples. The serialized
    # processor is optional because it is generated by the SGLang runtime and
    # should never be fabricated by this dependency-free client.
    no_repeat_ngram_size: int = 35
    ngram_window_single: int = 128
    ngram_window_multi: int = 1024
    custom_logit_processor: str = ""

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "UnlimitedOCRConfig":
        source: Mapping[str, Any] = values or {}
        nested = source.get("unlimited_ocr")
        if isinstance(nested, Mapping):
            source = nested
        return cls(
            enabled=_as_bool(source.get("enabled")),
            base_url=str(source.get("base_url") or "").strip(),
            model=str(source.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            timeout=_bounded_float(source.get("timeout"), 120.0, 1.0, 1200.0),
            max_output=_bounded_int(source.get("max_output"), 32768, 1, 131072),
            allow_remote=_as_bool(source.get("allow_remote")),
            api_key=str(source.get("api_key") or "").strip(),
            no_repeat_ngram_size=_bounded_int(
                source.get("no_repeat_ngram_size"), 35, 0, 256
            ),
            ngram_window_single=_bounded_int(
                source.get("ngram_window_single"), 128, 0, 8192
            ),
            ngram_window_multi=_bounded_int(
                source.get("ngram_window_multi"), 1024, 0, 8192
            ),
            custom_logit_processor=str(
                source.get("custom_logit_processor") or ""
            ).strip(),
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "UnlimitedOCRConfig":
        env = os.environ if environ is None else environ
        return cls.from_mapping(
            {
                "enabled": env.get("UNLIMITED_OCR_ENABLED", "true"),
                "base_url": env.get(
                    "UNLIMITED_OCR_BASE_URL", "http://127.0.0.1:10000"
                ),
                "model": env.get("UNLIMITED_OCR_MODEL"),
                "timeout": env.get("UNLIMITED_OCR_TIMEOUT"),
                "max_output": env.get("UNLIMITED_OCR_MAX_OUTPUT"),
                "allow_remote": env.get("UNLIMITED_OCR_ALLOW_REMOTE"),
                "api_key": env.get("UNLIMITED_OCR_API_KEY"),
                "no_repeat_ngram_size": env.get("UNLIMITED_OCR_NGRAM_SIZE"),
                "ngram_window_single": env.get("UNLIMITED_OCR_NGRAM_WINDOW_SINGLE"),
                "ngram_window_multi": env.get("UNLIMITED_OCR_NGRAM_WINDOW_MULTI"),
                "custom_logit_processor": env.get(
                    "UNLIMITED_OCR_CUSTOM_LOGIT_PROCESSOR"
                ),
            }
        )


@dataclass(frozen=True)
class UnlimitedOCRResult:
    """A successful OCR response or a safe, non-throwing degradation result."""

    text: str = ""
    ok: bool = False
    degraded: bool = False
    source: str = "unlimited-ocr"
    error: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class UnlimitedOCRHealth:
    """Read-only service health information."""

    configured: bool
    available: bool
    model_available: bool = False
    detail: str = ""


Fallback = Callable[[str, list[str]], str | UnlimitedOCRResult]


class UnlimitedOCRClient:
    """Small HTTP client for an OpenAI-compatible Unlimited-OCR deployment."""

    def __init__(
        self,
        config: UnlimitedOCRConfig | Mapping[str, Any] | None = None,
        *,
        opener: OpenerDirector | None = None,
        remote_resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    ) -> None:
        if isinstance(config, UnlimitedOCRConfig):
            self.config = config
        else:
            self.config = UnlimitedOCRConfig.from_mapping(config)
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirectHandler())
        self._remote_resolver = remote_resolver
        self._ready_checked_at = 0.0
        self._ready_available = False

    @property
    def configured(self) -> bool:
        if not self.config.enabled or not self.config.base_url.strip():
            return False
        try:
            normalize_unlimited_ocr_base_url(self.config.base_url)
        except UnlimitedOCRConfigurationError:
            return False
        return True

    def ready(self, *, cache_ttl: float = 30.0) -> bool:
        """Fast, cached liveness probe used by automatic skill dispatch."""
        if not self.configured:
            return False
        now = monotonic()
        if now - self._ready_checked_at < max(0.0, cache_ttl):
            return self._ready_available
        available = False
        try:
            request = Request(
                self._health_endpoint(), headers=self._headers(), method="GET"
            )
            with self._opener.open(
                request, timeout=min(self.config.timeout, 2.0)
            ) as response:
                raw = response.read(64 * 1024 + 1)
                available = len(raw) <= 64 * 1024
        except (
            UnlimitedOCRError,
            HTTPError,
            URLError,
            OSError,
            ValueError,
        ):
            available = False
        self._ready_checked_at = now
        self._ready_available = available
        return available

    def _endpoint(self, resource: str) -> str:
        base = normalize_unlimited_ocr_base_url(self.config.base_url)
        if base.endswith("/v1"):
            return f"{base}/{resource.lstrip('/')}"
        return f"{base}/v1/{resource.lstrip('/')}"

    def _health_endpoint(self) -> str:
        base = normalize_unlimited_ocr_base_url(self.config.base_url)
        if base.endswith("/v1"):
            base = base[:-3]
        return f"{base.rstrip('/')}/health"

    def _headers(self, *, stream: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "text/event-stream" if stream else "application/json",
            "Content-Type": "application/json",
        }
        if self.config.api_key:
            if "\r" in self.config.api_key or "\n" in self.config.api_key:
                raise UnlimitedOCRConfigurationError("Unlimited-OCR API key is invalid")
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def health(self) -> UnlimitedOCRHealth:
        if not self.config.enabled:
            return UnlimitedOCRHealth(False, False, detail="disabled")
        if not self.config.base_url.strip():
            return UnlimitedOCRHealth(False, False, detail="base_url is not configured")
        if not self.configured:
            return UnlimitedOCRHealth(False, False, detail="invalid configuration")
        try:
            # /health is a liveness endpoint. Do not infer model availability
            # from its body; official SGLang deployments may return plain text.
            request = Request(
                self._health_endpoint(), headers=self._headers(), method="GET"
            )
            with self._opener.open(
                request, timeout=min(self.config.timeout, 5.0)
            ) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise UnlimitedOCRRequestError(
                        "health response exceeded the size limit"
                    )
        except (
            UnlimitedOCRError,
            HTTPError,
            URLError,
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            return UnlimitedOCRHealth(True, False, detail="service unavailable")

        try:
            models_request = Request(
                self._endpoint("models"), headers=self._headers(), method="GET"
            )
            with self._opener.open(
                models_request, timeout=min(self.config.timeout, 15.0)
            ) as response:
                models_raw = response.read(1024 * 1024 + 1)
                if len(models_raw) > 1024 * 1024:
                    raise UnlimitedOCRRequestError("model response exceeded the size limit")
            payload = json.loads(models_raw.decode("utf-8"))
            items = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise UnlimitedOCRRequestError("model response is invalid")
            models = {
                str(item.get("id", ""))
                for item in items
                if isinstance(item, dict) and item.get("id")
            }
            return UnlimitedOCRHealth(
                configured=True,
                available=True,
                model_available=self.config.model in models,
                detail="ok" if self.config.model in models else "service online; model ID not found",
            )
        except (
            UnlimitedOCRError,
            HTTPError,
            URLError,
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            return UnlimitedOCRHealth(
                configured=True,
                available=True,
                model_available=False,
                detail="service online; model list unavailable",
            )

    def parse(
        self,
        images: Iterable[str | Path],
        *,
        prompt: str | None = None,
        image_mode: Literal["gundam", "base"] = "gundam",
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
        fallback: Fallback | None = None,
    ) -> UnlimitedOCRResult:
        paths = [str(image) for image in images]
        try:
            return self.parse_strict(
                paths,
                prompt=prompt,
                image_mode=image_mode,
                stream=stream,
                on_delta=on_delta,
            )
        except (UnlimitedOCRError, HTTPError, URLError, OSError, ValueError) as error:
            reason = self._safe_error(error)
            if fallback is not None:
                try:
                    fallback_prompt = prompt or (
                        DEFAULT_PROMPT if len(paths) == 1 else DEFAULT_MULTI_PAGE_PROMPT
                    )
                    fallback_value = fallback(fallback_prompt, paths)
                    if isinstance(fallback_value, UnlimitedOCRResult):
                        return fallback_value
                    return UnlimitedOCRResult(
                        text=str(fallback_value or ""),
                        ok=bool(fallback_value),
                        degraded=True,
                        source="fallback",
                        error=reason,
                    )
                except Exception:
                    reason = f"{reason}; fallback failed"
            return UnlimitedOCRResult(degraded=True, error=reason)

    def parse_strict(
        self,
        images: Iterable[str | Path],
        *,
        prompt: str | None = None,
        image_mode: Literal["gundam", "base"] = "gundam",
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
    ) -> UnlimitedOCRResult:
        if not self.config.enabled:
            raise UnlimitedOCRConfigurationError("Unlimited-OCR is disabled")
        if not self.config.base_url.strip():
            raise UnlimitedOCRConfigurationError(
                "Unlimited-OCR base_url is not configured"
            )
        image_values = [str(image) for image in images]
        if not image_values:
            raise UnlimitedOCRConfigurationError("At least one image is required")
        if len(image_values) > MAX_IMAGES:
            raise UnlimitedOCRConfigurationError(
                f"At most {MAX_IMAGES} images are allowed"
            )
        if image_mode not in {"gundam", "base"}:
            raise UnlimitedOCRConfigurationError("image_mode must be gundam or base")
        if len(image_values) > 1 and image_mode != "base":
            raise UnlimitedOCRConfigurationError("Multiple images require base mode")

        content, total_bytes = self._build_content(image_values, prompt)
        if total_bytes > MAX_TOTAL_IMAGE_BYTES:
            raise UnlimitedOCRConfigurationError("Combined local images exceed 64 MB")
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "skip_special_tokens": False,
            "images_config": {"image_mode": image_mode},
            "stream": stream,
        }
        ngram_window = (
            self.config.ngram_window_multi
            if len(image_values) > 1
            else self.config.ngram_window_single
        )
        if self.config.no_repeat_ngram_size > 0 and ngram_window > 0:
            payload["custom_params"] = {
                "ngram_size": self.config.no_repeat_ngram_size,
                "window_size": ngram_window,
            }
            processor = validate_custom_logit_processor(
                self.config.custom_logit_processor
            )
            if processor:
                payload["custom_logit_processor"] = processor
        request = Request(
            self._endpoint("chat/completions"),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(stream=stream),
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.config.timeout) as response:
                if stream:
                    text, truncated = self._parse_stream(response, on_delta)
                else:
                    text, truncated = self._parse_response(response)
        except HTTPError as error:
            raise UnlimitedOCRRequestError(
                f"service returned HTTP {error.code}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise UnlimitedOCRRequestError("service unavailable") from error

        if not text:
            raise UnlimitedOCRRequestError("service returned an empty OCR result")
        return UnlimitedOCRResult(text=text, ok=True, truncated=truncated)

    def _build_content(
        self,
        images: list[str],
        prompt: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        effective_prompt = (prompt or "").strip()
        if not effective_prompt:
            effective_prompt = (
                DEFAULT_PROMPT if len(images) == 1 else DEFAULT_MULTI_PAGE_PROMPT
            )
        content: list[dict[str, Any]] = [{"type": "text", "text": effective_prompt}]
        total_bytes = 0
        for image_value in images:
            parsed = urlsplit(image_value)
            if parsed.scheme in {"http", "https"}:
                if not self.config.allow_remote:
                    raise UnlimitedOCRConfigurationError("Remote images are disabled")
                url = validate_remote_image_url(
                    image_value,
                    resolver=self._remote_resolver,
                )
            else:
                image = load_validated_image(image_value, max_size=MAX_IMAGE_FILE_SIZE)
                total_bytes += len(image.data)
                encoded = base64.b64encode(image.data).decode("ascii")
                url = f"data:{image.media_type};base64,{encoded}"
            content.append({"type": "image_url", "image_url": {"url": url}})
        return content, total_bytes

    def _parse_response(self, response: Any) -> tuple[str, bool]:
        response_limit = max(64 * 1024, self.config.max_output * 8 + 16 * 1024)
        raw = response.read(response_limit + 1)
        if len(raw) > response_limit:
            raise UnlimitedOCRRequestError("service response exceeded the size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
            choice = payload["choices"][0]
            value = choice.get("message", {}).get("content", choice.get("text", ""))
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
        ) as error:
            raise UnlimitedOCRRequestError(
                "service returned an invalid response"
            ) from error
        text = self._content_text(value)
        return self._limit_text(text)

    def _parse_stream(
        self,
        response: Any,
        on_delta: Callable[[str], None] | None,
    ) -> tuple[str, bool]:
        parts: list[str] = []
        length = 0
        truncated = False
        stream_bytes = 0
        stream_limit = max(1024 * 1024, self.config.max_output * 16 + 64 * 1024)
        for raw_line in response:
            stream_bytes += (
                len(raw_line) if isinstance(raw_line, bytes) else len(str(raw_line))
            )
            if stream_bytes > stream_limit:
                raise UnlimitedOCRRequestError("service stream exceeded the size limit")
            try:
                line = (
                    raw_line.decode("utf-8")
                    if isinstance(raw_line, bytes)
                    else str(raw_line)
                )
            except UnicodeDecodeError as error:
                raise UnlimitedOCRRequestError(
                    "service returned an invalid stream"
                ) from error
            line = line.strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
                value = event["choices"][0].get("delta", {}).get("content", "")
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
                raise UnlimitedOCRRequestError(
                    "service returned an invalid stream event"
                ) from error
            delta = self._content_text(value)
            if not delta:
                continue
            remaining = self.config.max_output - length
            if remaining <= 0:
                truncated = True
                break
            accepted = delta[:remaining]
            if accepted:
                parts.append(accepted)
                length += len(accepted)
                if on_delta is not None:
                    on_delta(accepted)
            if len(accepted) < len(delta):
                truncated = True
                break
        return "".join(parts), truncated

    @staticmethod
    def _content_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(
                str(item.get("text", ""))
                for item in value
                if isinstance(item, dict) and item.get("type") in {None, "text"}
            )
        return ""

    def _limit_text(self, value: str) -> tuple[str, bool]:
        if len(value) <= self.config.max_output:
            return value, False
        return value[: self.config.max_output], True

    @staticmethod
    def _safe_error(error: Exception) -> str:
        if isinstance(error, UnlimitedOCRConfigurationError):
            return str(error)
        if isinstance(error, UnlimitedOCRRequestError):
            return str(error)
        if isinstance(error, HTTPError):
            return f"service returned HTTP {error.code}"
        return "Unlimited-OCR service unavailable"


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_MULTI_PAGE_PROMPT",
    "DEFAULT_PROMPT",
    "UnlimitedOCRClient",
    "UnlimitedOCRConfig",
    "UnlimitedOCRConfigurationError",
    "UnlimitedOCRError",
    "UnlimitedOCRHealth",
    "UnlimitedOCRRequestError",
    "UnlimitedOCRResult",
    "normalize_unlimited_ocr_base_url",
    "validate_custom_logit_processor",
    "validate_remote_image_url",
]
