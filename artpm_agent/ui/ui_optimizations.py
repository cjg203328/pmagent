"""UI response optimization for settings and callbacks.

Optimizes:
1. Success/info message display (toast instead of full alert)
2. Async operations for API key validation
3. Debounced updates to reduce re-renders
4. Cached expensive operations
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import time
from typing import Any, Callable

import streamlit as st


# ============================================================================
# Toast-style notifications (faster than st.success/st.info)
# ============================================================================

def show_toast(
    message: str,
    *,
    icon: str = "✅",
    duration: int = 3
) -> None:
    """Show toast notification (non-blocking, faster than st.success).

    Args:
        message: Message to display
        icon: Emoji icon
        duration: Duration in seconds (Streamlit default: 3s)
    """
    try:
        # Streamlit 1.59+ supports st.toast
        st.toast(f"{icon} {message}", icon=icon)
    except AttributeError:
        # Fallback for older versions
        st.success(message)


def show_toast_info(message: str, duration: int = 3) -> None:
    """Show info toast."""
    show_toast(message, icon="ℹ️", duration=duration)


def show_toast_warning(message: str, duration: int = 3) -> None:
    """Show warning toast."""
    show_toast(message, icon="⚠️", duration=duration)


def show_toast_error(message: str, duration: int = 5) -> None:
    """Show error toast."""
    show_toast(message, icon="❌", duration=duration)


# ============================================================================
# Async API validation (non-blocking)
# ============================================================================

async def validate_api_key_async(
    provider: str,
    api_key: str,
    api_base: str | None = None
) -> tuple[bool, str]:
    """Validate API key asynchronously.

    Args:
        provider: LLM provider (openai, anthropic, custom)
        api_key: API key to validate
        api_base: Optional API base URL

    Returns:
        (is_valid, message)
    """
    if not api_key or api_key.startswith("sk-your-"):
        return False, "请输入有效的 API Key"

    try:
        if provider == "openai" or provider == "custom":
            import httpx
            base_url = api_base or "https://api.openai.com/v1"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"}
                )
                if response.status_code == 200:
                    return True, "API Key 有效"
                elif response.status_code == 401:
                    return False, "API Key 无效"
                else:
                    return False, f"验证失败: {response.status_code}"

        elif provider == "anthropic":
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-3-haiku-20240307",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}]
                    }
                )
                if response.status_code in (200, 400):
                    return True, "API Key 有效"
                elif response.status_code == 401:
                    return False, "API Key 无效"
                else:
                    return False, f"验证失败: {response.status_code}"

    except asyncio.TimeoutError:
        return False, "验证超时（5秒）"
    except Exception as e:
        return False, f"验证失败: {str(e)[:50]}"

    return True, "跳过验证"


def validate_api_key_sync(
    provider: str,
    api_key: str,
    api_base: str | None = None
) -> tuple[bool, str]:
    """Synchronous wrapper for API key validation."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(
        validate_api_key_async(provider, api_key, api_base)
    )


# ============================================================================
# Debounced operations (reduce re-renders)
# ============================================================================

class Debouncer:
    """Debounce expensive operations."""

    def __init__(self, delay: float = 0.5):
        """Initialize debouncer.

        Args:
            delay: Delay in seconds before executing
        """
        self.delay = delay
        self._last_call: dict[str, float] = {}

    def should_execute(self, key: str) -> bool:
        """Check if enough time has passed since last call.

        Args:
            key: Unique identifier for the operation

        Returns:
            True if should execute
        """
        now = time.time()
        last = self._last_call.get(key, 0)

        if now - last >= self.delay:
            self._last_call[key] = now
            return True

        return False

    def __call__(self, func: Callable) -> Callable:
        """Decorator to debounce a function.

        Args:
            func: Function to debounce

        Returns:
            Wrapped function
        """
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = f"{func.__name__}_{id(args)}_{id(kwargs)}"
            if self.should_execute(key):
                return func(*args, **kwargs)
            return None

        return wrapper


# Global debouncer instance
_debouncer = Debouncer(delay=0.3)


