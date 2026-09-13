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

from collections.abc import Mapping
from dataclasses import dataclass
from inspect import Parameter, signature
from threading import RLock
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


def _make_llm_callable(runtime: Any):
    """Extract a best-effort compressor callable from the public runtime."""
    if runtime is None:
        return None
    factory = getattr(runtime, "make_llm_callable", None)
    return factory() if callable(factory) else None


_COMPRESSION_INJECTOR_ATTR = "_artpm_compression_injector"
_COMPRESSION_INJECTOR_LOCK = RLock()


def _compression_injector(knowledge_store: Any, runtime: Any):
    """Reuse compression state for the lifetime of the owning store/runtime."""
    from artpm_agent.memory.memory_injector import MemoryInjector

    owner = knowledge_store
    if owner is None:
        owner = getattr(runtime, "target", None) or runtime
    if owner is None:
        return MemoryInjector(knowledge_store)

    with _COMPRESSION_INJECTOR_LOCK:
        cached = getattr(owner, _COMPRESSION_INJECTOR_ATTR, None)
        if isinstance(cached, MemoryInjector) and cached.store is knowledge_store:
            return cached
        injector = MemoryInjector(knowledge_store)
        try:
            setattr(owner, _COMPRESSION_INJECTOR_ATTR, injector)
        except (AttributeError, TypeError):
            # Slot-only third-party stores retain the previous best-effort path.
            return injector
        return injector


def _build_query(user_input: str, history: Optional[List[dict]]) -> str:
    """Compose a bounded query without feeding model output back into recall."""
    current = " ".join(str(user_input or "").split())
    if not current:
        return ""

    # Assistant messages can contain guesses or stale facts. Using them as
    # retrieval terms reinforces those guesses and can displace user-confirmed
    # memories, so only recent user turns are eligible as disambiguating context.
    parts = [current[:800]]
    seen = {_snippet_key(current)}
    previous_user_turns: list[str] = []
    for message in reversed(list(history or [])):
        if isinstance(message, dict):
            if str(message.get("role") or "").casefold() != "user":
                continue
            text = str(message.get("content") or message.get("text") or "")
        elif isinstance(message, str):
            text = message
        else:
            continue
        clean = " ".join(text.split())
        key = _snippet_key(clean)
        if not clean or not key or key in seen:
            continue
        seen.add(key)
        previous_user_turns.append(clean[:300])
        if len(previous_user_turns) >= 2:
            break

    parts.extend(reversed(previous_user_turns))
    return "\n".join(parts)[:1200].rstrip()


def _snippet_key(text: str) -> str:
    # Do not truncate the identity: two documents can share a long template
    # prefix and contain different facts near the end.
    return " ".join(str(text or "").casefold().split())


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
    if title:
        clean = f"{title}：{clean}"
    clean = clean[:max_chars].rstrip()
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


def _scoped_active_entries(store: Any, scope: Any) -> list[Any]:
    """Read behavior memory inside the authenticated turn scope."""

    active = getattr(store, "active", None)
    if not callable(active):
        return []
    kwargs = {
        "tenant_id": scope.tenant_id,
        "workspace_id": scope.workspace_id,
        "principal_id": scope.actor_id,
    }
    try:
        return list(active(**kwargs) or [])
    except TypeError:
        try:
            return list(
                active(
                    tenant_id=scope.tenant_id,
                    workspace_id=scope.workspace_id,
                )
                or []
            )
        except TypeError:
            if (
                scope.tenant_id != "local"
                or scope.workspace_id != "local-default"
            ):
                return []
            return list(active() or [])


