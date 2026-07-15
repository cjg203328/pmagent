"""Memory retrieval hook — activates the long-term memory every turn.

Before ``run_turn`` runs, ``inject_memory_context`` gathers three kinds of
context and appends them to ``ctx.knowledge_context`` (consumed downstream by
the skill / workflow / model handlers):

    1. Retrieved long-term memory (MemoryManager RAG or WorkspaceKnowledgeStore).
    2. Active user preferences / corrections (FeedbackStore).
    3. Approved evolution strategies (StrategyStore).

The hook is read-only with respect to the turn and never raises: any backend
failure degrades to "no extra context" rather than breaking the turn.

Importing this module has no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from artpm_agent.memory.feedback_store import (
    FeedbackEntry,
    get_default_feedback_store,
)
from artpm_agent.memory.sqlite_manager import SQLiteManager  # noqa: F401 (kept for parity)
from artpm_agent.evolution.strategy_store import (
    Strategy,
    get_default_strategy_store,
)


@dataclass(frozen=True)
class MemoryContextBudget:
    """Bounds injected memory so recall stays useful and cheap."""

    top_k: int = 4
    max_snippet_chars: int = 800
    max_total_chars: int = 3200
    confidence_floor: float = 0.05
    max_feedback_entries: int = 12
    max_strategy_entries: int = 8
    # ── Token-aware budgeting (Phase 4 extension) ──
    # When ``inject_memory_context(max_context_tokens > 0)`` is used, blocks are
    # capped by *tokens* (via harness.token_budget) instead of characters.
    max_total_tokens: int = 0
    compression: str = "truncate"  # "truncate" (cut) | "compact" (clean+cut)
    token_model: str = ""  # tiktoken model name (optional; falls back to heuristic)
    token_counter: Optional[Callable] = None  # custom token counter (optional)
    summarize_fn: Optional[Callable] = None  # LLM summarizer hook (optional)


def _build_query(user_input: str, history: Optional[List[dict]]) -> str:
    """Compose a retrieval query from the current input and recent history."""
    parts = [user_input.strip()] if user_input else []
    if history:
        for msg in history[-3:]:
            text = ""
            if isinstance(msg, dict):
                text = str(msg.get("content") or msg.get("text") or "")
            elif isinstance(msg, str):
                text = msg
            if text.strip():
                parts.append(text.strip())
    return "\n".join(parts).strip()


def _snippet_key(text: str) -> str:
    return " ".join(str(text or "").casefold().split())[:240]


def _append_snippet(
    buckets: dict[str, list[str]],
    seen: set[str],
    bucket: str,
    text: str,
    *,
    title: str = "",
    max_chars: int = 800,
) -> None:
    clean = " ".join(str(text or "").split())
    if not clean:
        return
    key = _snippet_key(clean)
    if not key or key in seen:
        return
    seen.add(key)
    clean = clean[:max_chars].rstrip()
    if title:
        clean = f"{title}：{clean}"
    buckets.setdefault(bucket, []).append(clean)


def _record_confidence(record: Any) -> float | None:
    """Return an optional confidence/similarity score from common result shapes."""
    if not isinstance(record, dict):
        return None
    candidates: list[Any] = []
    for container in (
        record,
        record.get("metadata"),
        record.get("data"),
    ):
        if isinstance(container, dict):
            candidates.extend(
                container.get(key)
                for key in ("confidence", "score", "similarity")
                if key in container
            )
    for value in candidates:
        if isinstance(value, bool):
            continue
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= score <= 1:
            return score
    return None


def _passes_confidence(record: Any, floor: float) -> bool:
    confidence = _record_confidence(record)
    return confidence is None or confidence >= floor


def retrieve_memory_context(
    user_input: str,
    history: Optional[List[dict]] = None,
    *,
    memory_manager: Optional[Any] = None,
    knowledge_store: Optional[Any] = None,
    top_k: int = 4,
    max_snippet_chars: int = 800,
    max_total_chars: int = 3200,
    confidence_floor: float = 0.05,
) -> str:
    """Retrieve relevant long-term memory and return a formatted block.

    Merges long-term memory and workspace knowledge in separate buckets. Each
    layer is best-effort, deduplicated, and bounded so a noisy source cannot
    pollute the whole model context.
    """
    query = _build_query(user_input, history)
    if not query:
        return ""

    buckets: dict[str, list[str]] = {}
    seen: set[str] = set()

    if memory_manager is not None and hasattr(memory_manager, "retrieve"):
        try:
            results = memory_manager.retrieve(query, top_k=top_k)
            for r in results[:top_k]:
                if not _passes_confidence(r, confidence_floor):
                    continue
                text = _extract_text(r)
                title = _extract_title(r)
                _append_snippet(
                    buckets,
                    seen,
                    "长期记忆",
                    text,
                    title=title,
                    max_chars=max_snippet_chars,
                )
        except Exception:  # noqa: BLE001 - retrieval must never break a turn
            pass

    if knowledge_store is not None and hasattr(
        knowledge_store, "search"
    ):
        try:
            try:
                results = knowledge_store.search(
                    query,
                    limit=top_k,
                    include_rules=False,
                    max_text_chars=max_snippet_chars,
                    use_confidence=True,
                    confidence_floor=0.05,
                )
            except TypeError:
                results = knowledge_store.search(query, limit=top_k)
            for r in results[:top_k]:
                if not _passes_confidence(r, confidence_floor):
                    continue
                text = _extract_text(r)
                title = _extract_title(r)
                _append_snippet(
                    buckets,
                    seen,
                    "工作区资料",
                    text,
                    title=title,
                    max_chars=max_snippet_chars,
                )
        except Exception:  # noqa: BLE001
            pass

    if not any(buckets.values()):
        return ""
    lines = ["【相关记忆】"]
    for bucket_name in ("长期记忆", "工作区资料"):
        snippets = buckets.get(bucket_name) or []
        if not snippets:
            continue
        lines.append(f"{bucket_name}：")
        lines.extend(f"- {snippet}" for snippet in snippets[:top_k])
    return "\n".join(lines)[:max_total_chars].rstrip()


def _extract_text(record: Any) -> str:
    """Pull a readable text snippet out of a memory/knowledge result dict."""
    if not isinstance(record, dict):
        return ""
    # MemoryManager.retrieve returns {"data": {...}, "metadata": {...}}
    data = record.get("data", record)
    if isinstance(data, dict):
        raw = data.get("raw_text") or ""
        extracted = data.get("extracted_data")
        if isinstance(extracted, str):
            raw = f"{raw}\n{extracted}" if raw else extracted
        text = str(raw).strip()
        if text:
            return text[:800]
    # WorkspaceKnowledgeStore.search returns resource rows
    if "text" in record:
        return str(record.get("text", "")).strip()[:800]
    if "searchable_text" in record:
        return str(record.get("searchable_text", "")).strip()[:800]
    if "content" in record and isinstance(record["content"], str):
        return record["content"].strip()[:800]
    return ""


def _extract_title(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    data = record.get("data", record)
    if isinstance(data, dict):
        for key in ("title", "name", "source", "document_type"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:80]
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        for key in ("title", "name", "source"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:80]
    return ""


def format_feedback_context(
    entries: List[FeedbackEntry],
    *,
    max_entries: int = 12,
    max_chars: int = 2400,
) -> str:
    """Render active preferences/corrections as a system-prompt-style block."""
    if not entries:
        return ""
    lines: List[str] = []
    for e in entries:
        if len(lines) >= max_entries:
            break
        # Entries flagged ``no_inject`` (e.g. a bare 👎 with no concrete
        # reason) are recorded for reflection but must not pollute every
        # future turn's context with low-value noise.
        if getattr(e, "metadata", None) and e.metadata.get("no_inject"):
            continue
        label = {
            "avoid": "避免",
            "correction": "纠正",
            "blacklist": "禁止",
            "preference": "偏好",
        }.get(e.kind, "偏好")
        scope = "" if e.scope in ("global", "") else f"（范围：{e.scope}）"
        content = str(e.content or "").strip()
        if not content:
            continue
        lines.append(f"- [{label}]{scope} {content[:300].rstrip()}")
    if not lines:
        return ""
    return ("【用户偏好与纠正】\n" + "\n".join(lines))[:max_chars].rstrip()


def format_strategy_context(
    strategies: List[Strategy],
    *,
    max_entries: int = 8,
    max_chars: int = 1600,
) -> str:
    """Render active evolution strategies as an instructional block."""
    if not strategies:
        return ""
    lines = [
        f"- {str(s.rule_text or '').strip()[:300].rstrip()}"
        for s in strategies[:max_entries]
        if str(s.rule_text or "").strip()
    ]
    if not lines:
        return ""
    return ("【优化策略（来自经验复盘）】\n" + "\n".join(lines))[:max_chars].rstrip()


def inject_memory_context(
    ctx: Any,
    *,
    memory_manager: Optional[Any] = None,
    knowledge_store: Optional[Any] = None,
    feedback_store: Optional[Any] = None,
    strategy_store: Optional[Any] = None,
    max_context_chars: int = 0,
    max_context_tokens: int = 0,
) -> None:
    """Append retrieved memory + preferences + strategies to ``ctx.knowledge_context``.

    Backends default to ``ctx.agent.memory`` (if present) and the lazily-built
    default FeedbackStore / StrategyStore, so callers can invoke this with no
    arguments and get automatic memory activation.
    """
    if not isinstance(getattr(ctx, "extra", None), dict):
        ctx.extra = {}

    budget = MemoryContextBudget()

    if memory_manager is None:
        memory_manager = getattr(ctx.agent, "memory", None)
    if feedback_store is None:
        feedback_store = get_default_feedback_store()
    if strategy_store is None:
        strategy_store = get_default_strategy_store()

    blocks: List[str] = []
    existing_knowledge = str(getattr(ctx, "knowledge_context", "") or "").strip()
    if existing_knowledge:
        blocks.append(existing_knowledge)

    memory_block = ""
    try:
        memory_block = retrieve_memory_context(
            ctx.user_input,
            getattr(ctx, "conversation_history", None),
            memory_manager=memory_manager,
            knowledge_store=None if existing_knowledge else knowledge_store,
            top_k=budget.top_k,
            max_snippet_chars=budget.max_snippet_chars,
            max_total_chars=budget.max_total_chars,
            confidence_floor=budget.confidence_floor,
        )
        if memory_block:
            blocks.append(memory_block)
    except Exception:  # noqa: BLE001
        pass

    if feedback_store is not None:
        try:
            fb_block = format_feedback_context(
                feedback_store.active(),
                max_entries=budget.max_feedback_entries,
            )
            if fb_block:
                blocks.append(fb_block)
        except Exception:  # noqa: BLE001
            pass

    if strategy_store is not None:
        try:
            st_block = format_strategy_context(
                strategy_store.active(),
                max_entries=budget.max_strategy_entries,
            )
            if st_block:
                blocks.append(st_block)
        except Exception:  # noqa: BLE001
            pass

    # Phase 4 — 元记忆：识别知识缺口并给出「联网检索 / 向用户澄清」建议。
    # 仅在确有缺口时追加，避免无谓污染每轮上下文。
    try:
        from artpm_agent.evolution.meta_memory import (
            format_meta_memory_context,
            get_default_meta_memory,
            get_default_meta_memory_store,
        )

        meta = get_default_meta_memory().analyze(
            ctx.user_input,
            retrieved_block=memory_block,
            knowledge_store=knowledge_store,
            strategy_store=strategy_store,
            feedback_store=feedback_store,
            meta_store=get_default_meta_memory_store(),
        )
        meta_block = format_meta_memory_context(meta)
        if meta_block:
            blocks.append(meta_block)
            ctx.extra["meta_memory"] = meta.as_dict()
    except Exception:  # noqa: BLE001
        pass

    # Context budget: bound the injected context so long conversations don't
    # blow up prompt size, cost, or latency.
    # Token budgeting (Phase 4 extension) is preferred when requested; the
    # character budget remains the default for backward compatibility.
    if max_context_tokens and max_context_tokens > 0:
        from artpm_agent.harness.token_budget import apply_token_budget

        blocks = apply_token_budget(
            blocks,
            max_context_tokens,
            counter=budget.token_counter,
            model=budget.token_model,
            compression=budget.compression,
            priority_fn=_block_priority,
            summarize_fn=budget.summarize_fn,
        )
    elif max_context_chars and max_context_chars > 0:
        blocks = _apply_context_budget(blocks, max_context_chars)

    ctx.knowledge_context = "\n\n".join(b for b in blocks if b).strip()
    # Expose structured fragments for handlers that prefer to read them directly.
    ctx.extra["memory_injected"] = True


# Priority for context budgeting: higher = kept first when over budget.
_BLOCK_PRIORITY = {
    "【相关记忆】": 4,
    "【用户偏好与纠正】": 3,
    "【优化策略": 2,
    "【元记忆": 1,
}


def _block_priority(block: str) -> int:
    for marker, prio in _BLOCK_PRIORITY.items():
        if block.startswith(marker):
            return prio
    return 0


def _apply_context_budget(blocks: List[str], max_chars: int) -> List[str]:
    """Drop lowest-priority blocks (then truncate memory) until within budget."""
    kept = [b for b in blocks if b and b.strip()]
    total = sum(len(b) for b in kept)
    if total <= max_chars:
        return kept
    # Drop whole low-priority blocks first; never drop the last remaining one.
    for block in sorted(kept, key=_block_priority):
        if total <= max_chars:
            break
        if len(kept) == 1:
            break
        total -= len(block)
        kept.remove(block)
    # If still over (a single top-priority block), truncate it in place.
    if total > max_chars and kept:
        top = kept[0]
        if _block_priority(top) < _BLOCK_PRIORITY["【用户偏好与纠正】"]:
            return []
        if len(top) > max_chars:
            kept[0] = top[: max_chars - 1].rstrip() + "…"
    return kept


def _snip(text: str, n: int = 300) -> str:
    """Truncate a string for compact storage in feedback metadata."""
    text = str(text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def record_turn_feedback(
    turn_id: str,
    positive: bool,
    *,
    user_prompt: str = "",
    assistant_content: str = "",
    correction: Optional[str] = None,
    scope: str = "chat",
) -> dict[str, Any]:
    """Persist a 👍 / 👎 signal raised from the chat UI.

    The signal is written to two sinks so it closes the full loop:

    * ``FeedbackStore``  -> injected into *future* turns (memory activation),
      so the agent visibly "remembers" the user's preference next time.
    * ``Episode.feedback`` -> mined by ``ReflectionJob`` (evolution loop),
      so repeated 👎 on a handler can spawn an ``avoid`` strategy.

    Returns a small status dict; callers may safely ignore it. This function
    never raises — a backend failure simply means the signal is not persisted.
    """
    result: dict[str, Any] = {"feedback_id": None, "episode_updated": 0}
    if not turn_id:
        return result

    try:
        from artpm_agent.memory.feedback_store import (
            KIND_AVOID,
            KIND_PREFERENCE,
            get_default_feedback_store,
        )

        store = get_default_feedback_store()
        if store is None:
            return result

        if positive:
            content = (
                f"用户认可本回合（{turn_id}）的回答方向（👍），"
                f"后续同类请求可沿用此风格。"
            )
            meta = {
                "turn_id": turn_id,
                "user_prompt": _snip(user_prompt, 300),
                "assistant_content": _snip(assistant_content, 300),
                "signal": "positive",
            }
            result["feedback_id"] = store.add(
                KIND_PREFERENCE, content, scope=scope, metadata=meta
            )
            episode_fb = f"👍 用户认可（回合 {turn_id}）"
        else:
            has_reason = bool(correction and correction.strip())
            content = correction.strip() if has_reason else (
                f"用户对本回合（{turn_id}）的回答不满意（👎）。"
            )
            meta = {
                "turn_id": turn_id,
                "user_prompt": _snip(user_prompt, 300),
                "assistant_content": _snip(assistant_content, 300),
                "signal": "negative",
                # Bare negatives are noise if injected; only concrete reasons
                # (user-authored text) become an injected "avoid" preference.
                "no_inject": not has_reason,
            }
            result["feedback_id"] = store.add(
                KIND_AVOID, content, scope=scope, metadata=meta
            )
            episode_fb = (
                f"👎 用户反馈（回合 {turn_id}）：{correction.strip()}"
                if has_reason
                else f"👎 用户不满意（回合 {turn_id}）"
            )

        # Tag the episode so ReflectionJob can mine it in the next auto-run.
        try:
            from artpm_agent.memory.episode_store import EpisodeStore
            from artpm_agent.harness import outcome_recorder

            ep_store = EpisodeStore(outcome_recorder.default_episode_db_path())
            result["episode_updated"] = ep_store.set_feedback(turn_id, episode_fb)
            if result["episode_updated"] <= 0:
                cached_store = getattr(outcome_recorder, "_DEFAULT_STORE", None)
                if cached_store is not None:
                    result["episode_updated"] = cached_store.set_feedback(
                        turn_id,
                        episode_fb,
                    )
        except Exception:  # noqa: BLE001 - reflection tag is best-effort
            pass
    except Exception:  # noqa: BLE001 - never break the UI on a feedback click
        pass
    return result
