"""Format-aware verification for generated workspace artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


class ArtifactVerificationError(RuntimeError):
    """Raised when a generated file cannot prove its delivery contract."""


def _require_file(path: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ArtifactVerificationError("generated artifact is empty or missing")


def _verify_xlsx(path: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        expected_sheets = list(expected["sheets"])
        if workbook.sheetnames != [str(sheet["name"]) for sheet in expected_sheets]:
            raise ArtifactVerificationError("worksheet list does not match the plan")
        total_rows = 0
        max_columns = 0
        for sheet in expected_sheets:
            expected_values = [tuple(row) for row in sheet["values"]]
            expected_columns = len(expected_values[0]) if expected_values else 0
            values = list(
                workbook[str(sheet["name"])].iter_rows(
                    max_col=expected_columns,
                    values_only=True,
                )
            )
            if values != expected_values:
                raise ArtifactVerificationError(
                    "worksheet content does not match the plan"
                )
            total_rows += max(0, len(values) - 1)
            max_columns = max(max_columns, len(values[0]) if values else 0)
        return {
            "status": "passed",
            "reopened": True,
            "sheet_count": len(workbook.sheetnames),
            "row_count": total_rows,
            "column_count": max_columns,
            "content_match": True,
        }
    finally:
        workbook.close()


def _verify_xlsx_monthly(path: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a preserve-format monthly workbook and its processing note."""
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=False, data_only=True)
    try:
        target_month = str(expected.get("target_month") or "")
        mode = str(expected.get("mode") or "new_items")
        note_sheet = workbook["处理说明"] if "处理说明" in workbook.sheetnames else None
        if note_sheet is None:
            raise ArtifactVerificationError(
                "monthly workbook is missing processing notes"
            )
        notes = {
            str(row[0].value): row[1].value
            for row in note_sheet.iter_rows(min_row=1, values_only=False)
            if row and row[0].value is not None and len(row) > 1
        }
        if str(notes.get("目标月份") or "") != target_month:
            raise ArtifactVerificationError("monthly target month does not match")
        expected_mode = "截至目标月累计" if mode == "cumulative" else "目标月新增"
        if str(notes.get("处理口径") or "") != expected_mode:
            raise ArtifactVerificationError("monthly processing mode does not match")
        if expected.get("source_sha256") and str(
            notes.get("源文件 SHA-256") or ""
        ) != str(expected["source_sha256"]):
            raise ArtifactVerificationError("source workbook hash does not match")
        expected_rows = expected.get("matched_rows")
        if expected_rows is not None and int(notes.get("保留行数") or -1) != int(
            expected_rows
        ):
            raise ArtifactVerificationError("monthly matched row count does not match")
        if expected.get("headers"):
            sheet_name = str(expected.get("sheet_name") or "")
            if not sheet_name or sheet_name not in workbook.sheetnames:
                raise ArtifactVerificationError("monthly data sheet is missing")
            actual_headers = [
                cell.value
                for cell in workbook[sheet_name][int(expected.get("header_row", 1))]
            ]
            if actual_headers[: len(expected["headers"])] != list(expected["headers"]):
                raise ArtifactVerificationError("monthly headers do not match")
        return {
            "status": "passed",
            "reopened": True,
            "content_match": True,
            "monthly_target": target_month,
            "mode": mode,
            "matched_rows": int(notes.get("保留行数") or 0),
            "uncovered_rows": int(notes.get("未覆盖行数") or 0),
            "source_sha256": str(notes.get("源文件 SHA-256") or ""),
        }
    finally:
        workbook.close()


def _verify_docx(path: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    from docx import Document

    document = Document(path)
    actual = [paragraph.text for paragraph in document.paragraphs]
    expected_text = [str(value) for value in expected["paragraphs"]]
    if actual != expected_text:
        raise ArtifactVerificationError("document text does not match the plan")
    return {
        "status": "passed",
        "reopened": True,
        "paragraph_count": len(actual),
        "text_present": all(text in "\n".join(actual) for text in expected_text),
        "content_match": True,
    }


def _slide_text(slide: Any) -> str:
    return "\n".join(
        str(shape.text)
        for shape in slide.shapes
        if hasattr(shape, "text") and str(shape.text).strip()
    )


def _verify_pptx(path: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    try:
        from pptx import Presentation
    except ImportError as error:
        raise ArtifactVerificationError(
            "PPTX verification requires the documents profile"
        ) from error

    presentation = Presentation(path)
    expected_slides = list(expected["slides"])
    if len(presentation.slides) != len(expected_slides):
        raise ArtifactVerificationError(
            "presentation slide count does not match the plan"
        )
    actual_text = [_slide_text(slide) for slide in presentation.slides]
    for index, slide in enumerate(expected_slides):
        required = [str(slide["title"]), *map(str, slide.get("bullets", []))]
        if any(text not in actual_text[index] for text in required if text):
            raise ArtifactVerificationError(
                f"presentation slide {index + 1} is missing planned text"
            )
    return {
        "status": "passed",
        "reopened": True,
        "slide_count": len(actual_text),
        "text_present": True,
        "content_match": True,
    }


def _verify_pdf(path: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    try:
        import pymupdf
    except ImportError as error:
        raise ArtifactVerificationError(
            "PDF verification requires the documents profile"
        ) from error

    document = pymupdf.open(path)
    try:
        if document.page_count <= 0:
            raise ArtifactVerificationError("PDF contains no pages")
        extracted = "\n".join(page.get_text() for page in document)
        expected_text = [str(value) for value in expected["paragraphs"]]
        text_present = all(text in extracted for text in expected_text)
        if not text_present:
            raise ArtifactVerificationError("PDF text does not match the plan")
        return {
            "status": "passed",
            "reopened": True,
            "page_count": document.page_count,
            "text_present": True,
            "content_match": True,
        }
    finally:
        document.close()


def verify_artifact(
    path: str | Path,
    artifact_format: str,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    """Reopen a generated file and prove its planned content is present."""
    artifact_path = Path(path).resolve()
    _require_file(artifact_path)
    verifiers = {
        "xlsx": _verify_xlsx,
        "xlsx_monthly": _verify_xlsx_monthly,
        "docx": _verify_docx,
        "pptx": _verify_pptx,
        "pdf": _verify_pdf,
    }
    normalized = str(artifact_format).lower().lstrip(".")
    verifier = verifiers.get(normalized)
    if verifier is None:
        raise ArtifactVerificationError(
            f"unsupported verification format: {normalized}"
        )
    result = verifier(artifact_path, expected)
    result["format"] = normalized
    result["size_bytes"] = artifact_path.stat().st_size
    return result


__all__ = ["ArtifactVerificationError", "verify_artifact"]