def retrieve_memory_context(
    user_input: str,
    history: Optional[List[dict]] = None,
    *,
    memory_manager: Optional[Any] = None,
    tencentdb_memory: Optional[Any] = None,
    tencentdb_scope: Optional[Any] = None,
    knowledge_store: Optional[Any] = None,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
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

    if tencentdb_memory is not None and tencentdb_scope is not None:
        try:
            context = tencentdb_memory.recall(query, tencentdb_scope)
            _append_snippet(
                buckets,
                seen,
                "TencentDB Agent Memory",
                context,
                max_chars=min(max_total_chars, max_snippet_chars * top_k),
            )
        except Exception:  # noqa: BLE001 - recall must never break a turn
            pass

    if (
        tencentdb_memory is None
        and memory_manager is not None
        and hasattr(memory_manager, "retrieve")
    ):
        try:
            filters: dict[str, Any] = {}
            if tenant_id:
                filters["tenant_id"] = tenant_id
            if workspace_id:
                filters["workspace_id"] = workspace_id
            try:
                results = memory_manager.retrieve(
                    query,
                    top_k=top_k,
                    filters=filters or None,
                )
            except TypeError:
                if (
                    tenant_id and tenant_id != "local"
                ) or (
                    workspace_id and workspace_id != "local-default"
                ):
                    raise
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
        # Accepted rules are a separate authoritative channel. Retrieve them
        # independently so a backend/search adapter failure cannot hide a
        # confirmed workspace constraint behind a fuzzy-document exception.
        list_rules = getattr(knowledge_store, "get_active_rules", None)
        if callable(list_rules):
            try:
                active_rules = _invoke_scoped(
                    list_rules,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id or getattr(
                        knowledge_store, "DEFAULT_WORKSPACE_ID", "local-default"
                    ),
                    limit=top_k,
                )
                for rule in active_rules or []:
                    if isinstance(rule, Mapping):
                        _append_snippet(
                            buckets,
                            seen,
                            "工作区规则",
                            str(rule.get("statement") or ""),
                            title="已采纳规则",
                            max_chars=max_snippet_chars,
                        )
            except Exception:  # noqa: BLE001 - rule recall is best effort
                pass
        try:
            try:
                results = _invoke_scoped(
                    knowledge_store.search,
                    query,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    limit=top_k,
                    include_rules=False,
                    max_text_chars=max_snippet_chars,
                    use_confidence=True,
                    confidence_floor=confidence_floor,
                )
            except TypeError:
                compatibility_kwargs: dict[str, Any] = {"limit": top_k}
                if tenant_id:
                    compatibility_kwargs["tenant_id"] = tenant_id
                if workspace_id:
                    compatibility_kwargs["workspace_id"] = workspace_id
                try:
                    results = knowledge_store.search(query, **compatibility_kwargs)
                except TypeError:
                    default_workspace = getattr(
                        knowledge_store,
                        "DEFAULT_WORKSPACE_ID",
                        "local-default",
                    )
                    if (
                        tenant_id and tenant_id != "local"
                    ) or (
                        workspace_id and workspace_id != default_workspace
                    ):
                        # A legacy backend that cannot scope its query must fail
                        # closed rather than leak the default workspace.
                        raise
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
    return _format_retrieved_buckets(
        buckets,
        top_k=top_k,
        max_total_chars=max_total_chars,
    )


def _invoke_scoped(
    method: Callable[..., Any],
    *args: Any,
    tenant_id: Optional[str],
    workspace_id: Optional[str],
    **kwargs: Any,
) -> Any:
    """Invoke a backend only when its supported arguments preserve scope."""

    try:
        parameters = list(signature(method).parameters.values())
    except (TypeError, ValueError) as error:
        raise TypeError("scoped backend signature is unavailable") from error
    names = {parameter.name for parameter in parameters}
    supports_kwargs = any(
        parameter.kind is Parameter.VAR_KEYWORD for parameter in parameters
    )
    if workspace_id:
        if "workspace_id" in names or supports_kwargs:
            kwargs["workspace_id"] = workspace_id
        elif workspace_id != "local-default":
            raise TypeError("backend must support workspace-scoped retrieval")
    if tenant_id:
        if "tenant_id" in names or supports_kwargs:
            kwargs["tenant_id"] = tenant_id
        elif tenant_id != "local":
            raise TypeError("backend must support tenant-scoped retrieval")
    return method(*args, **kwargs)


def tencentdb_scope_from_context(ctx: Any, tencentdb_memory: Any) -> Any:
    """Build an opaque TencentDB memory scope from a trusted turn context."""

    if tencentdb_memory is None:
        return None
    try:
        from artpm_agent.memory.tencentdb_agent_memory import (
            TencentDBAgentMemoryScope,
        )
        from artpm_agent.tenancy import TenantContext

        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            return None
        tenant_context = extra.get("tenant_context")
        if isinstance(tenant_context, TenantContext):
            tenant_id = tenant_context.tenant_id
            workspace_id = tenant_context.require_workspace(
                extra.get("workspace_id")
            )
            principal_id = tenant_context.principal_id
        else:
            workspace_id = str(extra.get("workspace_id") or "").strip()
            tenant_id = str(extra.get("tenant_id") or "").strip()
            principal_id = str(
                extra.get("actor_id")
                or extra.get("principal_id")
                or extra.get("user_id")
                or ""
            ).strip()
            if workspace_id not in {"", "local-default"} or tenant_id not in {
                "",
                "local",
            }:
                return None
            tenant_id = tenant_id or "local"
            workspace_id = workspace_id or "local-default"
            principal_id = principal_id or "local-user"
        settings = getattr(tencentdb_memory, "settings", None)
        agent_id = str(
            extra.get("agent_id")
            or getattr(settings, "agent_id", "artpm-agent")
        ).strip()
        conversation_id = str(
            extra.get("conversation_id")
            or getattr(ctx, "conversation_id", "")
        ).strip()
        return TencentDBAgentMemoryScope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            principal_id=principal_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
        )
    except Exception:  # noqa: BLE001 - a missing trusted scope must fail closed
        return None


