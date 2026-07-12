"""OpenAI-compatible model discovery helpers."""
from __future__ import annotations

import json
from typing import Any, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

from .llm_client import is_valid_api_key


class ModelCatalogError(RuntimeError):
    """A safe, user-facing model discovery error."""


def normalize_openai_base_url(value: str) -> str:
    """Validate and normalize an OpenAI-compatible API root URL."""
    candidate = (value or "").strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelCatalogError("API Base URL 必须是完整的 http(s) 地址")
    if parsed.username or parsed.password:
        raise ModelCatalogError("API Base URL 不能包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise ModelCatalogError("API Base URL 不能包含查询参数或片段")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _model_id(item: Any) -> Optional[str]:
    if isinstance(item, str):
        value = item
    elif isinstance(item, dict):
        value = item.get("id") or item.get("name")
    else:
        value = getattr(item, "id", None) or getattr(item, "name", None)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def normalize_model_ids(items: Iterable[Any]) -> List[str]:
    """Extract unique model IDs while preserving deterministic ordering."""
    model_ids = {_model_id(item) for item in items}
    model_ids.discard(None)
    return sorted(model_ids, key=str.casefold)


def fetch_openai_compatible_models(
    api_key: str,
    base_url: str,
    *,
    timeout: float = 15.0,
) -> List[str]:
    """Fetch model IDs through an OpenAI-compatible ``GET /models`` call."""
    if not is_valid_api_key(api_key):
        raise ModelCatalogError("请先填写有效的 API Key")

    normalized_base_url = normalize_openai_base_url(base_url)
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key.strip(),
            base_url=normalized_base_url,
            timeout=timeout,
            max_retries=1,
        )
        response = client.models.list()
        items = getattr(response, "data", response)
        models = normalize_model_ids(items)
    except ModelCatalogError:
        raise
    except Exception as error:
        status_code = getattr(error, "status_code", None)
        if status_code in {401, 403}:
            message = "模型同步鉴权失败，请检查 API Key"
        elif status_code:
            message = f"模型服务返回 HTTP {status_code}"
        else:
            message = "无法连接模型服务，请检查 API Base URL 和网络"
        raise ModelCatalogError(message) from error

    if not models:
        raise ModelCatalogError("模型服务未返回可用的模型 ID")
    return models


def parse_cached_models(value: str) -> List[str]:
    """Parse the persisted JSON model list, tolerating older comma lists."""
    if not value or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        parsed = [item.strip() for item in value.split(",")]
    if not isinstance(parsed, list):
        return []
    return normalize_model_ids(parsed)


def serialize_cached_models(models: Iterable[Any]) -> str:
    """Serialize a normalized model list for dotenv persistence."""
    return json.dumps(normalize_model_ids(models), ensure_ascii=False, separators=(",", ":"))
