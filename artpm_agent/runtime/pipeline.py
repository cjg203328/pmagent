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
import json
import math
import re
import time
from collections.abc import Mapping
from pathlib import Path
from threading import RLock
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
    [ToolCall, AgentTool, Mapping[str, Any], Mapping[str, Any]],
    Optional[BeforeToolCallDecision],
]
PostToolPolicy = Callable[
    [ToolCall, AgentTool, ToolResult, Mapping[str, Any]],
    Optional[ToolResult],
]

#: Default spill budget mirrors the harness editor cap (16k chars).
DEFAULT_SPILL_MAX_CHARS = 16_000
DEFAULT_DETAILS_MAX_CHARS = 16_000
DEFAULT_SPILL_DIR_NAME = "tool_spill"
DEFAULT_SPILL_TTL_SECONDS = 24 * 60 * 60
DEFAULT_SUMMARY_SUFFIX = (
    "\n…[output truncated; full result spilled to {path}]"
)
_MANAGED_SPILL_SUFFIXES = frozenset({".json", ".txt"})
_ACTIVE_SPILL_PATHS: set[Path] = set()
_ACTIVE_SPILL_LOCK = RLock()


def _json_safe(value: Any, *, _seen: Optional[set[int]] = None) -> Any:
    """Return a deterministic JSON-compatible copy of arbitrary details."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)

    seen = _seen if _seen is not None else set()
    if isinstance(value, (Mapping, list, tuple)):
        identity = id(value)
        if identity in seen:
            return "<recursive>"
        seen.add(identity)
        try:
            if isinstance(value, Mapping):
                return {
                    str(key): _json_safe(item, _seen=seen)
                    for key, item in value.items()
                }
            return [_json_safe(item, _seen=seen) for item in value]
        finally:
            seen.remove(identity)

    try:
        return str(value)
    except Exception:  # pragma: no cover - hostile third-party repr/str
        return f"<{value.__class__.__name__}>"


def _safe_tool_filename(tool_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(tool_name)).strip("._")
    return (normalized or "tool")[:80]


def _scope_component(value: Any, *, fallback: str, prefix: str) -> str:
    raw = str(value or "").strip() or fallback
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")[:40]
    return f"{prefix}-{slug or 'scope'}-{digest}"


def _spill_root(spill_dir: Optional[Path | str], *, create: bool) -> Path:
    root = Path(spill_dir or (Path.cwd() / DEFAULT_SPILL_DIR_NAME)).expanduser()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root.resolve(strict=False)


def _require_within(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError("spill path escapes its managed scope") from None
    return resolved


def spill_scope_directory(
    *,
    spill_dir: Optional[Path | str] = None,
    tenant_id: Any = None,
    workspace_id: Any = None,
    create: bool = True,
) -> Path:
    """Return a traversal-safe tenant/workspace directory under the spill root."""
    root = _spill_root(spill_dir, create=create)
    tenant_path = root / _scope_component(
        tenant_id,
        fallback="local",
        prefix="tenant",
    )
    target = tenant_path / _scope_component(
        workspace_id,
        fallback="local-default",
        prefix="workspace",
    )
    if tenant_path.is_symlink() or target.is_symlink():
        raise ValueError("symbolic links are not valid spill scope directories")
    resolved = _require_within(target, root)
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
        resolved = _require_within(resolved, root)
    return resolved


def _managed_spill_path(path: Path | str, scope_dir: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = scope_dir / candidate
    if candidate.is_symlink():
        raise ValueError("symbolic links are not managed spill files")
    resolved = _require_within(candidate, scope_dir)
    if resolved.parent != scope_dir:
        raise ValueError("spill file must be directly inside its managed scope")
    if resolved.suffix.lower() not in _MANAGED_SPILL_SUFFIXES:
        raise ValueError("path is not a managed spill file")
    return resolved


def spill_scope_values(context: Mapping[str, Any]) -> tuple[Any, Any]:
    tenant_context = context.get("tenant_context")
    if tenant_context is not None and hasattr(tenant_context, "tenant_id"):
        # An authenticated context owns both dimensions.  Even an empty
        # workspace must not fall back to caller-controlled scalar fields.
        return (
            getattr(tenant_context, "tenant_id", None),
            getattr(tenant_context, "workspace_id", None),
        )
    return context.get("tenant_id"), context.get("workspace_id")


def _write_spill(
    spill_path: Path,
    *,
    tool_name: str,
    payload: str,
    suffix: str,
) -> Path:
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    filename = (
        f"{_safe_tool_filename(tool_name)}-{digest}-{uuid4().hex[:8]}{suffix}"
    )
    target = _managed_spill_path(spill_path / filename, spill_path)
    with _ACTIVE_SPILL_LOCK:
        _ACTIVE_SPILL_PATHS.add(target)
    try:
        target.write_text(payload, encoding="utf-8")
    finally:
        with _ACTIVE_SPILL_LOCK:
            _ACTIVE_SPILL_PATHS.discard(target)
    return target


def cleanup_spilled_results(
    *,
    spill_dir: Optional[Path | str] = None,
    tenant_id: Any = None,
    workspace_id: Any = None,
    ttl_seconds: float = DEFAULT_SPILL_TTL_SECONDS,
    active_paths: tuple[Path | str, ...] = (),
    now: Optional[float] = None,
) -> tuple[Path, ...]:
    """Delete expired spill files from exactly one tenant/workspace scope."""
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
        raise TypeError("ttl_seconds must be a number")
    if not math.isfinite(float(ttl_seconds)) or ttl_seconds < 0:
        raise ValueError("ttl_seconds must be finite and non-negative")
    if now is None:
        now = time.time()
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise TypeError("now must be a number or None")
    if not math.isfinite(float(now)):
        raise ValueError("now must be finite")

    scope_dir = spill_scope_directory(
        spill_dir=spill_dir,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        create=False,
    )
    if not scope_dir.is_dir():
        return ()

    declared_active = {
        _managed_spill_path(path, scope_dir)
        for path in active_paths
    }
    with _ACTIVE_SPILL_LOCK:
        active = set(_ACTIVE_SPILL_PATHS) | declared_active

    cutoff = float(now) - float(ttl_seconds)
    deleted: list[Path] = []
    for candidate in scope_dir.iterdir():
        if (
            candidate.is_symlink()
            or candidate.suffix.lower() not in _MANAGED_SPILL_SUFFIXES
        ):
            continue
        resolved = _managed_spill_path(candidate, scope_dir)
        if resolved in active or not resolved.is_file():
            continue
        try:
            before = resolved.stat()
        except FileNotFoundError:
            continue
        if before.st_mtime > cutoff:
            continue
        try:
            after = resolved.stat()
        except FileNotFoundError:
            continue
        if (
            after.st_mtime_ns != before.st_mtime_ns
            or after.st_size != before.st_size
        ):
            continue
        try:
            resolved.unlink()
        except FileNotFoundError:
            continue
        deleted.append(resolved)
    return tuple(deleted)


def delete_spilled_result(
    path: Path | str,
    *,
    spill_dir: Optional[Path | str] = None,
    tenant_id: Any = None,
    workspace_id: Any = None,
) -> bool:
    """Delete one spill file after enforcing its tenant/workspace boundary."""
    scope_dir = spill_scope_directory(
        spill_dir=spill_dir,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        create=False,
    )
    target = _managed_spill_path(path, scope_dir)
    with _ACTIVE_SPILL_LOCK:
        if target in _ACTIVE_SPILL_PATHS:
            return False
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    return True


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
        user_hook: Optional[PreToolPolicy] = None,
    ) -> Optional[PreToolPolicy]:
        """Return a combined pre hook: pipeline policies, then the user hook."""
        policies = list(self._pre)

        def combined(
            call: ToolCall,
            tool: AgentTool,
            context: Mapping[str, Any],
            turn_ctx: Mapping[str, Any],
        ) -> Optional[BeforeToolCallDecision]:
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
        user_hook: Optional[PostToolPolicy] = None,
    ) -> Optional[PostToolPolicy]:
        """Return a combined post hook: pipeline policies in order, then the
        user hook; each result feeds the next stage."""
        policies = list(self._post)

        def combined(
            call: ToolCall,
            tool: AgentTool,
            result: ToolResult,
            context: Mapping[str, Any],
        ) -> Optional[ToolResult]:
            current = result
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
    details_max_chars: Optional[int] = None,
    spill_dir: Optional[Path | str] = None,
    tool_name: str = "tool",
    tenant_id: Any = None,
    workspace_id: Any = None,
    ttl_seconds: Optional[float] = DEFAULT_SPILL_TTL_SECONDS,
) -> ToolResult:
    """Truncate an oversized tool result and spill the full payload to disk.

    The returned ``ToolResult`` keeps a bounded summary ending with a pointer
    to the spilled file; ``details`` records ``spilled_path`` and the original
    length so callers (UI, replay) can fetch the full payload if needed.
    """
    if not isinstance(max_chars, int) or max_chars < 512:
        raise ValueError("max_chars must be an integer >= 512")
    if details_max_chars is None:
        details_max_chars = max_chars
    if not isinstance(details_max_chars, int) or details_max_chars < 512:
        raise ValueError("details_max_chars must be an integer >= 512")

    content = result.content
    original_details = dict(result.details)
    try:
        original_serialized_details = json.dumps(
            original_details,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        original_serialized_details = None

    safe_details = _json_safe(original_details)
    details = dict(safe_details) if isinstance(safe_details, dict) else {}
    summary = content
    spill_path: Optional[Path] = None

    def ensure_spill_path() -> Path:
        nonlocal spill_path
        if spill_path is None:
            if ttl_seconds is not None:
                cleanup_spilled_results(
                    spill_dir=spill_dir,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    ttl_seconds=ttl_seconds,
                )
            spill_path = spill_scope_directory(
                spill_dir=spill_dir,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        return spill_path

    if len(content) > max_chars:
        target = _write_spill(
            ensure_spill_path(),
            tool_name=tool_name,
            payload=content,
            suffix=".txt",
        )
        suffix = DEFAULT_SUMMARY_SUFFIX.format(path=str(target))
        head_limit = max_chars - len(suffix)
        summary = (
            content[:head_limit] + suffix
            if head_limit > 0
            else suffix[:max_chars]
        )
        details["spilled_path"] = str(target)
        details["spilled_original_length"] = len(content)
        details["spilled_summary_length"] = len(summary)

    serialized_details = json.dumps(
        details,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if (
        len(content) <= max_chars
        and len(serialized_details) <= details_max_chars
        and serialized_details == original_serialized_details
    ):
        return result
    if len(serialized_details) > details_max_chars:
        details_target = _write_spill(
            ensure_spill_path(),
            tool_name=f"{tool_name}-details",
            payload=serialized_details,
            suffix=".json",
        )
        bounded_details = {
            "spilled_details_path": str(details_target),
            "spilled_details_original_length": len(serialized_details),
        }
        for key in (
            "spilled_path",
            "spilled_original_length",
            "spilled_summary_length",
        ):
            if key in details:
                bounded_details[key] = details[key]
        details = bounded_details

    return ToolResult(
        content=summary,
        details=details,
        is_error=result.is_error,
        terminate=result.terminate,
    )


def spill_policy(
    *,
    max_chars: int = DEFAULT_SPILL_MAX_CHARS,
    details_max_chars: Optional[int] = None,
    spill_dir: Optional[Path | str] = None,
    ttl_seconds: Optional[float] = DEFAULT_SPILL_TTL_SECONDS,
) -> PostToolPolicy:
    """Build a post policy that spills any result longer than ``max_chars``."""

    def policy(
        call: ToolCall,
        tool: AgentTool,
        result: ToolResult,
        context: Mapping[str, Any],
    ) -> Optional[ToolResult]:
        if result is None:
            return None
        tenant_id, workspace_id = spill_scope_values(context)
        return spill_tool_result(
            result,
            max_chars=max_chars,
            details_max_chars=details_max_chars,
            spill_dir=spill_dir,
            tool_name=tool.name or call.name,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            ttl_seconds=ttl_seconds,
        )

    return policy


def load_spilled_result(
    path: Path | str,
    *,
    spill_dir: Optional[Path | str] = None,
    tenant_id: Any = None,
    workspace_id: Any = None,
) -> str:
    """Read a spilled tool-result payload back (for UI/replay)."""
    scope_dir = spill_scope_directory(
        spill_dir=spill_dir,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        create=False,
    )
    target = _managed_spill_path(path, scope_dir)
    try:
        return target.read_text(encoding="utf-8")
    except OSError as error:
        raise FileNotFoundError(f"spilled tool result is unavailable: {error}") from None