def _format_retrieved_buckets(
    buckets: dict[str, list[str]],
    *,
    top_k: int,
    max_total_chars: int,
) -> str:
    """Render recall fairly so one backend cannot consume the whole budget."""
    bucket_names = [
        name
        for name in (
            "TencentDB Agent Memory",
            "长期记忆",
            "工作区规则",
            "工作区资料",
        )
        if buckets.get(name)
    ]
    if not bucket_names or max_total_chars <= 0:
        return ""

    selected: dict[str, list[str]] = {name: [] for name in bucket_names}
    fixed_cost = len("【相关记忆】\n") + sum(
        len(f"{name}：\n") for name in bucket_names
    )
    remaining = max(0, max_total_chars - fixed_cost)

    # Reserve an equal slice for each source's best hit. If both top snippets do
    # not fit, keep bounded excerpts from both instead of dropping one source.
    first_lines = {name: f"- {buckets[name][0]}" for name in bucket_names}
    first_cost = sum(len(line) + 1 for line in first_lines.values())
    if first_cost <= remaining:
        for name, line in first_lines.items():
            selected[name].append(line)
            remaining -= len(line) + 1
    else:
        per_source = remaining // len(bucket_names)
        for name, line in first_lines.items():
            if per_source <= 4:
                continue
            bounded = line[: per_source - 2].rstrip() + "…"
            selected[name].append(bounded)
            remaining -= len(bounded) + 1

    # Continue round-robin so ranking is preserved inside each source.
    for index in range(1, top_k):
        for name in bucket_names:
            snippets = buckets[name]
            if index >= len(snippets):
                continue
            line = f"- {snippets[index]}"
            cost = len(line) + 1
            if cost <= remaining:
                selected[name].append(line)
                remaining -= cost

    if not any(selected.values()):
        return ""
    lines = ["【相关记忆】"]
    for name in bucket_names:
        if not selected[name]:
            continue
        lines.append(f"{name}：")
        lines.extend(selected[name])
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
            return text
    # WorkspaceKnowledgeStore.search returns resource rows
    if "text" in record:
        return str(record.get("text", "")).strip()
    if "searchable_text" in record:
        return str(record.get("searchable_text", "")).strip()
    if "content" in record and isinstance(record["content"], str):
        return record["content"].strip()
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
    tencentdb_memory: Optional[Any] = None,
    knowledge_store: Optional[Any] = None,
    feedback_store: Optional[Any] = None,
    strategy_store: Optional[Any] = None,
    meta_memory_store: Optional[Any] = None,
    max_context_chars: int = 0,
    max_context_tokens: int = 0,
) -> None:
    """Append retrieved memory + preferences + strategies to ``ctx.knowledge_context``.

    Backends default to ``ctx.runtime.memory_manager`` (if present) and the lazily-built
    default FeedbackStore / StrategyStore, so callers can invoke this with no
    arguments and get automatic memory activation.
    """
    if not isinstance(getattr(ctx, "extra", None), dict):
        ctx.extra = {}

    # Idempotency: run_turn() calls this exactly once per turn. A second call
    # (e.g. legacy pre-injection) must not double-append context. The explicit
    # field is authoritative for TurnContext; the extra flag remains for simple
    # namespace callers and older hosts.
    if getattr(ctx, "memory_injected", False) or ctx.extra.get("memory_injected"):
        return

    budget = MemoryContextBudget()

    runtime = getattr(ctx, "runtime", None)
    if runtime is None:
        # ``inject_memory_context`` is also a public helper used with simple
        # namespace contexts outside TurnContext. Keep that call shape
        # compatible while still routing through the adapter boundary.
        from .runtime import adapt_runtime

        runtime = adapt_runtime(agent=getattr(ctx, "agent", None))

    if memory_manager is None:
        memory_manager = getattr(runtime, "memory_manager", None)
    if tencentdb_memory is None:
        tencentdb_memory = getattr(runtime, "tencentdb_memory", None)
    if feedback_store is None:
        feedback_store = get_default_feedback_store()
    if strategy_store is None:
        strategy_store = get_default_strategy_store()
    if meta_memory_store is None:
        from artpm_agent.evolution.meta_memory import get_default_meta_memory_store

        meta_memory_store = get_default_meta_memory_store()

    blocks: List[str] = []
    seen_blocks: set[str] = set()

    def _add(block: str) -> None:
        block = (block or "").strip()
        if not block:
            return
        key = _snippet_key(block)
        if key in seen_blocks:
            return
        seen_blocks.add(key)
        blocks.append(block)

    existing_knowledge = str(getattr(ctx, "knowledge_context", "") or "").strip()
    if existing_knowledge:
        _add(existing_knowledge)  # user-confirmed workspace facts (highest prio)

    from .turn_service import get_turn_scope

    try:
        turn_scope = get_turn_scope(ctx)
        tenant_id = turn_scope.tenant_id or None
        workspace_id = turn_scope.workspace_id or None
    except Exception:  # noqa: BLE001 - untrusted helper scopes fail closed
        return
    tencentdb_scope = tencentdb_scope_from_context(ctx, tencentdb_memory)

    # Conversation compression summary (best-effort): keep in-turn continuity
    # for long dialogues without re-running cross-session RAG.
    try:
        history = list(getattr(ctx, "conversation_history", None) or [])
        if history:
            llm_fn = _make_llm_callable(runtime)
            comp = _compression_injector(knowledge_store, runtime).compression_block(
                history,
                getattr(ctx, "conversation_id", "") or "",
                llm_fn,
                workspace_id=workspace_id or "local-default",
                tenant_id=tenant_id or "local",
            )
            if comp:
                _add("【对话摘要】\n" + comp)
    except Exception:  # noqa: BLE001
        pass

    memory_block = ""
    try:
        memory_block = retrieve_memory_context(
            ctx.user_input,
            getattr(ctx, "conversation_history", None),
            memory_manager=memory_manager,
            tencentdb_memory=tencentdb_memory,
            tencentdb_scope=tencentdb_scope,
            knowledge_store=knowledge_store,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            top_k=budget.top_k,
            max_snippet_chars=budget.max_snippet_chars,
            max_total_chars=budget.max_total_chars,
            confidence_floor=budget.confidence_floor,
        )
        if memory_block:
            _add(memory_block)
    except Exception:  # noqa: BLE001
        pass

    if feedback_store is not None:
        try:
            fb_block = format_feedback_context(
                _scoped_active_entries(feedback_store, turn_scope),
                max_entries=budget.max_feedback_entries,
            )
            if fb_block:
                _add(fb_block)
        except Exception:  # noqa: BLE001
            pass

    if strategy_store is not None:
        try:
            st_block = format_strategy_context(
                _scoped_active_entries(strategy_store, turn_scope),
                max_entries=budget.max_strategy_entries,
            )
            if st_block:
                _add(st_block)
        except Exception:  # noqa: BLE001
            pass

    # Phase 4 — 元记忆：识别知识缺口并给出「联网检索 / 向用户澄清」建议。
    # 仅在确有缺口时追加，避免无谓污染每轮上下文。
    try:
        from artpm_agent.evolution.meta_memory import (
            format_meta_memory_context,
            get_default_meta_memory,
        )

        meta = get_default_meta_memory().analyze(
            ctx.user_input,
            retrieved_block=memory_block,
            knowledge_store=knowledge_store,
            strategy_store=strategy_store,
            feedback_store=feedback_store,
            meta_store=meta_memory_store,
            workspace_id=workspace_id,
            tenant_id=turn_scope.tenant_id,
            principal_id=turn_scope.actor_id,
        )
        meta_block = format_meta_memory_context(meta)
        if meta_block:
            _add(meta_block)
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
    if hasattr(ctx, "memory_injected"):
        ctx.memory_injected = True
    ctx.extra["memory_injected"] = True


