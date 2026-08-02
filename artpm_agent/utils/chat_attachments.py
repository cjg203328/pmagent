"""Safe persistence and selection helpers for chat attachments."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import os
from pathlib import Path
import re
import shutil
from typing import Any
from uuid import uuid4

from .mineru_adapter import MINERU_SUPPORTED_SUFFIXES


DEFAULT_ALLOWED_EXTENSIONS = frozenset(
    {
        "xlsx",
        "xls",
        "csv",
        "json",
        "txt",
        "md",
        "pdf",
        "docx",
        "png",
        "jpg",
        "jpeg",
        "webp",
    }
    | {suffix.lstrip(".") for suffix in MINERU_SUPPORTED_SUFFIXES}
)
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024
DEFAULT_MAX_TOTAL_SIZE = 100 * 1024 * 1024
_CONVERSATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ATTACHMENT_REFERENCE_PHRASES = (
    "这个附件",
    "这些附件",
    "这份附件",
    "上个附件",
    "上一份附件",
    "这个文件",
    "这些文件",
    "这份文件",
    "上个文件",
    "上一份文件",
    "继续分析",
    "继续处理",
    "继续编辑",
    "使用附件",
    "使用文件",
    "用附件",
    "用文件",
    "这份报价",
    "这个模板",
    "这份模板",
    "这个格式",
    "这种格式",
    "保存为模板",
    "沿用模板",
    "按模板",
    "template",
    "format",
)


class ChatAttachmentStore:
    """Persist uploaded chat files below an isolated workspace directory."""

    def __init__(
        self,
        root: str | Path,
        allowed_extensions: Iterable[str] = DEFAULT_ALLOWED_EXTENSIONS,
        max_files: int = 3,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
        max_total_size: int = DEFAULT_MAX_TOTAL_SIZE,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ValueError(f"Attachment root is not a directory: {self.root}")

        normalized_extensions = {
            str(extension).strip().lower().lstrip(".")
            for extension in allowed_extensions
            if str(extension).strip().lstrip(".")
        }
        if not normalized_extensions:
            raise ValueError("allowed_extensions must contain at least one extension")
        self.allowed_extensions = frozenset(normalized_extensions)
        self.max_files = self._positive_integer(max_files, "max_files")
        self.max_file_size = self._positive_integer(
            max_file_size, "max_file_size"
        )
        self.max_total_size = self._positive_integer(
            max_total_size, "max_total_size"
        )

    @staticmethod
    def _positive_integer(value: int, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field} must be a positive integer")
        return value

    @staticmethod
    def _conversation_id(value: str) -> str:
        if not isinstance(value, str) or not _CONVERSATION_ID_PATTERN.fullmatch(value):
            raise ValueError("conversation_id contains unsupported characters")
        return value

    def _safe_path(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ValueError("Attachment path must be relative to the attachment root")
        resolved = (self.root / relative).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as error:
            raise ValueError("Attachment path escapes the attachment root") from error
        return resolved

    @staticmethod
    def _display_name(uploaded_file: Any) -> str:
        raw_name = str(getattr(uploaded_file, "name", "") or "").strip()
        display_name = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
        if display_name in {"", ".", ".."} or "\x00" in display_name:
            raise ValueError("Uploaded file must have a valid name")
        return display_name

    @staticmethod
    def _restore_position(uploaded_file: Any, position: int | None) -> None:
        if position is None or not hasattr(uploaded_file, "seek"):
            return
        try:
            uploaded_file.seek(position)
        except (OSError, ValueError):
            pass

    def save_files(
        self,
        conversation_id: str,
        uploaded_files: Iterable[Any] | None,
    ) -> list[dict[str, Any]]:
        """Save one submitted batch atomically from the caller's perspective."""
        conversation_id = self._conversation_id(conversation_id)
        files = list(uploaded_files or [])
        if len(files) > self.max_files:
            raise ValueError(f"A message can contain at most {self.max_files} files")
        if not files:
            return []

        conversation_dir = self._safe_path(conversation_id)
        if conversation_dir.exists() and not conversation_dir.is_dir():
            raise ValueError("Conversation attachment path is not a directory")
        conversation_dir.mkdir(parents=True, exist_ok=True)

        metadata: list[dict[str, Any]] = []
        completed_paths: list[Path] = []
        part_paths: list[Path] = []
        total_size = 0
        try:
            for uploaded_file in files:
                display_name = self._display_name(uploaded_file)
                extension = Path(display_name).suffix.lower().lstrip(".")
                if extension not in self.allowed_extensions:
                    raise ValueError(f"Unsupported attachment extension: .{extension}")
                if not hasattr(uploaded_file, "read"):
                    raise TypeError("Uploaded file must provide a read() method")

                attachment_id = uuid4().hex
                relative_path = Path(conversation_id) / f"{attachment_id}.{extension}"
                destination = self._safe_path(relative_path)
                part_path = destination.with_name(f"{destination.name}.part")
                part_paths.append(part_path)

                original_position: int | None = None
                if hasattr(uploaded_file, "tell"):
                    try:
                        original_position = int(uploaded_file.tell())
                    except (OSError, TypeError, ValueError):
                        original_position = None
                if hasattr(uploaded_file, "seek"):
                    uploaded_file.seek(0)

                digest = hashlib.sha256()
                file_size = 0
                try:
                    with part_path.open("xb") as destination_file:
                        while True:
                            chunk = uploaded_file.read(1024 * 1024)
                            if not chunk:
                                break
                            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                                raise TypeError("Uploaded file must return bytes")
                            chunk = bytes(chunk)
                            file_size += len(chunk)
                            total_size += len(chunk)
                            if file_size > self.max_file_size:
                                raise ValueError(
                                    f"Attachment exceeds {self.max_file_size} bytes: "
                                    f"{display_name}"
                                )
                            if total_size > self.max_total_size:
                                raise ValueError(
                                    f"Attachment batch exceeds {self.max_total_size} bytes"
                                )
                            digest.update(chunk)
                            destination_file.write(chunk)
                finally:
                    self._restore_position(uploaded_file, original_position)

                if file_size == 0:
                    raise ValueError(f"Attachment is empty: {display_name}")
                os.replace(part_path, destination)
                part_paths.remove(part_path)
                completed_paths.append(destination)
                metadata.append(
                    {
                        "id": attachment_id,
                        "name": display_name,
                        "original_name": display_name,
                        "stored_path": relative_path.as_posix(),
                        "extension": extension,
                        "mime_type": str(getattr(uploaded_file, "type", "") or ""),
                        "size": file_size,
                        "sha256": digest.hexdigest(),
                    }
                )
            return metadata
        except Exception:
            for path in part_paths:
                path.unlink(missing_ok=True)
            for path in completed_paths:
                path.unlink(missing_ok=True)
            try:
                conversation_dir.rmdir()
            except OSError:
                pass
            raise

    def remove_conversation(self, conversation_id: str) -> None:
        """Remove all files owned by one conversation."""
        conversation_id = self._conversation_id(conversation_id)
        conversation_dir = self._safe_path(conversation_id)
        if not conversation_dir.exists():
            return
        if not conversation_dir.is_dir():
            raise ValueError("Conversation attachment path is not a directory")
        shutil.rmtree(conversation_dir)

    def remove_files(self, attachments: Iterable[Mapping[str, Any]]) -> None:
        """Best-effort rollback for a just-saved attachment batch."""
        parents: set[Path] = set()
        for attachment in attachments:
            if not isinstance(attachment, Mapping):
                continue
            stored_path = attachment.get("stored_path")
            if not isinstance(stored_path, str) or not stored_path.strip():
                continue
            path = self._safe_path(stored_path)
            path.unlink(missing_ok=True)
            parents.add(path.parent)
        for parent in sorted(parents, key=lambda item: len(item.parts), reverse=True):
            if parent == self.root:
                continue
            try:
                parent.rmdir()
            except OSError:
                pass

    def resolve_paths(self, attachments: Iterable[Mapping[str, Any]]) -> list[str]:
        """Resolve persisted metadata into verified, existing local file paths."""
        resolved_paths: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, Mapping):
                raise ValueError("Attachment metadata must be a mapping")
            stored_path = attachment.get("stored_path")
            if not isinstance(stored_path, str) or not stored_path.strip():
                raise ValueError("Attachment metadata is missing stored_path")
            resolved = self._safe_path(stored_path)
            if not resolved.is_file():
                raise FileNotFoundError(f"Attachment does not exist: {stored_path}")
            resolved_paths.append(str(resolved))
        return resolved_paths