def debounced(func: Callable) -> Callable:
    """Decorator to debounce expensive operations.

    Usage:
        @debounced
        def expensive_operation():
            ...
    """
    return _debouncer(func)


# ============================================================================
# Cached operations (reduce redundant work)
# ============================================================================

def cached_operation(
    ttl: int = 60,
    key_func: Callable[[Any], str] | None = None
) -> Callable:
    """Decorator to cache operation results.

    Args:
        ttl: Time to live in seconds
        key_func: Function to generate cache key from args

    Returns:
        Decorator function
    """
    cache: dict[str, tuple[Any, float]] = {}

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                key_str = f"{func.__name__}_{args}_{kwargs}"
                cache_key = hashlib.md5(key_str.encode()).hexdigest()

            # Check cache
            now = time.time()
            if cache_key in cache:
                value, timestamp = cache[cache_key]
                if now - timestamp < ttl:
                    return value

            # Execute and cache
            result = func(*args, **kwargs)
            cache[cache_key] = (result, now)

            return result

        return wrapper

    return decorator


# ============================================================================
# Optimized callback patterns
# ============================================================================

def with_spinner_overlay(message: str = "处理中...") -> Callable:
    """Decorator to show spinner during callback execution.

    Args:
        message: Spinner message

    Returns:
        Decorator function
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with st.spinner(message):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def optimized_callback(
    success_message: str | None = None,
    error_message: str | None = None,
    debounce: bool = False,
    cache_ttl: int | None = None
) -> Callable:
    """Decorator for optimized callback functions.

    Features:
    - Toast notifications instead of full alerts
    - Optional debouncing
    - Optional caching
    - Error handling

    Args:
        success_message: Message on success (toast)
        error_message: Message on error (toast)
        debounce: Whether to debounce calls
        cache_ttl: Cache TTL in seconds (None = no cache)

    Returns:
        Decorator function
    """
    def decorator(func: Callable) -> Callable:
        wrapped = func

        # Apply caching
        if cache_ttl:
            wrapped = cached_operation(ttl=cache_ttl)(wrapped)

        # Apply debouncing
        if debounce:
            wrapped = debounced(wrapped)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                result = wrapped(*args, **kwargs)

                if success_message:
                    show_toast(success_message)

                return result

            except Exception as e:
                msg = error_message or f"操作失败: {str(e)}"
                show_toast_error(msg)
                raise

        return wrapper

    return decorator


# ============================================================================
# Progress indicators (non-blocking)
# ============================================================================

class ProgressIndicator:
    """Non-blocking progress indicator."""

    def __init__(self, total: int, message: str = "处理中..."):
        """Initialize progress indicator.

        Args:
            total: Total steps
            message: Progress message
        """
        self.total = total
        self.message = message
        self.current = 0
        self._progress_bar = None

    def __enter__(self):
        self._progress_bar = st.progress(0, text=self.message)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._progress_bar:
            self._progress_bar.empty()

    def update(self, step: int = 1, message: str | None = None):
        """Update progress.

        Args:
            step: Steps to advance
            message: Optional new message
        """
        self.current = min(self.current + step, self.total)
        progress = self.current / self.total

        if self._progress_bar:
            self._progress_bar.progress(
                progress,
                text=message or self.message
            )


# ============================================================================
# Example usage
# ============================================================================

if __name__ == "__main__":
    # Example 1: Optimized callback
    @optimized_callback(
        success_message="配置已保存",
        debounce=True,
        cache_ttl=10
    )
    def save_settings(config: dict):
        """Save settings with optimized callback."""
        time.sleep(0.1)  # Simulate work
        return config

    # Example 2: API validation
    is_valid, msg = validate_api_key_sync(
        "openai",
        "sk-test-key",
        "https://api.openai.com/v1"
    )
    print(f"Valid: {is_valid}, Message: {msg}")

    # Example 3: Progress indicator
    with ProgressIndicator(100, "加载中...") as progress:
        for i in range(100):
            time.sleep(0.01)
            progress.update(1)

    # Example 4: Toast notifications
    show_toast("操作成功")
    show_toast_warning("配置可能需要重启")
    show_toast_error("连接失败")