# Priority for context budgeting: higher = kept first when over budget.
# Ordering (P1): the user's confirmed workspace facts beat corrections, which
# beat learned strategies, which beat fuzzy long-term recall, which beat
# meta-memory gap suggestions. The conversation compression summary is kept
# near the top because it carries in-turn continuity.
_BLOCK_PRIORITY = {
    # existing ctx.knowledge_context (user-confirmed workspace facts) is handled
    # specially with EXISTING_PRIORITY below.
    "【对话摘要】": 5,
    "【用户偏好与纠正】": 4,
    "【优化策略": 3,
    "【相关记忆】": 2,
    "【元记忆": 1,
}
# The pre-existing knowledge_context (workspace facts supplied by the caller)
# always wins ties; treat it as the highest priority block.
EXISTING_PRIORITY = 6


def _block_priority(block: str) -> int:
    for marker, prio in _BLOCK_PRIORITY.items():
        if block.startswith(marker):
            return prio
    # Unmarked blocks are the caller-supplied workspace facts, which must win
    # any budget tie over the heuristically-retrieved blocks above.
    return EXISTING_PRIORITY


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
    # If still over (a single remaining block), truncate it in place to fit
    # the budget — dropping it entirely would lose information we can keep as
    # a fragment. The budget is always honoured either way.
    if total > max_chars and kept:
        top = kept[0]
        if len(top) > max_chars:
            kept[0] = top[: max_chars - 1].rstrip() + "…"
    return kept


