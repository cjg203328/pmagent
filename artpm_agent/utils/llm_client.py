"""
LLM Client Module - Unified interface for OpenAI and Anthropic
"""
import base64
import time
from typing import Dict, Any, Iterator, List, Optional
from abc import ABC, abstractmethod

from artpm_agent.utils.image_validation import load_validated_image


def is_valid_api_key(value: Optional[str]) -> bool:
    """Return False for empty values and common template placeholders."""
    if not value or len(value.strip()) < 8:
        return False
    normalized = value.strip().lower()
    placeholders = ("your-key", "your_key", "your-api", "placeholder", "-here")
    return not any(marker in normalized for marker in placeholders)


class BaseLLMClient(ABC):
    """Base LLM Client"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model = config.get("model", "")
        self.temperature = config.get("temperature", 0.7)
        self.max_tokens = config.get("max_tokens", 4000)
        self.retry_max_attempts = max(1, int(config.get("retry_max_attempts", 1)))
        self.retry_backoff_factor = max(0, float(config.get("retry_backoff_factor", 2)))

    @abstractmethod
    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Send chat completion request"""
        pass

    def stream_chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Iterator[str]:
        """Yield a complete response for clients without native streaming."""
        response = self.chat(prompt, system_prompt=system_prompt, history=history)
        if response:
            yield response

    def _prepare_history(
        self,
        prompt: str,
        history: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, str]]:
        """Sanitize, deduplicate, and bound conversation history."""
        cleaned: List[Dict[str, str]] = []
        for item in history or []:
            if not isinstance(item, dict) or item.get("status") == "error":
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            cleaned.append({"role": role, "content": content.strip()})

        if cleaned and cleaned[-1] == {"role": "user", "content": prompt.strip()}:
            cleaned.pop()

        max_messages = max(0, int(self.config.get("history_max_messages", 12)))
        max_chars = max(0, int(self.config.get("history_max_chars", 8000)))
        selected: List[Dict[str, str]] = []
        used_chars = 0
        for item in reversed(cleaned):
            if len(selected) >= max_messages:
                break
            content = item["content"]
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            if len(content) > remaining:
                if selected:
                    break
                content = content[:remaining]
            selected.append({"role": item["role"], "content": content})
            used_chars += len(content)

        selected.reverse()
        while selected and selected[0]["role"] == "assistant":
            selected.pop(0)
        return selected

    def _retry_request(self, request_func, *args, **kwargs):
        """Retry request with exponential backoff"""
        for attempt in range(self.retry_max_attempts):
            try:
                return request_func(*args, **kwargs)
            except Exception as e:
                if attempt == self.retry_max_attempts - 1:
                    raise
                status_code = getattr(e, "status_code", None)
                if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                    raise
                wait_time = self.retry_backoff_factor ** attempt
                time.sleep(wait_time)

    @staticmethod
    def _load_image(image_path: str) -> tuple[str, str]:
        """Validate and return an image as ``(media_type, base64_data)``."""
        image = load_validated_image(image_path)
        return image.media_type, base64.b64encode(image.data).decode("ascii")

    def chat_with_images(
        self,
        prompt: str,
        image_paths: List[str],
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Send a multimodal request when the concrete client supports it."""
        raise NotImplementedError("当前模型客户端不支持图片理解")


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude Client (支持自定义 API Base URL)"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        api_key = config.get("anthropic_api_key")
        if not is_valid_api_key(api_key):
            raise ValueError("ANTHROPIC_API_KEY not found in config")

        try:
            from anthropic import Anthropic
            # 支持自定义 API Base URL
            base_url = config.get("anthropic_api_base")
            if base_url:
                self.client = Anthropic(api_key=api_key, base_url=base_url)
            else:
                self.client = Anthropic(api_key=api_key)
        except ImportError:
            raise ImportError("anthropic package not installed. Run: pip install anthropic")

    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Send chat completion request to Claude"""

        def _request():
            messages = self._prepare_history(prompt, history)
            messages.append({"role": "user", "content": prompt})

            kwargs = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "messages": messages
            }

            if system_prompt:
                kwargs["system"] = system_prompt

            response = self.client.messages.create(**kwargs)
            return response.content[0].text

        return self._retry_request(_request)

    def chat_with_images(
        self,
        prompt: str,
        image_paths: List[str],
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        def _request():
            messages: List[Dict[str, Any]] = list(
                self._prepare_history(prompt, history)
            )
            content: List[Dict[str, Any]] = []
            for image_path in image_paths[:3]:
                media_type, data = self._load_image(image_path)
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        },
                    }
                )
            content.append({"type": "text", "text": prompt})
            messages.append({"role": "user", "content": content})
            kwargs = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "messages": messages,
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            response = self.client.messages.create(**kwargs)
            return response.content[0].text

        return self._retry_request(_request)


