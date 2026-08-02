"""Optional MinerU document conversion adapter.

The application owns a small, versioned result contract and treats MinerU as
an external conversion backend.  This avoids importing MinerU's model runtime
into the Streamlit process and keeps the existing local parsers available as a
fallback when the sidecar or CLI is unavailable.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from html import unescape
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
from tempfile import TemporaryDirectory
from threading import RLock
import time
from typing import Any
from urllib.parse import urljoin, urlsplit
import zipfile

import requests


MINERU_INTEGRATION_SCHEMA = "artpm-mineru-v1"
MINERU_SUPPORTED_SUFFIXES = frozenset(
    {
        ".bmp",
        ".docx",
        ".gif",
        ".jp2",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".pptx",
        ".tif",
        ".tiff",
        ".webp",
        ".xlsx",
    }
)
MINERU_IMAGE_SUFFIXES = frozenset(
    {".bmp", ".gif", ".jp2", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
)
_IMAGE_SUFFIXES = MINERU_IMAGE_SUFFIXES
_BACKENDS = frozenset(
    {
        "pipeline",
        "vlm-engine",
        "hybrid-engine",
        "vlm-http-client",
        "hybrid-http-client",
    }
)
_MODES = frozenset({"auto", "local", "remote", "disabled"})
_PARSE_METHODS = frozenset({"auto", "txt", "ocr"})
_EFFORTS = frozenset({"medium", "high"})
_VERSION_PATTERN = re.compile(r"(?<!\d)(\d+\.\d+(?:\.\d+)?)(?!\d)")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _bounded_float(
    value: Any,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _choice(value: Any, allowed: frozenset[str], default: str) -> str:
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in allowed else default


def _command_tokens(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        tokens = [str(item).strip() for item in value if str(item).strip()]
    else:
        text = str(value or "mineru").strip() or "mineru"
        try:
            tokens = shlex.split(text, posix=os.name != "nt")
        except ValueError:
            tokens = [text]
    normalized = tuple(token.strip().strip('"') for token in tokens if token.strip())
    return normalized or ("mineru",)


def _api_path(value: Any, default: str) -> str:
    text = str(value or default).strip()
    if not text:
        return default
    if not text.startswith("/"):
        text = "/" + text
    return "/" + "/".join(part for part in text.split("/") if part)


@dataclass(frozen=True)
class MinerUConfig:
    """Deployment-owned MinerU adapter configuration."""

    enabled: bool = True
    mode: str = "auto"
    command: tuple[str, ...] = ("mineru",)
    api_url: str = ""
    api_key: str = ""
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer"
    api_path: str = "/file_parse"
    health_path: str = "/health"
    allow_insecure_http: bool = False
    backend: str = "pipeline"
    parse_method: str = "auto"
    effort: str = "medium"
    language: str = "ch"
    formula_enable: bool = True
    table_enable: bool = True
    image_analysis: bool = True
    timeout_seconds: float = 600.0
    connect_timeout_seconds: float = 10.0
    output_dir: str = "./data/mineru"
    max_file_size: int = 50 * 1024 * 1024
    max_output_bytes: int = 128 * 1024 * 1024
    max_text_chars: int = 256 * 1024
    max_structured_bytes: int = 384 * 1024
    max_blocks: int = 5000
    max_archive_members: int = 10_000
    cache_enabled: bool = True
    cache_max_entries: int = 64

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "MinerUConfig":
        source = dict(values or {})
        language = str(source.get("language") or "ch").strip()[:32] or "ch"
        api_url = str(source.get("api_url") or source.get("base_url") or "").strip()
        output_dir = str(source.get("output_dir") or "./data/mineru").strip()
        api_key_prefix = source.get("api_key_prefix")
        if api_key_prefix is None:
            api_key_prefix = "Bearer"
        return cls(
            enabled=_as_bool(source.get("enabled"), True),
            mode=_choice(source.get("mode"), _MODES, "auto"),
            command=_command_tokens(source.get("command", "mineru")),
            api_url=api_url.rstrip("/"),
            api_key=str(source.get("api_key") or "").strip(),
            api_key_header=(
                str(source.get("api_key_header") or "Authorization").strip()
                or "Authorization"
            )[:64],
            api_key_prefix=str(api_key_prefix).strip()[:32],
            api_path=_api_path(source.get("api_path"), "/file_parse"),
            health_path=_api_path(source.get("health_path"), "/health"),
            allow_insecure_http=_as_bool(source.get("allow_insecure_http"), False),
            backend=_choice(source.get("backend"), _BACKENDS, "pipeline"),
            parse_method=_choice(
                source.get("parse_method"),
                _PARSE_METHODS,
                "auto",
            ),
            effort=_choice(source.get("effort"), _EFFORTS, "medium"),
            language=language,
            formula_enable=_as_bool(source.get("formula_enable"), True),
            table_enable=_as_bool(source.get("table_enable"), True),
            image_analysis=_as_bool(source.get("image_analysis"), True),
            timeout_seconds=_bounded_float(
                source.get("timeout_seconds", source.get("timeout")),
                600.0,
                1.0,
                7200.0,
            ),
            connect_timeout_seconds=_bounded_float(
                source.get("connect_timeout_seconds"),
                10.0,
                0.5,
                120.0,
            ),
            output_dir=output_dir or "./data/mineru",
            max_file_size=_bounded_int(
                source.get("max_file_size"),
                50 * 1024 * 1024,
                1024,
                1024 * 1024 * 1024,
            ),
            max_output_bytes=_bounded_int(
                source.get("max_output_bytes"),
                128 * 1024 * 1024,
                1024 * 1024,
                2 * 1024 * 1024 * 1024,
            ),
            max_text_chars=_bounded_int(
                source.get("max_text_chars"),
                256 * 1024,
                1024,
                10_000_000,
            ),
            max_structured_bytes=_bounded_int(
                source.get("max_structured_bytes"),
                384 * 1024,
                16 * 1024,
                8 * 1024 * 1024,
            ),
            max_blocks=_bounded_int(
                source.get("max_blocks"),
                5000,
                1,
                100_000,
            ),
            max_archive_members=_bounded_int(
                source.get("max_archive_members"),
                10_000,
                1,
                100_000,
            ),
            cache_enabled=_as_bool(source.get("cache_enabled"), True),
            cache_max_entries=_bounded_int(
                source.get("cache_max_entries"),
                64,
                1,
                10_000,
            ),
        )


@dataclass(frozen=True)
class MinerURuntimeStatus:
    enabled: bool
    mode: str
    available: bool
    backend: str
    detail: str
    command_available: bool = False
    api_configured: bool = False
    runtime_version: str | None = None
    protocol_version: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "available": self.available,
            "backend": self.backend,
            "detail": self.detail,
            "command_available": self.command_available,
            "api_configured": self.api_configured,
            "runtime_version": self.runtime_version,
            "protocol_version": self.protocol_version,
        }


@dataclass(frozen=True)
class MinerUConversionResult:
    success: bool
    markdown: str = ""
    structured_data: dict[str, Any] = field(default_factory=dict)
    source_format: str = ""
    backend: str = ""
    mode: str = ""
    runtime_version: str | None = None
    cache_hit: bool = False
    attempted: bool = False
    truncated: bool = False
    artifacts: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    error: str | None = None

    def as_document_result(self, file_path: str | Path, *, source: str) -> dict[str, Any]:
        path = Path(file_path).expanduser().resolve()
        suffix = path.suffix.lower()
        summary = self.structured_data.get("summary", {})
        extracted_data = {
            "page_count": summary.get("page_count"),
            "block_counts": summary.get("block_counts", {}),
            "mineru": self.structured_data,
        }
        document_type = {
            ".docx": "Word document",
            ".pdf": "PDF document",
            ".pptx": "PowerPoint document",
            ".xlsx": "Excel document",
        }.get(suffix, "Image document" if suffix in _IMAGE_SUFFIXES else "Document")
        return {
            "success": True,
            "document_type": document_type,
            "source": source,
            "file_path": str(path),
            "extracted_data": extracted_data,
            "raw_text": self.markdown,
            "markdown": self.markdown,
            "confidence": None,
            "requires_vision": False,
            "ocr_available": suffix in _IMAGE_SUFFIXES or suffix == ".pdf",
            "ocr_status": (
                "completed" if suffix in _IMAGE_SUFFIXES or suffix == ".pdf" else "not_applicable"
            ),
            "preprocessor": {
                "name": "mineru",
                "integration_schema": MINERU_INTEGRATION_SCHEMA,
                "source_format": self.source_format,
                "backend": self.backend,
                "mode": self.mode,
                "runtime_version": self.runtime_version,
                "cache_hit": self.cache_hit,
                "truncated": self.truncated,
                "artifacts": self.artifacts,
                "warnings": list(self.warnings),
            },
        }

    def to_langchain_documents(self, *, source_name: str = "") -> list[Any]:
        """Expose bounded reading-order blocks to LangChain/LangGraph consumers."""
        from langchain_core.documents import Document

        raw_blocks = self.structured_data.get("content_list")
        blocks = raw_blocks if isinstance(raw_blocks, list) else []
        documents = []
        for index, block in enumerate(blocks):
            if not isinstance(block, Mapping):
                continue
            text = MinerUDocumentConverter._markdown_from_content([block]).strip()
            if not text:
                continue
            metadata = {
                "source": Path(source_name).name if source_name else "",
                "parser": "mineru",
                "integration_schema": MINERU_INTEGRATION_SCHEMA,
                "runtime_version": self.runtime_version,
                "backend": self.backend,
                "block_index": index,
                "block_type": str(block.get("type") or "unknown"),
                "page": block.get("page_idx"),
                "bbox": block.get("bbox"),
            }
            documents.append(Document(page_content=text, metadata=metadata))
        if not documents and self.markdown.strip():
            documents.append(
                Document(
                    page_content=self.markdown.strip(),
                    metadata={
                        "source": Path(source_name).name if source_name else "",
                        "parser": "mineru",
                        "integration_schema": MINERU_INTEGRATION_SCHEMA,
                        "runtime_version": self.runtime_version,
                        "backend": self.backend,
                    },
                )
            )
        return documents


class MinerUDocumentConverter:
    """Convert one document through a MinerU REST sidecar or local CLI."""

    def __init__(
        self,
        config: Mapping[str, Any] | MinerUConfig | None = None,
        *,
        runner: Callable[..., Any] | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = (
            config if isinstance(config, MinerUConfig) else MinerUConfig.from_mapping(config)
        )
        self.output_root = Path(self.config.output_dir).expanduser().resolve()
        self.cache_root = self.output_root / "cache"
        self._runner = runner or subprocess.run
        self._session = session or requests.Session()
        self._owns_session = session is None
        self._lock = RLock()
        self._runtime_version: str | None = None
        self._protocol_version: int | None = None

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def supports(self, file_path: str | Path) -> bool:
        return Path(file_path).suffix.lower() in MINERU_SUPPORTED_SUFFIXES

    def runtime_status(self, *, probe: bool = False) -> MinerURuntimeStatus:
        if not self.config.enabled or self.config.mode == "disabled":
            return MinerURuntimeStatus(
                False,
                "disabled",
                False,
                self.config.backend,
                "MinerU conversion is disabled",
            )

        command_available = self._resolved_command() is not None
        api_configured = bool(self.config.api_url)
        mode = self._selected_mode(command_available=command_available)
        version = self._runtime_version
        protocol = self._protocol_version
        if mode == "remote":
            detail = "MinerU API configured; probe not requested"
            available = False
        elif mode == "local":
            detail = "available" if command_available else "MinerU CLI is not installed"
            available = command_available
        else:
            detail = "MinerU backend is not configured"
            available = False

        if mode == "remote" and probe:
            try:
                version, protocol = self._probe_remote()
            except (requests.RequestException, ValueError) as error:
                available = False
                detail = f"MinerU API unavailable: {error}"
        elif mode == "local" and probe:
            version = self._probe_local_version()
        elif mode is None:
            detail = (
                "MinerU API URL is not configured"
                if self.config.mode == "remote"
                else "MinerU CLI is not installed and no API URL is configured"
            )

        return MinerURuntimeStatus(
            True,
            mode or self.config.mode,
            available,
            self.config.backend,
            detail,
            command_available=command_available,
            api_configured=api_configured,
            runtime_version=version,
            protocol_version=protocol,
        )

    def convert(
        self,
        file_path: str | Path,
        *,
        user_hint: str = "",
    ) -> MinerUConversionResult:
        del user_hint  # User text never becomes a command-line or API parameter.
        path = Path(file_path).expanduser().resolve()
        suffix = path.suffix.lower()
        if not self.config.enabled or self.config.mode == "disabled":
            return MinerUConversionResult(
                False,
                source_format=suffix,
                backend=self.config.backend,
                mode="disabled",
                error="MinerU conversion is disabled",
            )
        if suffix not in MINERU_SUPPORTED_SUFFIXES:
            return MinerUConversionResult(
                False,
                source_format=suffix,
                backend=self.config.backend,
                mode=self.config.mode,
                error=f"unsupported MinerU input format: {suffix or 'none'}",
            )
        if not path.is_file():
            return MinerUConversionResult(
                False,
                source_format=suffix,
                backend=self.config.backend,
                mode=self.config.mode,
                error="input file does not exist",
            )
        file_size = path.stat().st_size
        if file_size <= 0:
            return MinerUConversionResult(
                False,
                source_format=suffix,
                backend=self.config.backend,
                mode=self.config.mode,
                error="input file is empty",
            )
        if file_size > self.config.max_file_size:
            return MinerUConversionResult(
                False,
                source_format=suffix,
                backend=self.config.backend,
                mode=self.config.mode,
                error="input file exceeds the MinerU adapter size limit",
            )

        command_available = self._resolved_command() is not None
        mode = self._selected_mode(command_available=command_available)
        if mode is None:
            return MinerUConversionResult(
                False,
                source_format=suffix,
                backend=self.config.backend,
                mode=self.config.mode,
                error="MinerU CLI is not installed and no API URL is configured",
            )

        try:
            runtime_version = (
                self._probe_remote()[0]
                if mode == "remote"
                else self._probe_local_version()
            )
        except (requests.RequestException, ValueError):
            runtime_version = self._runtime_version

        with self._lock:
            try:
                digest = self._cache_key(path, mode, runtime_version)
                cache_dir = self.cache_root / digest
                marker = cache_dir / ".artpm-mineru.json"
                if self.config.cache_enabled and marker.is_file():
                    cached = self._load_output(
                        cache_dir,
                        path,
                        mode=mode,
                        runtime_version=runtime_version,
                        cache_hit=True,
                    )
                    if cached.success:
                        return cached

                self.output_root.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(
                    prefix=".mineru-",
                    dir=str(self.output_root),
                ) as temp_dir:
                    work_dir = Path(temp_dir).resolve()
                    if mode == "remote":
                        self._run_remote(path, work_dir)
                    else:
                        self._run_local(path, work_dir)
                    result = self._load_output(
                        work_dir,
                        path,
                        mode=mode,
                        runtime_version=runtime_version,
                        cache_hit=False,
                    )
                    if not result.success:
                        return result
                    if self.config.cache_enabled:
                        marker_payload = {
                            "integration_schema": MINERU_INTEGRATION_SCHEMA,
                            "source_sha256": self._file_sha256(path),
                            "runtime_version": runtime_version,
                            "backend": self.config.backend,
                            "created_at": int(time.time()),
                        }
                        (work_dir / ".artpm-mineru.json").write_text(
                            json.dumps(marker_payload, ensure_ascii=True, sort_keys=True),
                            encoding="utf-8",
                        )
                        self.cache_root.mkdir(parents=True, exist_ok=True)
                        if not cache_dir.exists():
                            try:
                                os.replace(work_dir, cache_dir)
                            except FileExistsError:
                                # Another application process won the same
                                # content-addressed cache key. Never remove or
                                # consume its directory before its marker exists.
                                pass
                        self._prune_cache()
                        if (cache_dir / ".artpm-mineru.json").is_file():
                            return self._load_output(
                                cache_dir,
                                path,
                                mode=mode,
                                runtime_version=runtime_version,
                                cache_hit=False,
                            )
                    return result
            except subprocess.TimeoutExpired:
                return MinerUConversionResult(
                    False,
                    source_format=suffix,
                    backend=self.config.backend,
                    mode=mode,
                    attempted=True,
                    runtime_version=runtime_version,
                    error="MinerU conversion timed out",
                )
            except (OSError, ValueError, zipfile.BadZipFile, requests.RequestException) as error:
                return MinerUConversionResult(
                    False,
                    source_format=suffix,
                    backend=self.config.backend,
                    mode=mode,
                    attempted=True,
                    runtime_version=runtime_version,
                    error=f"MinerU conversion failed: {error}",
                )

    def _selected_mode(self, *, command_available: bool) -> str | None:
        if self.config.mode == "remote":
            return "remote" if self.config.api_url else None
        if self.config.mode == "local":
            return "local" if command_available else None
        if self.config.api_url:
            return "remote"
        return "local" if command_available else None

    def _resolved_command(self) -> tuple[str, ...] | None:
        executable = self.config.command[0]
        explicit = Path(executable).expanduser()
        if explicit.is_file():
            resolved = str(explicit.resolve())
        else:
            resolved = shutil.which(executable) or ""
        if not resolved:
            return None
        return (resolved, *self.config.command[1:])

    def _probe_local_version(self) -> str | None:
        if self._runtime_version is not None:
            return self._runtime_version
        command = self._resolved_command()
        if command is None:
            return None
        try:
            completed = self._runner(
                [*command, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(10.0, self.config.connect_timeout_seconds),
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired, TypeError):
            return None
        output = f"{getattr(completed, 'stdout', '')}\n{getattr(completed, 'stderr', '')}"
        match = _VERSION_PATTERN.search(output)
        self._runtime_version = match.group(1) if match else None
        return self._runtime_version

    def _probe_remote(self) -> tuple[str | None, int | None]:
        base_url = self._validated_api_url()
        response = self._session.get(
            self._api_endpoint(base_url, self.config.health_path),
            headers=self._request_headers(),
            timeout=(self.config.connect_timeout_seconds, 10.0),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("MinerU health response must be an object")
        version = str(payload.get("version") or "").strip() or None
        protocol_raw = payload.get("protocol_version")
        try:
            protocol = int(protocol_raw) if protocol_raw is not None else None
        except (TypeError, ValueError):
            protocol = None
        self._runtime_version = version
        self._protocol_version = protocol
        return version, protocol

    def _validated_api_url(self) -> str:
        value = self.config.api_url.rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MinerU API URL must use http or https")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("MinerU API URL contains unsupported components")
        if (
            parsed.scheme == "http"
            and (parsed.hostname or "").casefold() not in {"localhost", "127.0.0.1", "::1"}
            and not self.config.allow_insecure_http
        ):
            raise ValueError("remote MinerU endpoints must use HTTPS")
        return value

    @staticmethod
    def _api_endpoint(base_url: str, path: str) -> str:
        return urljoin(f"{base_url.rstrip('/')}/", str(path or "").lstrip("/"))

    def _request_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/zip, application/json"}
        if self.config.api_key:
            value = self.config.api_key
            if self.config.api_key_prefix:
                value = f"{self.config.api_key_prefix} {value}"
            headers[self.config.api_key_header] = value
        return headers

    def _run_local(self, path: Path, output_dir: Path) -> None:
        command = self._resolved_command()
        if command is None:
            raise OSError("MinerU CLI is not installed")
        args = [
            *command,
            "-p",
            str(path),
            "-o",
            str(output_dir),
            "-b",
            self.config.backend,
            "-m",
            self.config.parse_method,
            "-l",
            self.config.language,
            "--effort",
            self.config.effort,
            "-f",
            str(self.config.formula_enable).lower(),
            "-t",
            str(self.config.table_enable).lower(),
            "--image-analysis",
            str(self.config.image_analysis).lower(),
        ]
        completed = self._runner(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.config.timeout_seconds,
            check=False,
            shell=False,
        )
        return_code = int(getattr(completed, "returncode", 1))
        if return_code != 0:
            stderr = str(getattr(completed, "stderr", "") or "").strip()
            stderr = " ".join(stderr.split())[:1000]
            raise OSError(
                f"MinerU CLI exited with code {return_code}"
                + (f": {stderr}" if stderr else "")
            )

    def _run_remote(self, path: Path, output_dir: Path) -> None:
        base_url = self._validated_api_url()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        form_data = [
            ("lang_list", self.config.language),
            ("backend", self.config.backend),
            ("effort", self.config.effort),
            ("parse_method", self.config.parse_method),
            ("formula_enable", str(self.config.formula_enable).lower()),
            ("table_enable", str(self.config.table_enable).lower()),
            ("image_analysis", str(self.config.image_analysis).lower()),
            ("return_md", "true"),
            ("return_middle_json", "true"),
            ("return_model_output", "false"),
            ("return_content_list", "true"),
            ("return_images", "true"),
            ("response_format_zip", "true"),
            ("return_original_file", "false"),
            ("client_side_output_generation", "false"),
            ("start_page_id", "0"),
        ]
        with path.open("rb") as source_file:
            response = self._session.post(
                self._api_endpoint(base_url, self.config.api_path),
                files={"files": (path.name, source_file, mime_type)},
                data=form_data,
                headers=self._request_headers(),
                timeout=(
                    self.config.connect_timeout_seconds,
                    self.config.timeout_seconds,
                ),
                stream=True,
            )
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type", "")).casefold()
        payload = self._bounded_response_bytes(response)
        if "zip" in content_type or payload.startswith(b"PK\x03\x04"):
            self._safe_extract_zip(payload, output_dir)
            return
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("MinerU API returned neither ZIP nor JSON") from error
        if not isinstance(decoded, Mapping):
            raise ValueError("MinerU API result must be an object")
        if not isinstance(decoded.get("results"), Mapping) and decoded.get("task_id"):
            result_payload = self._wait_for_remote_task(base_url, decoded)
            if isinstance(result_payload, bytes):
                self._safe_extract_zip(result_payload, output_dir)
                return
            decoded = result_payload
        self._materialize_json_result(decoded, output_dir, path.stem)

    def _wait_for_remote_task(
        self,
        base_url: str,
        submission: Mapping[str, Any],
    ) -> Mapping[str, Any] | bytes:
        task_id = str(submission.get("task_id") or "").strip()
        if not task_id or len(task_id) > 256:
            raise ValueError("MinerU task response has an invalid task_id")
        status_url = self._same_origin_url(
            base_url,
            submission.get("status_url")
            or self._api_endpoint(base_url, f"tasks/{task_id}"),
        )
        result_url = self._same_origin_url(
            base_url,
            submission.get("result_url")
            or self._api_endpoint(base_url, f"tasks/{task_id}/result"),
        )
        deadline = time.monotonic() + self.config.timeout_seconds
        transient_errors = 0
        while time.monotonic() < deadline:
            try:
                response = self._session.get(
                    status_url,
                    headers=self._request_headers(),
                    timeout=(self.config.connect_timeout_seconds, 30.0),
                )
                response.raise_for_status()
                snapshot = response.json()
                transient_errors = 0
            except (requests.RequestException, ValueError) as error:
                transient_errors += 1
                if transient_errors >= 3:
                    raise ValueError("MinerU task status is unavailable") from error
                time.sleep(0.5)
                continue
            if not isinstance(snapshot, Mapping):
                raise ValueError("MinerU task status must be an object")
            status_value = str(snapshot.get("status") or "").casefold()
            if status_value == "completed":
                break
            if status_value in {"failed", "cancelled", "canceled"}:
                error = snapshot.get("error") or snapshot.get("message")
                raise ValueError(f"MinerU task failed: {error or status_value}")
            time.sleep(0.5)
        else:
            raise subprocess.TimeoutExpired("mineru-api task", self.config.timeout_seconds)

        response = self._session.get(
            result_url,
            headers=self._request_headers(),
            timeout=(
                self.config.connect_timeout_seconds,
                self.config.timeout_seconds,
            ),
            stream=True,
        )
        response.raise_for_status()
        payload = self._bounded_response_bytes(response)
        content_type = str(response.headers.get("Content-Type", "")).casefold()
        if "zip" in content_type or payload.startswith(b"PK\x03\x04"):
            return payload
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("MinerU task result is neither ZIP nor JSON") from error
        if not isinstance(decoded, Mapping):
            raise ValueError("MinerU task result must be an object")
        return decoded

    @staticmethod
    def _same_origin_url(base_url: str, endpoint: Any) -> str:
        resolved = urljoin(f"{base_url.rstrip('/')}/", str(endpoint or ""))
        base = urlsplit(base_url)
        candidate = urlsplit(resolved)
        if (
            candidate.scheme != base.scheme
            or candidate.netloc != base.netloc
            or candidate.username
            or candidate.password
        ):
            raise ValueError("MinerU task URL must use the configured API origin")
        return resolved

    def _bounded_response_bytes(self, response: Any) -> bytes:
        buffer = BytesIO()
        iterator = getattr(response, "iter_content", None)
        chunks = iterator(chunk_size=1024 * 1024) if callable(iterator) else [response.content]
        size = 0
        for chunk in chunks:
            if not chunk:
                continue
            size += len(chunk)
            if size > self.config.max_output_bytes:
                raise ValueError("MinerU response exceeds the configured size limit")
            buffer.write(chunk)
        return buffer.getvalue()

    def _safe_extract_zip(self, payload: bytes, output_dir: Path) -> None:
        output_root = output_dir.resolve()
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) > self.config.max_archive_members:
                raise ValueError("MinerU archive contains too many files")
            total_size = sum(max(0, member.file_size) for member in members)
            if total_size > self.config.max_output_bytes:
                raise ValueError("MinerU archive exceeds the configured size limit")
            for member in members:
                relative = PurePosixPath(member.filename.replace("\\", "/"))
                if (
                    not member.filename.strip()
                    or relative == PurePosixPath(".")
                    or relative.is_absolute()
                    or ".." in relative.parts
                ):
                    raise ValueError("MinerU archive contains an unsafe path")
                file_type = (member.external_attr >> 16) & 0o170000
                if file_type == stat.S_IFLNK:
                    raise ValueError("MinerU archive contains a symbolic link")
                destination = (output_root / Path(*relative.parts)).resolve()
                try:
                    destination.relative_to(output_root)
                except ValueError as error:
                    raise ValueError("MinerU archive escapes the output directory") from error
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)

    def _materialize_json_result(
        self,
        payload: Mapping[str, Any],
        output_dir: Path,
        fallback_stem: str,
    ) -> None:
        results = payload.get("results")
        if not isinstance(results, Mapping):
            error = payload.get("error") or payload.get("message")
            raise ValueError(str(error or "MinerU JSON response has no results"))
        for index, (raw_name, raw_result) in enumerate(results.items()):
            if index >= 20 or not isinstance(raw_result, Mapping):
                break
            safe_stem = Path(str(raw_name or fallback_stem)).stem
            safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", safe_stem).strip("._")
            safe_stem = safe_stem[:120] or fallback_stem[:120] or "document"
            document_dir = output_dir / safe_stem
            document_dir.mkdir(parents=True, exist_ok=True)
            fields = (
                ("md_content", f"{safe_stem}.md", False),
                ("content_list", f"{safe_stem}_content_list.json", True),
                ("content_list_v2", f"{safe_stem}_content_list_v2.json", True),
                ("middle_json", f"{safe_stem}_middle.json", True),
                ("model_output", f"{safe_stem}_model.json", True),
            )
            for field_name, filename, is_json in fields:
                value = raw_result.get(field_name)
                if value is None:
                    continue
                if is_json:
                    content = json.dumps(value, ensure_ascii=False, indent=2)
                else:
                    content = str(value)
                if len(content.encode("utf-8")) > self.config.max_output_bytes:
                    raise ValueError("MinerU JSON artifact exceeds the size limit")
                (document_dir / filename).write_text(content, encoding="utf-8")

    def _load_output(
        self,
        root: Path,
        source_path: Path,
        *,
        mode: str,
        runtime_version: str | None,
        cache_hit: bool,
    ) -> MinerUConversionResult:
        files = self._safe_output_files(root)
        markdown_path = self._select_file(files, source_path.stem, ".md")
        legacy_path = self._select_named_json(files, source_path.stem, "_content_list.json")
        v2_path = self._select_named_json(files, source_path.stem, "_content_list_v2.json")
        middle_path = self._select_named_json(files, source_path.stem, "_middle.json")
        model_path = self._select_named_json(files, source_path.stem, "_model.json")
        warnings: list[str] = []

        legacy = self._load_json(legacy_path, warnings)
        v2 = self._load_json(v2_path, warnings)
        middle = self._load_json(middle_path, warnings)
        legacy = self._bounded_legacy_blocks(legacy)
        v2 = self._bounded_v2_blocks(v2)
        legacy = self._sanitize_paths(legacy)
        v2 = self._sanitize_paths(v2)

        markdown = ""
        truncated = False
        if markdown_path is not None:
            markdown, truncated = self._read_bounded_text(markdown_path)
        if not markdown:
            markdown = self._markdown_from_content(legacy or v2)
            if len(markdown) > self.config.max_text_chars:
                markdown = markdown[: self.config.max_text_chars].rstrip()
                truncated = True
        if not markdown.strip():
            return MinerUConversionResult(
                False,
                source_format=source_path.suffix.lower(),
                backend=self.config.backend,
                mode=mode,
                runtime_version=runtime_version,
                cache_hit=cache_hit,
                attempted=True,
                warnings=tuple(warnings),
                error="MinerU output contains no readable Markdown or content list",
            )

        middle_summary = self._middle_summary(middle)
        version = runtime_version or middle_summary.get("version")
        block_counts, page_count = self._content_summary(legacy, v2, middle_summary)
        artifacts = self._artifact_map(
            root,
            markdown_path=markdown_path,
            legacy_path=legacy_path,
            v2_path=v2_path,
            middle_path=middle_path,
            model_path=model_path,
            files=files,
        )
        structured: dict[str, Any] = {
            "integration_schema": MINERU_INTEGRATION_SCHEMA,
            "runtime_version": version,
            "backend": middle_summary.get("backend") or self.config.backend,
            "source_format": source_path.suffix.lower(),
            "summary": {
                "page_count": page_count,
                "block_counts": block_counts,
                "content_blocks": sum(block_counts.values()),
            },
            "content_list": legacy,
            "content_list_v2": v2,
            "middle": middle_summary or None,
            "artifacts": artifacts,
        }
        structured = self._fit_structured_payload(structured, warnings)
        return MinerUConversionResult(
            True,
            markdown=markdown.strip(),
            structured_data=structured,
            source_format=source_path.suffix.lower(),
            backend=str(structured.get("backend") or self.config.backend),
            mode=mode,
            runtime_version=version,
            cache_hit=cache_hit,
            attempted=True,
            truncated=truncated,
            artifacts=artifacts,
            warnings=tuple(warnings),
        )

    def _safe_output_files(self, root: Path) -> list[Path]:
        resolved_root = root.resolve()
        files: list[Path] = []
        total_size = 0
        for candidate in sorted(root.rglob("*")):
            if candidate.is_symlink():
                raise ValueError("MinerU output contains a symbolic link")
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            try:
                resolved.relative_to(resolved_root)
            except ValueError as error:
                raise ValueError("MinerU output escapes its workspace") from error
            total_size += candidate.stat().st_size
            if total_size > self.config.max_output_bytes:
                raise ValueError("MinerU output exceeds the configured size limit")
            files.append(candidate)
            if len(files) > self.config.max_archive_members:
                raise ValueError("MinerU output contains too many files")
        return files

    @staticmethod
    def _select_file(files: list[Path], stem: str, suffix: str) -> Path | None:
        matches = [path for path in files if path.suffix.casefold() == suffix]
        if not matches:
            return None
        preferred = [path for path in matches if path.stem.casefold() == stem.casefold()]
        return min(preferred or matches, key=lambda path: (len(path.parts), str(path)))

    @staticmethod
    def _select_named_json(
        files: list[Path],
        stem: str,
        ending: str,
    ) -> Path | None:
        matches = [path for path in files if path.name.casefold().endswith(ending)]
        if not matches:
            return None
        expected = f"{stem}{ending}".casefold()
        preferred = [path for path in matches if path.name.casefold() == expected]
        return min(preferred or matches, key=lambda path: (len(path.parts), str(path)))

    def _load_json(self, path: Path | None, warnings: list[str]) -> Any:
        if path is None:
            return None
        if path.stat().st_size > self.config.max_output_bytes:
            warnings.append(f"ignored oversized artifact: {path.name}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            warnings.append(f"ignored invalid JSON artifact: {path.name}")
            return None

    def _read_bounded_text(self, path: Path) -> tuple[str, bool]:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            text = stream.read(self.config.max_text_chars + 1)
        if len(text) > self.config.max_text_chars:
            return text[: self.config.max_text_chars].rstrip(), True
        return text, False

    def _bounded_legacy_blocks(self, value: Any) -> list[Any] | None:
        if not isinstance(value, list):
            return None
        return value[: self.config.max_blocks]

    def _bounded_v2_blocks(self, value: Any) -> list[Any] | None:
        if not isinstance(value, list):
            return None
        remaining = self.config.max_blocks
        pages: list[Any] = []
        for page in value:
            if remaining <= 0:
                break
            if isinstance(page, list):
                bounded = page[:remaining]
                remaining -= len(bounded)
                pages.append(bounded)
            elif isinstance(page, Mapping):
                pages.append(dict(page))
                remaining -= 1
        return pages

    def _sanitize_paths(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self._sanitize_paths(item) for item in value]
        if not isinstance(value, Mapping):
            return value
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key.casefold().endswith(("_path", "path")) and isinstance(raw_value, str):
                normalized = PurePosixPath(raw_value.replace("\\", "/"))
                if normalized.is_absolute() or ".." in normalized.parts:
                    sanitized[key] = normalized.name
                else:
                    sanitized[key] = normalized.as_posix()
            else:
                sanitized[key] = self._sanitize_paths(raw_value)
        return sanitized

    @staticmethod
    def _middle_summary(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        pages = value.get("pdf_info")
        return {
            "backend": str(value.get("_backend") or "") or None,
            "version": str(value.get("_version_name") or "") or None,
            "page_count": len(pages) if isinstance(pages, list) else None,
        }

    @staticmethod
    def _content_summary(
        legacy: Any,
        v2: Any,
        middle_summary: Mapping[str, Any],
    ) -> tuple[dict[str, int], int | None]:
        blocks: list[Any] = []
        if isinstance(legacy, list) and legacy:
            blocks = legacy
        elif isinstance(v2, list):
            blocks = [item for page in v2 for item in (page if isinstance(page, list) else [page])]
        counts = Counter(
            str(block.get("type") or "unknown")
            for block in blocks
            if isinstance(block, Mapping)
        )
        page_indices = []
        for block in blocks:
            if not isinstance(block, Mapping):
                continue
            try:
                page_indices.append(int(block.get("page_idx")))
            except (TypeError, ValueError):
                continue
        page_count = max(page_indices) + 1 if page_indices else None
        if page_count is None and isinstance(v2, list):
            page_count = len(v2)
        if page_count is None:
            raw_page_count = middle_summary.get("page_count")
            page_count = int(raw_page_count) if isinstance(raw_page_count, int) else None
        return dict(sorted(counts.items())), page_count

    @classmethod
    def _markdown_from_content(cls, value: Any) -> str:
        sections: list[str] = []

        def visit(item: Any) -> None:
            if isinstance(item, list):
                for child in item:
                    visit(child)
                return
            if not isinstance(item, Mapping):
                return
            item_type = str(item.get("type") or "").casefold()
            content = item.get("content")
            candidates: list[Any] = [
                item.get("text"),
                item.get("table_body"),
                item.get("code_body"),
                item.get("algorithm_content"),
                item.get("list_items"),
            ]
            if isinstance(content, Mapping):
                candidates.extend(content.values())
            elif content is not None:
                candidates.append(content)
            rendered: list[str] = []

            def collect(value: Any) -> None:
                if isinstance(value, str):
                    text = unescape(_HTML_TAG_PATTERN.sub(" ", value))
                    text = " ".join(text.split()).strip()
                    if text:
                        rendered.append(text)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)
                elif isinstance(value, Mapping):
                    for child in value.values():
                        collect(child)

            for candidate in candidates:
                collect(candidate)
            text = "\n".join(dict.fromkeys(rendered))
            if not text:
                return
            if item_type in {"title"} or item.get("text_level"):
                try:
                    level = max(1, min(int(item.get("text_level") or 1), 6))
                except (TypeError, ValueError):
                    level = 1
                sections.append(f"{'#' * level} {text}")
            elif item_type in {"equation", "equation_interline"}:
                sections.append(text)
            else:
                sections.append(text)

        visit(value)
        return "\n\n".join(dict.fromkeys(sections))

    def _fit_structured_payload(
        self,
        value: dict[str, Any],
        warnings: list[str],
    ) -> dict[str, Any]:
        def size(payload: Mapping[str, Any]) -> int:
            return len(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            )

        if size(value) <= self.config.max_structured_bytes:
            return value
        value["content_list_v2"] = None
        warnings.append("content_list_v2 omitted to respect the structured-data limit")
        blocks = value.get("content_list")
        while isinstance(blocks, list) and blocks and size(value) > self.config.max_structured_bytes:
            blocks = blocks[: max(1, len(blocks) // 2)]
            value["content_list"] = blocks
        if size(value) > self.config.max_structured_bytes:
            value["content_list"] = None
        warnings.append("content_list truncated to respect the structured-data limit")
        return value

    @staticmethod
    def _artifact_map(
        root: Path,
        *,
        markdown_path: Path | None,
        legacy_path: Path | None,
        v2_path: Path | None,
        middle_path: Path | None,
        model_path: Path | None,
        files: list[Path],
    ) -> dict[str, Any]:
        resolved_root = root.resolve()

        def relative(path: Path | None) -> str | None:
            return path.resolve().relative_to(resolved_root).as_posix() if path else None

        image_count = sum(path.suffix.lower() in _IMAGE_SUFFIXES for path in files)
        return {
            "markdown": relative(markdown_path),
            "content_list": relative(legacy_path),
            "content_list_v2": relative(v2_path),
            "middle": relative(middle_path),
            "model": relative(model_path),
            "image_count": image_count,
        }

    def _cache_key(self, path: Path, mode: str, runtime_version: str | None) -> str:
        digest = sha256()
        digest.update(self._file_sha256(path).encode("ascii"))
        fingerprint = {
            "schema": MINERU_INTEGRATION_SCHEMA,
            "mode": mode,
            "runtime_version": runtime_version,
            "backend": self.config.backend,
            "parse_method": self.config.parse_method,
            "effort": self.config.effort,
            "language": self.config.language,
            "formula_enable": self.config.formula_enable,
            "table_enable": self.config.table_enable,
            "image_analysis": self.config.image_analysis,
        }
        digest.update(json.dumps(fingerprint, sort_keys=True).encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _prune_cache(self) -> None:
        if not self.cache_root.is_dir():
            return
        entries = [
            path
            for path in self.cache_root.iterdir()
            if path.is_dir() and not path.is_symlink()
        ]
        entries.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for stale in entries[self.config.cache_max_entries :]:
            try:
                stale.resolve().relative_to(self.cache_root.resolve())
            except ValueError:
                continue
            shutil.rmtree(stale)


__all__ = [
    "MINERU_INTEGRATION_SCHEMA",
    "MINERU_IMAGE_SUFFIXES",
    "MINERU_SUPPORTED_SUFFIXES",
    "MinerUConfig",
    "MinerUConversionResult",
    "MinerUDocumentConverter",
    "MinerURuntimeStatus",
]
