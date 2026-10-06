"""Operator-facing configuration admin for the local REST gateway.

The gateway is configured entirely through the project ``.env`` file, which the
Streamlit host has always edited by hand. This module exposes the same file over
HTTP so the React settings page can manage it without shell access.

Trust boundary:

* Only the allowlisted keys below can be read or written. The schema is the
  authority; a request can never introduce a new environment variable.
* Secret values are never returned. A configured secret is reported as a
  boolean plus a masked preview, so the UI can show "set / not set" without the
  plaintext ever leaving the process.
* Writes go through ``python-dotenv`` first, mirroring
  ``artpm_agent/views/settings.py``, so comments and key order survive.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_SECRET_PREVIEW_CHARS = 4


class ConfigValidationError(ValueError):
    """Raised when a submitted key or value is outside the allowlisted schema."""


@dataclass(frozen=True)
class ConfigField:
    """One editable environment variable."""

    key: str
    label: str
    kind: str = "text"  # text | secret | bool | int | float | select
    options: tuple[str, ...] = ()
    help: str = ""
    placeholder: str = ""

    @property
    def is_secret(self) -> bool:
        return self.kind == "secret"


@dataclass(frozen=True)
class ConfigGroup:
    """A titled cluster of related fields."""

    id: str
    title: str
    description: str
    fields: tuple[ConfigField, ...]


_PROVIDER_OPTIONS = ("", "anthropic", "openai", "deepseek", "zhipu")

CONFIG_GROUPS: tuple[ConfigGroup, ...] = (
    ConfigGroup(
        id="llm",
        title="主模型",
        description="驱动通用对话的文本模型与请求预算。未配置 API Key 时界面以离线模式运行。",
        fields=(
            ConfigField(
                "LLM_PROVIDER",
                "Provider",
                "select",
                _PROVIDER_OPTIONS,
                help="留空则按离线模式运行，仅本地能力可用。",
            ),
            ConfigField("LLM_MODEL", "模型名", "text", placeholder="claude-3-5-sonnet-20241022"),
            ConfigField(
                "LLM_FRAMEWORK",
                "调用框架",
                "select",
                ("langchain", "native"),
                help="langchain 为推荐路径；native 仅用于兼容旧行为。",
            ),
            ConfigField("LLM_MAX_TOKENS", "单次最大输出 Token", "int", placeholder="1200"),
            ConfigField("LLM_REQUEST_TIMEOUT_SECONDS", "主请求超时（秒）", "float", placeholder="12"),
            ConfigField("LLM_FAILOVER_MAX_ATTEMPTS", "失败转移最大尝试次数", "int", placeholder="2"),
            ConfigField(
                "LLM_FAILOVER_REQUEST_TIMEOUT_SECONDS", "转移请求超时（秒）", "float", placeholder="8"
            ),
            ConfigField("LLM_HISTORY_MAX_MESSAGES", "注入历史消息条数", "int", placeholder="8"),
            ConfigField("LLM_HISTORY_MAX_CHARS", "注入历史字符上限", "int", placeholder="4000"),
        ),
    ),
    ConfigGroup(
        id="keys",
        title="API Key 与接入点",
        description="各 Provider 的凭据。密钥仅写入项目根目录 .env，读取时一律脱敏。",
        fields=(
            ConfigField("ANTHROPIC_API_KEY", "Anthropic API Key", "secret"),
            ConfigField("ANTHROPIC_API_BASE", "Anthropic Base URL", "text", placeholder="留空用官方地址"),
            ConfigField("OPENAI_API_KEY", "OpenAI API Key", "secret"),
            ConfigField("OPENAI_API_BASE", "OpenAI Base URL", "text", placeholder="留空用官方地址"),
            ConfigField("DEEPSEEK_API_KEY", "DeepSeek API Key", "secret"),
            ConfigField(
                "DEEPSEEK_API_BASE",
                "DeepSeek Base URL",
                "text",
                placeholder="https://api.deepseek.com",
            ),
            ConfigField(
                "DEEPSEEK_REASONING_EFFORT",
                "DeepSeek 思维链强度",
                "select",
                ("", "off", "high", "max"),
                help="仅对支持 thinking 的模型生效。",
            ),
            ConfigField("ZHIPU_API_KEY", "智谱 API Key", "secret"),
            ConfigField("ZHIPU_API_BASE", "智谱 Base URL", "text", placeholder="留空用官方地址"),
        ),
    ),
    ConfigGroup(
        id="vision",
        title="视觉模型",
        description="主模型不支持图片时，图片请求自动路由到独立的视觉模型。",
        fields=(
            ConfigField("LLM_VISION_PROVIDER", "视觉 Provider", "select", _PROVIDER_OPTIONS),
            ConfigField("LLM_VISION_MODEL", "视觉模型名", "text", placeholder="glm-4v-flash"),
            ConfigField("LLM_VISION_API_BASE", "视觉 Base URL", "text"),
            ConfigField(
                "LLM_VISION_API_KEY",
                "视觉 API Key",
                "secret",
                help="留空则回落到视觉 Provider 的默认 Key。",
            ),
        ),
    ),
    ConfigGroup(
        id="embedding",
        title="向量与检索",
        description="长期记忆的向量化与存储后端。切换 Embedding 维度会触发索引重建。",
        fields=(
            ConfigField(
                "EMBEDDING_PROVIDER",
                "Embedding Provider",
                "select",
                ("local_feature_hash", "openai", "zhipu"),
            ),
            ConfigField("EMBEDDING_DIMENSION", "向量维度", "int", placeholder="1536"),
            ConfigField("VECTOR_BACKEND", "向量后端", "select", ("qdrant", "faiss")),
            ConfigField("QDRANT_URL", "Qdrant URL", "text", placeholder="留空使用本地 FAISS"),
            ConfigField("QDRANT_API_KEY", "Qdrant API Key", "secret"),
            ConfigField("QDRANT_COLLECTION", "Qdrant 集合名", "text", placeholder="artpm_memory"),
        ),
    ),
    ConfigGroup(
        id="ocr",
        title="OCR 服务",
        description="部署方提供的 OCR 端点，Agent 技能自动调用；不可用时回落到内置解析器。",
        fields=(
            ConfigField("UNLIMITED_OCR_ENABLED", "启用 OCR", "bool"),
            ConfigField("UNLIMITED_OCR_BASE_URL", "OCR 端点", "text", placeholder="http://127.0.0.1:10000"),
            ConfigField("UNLIMITED_OCR_MODEL", "OCR 模型名", "text", placeholder="Unlimited-OCR"),
            ConfigField("UNLIMITED_OCR_API_KEY", "OCR API Key", "secret"),
            ConfigField("UNLIMITED_OCR_TIMEOUT", "OCR 超时（秒）", "int", placeholder="120"),
            ConfigField(
                "UNLIMITED_OCR_ALLOW_REMOTE",
                "允许远程端点",
                "bool",
                help="仅在信任内网明文 HTTP 时开启。",
            ),
        ),
    ),
    ConfigGroup(
        id="mineru",
        title="文档解析（MinerU）",
        description="多模态文档后端。缺失或失败时自动回落内置解析路径。",
        fields=(
            ConfigField("MINERU_ENABLED", "启用 MinerU", "bool"),
            ConfigField(
                "MINERU_MODE",
                "运行模式",
                "select",
                ("auto", "local", "remote", "disabled"),
            ),
            ConfigField("MINERU_COMMAND", "本地命令", "text", placeholder="mineru"),
            ConfigField("MINERU_API_URL", "远程端点", "text", placeholder="http://127.0.0.1:8000"),
            ConfigField("MINERU_API_KEY", "MinerU API Key", "secret"),
            ConfigField(
                "MINERU_BACKEND",
                "解析后端",
                "select",
                ("pipeline", "hybrid-engine", "vlm-engine"),
            ),
            ConfigField("MINERU_TIMEOUT_SECONDS", "解析超时（秒）", "int", placeholder="600"),
        ),
    ),
    ConfigGroup(
        id="voice",
        title="实时语音",
        description="可选的实时语音通道，默认关闭。Provider 不可用时自动回落到文本。",
        fields=(
            ConfigField("ARTPM_VOICE_ENABLED", "启用语音", "bool"),
            ConfigField("VOICE_STT_PROVIDER", "语音识别 Provider", "text", placeholder="cartesia"),
            ConfigField("VOICE_STT_MODEL", "语音识别模型", "text", placeholder="ink-whisper"),
            ConfigField("VOICE_TTS_PROVIDER", "语音合成 Provider", "text", placeholder="minimax"),
            ConfigField("CARTESIA_API_KEY", "Cartesia API Key", "secret"),
            ConfigField("MINIMAX_API_KEY", "MiniMax API Key", "secret"),
            ConfigField("LIVEKIT_URL", "LiveKit URL", "text"),
            ConfigField("LIVEKIT_API_KEY", "LiveKit API Key", "secret"),
            ConfigField("LIVEKIT_API_SECRET", "LiveKit API Secret", "secret"),
        ),
    ),
    ConfigGroup(
        id="runtime",
        title="运行时与缓存",
        description="响应缓存、工具调用开关与记忆注入预算。",
        fields=(
            ConfigField("ARTPM_RESPONSE_CACHE", "启用响应缓存", "bool"),
            ConfigField("ARTPM_RESPONSE_CACHE_TTL", "缓存有效期（秒）", "int", placeholder="1800"),
            ConfigField(
                "AGENT_MODEL_TOOL_CALLS_ENABLED",
                "启用模型工具调用",
                "bool",
                help="仅在宿主提供审批边界时开启；敏感工具默认失败即关闭。",
            ),
            ConfigField("MEMORY_CONTEXT_MAX_TOKENS", "记忆注入 Token 上限", "int", placeholder="1200"),
            ConfigField(
                "ARTPM_LOG_LEVEL",
                "日志级别",
                "select",
                ("DEBUG", "INFO", "WARNING", "ERROR"),
            ),
        ),
    ),
)

_FIELDS_BY_KEY: dict[str, ConfigField] = {
    field.key: field for group in CONFIG_GROUPS for field in group.fields
}

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def known_keys() -> frozenset[str]:
    """Return the allowlisted environment keys."""

    return frozenset(_FIELDS_BY_KEY)


def _env_path() -> Path:
    from artpm_agent.config import PROJECT_ENV_PATH

    return Path(PROJECT_ENV_PATH)


def _mask(value: str) -> str:
    """Return a preview that proves a secret is set without revealing it."""

    stripped = value.strip()
    if not stripped:
        return ""
    if len(stripped) <= _SECRET_PREVIEW_CHARS:
        return "•" * len(stripped)
    return "•" * 6 + stripped[-_SECRET_PREVIEW_CHARS:]


def _coerce(field: ConfigField, raw: Any) -> str:
    """Validate one submitted value and return the string to persist."""

    if raw is None:
        return ""
    if isinstance(raw, bool):
        text = "true" if raw else "false"
    else:
        text = str(raw).strip()

    if field.kind == "bool":
        lowered = text.strip().casefold()
        if not lowered:
            return ""
        if lowered in _TRUE_VALUES:
            return "true"
        if lowered in _FALSE_VALUES:
            return "false"
        raise ConfigValidationError(f"{field.key} must be a boolean value")

    if field.kind == "int":
        if not text:
            return ""
        try:
            return str(int(text))
        except ValueError as error:
            raise ConfigValidationError(f"{field.key} must be an integer") from error

    if field.kind == "float":
        if not text:
            return ""
        try:
            return str(float(text))
        except ValueError as error:
            raise ConfigValidationError(f"{field.key} must be a number") from error

    if field.kind == "select":
        if text and text not in field.options:
            allowed = ", ".join(option or "(留空)" for option in field.options)
            raise ConfigValidationError(f"{field.key} must be one of: {allowed}")
        return text

    if "\n" in text or "\r" in text:
        raise ConfigValidationError(f"{field.key} must not contain line breaks")
    return text


def read_config() -> dict[str, Any]:
    """Return the current configuration with every secret masked."""

    # Importing ``artpm_agent.config`` is what loads the project ``.env`` into
    # ``os.environ``. Resolve the path first so the reads below never see an
    # unpopulated environment on a cold start.
    env_path = _env_path()

    groups: list[dict[str, Any]] = []
    for group in CONFIG_GROUPS:
        items: list[dict[str, Any]] = []
        for field in group.fields:
            raw = os.getenv(field.key, "") or ""
            item: dict[str, Any] = {
                "key": field.key,
                "label": field.label,
                "kind": field.kind,
                "help": field.help,
                "options": list(field.options),
                "placeholder": field.placeholder,
                "configured": bool(raw.strip()),
            }
            if field.is_secret:
                # The plaintext never crosses this boundary.
                item["value"] = ""
                item["preview"] = _mask(raw)
            else:
                item["value"] = raw
            items.append(item)
        groups.append(
            {
                "id": group.id,
                "title": group.title,
                "description": group.description,
                "fields": items,
            }
        )
    return {
        "env_path": str(env_path),
        "groups": groups,
        "provider": os.getenv("LLM_PROVIDER", "") or "",
        "model": os.getenv("LLM_MODEL", "") or "",
    }


def _detect_newline(env_path: Path) -> bytes:
    """Return the line ending already used by the file.

    ``.env`` in this project is stored with LF. Rewriting it with the platform
    default would convert every line to CRLF, which turns a one-key edit into a
    whole-file diff, so the original style is preserved instead.
    """

    try:
        raw = env_path.read_bytes()
    except OSError:
        return b"\n"
    return b"\r\n" if b"\r\n" in raw else b"\n"


def _restore_newlines(env_path: Path, newline: bytes) -> None:
    """Normalize the file back to ``newline`` after a write.

    Both ``python-dotenv`` and ``Path.write_text`` open in text mode, which
    translates ``\\n`` to ``os.linesep`` on Windows. This runs last so the
    on-disk style matches whatever the file already used.
    """

    try:
        raw = env_path.read_bytes()
    except OSError:
        return
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if newline == b"\r\n":
        normalized = normalized.replace(b"\n", b"\r\n")
    if normalized != raw:
        env_path.write_bytes(normalized)


def _persist(values: Mapping[str, str]) -> None:
    """Write the values into the project .env, preserving comments and order.

    ``python-dotenv`` is preferred because it keeps existing comments and key
    order intact. It cannot express an *empty* assignment, though: it writes
    ``KEY=''`` instead of the ``KEY=`` form the rest of this project uses, so a
    batch that clears a value goes through the in-place writer.
    """

    env_path = _env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    newline = _detect_newline(env_path)

    if any(value == "" for value in values.values()):
        _persist_in_place(env_path, values)
        _restore_newlines(env_path, newline)
        return

    try:
        from dotenv import set_key

        for key, value in values.items():
            set_key(str(env_path), key, value, quote_mode="auto")
        _restore_newlines(env_path, newline)
        return
    except Exception as error:  # noqa: BLE001 - fall back to the in-place writer
        logger.warning("dotenv set_key failed, using in-place writer: %s", error)
    _persist_in_place(env_path, values)
    _restore_newlines(env_path, newline)


def _dotenv_value(value: str) -> str:
    """Quote a value the way ``python-dotenv`` would, mirroring the Streamlit host.

    An empty value stays bare (``KEY=``) so the file keeps the shape used by
    ``.env.example``; anything containing whitespace or a shell-significant
    character is JSON-quoted so it round-trips through ``dotenv`` unchanged.
    """

    if value == "":
        return ""
    if any(character.isspace() for character in value) or any(
        character in value for character in "#'\"\\"
    ):
        import json

        return json.dumps(value, ensure_ascii=False)
    return value


def _persist_in_place(env_path: Path, values: Mapping[str, str]) -> None:
    """Fallback writer for Windows replace/permission failures."""

    import re
    import stat

    env_path.touch(exist_ok=True)
    try:
        env_path.chmod(env_path.stat().st_mode | stat.S_IWRITE)
    except OSError:
        pass

    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    remaining = dict(values)
    updated_lines: list[str] = []

    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            prefix = "export " if line.lstrip().startswith("export ") else ""
            updated_lines.append(f"{prefix}{key}={_dotenv_value(remaining.pop(key))}")
        else:
            updated_lines.append(line)

    if remaining:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.extend(
            f"{key}={_dotenv_value(value)}" for key, value in remaining.items()
        )

    output = "\n".join(updated_lines)
    if output and not output.endswith("\n"):
        output += "\n"
    env_path.write_text(output, encoding="utf-8", newline="\n")


def apply_config(updates: Mapping[str, Any]) -> dict[str, Any]:
    """Validate, persist and hot-reload one batch of configuration changes.

    Every key is checked against the allowlisted schema before anything is
    written, so a rejected request leaves both the file and the running process
    untouched.
    """

    if not isinstance(updates, Mapping):
        raise ConfigValidationError("values must be an object of key/value pairs")
    if not updates:
        raise ConfigValidationError("no configuration values were provided")

    unknown = sorted(set(updates) - set(_FIELDS_BY_KEY))
    if unknown:
        raise ConfigValidationError(
            "unsupported configuration keys: " + ", ".join(unknown)
        )

    coerced: dict[str, str] = {}
    for key, raw in updates.items():
        coerced[key] = _coerce(_FIELDS_BY_KEY[key], raw)

    _persist(coerced)

    # Reflect the change in this process before re-reading .env, because
    # load_dotenv(override=False) at import time would otherwise keep stale
    # values that are already present in os.environ.
    for key, value in coerced.items():
        if value == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=_env_path(), override=True)
    except Exception as error:  # noqa: BLE001 - reload is best-effort
        logger.warning("dotenv reload failed after config write: %s", error)

    try:
        from artpm_agent.config import reset_config

        reset_config()
    except Exception as error:  # noqa: BLE001 - reload is best-effort
        logger.warning("config reset failed after config write: %s", error)

    return {
        "applied": sorted(coerced),
        "env_path": str(_env_path()),
    }


def invalidate_runtime(services: Any) -> bool:
    """Drop cached model clients so the next turn picks up the new settings.

    Returns ``True`` when a cached runtime was actually released. Hosts that do
    not expose an invalidation hook simply keep their existing objects, and the
    caller reports that a restart is needed.
    """

    hook = getattr(services, "invalidate_runtime", None)
    if not callable(hook):
        return False
    try:
        hook()
    except Exception as error:  # noqa: BLE001 - never fail the config write
        logger.warning("runtime invalidation failed after config write: %s", error)
        return False
    return True
