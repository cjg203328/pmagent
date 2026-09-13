"""Tool execution pipeline: ordered pre/post policies + result spilling.

Aligns the runtime with the deepseek-harness tool pipeline shape
(``pre-execute -> execute -> post-execute``) without breaking the existing
single-hook ``AgentLoop`` API:

- A pipeline holds an ordered list of pre policies and post policies.
- Pre policies run before execution; the first policy that returns a
  ``BeforeToolCallDecision`` short-circuits (approve/reject/defer).
- Post policies run after execution in order, each able to rewrite the
  ``ToolResult``.
- ``spill_policy`` truncates oversized tool results to a bounded summary and
  writes the full payload to a spill directory, so a huge tool result never
  blows the model context budget (the dsh tool-result spill idea).

``AgentLoop`` accepts an optional pipeline and composes it with any legacy
single hooks, keeping existing callers unchanged.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from .tools import (
    AgentTool,
    BeforeToolCallDecision,
    ToolCall,
    ToolResult,
)

#: Same shapes as the existing runtime hook types.
PreToolPolicy = Callable[
    [ToolCall, AgentTool, dict[str, Any], dict[str, Any]],
    Optional[BeforeToolCallDecision],
]
PostToolPolicy = Callable[
    [ToolCall, AgentTool, ToolResult, dict[str, Any]],
    Optional[ToolResult],
]

#: Default spill budget mirrors the harness editor cap (16k chars).
DEFAULT_SPILL_MAX_CHARS = 16_000
DEFAULT_SPILL_DIR_NAME = "tool_spill"
DEFAULT_SUMMARY_SUFFIX = (
    "\n…[output truncated; full result spilled to {path}]"
)


class ToolExecutionPipeline:
    """Ordered pre/post policy chain around one tool execution."""

    def __init__(self) -> None:
        self._pre: list[PreToolPolicy] = []
        self._post: list[PostToolPolicy] = []

    # ── assembly ──

    def add_pre(self, policy: PreToolPolicy) -> "ToolExecutionPipeline":
        if not callable(policy):
            raise TypeError("pre policy must be callable")
        self._pre.append(policy)
        return self

    def add_post(self, policy: PostToolPolicy) -> "ToolExecutionPipeline":
        if not callable(policy):
            raise TypeError("post policy must be callable")
        self._post.append(policy)
        return self

    @property
    def pre_count(self) -> int:
        return len(self._pre)

    @property
    def post_count(self) -> int:
        return len(self._post)

    def clear(self) -> None:
        self._pre.clear()
        self._post.clear()

    # ── composition with legacy single hooks ──

    def compose_pre(
        self,
        user_hook: Optional[Callable[..., Optional[BeforeToolCallDecision]]] = None,
    ) -> Optional[Callable[..., Optional[BeforeToolCallDecision]]]:
        """Return a combined pre hook: pipeline policies, then the user hook."""
        policies = list(self._pre)

        def combined(call, tool, context, turn_ctx):
            for policy in policies:
                decision = policy(call, tool, context, turn_ctx)
                if decision is not None:
                    return decision
            if user_hook is not None:
                return user_hook(call, tool, context, turn_ctx)
            return None

        return combined if (policies or user_hook is not None) else None

    def compose_post(
        self,
        user_hook: Optional[Callable[..., Optional[ToolResult]]] = None,
    ) -> Optional[Callable[..., Optional[ToolResult]]]:
        """Return a combined post hook: pipeline policies in order, then the
        user hook; each result feeds the next stage."""
        policies = list(self._post)

        def combined(call, tool, result, context):
            current: Optional[ToolResult] = result
            for policy in policies:
                rewritten = policy(call, tool, current, context)
                if rewritten is not None:
                    current = rewritten
            if user_hook is not None:
                rewritten = user_hook(call, tool, current, context)
                if rewritten is not None:
                    current = rewritten
            return current

        return combined if (policies or user_hook is not None) else None


# ── result spilling ──

def spill_tool_result(
    result: ToolResult,
    *,
    max_chars: int = DEFAULT_SPILL_MAX_CHARS,
    spill_dir: Optional[Path | str] = None,
    tool_name: str = "tool",
) -> ToolResult:
    """Truncate an oversized tool result and spill the full payload to disk.

    The returned ``ToolResult`` keeps a bounded summary ending with a pointer
    to the spilled file; ``details`` records ``spilled_path`` and the original
    length so callers (UI, replay) can fetch the full payload if needed.
    """
    if not isinstance(max_chars, int) or max_chars < 512:
        raise ValueError("max_chars must be an integer >= 512")
    content = result.content
    if len(content) <= max_chars:
        return result
    if spill_dir is None:
        spill_dir = Path.cwd() / DEFAULT_SPILL_DIR_NAME
    spill_path = Path(spill_dir)
    spill_path.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    filename = f"{tool_name}-{digest}-{uuid4().hex[:8]}.txt"
    target = spill_path / filename
    target.write_text(content, encoding="utf-8")
    suffix = DEFAULT_SUMMARY_SUFFIX.format(path=str(target))
    head_limit = max_chars - len(suffix)
    if head_limit > 0:
        summary = content[:head_limit] + suffix
    else:
        summary = suffix[:max_chars]
    details = dict(result.details)
    details["spilled_path"] = str(target)
    details["spilled_original_length"] = len(content)
    details["spilled_summary_length"] = len(summary)
    return ToolResult(
        content=summary,
        details=details,
        is_error=result.is_error,
        terminate=result.terminate,
    )


def spill_policy(
    *,
    max_chars: int = DEFAULT_SPILL_MAX_CHARS,
    spill_dir: Optional[Path | str] = None,
) -> PostToolPolicy:
    """Build a post policy that spills any result longer than ``max_chars``."""

    def policy(
        call: ToolCall,
        tool: AgentTool,
        result: ToolResult,
        context: dict[str, Any],
    ) -> Optional[ToolResult]:
        if result is None or result.is_error:
            return None
        return spill_tool_result(
            result,
            max_chars=max_chars,
            spill_dir=spill_dir,
            tool_name=tool.name or call.name,
        )

    return policy


def load_spilled_result(path: Path | str) -> str:
    """Read a spilled tool-result payload back (for UI/replay)."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise FileNotFoundError(f"spilled tool result is unavailable: {error}") from None
