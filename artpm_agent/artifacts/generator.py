"""Generate new XLSX and DOCX files below one workspace artifact root."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from hashlib import sha256
import math
from pathlib import Path
import re
import shutil
from typing import Any
import unicodedata
from uuid import uuid4

from docx import Document
from openpyxl import Workbook


_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_INVALID_SHEET_NAME = re.compile(r"[\\/*?:\[\]]")
_ALLOWED_CELL_TYPES = (str, int, float, bool, date, datetime)


class WorkspaceArtifactGenerator:
    """Create versioned artifacts without exposing overwrite or delete operations."""

    MAX_XLSX_ROWS = 1_048_575
    MAX_XLSX_COLUMNS = 16_384

    def __init__(
        self,
        artifacts_root: str | Path,
        *,
        max_rows: int = 10_000,
        max_columns: int = 100,
        max_paragraphs: int = 2_000,
        max_cell_chars: int = 32_000,
        max_total_text_chars: int = 1_000_000,
        max_file_size: int = 50 * 1024 * 1024,
        max_versions: int = 10_000,
    ) -> None:
        root = Path(artifacts_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise ValueError(f"Artifact root is not a directory: {root}")
        self.root = root
        self.max_rows = self._bounded_positive(
            max_rows,
            "max_rows",
            self.MAX_XLSX_ROWS,
        )
        self.max_columns = self._bounded_positive(
            max_columns,
            "max_columns",
            self.MAX_XLSX_COLUMNS,
        )
        self.max_paragraphs = self._positive(max_paragraphs, "max_paragraphs")
        self.max_cell_chars = self._positive(max_cell_chars, "max_cell_chars")
        self.max_total_text_chars = self._positive(
            max_total_text_chars,
            "max_total_text_chars",
        )
        self.max_file_size = self._positive(max_file_size, "max_file_size")
        self.max_versions = self._positive(max_versions, "max_versions")

    @staticmethod
    def _positive(value: int, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field} must be a positive integer")
        return value

    @classmethod
    def _bounded_positive(cls, value: int, field: str, maximum: int) -> int:
        value = cls._positive(value, field)
        if value > maximum:
            raise ValueError(f"{field} cannot exceed {maximum}")
        return value

    @staticmethod
    def _sequence(value: Any, field: str) -> Sequence[Any]:
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(
            value, Sequence
        ):
            raise ValueError(f"{field} must be a sequence")
        return value

    @staticmethod
    def _safe_filename(filename: str, extension: str) -> str:
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("filename must be a non-empty string")
        normalized = unicodedata.normalize("NFKC", filename.strip())
        if (
            "/" in normalized
            or "\\" in normalized
            or normalized in {".", ".."}
            or _WINDOWS_DRIVE.match(normalized)
        ):
            raise ValueError("filename must not contain a path")

        suffix = Path(normalized).suffix
        expected_suffix = f".{extension}"
        if suffix and suffix.casefold() != expected_suffix:
            raise ValueError(f"filename extension must be {expected_suffix}")
        stem = normalized[: -len(suffix)] if suffix else normalized
        stem = _INVALID_FILENAME.sub("_", stem).strip(" .")
        if not stem:
            raise ValueError("filename has no usable characters")
        if stem.upper() in _WINDOWS_RESERVED_NAMES:
            stem = f"_{stem}"

        max_stem_length = 120 - len(expected_suffix)
        stem = stem[:max_stem_length].rstrip(" .")
        if not stem:
            raise ValueError("filename has no usable characters")
        return f"{stem}{expected_suffix}"

    @staticmethod
    def _sheet_name(value: Any) -> str:
        if value is None:
            return "Sheet1"
        if not isinstance(value, str) or not value.strip():
            raise ValueError("sheet_name must be a non-empty string")
        name = _INVALID_SHEET_NAME.sub("_", value.strip())[:31].strip("'")
        if not name:
            raise ValueError("sheet_name has no usable characters")
        return name

    def _safe_path(self, filename: str) -> Path:
        candidate = (self.root / filename).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise ValueError("Artifact path escapes its workspace root") from error
        if candidate.parent != self.root:
            raise ValueError("Artifacts must be direct children of the workspace root")
        return candidate

    @staticmethod
    def _versioned_name(filename: str, version: int) -> str:
        path = Path(filename)
        if version == 1:
            return filename
        return f"{path.stem} ({version}){path.suffix}"

    def _publish_new(self, temporary_path: Path, filename: str) -> tuple[Path, int]:
        for version in range(1, self.max_versions + 1):
            destination = self._safe_path(self._versioned_name(filename, version))
            if destination.exists():
                continue
            try:
                with temporary_path.open("rb") as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
            except FileExistsError:
                continue
            except IsADirectoryError:
                continue
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            return destination, version
        raise FileExistsError(
            f"No free artifact version found after {self.max_versions} attempts"
        )

    def _finalize(
        self,
        temporary_path: Path,
        filename: str,
        artifact_format: str,
        mime_type: str,
        details: Mapping[str, Any],
    ) -> dict[str, Any]:
        size = temporary_path.stat().st_size
        if size <= 0:
            raise RuntimeError("Generated artifact is empty")
        if size > self.max_file_size:
            raise ValueError(
                f"Generated artifact exceeds {self.max_file_size} bytes"
            )
        digest = sha256(temporary_path.read_bytes()).hexdigest()
        destination, version = self._publish_new(temporary_path, filename)
        return {
            "id": uuid4().hex,
            "name": destination.name,
            "stored_path": destination.name,
            "path": str(destination),
            "format": artifact_format,
            "mime_type": mime_type,
            "size": size,
            "sha256": digest,
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(
                timespec="microseconds"
            ),
            **dict(details),
        }

    def _temporary_path(self) -> Path:
        return self._safe_path(f".artifact-{uuid4().hex}.tmp")

    def _cell_value(self, value: Any, field: str) -> Any:
        if value is None:
            return None
        if not isinstance(value, _ALLOWED_CELL_TYPES):
            raise ValueError(f"{field} contains an unsupported cell value")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{field} contains a non-finite number")
        if isinstance(value, str):
            if len(value) > self.max_cell_chars:
                raise ValueError(
                    f"{field} exceeds the {self.max_cell_chars} character limit"
                )
            if value.startswith("="):
                return f"'{value}"
        return value

    def _table_rows(
        self,
        columns: tuple[str, ...],
        rows: Iterable[Any],
    ) -> Iterable[list[Any]]:
        if isinstance(rows, (str, bytes, bytearray, Mapping)) or not isinstance(
            rows, Iterable
        ):
            raise ValueError("table.rows must be an iterable of rows")
        column_set = set(columns)
        for index, row in enumerate(rows, start=1):
            if index > self.max_rows:
                raise ValueError(f"table.rows cannot exceed {self.max_rows} rows")
            if isinstance(row, Mapping):
                unknown = set(row) - column_set
                if unknown:
                    raise ValueError(
                        "table row contains unknown columns: "
                        + ", ".join(sorted(str(item) for item in unknown))
                    )
                values = [row.get(column) for column in columns]
            else:
                values = list(self._sequence(row, f"table.rows[{index - 1}]"))
                if len(values) != len(columns):
                    raise ValueError(
                        f"table.rows[{index - 1}] must contain {len(columns)} cells"
                    )
            yield [
                self._cell_value(value, f"table.rows[{index - 1}]")
                for value in values
            ]

    def generate_xlsx(
        self,
        filename: str,
        table: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Generate one new workbook from columns and row mappings/sequences."""
        if not isinstance(table, Mapping):
            raise ValueError("table must be a mapping")
        unknown = set(table) - {"columns", "rows", "sheet_name"}
        if unknown:
            raise ValueError(
                "table contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown))
            )
        raw_columns = self._sequence(table.get("columns"), "table.columns")
        if not raw_columns or len(raw_columns) > self.max_columns:
            raise ValueError(
                f"table.columns must contain 1 to {self.max_columns} columns"
            )
        columns: list[str] = []
        header_values: list[str] = []
        for index, column in enumerate(raw_columns):
            if not isinstance(column, str) or not column.strip():
                raise ValueError(f"table.columns[{index}] must be non-empty text")
            column = column.strip()
            columns.append(column)
            header_values.append(
                self._cell_value(column, f"table.columns[{index}]")
            )
        if len(columns) != len(set(columns)):
            raise ValueError("table.columns must be unique")

        safe_name = self._safe_filename(filename, "xlsx")
        sheet_name = self._sheet_name(table.get("sheet_name"))
        normalized_rows = list(
            self._table_rows(tuple(columns), table.get("rows", []))
        )
        temporary_path = self._temporary_path()
        try:
            workbook = Workbook(write_only=True)
            worksheet = workbook.create_sheet(title=sheet_name)
            worksheet.append(header_values)
            for values in normalized_rows:
                worksheet.append(values)
            workbook.save(temporary_path)
            return self._finalize(
                temporary_path,
                safe_name,
                "xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                {
                    "rows": len(normalized_rows),
                    "columns": len(columns),
                    "sheet_name": sheet_name,
                },
            )
        finally:
            temporary_path.unlink(missing_ok=True)

    def _paragraph(self, value: Any, index: int) -> dict[str, Any]:
        if isinstance(value, str):
            paragraph: dict[str, Any] = {"text": value}
        elif isinstance(value, Mapping):
            paragraph = dict(value)
        else:
            raise ValueError(f"paragraphs[{index}] must be text or a mapping")
        unknown = set(paragraph) - {"text", "kind", "level", "bold", "italic"}
        if unknown:
            raise ValueError(
                f"paragraphs[{index}] contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown))
            )
        text = paragraph.get("text")
        if not isinstance(text, str):
            raise ValueError(f"paragraphs[{index}].text must be text")
        if len(text) > self.max_total_text_chars:
            raise ValueError(f"paragraphs[{index}].text is too large")
        kind = paragraph.get("kind", "paragraph")
        if kind not in {"paragraph", "heading", "bullet", "numbered"}:
            raise ValueError(f"paragraphs[{index}].kind is unsupported")
        level = paragraph.get("level", 1)
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 9:
            raise ValueError(f"paragraphs[{index}].level must be between 1 and 9")
        for key in ("bold", "italic"):
            if key in paragraph and not isinstance(paragraph[key], bool):
                raise ValueError(f"paragraphs[{index}].{key} must be boolean")
        return {
            "text": text,
            "kind": kind,
            "level": level,
            "bold": paragraph.get("bold", False),
            "italic": paragraph.get("italic", False),
        }

    def generate_docx(
        self,
        filename: str,
        paragraphs: Sequence[Any],
    ) -> dict[str, Any]:
        """Generate one new document from structured paragraphs."""
        raw_paragraphs = self._sequence(paragraphs, "paragraphs")
        if not raw_paragraphs or len(raw_paragraphs) > self.max_paragraphs:
            raise ValueError(
                f"paragraphs must contain 1 to {self.max_paragraphs} items"
            )
        normalized = [
            self._paragraph(value, index)
            for index, value in enumerate(raw_paragraphs)
        ]
        total_chars = sum(len(item["text"]) for item in normalized)
        if total_chars > self.max_total_text_chars:
            raise ValueError(
                "paragraph text exceeds the total character limit of "
                f"{self.max_total_text_chars}"
            )

        safe_name = self._safe_filename(filename, "docx")
        temporary_path = self._temporary_path()
        try:
            document = Document()
            for item in normalized:
                if item["kind"] == "heading":
                    paragraph = document.add_heading(level=item["level"])
                else:
                    style = {
                        "bullet": "List Bullet",
                        "numbered": "List Number",
                    }.get(item["kind"])
                    paragraph = document.add_paragraph(style=style)
                run = paragraph.add_run(item["text"])
                run.bold = item["bold"]
                run.italic = item["italic"]
            document.save(temporary_path)
            return self._finalize(
                temporary_path,
                safe_name,
                "docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                {
                    "paragraphs": len(normalized),
                    "text_chars": total_chars,
                },
            )
        finally:
            temporary_path.unlink(missing_ok=True)


__all__ = ["WorkspaceArtifactGenerator"]