class OpenAIClient(BaseLLMClient):
    """OpenAI GPT Client (支持自定义 API Base URL)"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        api_key = config.get("openai_api_key")
        if not is_valid_api_key(api_key):
            raise ValueError("OPENAI_API_KEY not found in config")

        try:
            from openai import OpenAI
            # 支持自定义 API Base URL
            base_url = config.get("openai_api_base")
            try:
                request_timeout = float(config.get("request_timeout_seconds", 30))
            except (TypeError, ValueError):
                request_timeout = 30.0
            request_timeout = max(5.0, min(request_timeout, 120.0))
            client_kwargs = {
                "api_key": api_key,
                "timeout": request_timeout,
                "max_retries": 0,
            }
            if base_url:
                client_kwargs["base_url"] = base_url
            self.client = OpenAI(**client_kwargs)
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai")

    def _build_messages(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(self._prepare_history(prompt, history))
        messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _extract_stream_content(chunk: Any) -> str:
        """Extract text from OpenAI and common compatible stream chunks."""
        if chunk is None:
            return ""
        choices = (
            chunk.get("choices")
            if isinstance(chunk, dict)
            else getattr(chunk, "choices", None)
        )
        if not choices:
            return ""

        choice = choices[0]
        if choice is None:
            return ""
        delta = (
            choice.get("delta")
            if isinstance(choice, dict)
            else getattr(choice, "delta", None)
        )
        if delta is None:
            return ""
        content = (
            delta.get("content")
            if isinstance(delta, dict)
            else getattr(delta, "content", None)
        )
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""

        text_parts: List[str] = []
        for part in content:
            text = (
                part.get("text")
                if isinstance(part, dict)
                else getattr(part, "text", None)
            )
            if isinstance(text, str):
                text_parts.append(text)
        return "".join(text_parts)

    def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Send chat completion request to GPT"""

        def _request():
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self._build_messages(prompt, system_prompt, history),
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )

            return response.choices[0].message.content

        return self._retry_request(_request)

    def stream_chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Iterator[str]:
        """Yield text deltas from an OpenAI-compatible chat completion."""
        stream = self._retry_request(
            self.client.chat.completions.create,
            model=self.model,
            messages=self._build_messages(prompt, system_prompt, history),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )
        for chunk in stream:
            content = self._extract_stream_content(chunk)
            if content:
                yield content

    def chat_with_images(
        self,
        prompt: str,
        image_paths: List[str],
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        def _request():
            messages: List[Dict[str, Any]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.extend(self._prepare_history(prompt, history))
            content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
            for image_path in image_paths[:3]:
                media_type, data = self._load_image(image_path)
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                    }
                )
            messages.append({"role": "user", "content": content})
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            return response.choices[0].message.content

        return self._retry_request(_request)


class ZhipuClient(OpenAIClient):
    """Zhipu GLM client through its OpenAI-compatible endpoint."""

    def __init__(self, config: Dict[str, Any]):
        adapted = dict(config)
        adapted["openai_api_key"] = config.get("zhipu_api_key")
        adapted["openai_api_base"] = config.get(
            "zhipu_api_base", "https://open.bigmodel.cn/api/paas/v4"
        )
        super().__init__(adapted)


def create_llm_client(config: Dict[str, Any]) -> BaseLLMClient:
    """
    Factory function to create LLM client

    Args:
        config: LLM configuration
            - provider: "anthropic", "openai", "zhipu", or "custom"
            - openai_api_base: custom API base URL (optional, for custom provider)

    Returns:
        LLM client instance
    """
    provider = config.get("provider", "anthropic").lower()

    if provider == "anthropic":
        return AnthropicClient(config)
    elif provider in ["openai", "custom"]:
        # custom provider uses OpenAI-compatible API
        return OpenAIClient(config)
    elif provider == "zhipu":
        return ZhipuClient(config)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")
