"""Pure chat state projections shared by the Streamlit chat page."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_LEGACY_MODEL_RUNTIME_RE = re.compile(
    r"当前配置的生成模型 ID 是 \*\*`(?P<model>[^`]+)`\*\*.*?"
    r"连接状态：\*\*(?P<state>[^*]+)\*\*",
    re.DOTALL,
)
_LEGACY_MODEL_NATURAL_RE = re.compile(
    r"根据当前运行配置，我使用的模型是\s*`?(?P<model>[A-Za-z0-9._:-]+)`?[。.]"
)
_LEGACY_FALLBACK_NOTICE_RE = re.compile(
    r"默认模型 `[^`]+` 暂时不可用，本次临时使用 `(?P<model>[^`]+)` "
    r"生成回答；默认设置未修改。\n\n",
)


def compact_legacy_assistant_copy(content: str) -> str:
    """Render older stored system copy using the current concise wording."""
    text = str(content or "")
    runtime_match = _LEGACY_MODEL_RUNTIME_RE.search(text)
    if runtime_match:
        return (
            f"当前模型：`{runtime_match.group('model')}`。"
            f"状态：{runtime_match.group('state')}。"
        )
    natural_match = _LEGACY_MODEL_NATURAL_RE.search(text)
    if natural_match:
        return f"当前模型：`{natural_match.group('model')}`。"
    return _LEGACY_FALLBACK_NOTICE_RE.sub(
        lambda match: f"已切换备用模型：`{match.group('model')}`。\n\n",
        text,
        count=1,
    )


def voice_provider_label(provider: Any) -> str:
    return {
        "cartesia": "Cartesia",
        "minimax": "MiniMax",
        "local": "本地语音",
    }.get(str(provider or "").strip().casefold(), "")


def preceding_user_prompt(messages: Sequence[Mapping[str, Any]], index: int) -> str:
    """Best-effort lookup of the user prompt preceding ``index``."""
    for position in range(index - 1, -1, -1):
        message = messages[position] if 0 <= position < len(messages) else None
        if message and message.get("role") == "user":
            return str(message.get("content") or message.get("text") or "")
    return ""


def feedback_was_saved(result: Any) -> bool:
    return isinstance(result, Mapping) and bool(result.get("feedback_id"))


__all__ = [
    "compact_legacy_assistant_copy",
    "feedback_was_saved",
    "preceding_user_prompt",
    "voice_provider_label",
]
