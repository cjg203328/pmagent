"""Token-aware context budgeting (Phase 4 extension of MemoryContextBudget).

The original :class:`MemoryContextBudget` capped injected memory by *characters*.
For long Chinese conversations that over/under-estimates token cost badly. This
module adds **token-accurate** budgeting:

* :func:`estimate_tokens` — tiktoken when available, else a CJK-aware heuristic
  (≈1.6 chars/token for CJK, ≈4 chars/token for Latin).
* :func:`compact_text` — lossy cleanup (drop duplicate/blank lines, collapse
  whitespace) that shrinks a block before truncation.
* :func:`apply_token_budget` — same priority-drop-then-truncate rule as the
  character budget, but measured in tokens, with an optional ``summarize_fn``
  hook for LLM summarization of an over-budget block.

All best-effort and additive: :func:`inject_memory_context` only uses the token
path when ``max_context_tokens > 0``.
"""

from __future__ import annotations

import math
import re
from typing import Callable, List, Optional

try:  # tiktoken is optional; heuristic fallback keeps us dependency-free.
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
except Exception:  # noqa: BLE001
    tiktoken = None  # type: ignore[assignment]
    _TIKTOKEN_AVAILABLE = False

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")


def estimate_tokens(
    text: str,
    *,
    model: str = "",
    counter: Optional[Callable[[str], int]] = None,
) -> int:
    """Estimate token count. Prefer ``counter``, then tiktoken, then heuristic."""
    if counter is not None:
        try:
            return max(0, int(counter(text) or 0))
        except Exception:  # noqa: BLE001 - fall through to built-in estimate
            pass
    if _TIKTOKEN_AVAILABLE and model:
        try:
            enc = tiktoken.encoding_for_model(model)
            return len(enc.encode(text or ""))
        except Exception:  # noqa: BLE001
            pass
    return _heuristic_token_count(text or "")


def _heuristic_token_count(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    non_cjk = len(text) - cjk
    # CJK ~1.6 chars/token; Latin ~4 chars/token.
    tokens = cjk / 1.6 + non_cjk / 4.0
    return max(1, math.ceil(tokens))


def compact_text(text: str, *, keep_ratio: float = 0.85) -> str:
    """Lossy cleanup: drop duplicate/blank lines, collapse whitespace.

    ``keep_ratio`` is a safety bound — if the cleaned text is still longer than
    ``keep_ratio`` of the original, the excess tail is dropped before any further
    token truncation (cheap redundancy removal for very long blocks).
    """
    original_len = len(text)
    lines = [ln.strip() for ln in text.splitlines()]
    seen: set[str] = set()
    deduped: List[str] = []
    for ln in lines:
        if not ln:
            continue
        if ln in seen:
            continue
        seen.add(ln)
        deduped.append(ln)
    cleaned = "\n".join(deduped)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    # Safety bound relative to the ORIGINAL length: only drop the tail when the
    # cleaned text is still longer than ``keep_ratio`` of the source. A short
    # text that dedups cleanly must never be truncated by this step.
    if keep_ratio < 1.0 and original_len > 0:
        limit = max(0, int(original_len * keep_ratio))
        if len(cleaned) > limit:
            cleaned = cleaned[:limit].rstrip()
    return cleaned


def _truncate_tokens(
    text: str,
    max_tokens: int,
    *,
    model: str = "",
    counter: Optional[Callable[[str], int]] = None,
) -> str:
    """Token-accurate prefix truncation, including the marker in the budget."""
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text, model=model, counter=counter) <= max_tokens:
        return text

    marker = "…"
    marker_tokens = estimate_tokens(marker, model=model, counter=counter)
    if marker_tokens > max_tokens:
        return ""

    # Search for the longest prefix whose prefix plus truncation marker still
    # fits. Counting the marker here prevents a subtle prompt-budget overflow.
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid].rstrip() + marker
        if estimate_tokens(candidate, model=model, counter=counter) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + marker if lo else marker


def apply_token_budget(
    blocks: List[str],
    max_tokens: int,
    *,
    counter: Optional[Callable[[str], int]] = None,
    model: str = "",
    compression: str = "truncate",
    priority_fn: Optional[Callable[[str], int]] = None,
    summarize_fn: Optional[Callable[[str], str]] = None,
) -> List[str]:
    """Bound injected context by tokens, mirroring the char-based budget.

    Drops lowest-priority whole blocks first (never the last one), then
    compresses + truncates the surviving top block to fit.
    """
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise TypeError("max_tokens must be an integer")
    if max_tokens < 0:
        raise ValueError("max_tokens must be non-negative")

    kept = [b for b in blocks if b and b.strip()]
    if max_tokens == 0:
        return []

    def _count(b: str) -> int:
        return estimate_tokens(b, model=model, counter=counter)

    def _prio(b: str) -> int:
        return priority_fn(b) if priority_fn is not None else 0

    def _joined_count(items: List[str]) -> int:
        # The caller joins blocks with two newlines. Include that separator in
        # the budget so the final rendered context cannot exceed the limit.
        return _count("\n\n".join(items))

    total = _joined_count(kept)
    if total <= max_tokens:
        return kept

    # Drop whole low-priority blocks first.
    for block in sorted(kept, key=_prio):
        if total <= max_tokens:
            break
        if len(kept) == 1:
            break
        kept.remove(block)
        total = _joined_count(kept)

    # Still over: compress then token-truncate the top surviving block.
    if total > max_tokens and kept:
        top = kept[0]
        if compression == "compact":
            top = compact_text(top)
        if summarize_fn is not None:
            try:
                summary = summarize_fn(top)
                if summary and summary.strip():
                    top = summary.strip()
            except Exception:  # noqa: BLE001 - summarizer is best-effort
                pass
        kept[0] = _truncate_tokens(top, max_tokens, model=model, counter=counter)
    return kept
