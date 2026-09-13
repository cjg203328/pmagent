"""LangChain v1 model adapter for ArtPM's provider-neutral client contract.

The application owns routing, failover, persistence, and approval gates. This
module only translates that contract to LangChain chat model messages so the
framework can be upgraded without replacing the existing safety boundaries.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Dict, List, Optional

from .llm_client import BaseLLMClient, is_valid_api_key


def _message_content(value: Any) -> str:
    """Normalize LangChain message content, including multimodal blocks."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return str(value or "")
    parts: list[str] = []
    for block in value:
        if isinstance(block, str):
            parts.append(block)
            continue
        if isinstance(block, Mapping):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _usage_from_response(response: Any) -> Optional[Dict[str, int]]:
    """Read usage metadata across LangChain provider integrations."""
    candidates = [
        _value(response, "usage_metadata"),
        _value(_value(response, "response_metadata", {}), "usage"),
        _value(_value(response, "response_metadata", {}), "token_usage"),
    ]
    usages = [item for item in candidates if isinstance(item, Mapping)]
    if not usages:
        return None

    def integer(*keys: str) -> int:
        for usage in usages:
            for key in keys:
                try:
                    value = usage.get(key)
                    if value is not None:
                        return max(0, int(value))
                except (TypeError, ValueError):
                    continue
        return 0

    prompt_tokens = integer("input_tokens", "prompt_tokens")
    completion_tokens = integer("output_tokens", "completion_tokens")
    total_tokens = integer("total_tokens") or prompt_tokens + completion_tokens
    cached_tokens = integer(
        "cached_tokens",
        "prompt_cache_hit_tokens",
        "cache_read_input_tokens",
    )
    for usage in usages:
        for details_key in ("input_token_details", "prompt_tokens_details"):
            details = usage.get(details_key)
            if not isinstance(details, Mapping):
                continue
            for key in ("cache_read", "cached_tokens"):
                try:
                    cached_tokens = max(
                        cached_tokens,
                        int(details.get(key, 0) or 0),
                    )
                except (TypeError, ValueError):
                    continue
    if not (prompt_tokens or completion_tokens or total_tokens or cached_tokens):
        return None
    result = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    if cached_tokens:
        result["cached_tokens"] = cached_tokens
    return result


class LangChainClient(BaseLLMClient):
    """Adapt LangChain chat models to ``BaseLLMClient``.

    ``provider`` remains the deployment-level provider selector. ``custom`` and
    ``zhipu`` use LangChain's OpenAI-compatible integration with their configured
    base URL; no provider-specific model assumptions are made here.
    """

    is_langchain = True

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.provider = str(config.get("provider", "") or "").strip().lower()
        self.last_usage: Optional[Dict[str, int]] = None
        self.model_client = self._build_model(config)
        # ``client`` is kept as a readable compatibility alias for integrations
        # that only need to inspect the active model object.
        self.client = self.model_client

    def _build_model(self, config: Dict[str, Any]) -> Any:
        try:
            timeout = float(config.get("request_timeout_seconds", 30))
        except (TypeError, ValueError):
            timeout = 30.0
        timeout = max(5.0, min(timeout, 120.0))
        if self.provider == "anthropic":
            api_key = config.get("anthropic_api_key")
            if not is_valid_api_key(api_key):
                raise ValueError("ANTHROPIC_API_KEY not found in config")
            try:
                from langchain_anthropic import ChatAnthropic
            except ImportError as error:
                raise ImportError(
                    "langchain-anthropic is required for LLM_FRAMEWORK=langchain"
                ) from error
            common = {
                "model_name": self.model,
                "temperature": self.temperature,
                "max_tokens_to_sample": self.max_tokens,
                "timeout": timeout,
                "max_retries": 0,
            }
            base_url = str(config.get("anthropic_api_base", "") or "").strip()
            if base_url:
                common["base_url"] = base_url
            common["api_key"] = api_key
            return ChatAnthropic(**common)

        if self.provider not in {"openai", "custom", "zhipu", "deepseek"}:
            raise ValueError(f"Unsupported LangChain provider: {self.provider}")
        if self.provider == "zhipu":
            api_key = config.get("zhipu_api_key")
            default_base_url = "https://open.bigmodel.cn/api/paas/v4"
        elif self.provider == "deepseek":
            api_key = config.get("deepseek_api_key")
            default_base_url = "https://api.deepseek.com"
        else:
            api_key = config.get("openai_api_key")
            default_base_url = ""
        if not is_valid_api_key(api_key):
            raise ValueError("OPENAI-compatible API key not found in config")
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as error:
            raise ImportError(
                "langchain-openai is required for LLM_FRAMEWORK=langchain"
            ) from error
        common = {
            "model": self.model,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_tokens,
            "timeout": timeout,
            "max_retries": 0,
        }
        base_url_key = {
            "zhipu": "zhipu_api_base",
            "deepseek": "deepseek_api_base",
        }.get(self.provider, "openai_api_base")
        base_url = str(
            config.get(base_url_key) or default_base_url
        ).strip()
        if base_url:
            common["base_url"] = base_url
        common["api_key"] = api_key
        return ChatOpenAI(**common)

    def _messages(
        self,
        prompt: str,
        system_prompt: Optional[str],
        history: Optional[List[Dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if system_prompt and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})
        messages.extend(self._prepare_history(prompt, history))
        messages.append({"role": "user", "content": prompt})
        return messages

    def _invoke(self, messages: list[dict[str, Any]]) -> Any:
        response = self._retry_request(self.model_client.invoke, messages)
        self.last_usage = _usage_from_response(response)
        return response

    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        response = self._invoke(self._messages(prompt, system_prompt, history))
        return _message_content(_value(response, "content"))

    def stream_chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Iterator[str]:
        stream = self._retry_request(
            self.model_client.stream,
            self._messages(prompt, system_prompt, history),
        )
        accumulated_usage: Optional[Dict[str, int]] = None
        try:
            for chunk in stream:
                usage = _usage_from_response(chunk)
                if usage:
                    accumulated_usage = usage
                content = _message_content(_value(chunk, "content"))
                if content:
                    yield content
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if accumulated_usage:
            self.last_usage = accumulated_usage

    def _multimodal_messages(
        self,
        prompt: str,
        image_paths: List[str],
        system_prompt: Optional[str],
        history: Optional[List[Dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        messages = self._messages(prompt, system_prompt, history)
        user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_path in image_paths[:3]:
            media_type, data = self._load_image(image_path)
            if self.provider == "anthropic":
                user_content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        },
                    }
                )
            else:
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{data}"
                        },
                    }
                )
        messages[-1] = {"role": "user", "content": user_content}
        return messages

    def chat_with_images(
        self,
        prompt: str,
        image_paths: List[str],
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        response = self._invoke(
            self._multimodal_messages(prompt, image_paths, system_prompt, history)
        )
        return _message_content(_value(response, "content"))

    def stream_chat_with_images(
        self,
        prompt: str,
        image_paths: List[str],
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Iterator[str]:
        stream = self._retry_request(
            self.model_client.stream,
            self._multimodal_messages(prompt, image_paths, system_prompt, history),
        )
        accumulated_usage: Optional[Dict[str, int]] = None
        try:
            for chunk in stream:
                usage = _usage_from_response(chunk)
                if usage:
                    accumulated_usage = usage
                content = _message_content(_value(chunk, "content"))
                if content:
                    yield content
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if accumulated_usage:
            self.last_usage = accumulated_usage