def _snip(text: str, n: int = 300) -> str:
    """Truncate a string for compact storage in feedback metadata."""
    text = str(text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# Structured negative-feedback categories (P1). Free-text "差/不好" is too
# coarse to drive learning; a category lets reflection mine recurring failure
# modes without guessing the user's intent.
FEEDBACK_CATEGORIES: tuple[str, ...] = (
    "too_verbose",     # 太啰嗦
    "too_brief",       # 太简短
    "not_direct",      # 没直接回答
    "ignored_context", # 忽略上下文/已知信息
    "factual_error",   # 事实错误
    "wrong_format",    # 格式不对
    "wrong_tool",      # 用错工具/能力
    "other",           # 其他
)


def record_turn_feedback(
    turn_id: str,
    positive: bool,
    *,
    user_prompt: str = "",
    assistant_content: str = "",
    correction: Optional[str] = None,
    category: str = "",
    scope: str = "chat",
    tenant_context: Optional[Any] = None,
    feedback_store: Optional[Any] = None,
    episode_store: Optional[Any] = None,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    principal_id: Optional[str] = None,
) -> dict[str, Any]:
    """Persist a 👍 / 👎 signal raised from the chat UI.

    The signal is written to two sinks so it closes the full loop:

    * ``FeedbackStore``  -> mined by the evolution loop (reflection). A *bare*
      👎 is recorded but NOT injected into future prompts (it carries no
      concrete instruction). A 👍 is recorded for reflection too, but is no
      longer auto-injected as a global "use this style" preference — that was
      noisy and conflicted with the Profile's own response_style.
    * ``Episode.feedback`` -> mined by ``ReflectionJob`` so repeated 👎 on a
      handler can spawn an ``avoid`` strategy.

    Returns a small status dict; callers may safely ignore it. This function
    never raises — a backend failure simply means the signal is not persisted.
    """
    result: dict[str, Any] = {"feedback_id": None, "episode_updated": 0}
    if not turn_id:
        return result

    category = category or ""
    if category and category not in FEEDBACK_CATEGORIES:
        category = "other"

    if tenant_context is not None:
        try:
            from artpm_agent.tenancy import TenantContext

            if not isinstance(tenant_context, TenantContext):
                return result
            context_values = {
                "tenant_id": tenant_context.tenant_id,
                "workspace_id": tenant_context.workspace_id,
                "principal_id": tenant_context.principal_id,
            }
            for name, value in context_values.items():
                requested = locals()[name]
                if requested and str(requested).strip() != str(value):
                    return result
            tenant_id = context_values["tenant_id"]
            workspace_id = context_values["workspace_id"]
            principal_id = context_values["principal_id"]
        except Exception:  # noqa: BLE001 - feedback must never break the UI
            return result

    try:
        from artpm_agent.memory.feedback_store import (
            KIND_AVOID,
            KIND_PREFERENCE,
            get_default_feedback_store,
        )

        store = feedback_store or get_default_feedback_store()
        tenant_id = str(tenant_id or "").strip()
        workspace_id = str(workspace_id or "").strip()
        principal_id = str(principal_id or "").strip()

        def _add_feedback(kind: str, content: str, metadata: dict[str, Any]) -> Any:
            kwargs: dict[str, Any] = {
                "scope": scope,
                "metadata": metadata,
            }
            if tenant_id or workspace_id or principal_id:
                kwargs.update(
                    tenant_id=tenant_id or None,
                    workspace_id=workspace_id or None,
                    principal_id=principal_id or None,
                )
            return store.add(kind, content, **kwargs)

        if positive:
            # Record the thumbs-up for reflection, but mark it no_inject so it
            # does not pollute every future turn's context with a vague "keep
            # this style" instruction (the Profile owns response style now).
            content = f"用户认可本回合（{turn_id}）的回答方向（👍）。"
            meta = {
                "turn_id": turn_id,
                "user_prompt": _snip(user_prompt, 300),
                "assistant_content": _snip(assistant_content, 300),
                "signal": "positive",
                "no_inject": True,
            }
            if store is not None:
                result["feedback_id"] = _add_feedback(
                    KIND_PREFERENCE, content, meta
                )
            episode_fb = f"👍 用户认可（回合 {turn_id}）"
        else:
            correction_text = correction.strip() if isinstance(correction, str) else ""
            has_reason = bool(correction_text)
            cat_label = _feedback_category_label(category) if category else ""
            if has_reason:
                content = correction_text
                if cat_label:
                    content = f"[{cat_label}] {content}"
            else:
                content = (
                    f"用户对本回合（{turn_id}）的回答不满意（👎）"
                    + (f"：{cat_label}" if cat_label else "。")
                )
            meta = {
                "turn_id": turn_id,
                "user_prompt": _snip(user_prompt, 300),
                "assistant_content": _snip(assistant_content, 300),
                "signal": "negative",
                "category": category or "other",
                # Bare negatives are noise if injected; only concrete reasons
                # (user-authored text) become an injected "avoid" preference.
                "no_inject": not has_reason,
            }
            if store is not None:
                result["feedback_id"] = _add_feedback(KIND_AVOID, content, meta)
            episode_fb = (
                f"👎 用户反馈（回合 {turn_id}）"
                + (f"[{cat_label}]" if cat_label else "")
                + (f"：{correction_text}" if has_reason else "")
            )

        # Tag the episode so ReflectionJob can mine it in the next auto-run.
        try:
            from artpm_agent.harness import outcome_recorder

            if episode_store is None:
                from artpm_agent.memory.episode_store import EpisodeStore

                episode_store = EpisodeStore(outcome_recorder.default_episode_db_path())
            feedback_kwargs: dict[str, Any] = {}
            if tenant_id or workspace_id or principal_id:
                feedback_kwargs.update(
                    tenant_id=tenant_id or None,
                    workspace_id=workspace_id or None,
                    principal_id=principal_id or None,
                )
            result["episode_updated"] = episode_store.set_feedback(
                turn_id,
                episode_fb,
                **feedback_kwargs,
            )
            if result["episode_updated"] <= 0:
                cached_store = getattr(outcome_recorder, "_DEFAULT_STORE", None)
                if cached_store is not None and cached_store is not episode_store:
                    result["episode_updated"] = cached_store.set_feedback(
                        turn_id,
                        episode_fb,
                        **feedback_kwargs,
                    )
        except Exception:  # noqa: BLE001 - reflection tag is best-effort
            pass
    except Exception:  # noqa: BLE001 - never break the UI on a feedback click
        pass
    return result


def _feedback_category_label(category: str) -> str:
    return {
        "too_verbose": "太啰嗦",
        "too_brief": "太简短",
        "not_direct": "没直接回答",
        "ignored_context": "忽略上下文",
        "factual_error": "事实错误",
        "wrong_format": "格式不对",
        "wrong_tool": "用错工具",
        "other": "其他",
    }.get(category, "其他")
