"""Server-owned path policy for documents consumed by agent skills."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DOCUMENT_PATH_NOT_ALLOWED = "document_path_not_allowed"
DOCUMENT_NOT_FOUND = "document_not_found"
DOCUMENT_NOT_FILE = "document_not_file"

_PUBLIC_MESSAGES = {
    DOCUMENT_PATH_NOT_ALLOWED: "该文档不在当前工作区允许的范围内",
    DOCUMENT_NOT_FOUND: "文档不存在",
    DOCUMENT_NOT_FILE: "目标不是普通文件",
}


class DocumentPathError(ValueError):
    """A path-policy failure whose public text never contains a filesystem path."""

    def __init__(self, code: str):
        if code not in _PUBLIC_MESSAGES:
            raise ValueError("unsupported document path error code")
        self.code = code
        super().__init__(_PUBLIC_MESSAGES[code])

    def as_result(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": str(self),
            "error_code": self.code,
        }


@dataclass(frozen=True, slots=True)
class TrustedDocumentRoots:
    """Immutable roots created by the host, never by model-visible inputs."""

    roots: tuple[Path, ...]

    def __post_init__(self) -> None:
        normalized: list[Path] = []
        for value in self.roots:
            if isinstance(value, bool) or not isinstance(value, (str, os.PathLike)):
                raise TypeError("document roots must be filesystem paths")
            path = Path(value).expanduser()
            if not path.is_absolute():
                raise ValueError("document roots must be absolute")
            if path not in normalized:
                normalized.append(path)
        if not normalized:
            raise ValueError("at least one document root is required")
        object.__setattr__(self, "roots", tuple(normalized))

    @classmethod
    def from_paths(cls, *roots: str | os.PathLike[str]) -> TrustedDocumentRoots:
        return cls(tuple(Path(root) for root in roots))


def _path_segment(value: str) -> str:
    # Tenant identifiers allow colons, while Windows path segments do not.
    return value.replace(":", "%3A")


def scoped_document_roots(
    tenant_context: Any,
    *,
    data_root: str | os.PathLike[str] | None = None,
    include_legacy_local_uploads: bool = False,
) -> TrustedDocumentRoots:
    """Build roots from a server-created tenant context and server data root."""

    from artpm_agent.config import resolve_data_root
    from artpm_agent.tenancy import TenantContext

    if not isinstance(tenant_context, TenantContext):
        raise TypeError("tenant_context must be a server-created TenantContext instance")
    tenant_id = _path_segment(tenant_context.tenant_id)
    workspace_id = _path_segment(tenant_context.require_workspace())
    root = Path(data_root).expanduser() if data_root is not None else resolve_data_root()
    root = root.resolve()

    roots: list[Path] = [
        root / "artifacts" / tenant_id / workspace_id,
        root / "chat_attachments" / tenant_id / workspace_id,
    ]
    if include_legacy_local_uploads and tenant_context == TenantContext.local():
        # The desktop host has one local workspace and its historical upload
        # store is conversation-scoped directly below this directory. Tenant-
        # bound model tools do not receive this broad compatibility root.
        roots.extend(
            (
                root / "chat_attachments",
                root / "artifacts" / "local-default",
            )
        )
    return TrustedDocumentRoots(tuple(roots))


def _roots_from_context(context: Mapping[str, Any] | None) -> TrustedDocumentRoots:
    if not isinstance(context, Mapping):
        raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED)
    roots = context.get("document_roots")
    if not isinstance(roots, TrustedDocumentRoots):
        raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED)
    return roots


def _existing_roots(roots: TrustedDocumentRoots) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for root in roots.roots:
        try:
            candidate = root.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if candidate.is_dir() and candidate not in resolved:
            resolved.append(candidate)
    if not resolved:
        raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED)
    return tuple(resolved)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def _validate_candidate(candidate: Path, root: Path) -> Path:
    if not _is_within(candidate, root):
        raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED)

    relative = candidate.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor /= part
        try:
            if _is_link(cursor):
                raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED)
        except OSError as error:
            raise DocumentPathError(DOCUMENT_NOT_FOUND) from error

    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise DocumentPathError(DOCUMENT_NOT_FOUND) from error
    except (OSError, RuntimeError) as error:
        raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED) from error

    if not _is_within(resolved, root):
        raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED)
    if not resolved.is_file():
        raise DocumentPathError(DOCUMENT_NOT_FILE)
    return resolved


def resolve_document_path(
    requested_path: Any,
    context: Mapping[str, Any] | None,
) -> Path:
    """Resolve one requested document while enforcing host-owned roots."""

    roots = _existing_roots(_roots_from_context(context))
    if isinstance(requested_path, bool) or not isinstance(
        requested_path, (str, os.PathLike)
    ):
        raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED)
    raw_value = os.fspath(requested_path)
    if not isinstance(raw_value, str) or not raw_value.strip() or "\x00" in raw_value:
        raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED)

    try:
        raw_path = Path(raw_value).expanduser()
    except (OSError, RuntimeError, ValueError) as error:
        raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED) from error
    if any(part == ".." for part in raw_path.parts):
        raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED)

    candidates: list[tuple[Path, Path]] = []
    if raw_path.is_absolute():
        candidate = Path(os.path.abspath(raw_path))
        candidates = [
            (candidate, root) for root in roots if _is_within(candidate, root)
        ]
        if not candidates:
            raise DocumentPathError(DOCUMENT_PATH_NOT_ALLOWED)
    else:
        candidates = [(root / raw_path, root) for root in roots]

    saw_non_file = False
    for candidate, root in candidates:
        if not candidate.exists() and not candidate.is_symlink():
            continue
        try:
            return _validate_candidate(candidate, root)
        except DocumentPathError as error:
            if error.code == DOCUMENT_NOT_FILE:
                saw_non_file = True
                continue
            raise
    if saw_non_file:
        raise DocumentPathError(DOCUMENT_NOT_FILE)
    raise DocumentPathError(DOCUMENT_NOT_FOUND)


__all__ = [
    "DOCUMENT_NOT_FILE",
    "DOCUMENT_NOT_FOUND",
    "DOCUMENT_PATH_NOT_ALLOWED",
    "DocumentPathError",
    "TrustedDocumentRoots",
    "resolve_document_path",
    "scoped_document_roots",
]