def normalize_chat_submission(value: Any) -> tuple[str, list[Any]]:
    """Normalize Streamlit string and ``ChatInputValue`` submissions."""
    if value is None:
        return "", []
    if isinstance(value, str):
        return value.strip(), []

    if isinstance(value, Mapping):
        text = value.get("text", "")
        files = value.get("files", [])
    else:
        text = getattr(value, "text", "")
        files = getattr(value, "files", [])

    if text is None:
        text = ""
    if not isinstance(text, str):
        raise TypeError("Chat submission text must be a string")
    if files is None:
        files = []
    elif isinstance(files, (str, bytes, bytearray)):
        raise TypeError("Chat submission files must be file objects")
    else:
        try:
            files = list(files)
        except TypeError:
            files = [files]
    return text.strip(), files


def chat_submission_audio(value: Any) -> Any | None:
    """Return Streamlit's recorded audio without treating it as an attachment."""

    if value is None or isinstance(value, str):
        return None
    if isinstance(value, Mapping):
        return value.get("audio")
    return getattr(value, "audio", None)


def _attachment_metadata(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def select_conversation_attachments(
    prompt: str,
    current_attachments: Iterable[Mapping[str, Any]] | None,
    messages: Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Select current files or reuse the most recent referenced chat files."""
    current = _attachment_metadata(current_attachments)
    if current:
        return current

    normalized_prompt = str(prompt or "").casefold()
    if not any(
        phrase in normalized_prompt for phrase in _ATTACHMENT_REFERENCE_PHRASES
    ):
        return []

    for message in reversed(list(messages or [])):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        metadata = message.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        attachments = _attachment_metadata(metadata.get("attachments"))
        if attachments:
            return attachments
    return []
