"""Small deterministic classifiers for chat meta-intents and UI status text."""

from __future__ import annotations

import unicodedata
from typing import Optional


_IDENTITY_QUERIES = {
    "你是谁",
    "你是什么",
    "自我介绍",
    "介绍一下自己",
    "请问你是谁",
    "whoareyou",
    "whatareyou",
    "introduceyourself",
}

_GREETINGS = {
    "你好",
    "您好",
    "嗨",
    "早上好",
    "下午好",
    "晚上好",
    "hi",
    "hello",
    "hey",
}

_MODEL_QUERIES = {
    "你的底层模型是什么",
    "你底层是什么模型",
    "你的底层是什么模型",
    "你用的是什么模型",
    "你用的什么模型",
    "你用哪个模型",
    "你是哪个模型",
    "当前用的是什么模型",
    "当前用的什么模型",
    "当前模型是什么",
    "当前使用的模型是什么",
    "你当前是什么模型",
    "你当前用的是什么模型",
    "你当前用的是哪个模型",
    "你现在是什么模型",
    "你现在用的是什么模型",
    "你现在用的是哪个模型",
    "你目前是什么模型",
    "你目前用的是什么模型",
    "你是什么模型",
    "你是哪一个模型",
    "你是哪个模型",
    "现在是什么模型",
    "现在使用的模型是什么",
    "现在用的什么模型",
    "目前是什么模型",
    "目前使用的模型是什么",
    "正在使用什么模型",
    "底层模型是什么",
    "模型版本是什么",
    "whatmodelareyouusing",
    "whichmodelareyouusing",
    "whatisyourunderlyingmodel",
    "whatisthecurrentmodel",
}

_CAPABILITY_QUERIES = {
    "你可以帮我做什么",
    "你能帮我做什么",
    "你可以做什么",
    "你能做什么",
    "你会做什么",
    "可以帮我做什么",
    "能帮我做什么",
    "可以做什么",
    "能做什么",
    "你有什么功能",
    "你有哪些功能",
    "有什么功能",
    "你有什么能力",
    "你有哪些能力",
    "有什么能力",
    "如何使用你",
    "怎么使用你",
    "使用帮助",
    "帮助",
    "help",
    "whatcanyoudo",
    "howcanyouhelp",
    "whatcanyouhelpwith",
    "showcapabilities",
}


def normalize_chat_phrase(value: str) -> str:
    """Normalize a short conversational phrase for exact intent matching."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith(("C", "P"))
    )


def is_identity_query(value: str) -> bool:
    return normalize_chat_phrase(value) in _IDENTITY_QUERIES


def is_exact_greeting(value: str) -> bool:
    return normalize_chat_phrase(value) in _GREETINGS


def is_model_query(value: str) -> bool:
    return normalize_chat_phrase(value) in _MODEL_QUERIES


def is_capability_query(value: str) -> bool:
    """Return whether a short prompt asks about the active Agent capabilities."""
    return normalize_chat_phrase(value) in _CAPABILITY_QUERIES


def is_local_fast_intent(value: str) -> bool:
    """Return whether a prompt has a complete deterministic local response.

    Keep this predicate intentionally narrow. App-level callers use it to skip
    knowledge, artifact, workflow, and model setup, so an action request must
    never be classified here merely because it contains words such as ``帮助``.
    """
    return any(
        classifier(value)
        for classifier in (
            is_model_query,
            is_identity_query,
            is_exact_greeting,
            is_capability_query,
        )
    )


def chat_processing_label(prompt: str, model_id: Optional[str] = None) -> str:
    """Return an accurate pending label without pretending every request is data work."""
    if is_model_query(prompt):
        return "正在确认当前模型配置"

    text = str(prompt or "").lower()
    if any(term in text for term in ("报价", "成本", "利润", "毛利", "净利")):
        return "正在计算报价与利润"
    if any(
        term in text
        for term in ("附件", "文档", "文件", "报价单", "合同", "excel", "pdf")
    ):
        return "正在读取并解析资料"
    if any(term in text for term in ("项目进度", "数据分析", "趋势分析", "项目评估")):
        return "正在分析项目数据"

    display_model = " ".join(str(model_id or "").split())
    if display_model:
        if len(display_model) > 28:
            display_model = f"{display_model[:28]}…"
        return f"{display_model} 正在思考并生成回答"
    return "正在思考并生成回答"
