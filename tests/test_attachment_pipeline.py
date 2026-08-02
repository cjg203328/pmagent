from __future__ import annotations

from pathlib import Path

import pytest

from artpm_agent.harness.attachment_pipeline import (
    parse_context_attachments,
    vision_attachment_paths,
)


def test_parse_context_attachments_bounds_inputs_and_redacts_paths(tmp_path):
    calls: list[str] = []

    def process(path: str, _hint: str):
        calls.append(path)
        return {
            "success": True,
            "document_type": "text",
            "raw_text": "source text",
            "markdown": "# Evidence\n\nvalue",
        }

    paths = [tmp_path / f"file-{index}.txt" for index in range(5)]
    parsed, evidence = parse_context_attachments(
        "summarise",
        {"file_paths": [str(path) for path in paths]},
        process,
    )

    assert calls == [str(path) for path in paths[:3]]
    assert len(parsed) == 3
    assert all(item["file_path"] == str(path.resolve()) for item, path in zip(parsed, paths))
    assert "<attachment_data>" in evidence
    assert "<attachment_markdown>" in evidence
    assert str(paths[0].resolve()) not in evidence


def test_parse_context_attachments_handles_invalid_parser_result(tmp_path):
    path = tmp_path / "broken.bin"

    parsed, evidence = parse_context_attachments(
        "inspect",
        {"file_path": str(path)},
        lambda _path, _hint: None,
    )

    assert parsed == [
        {
            "name": path.name,
            "file_path": str(path.resolve()),
            "success": False,
            "error": "invalid parser result",
        }
    ]
    assert '"success": false' in evidence
    assert "invalid parser result" in evidence


def test_parse_context_attachments_treats_scalar_file_paths_as_one_file(tmp_path):
    path = tmp_path / "single.txt"
    calls: list[str] = []

    def process(file_path: str, _hint: str):
        calls.append(file_path)
        return {"success": True, "raw_text": "one"}

    parsed, _evidence = parse_context_attachments(
        "inspect",
        {"file_paths": str(path)},
        process,
    )

    assert calls == [str(path)]
    assert [item["name"] for item in parsed] == [path.name]


def test_parse_context_attachments_reuses_matching_same_turn_snapshot(tmp_path):
    path = tmp_path / "brief.docx"
    path.write_bytes(b"docx")
    snapshot = {
        "name": path.name,
        "file_path": str(path.resolve()),
        "success": True,
        "markdown": "cached content",
    }

    parsed, evidence = parse_context_attachments(
        "continue",
        {
            "file_paths": [str(path)],
            "parsed_files": [snapshot],
            "attachment_context": "cached evidence",
        },
        lambda _path, _hint: pytest.fail("matching snapshot must not be parsed again"),
    )

    assert parsed == [snapshot]
    assert parsed[0] is not snapshot
    assert evidence == "cached evidence"


def test_attachment_evidence_emits_document_text_once(tmp_path):
    path = tmp_path / "brief.txt"
    marker = "UNIQUE_ATTACHMENT_BODY"

    _parsed, evidence = parse_context_attachments(
        "summarise",
        {"file_path": str(path)},
        lambda _path, _hint: {
            "success": True,
            "document_type": "text",
            "raw_text": marker,
            "markdown": marker,
        },
    )

    assert evidence.count(marker) == 1
    assert '"raw_text"' not in evidence
    assert '"markdown"' not in evidence.split("<attachment_markdown>", 1)[0]


def test_vision_attachment_paths_selects_images_and_cleans_pdf_pages(tmp_path):
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"placeholder")

    with vision_attachment_paths(
        [
            {
                "success": True,
                "file_path": str(image_path),
                "requires_vision": True,
            }
        ],
        visual_semantics_requested=False,
    ) as selected:
        assert selected == [str(image_path)]

    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "scan.pdf"
    document = fitz.open()
    page = document.new_page(width=72, height=72)
    page.insert_text((10, 30), "scan")
    document.save(str(pdf_path))
    document.close()

    generated: list[str] = []
    with vision_attachment_paths(
        [
            {
                "success": True,
                "file_path": str(pdf_path),
                "requires_vision": True,
                "extracted_data": {"pages_requiring_ocr": [1]},
            }
        ],
        visual_semantics_requested=False,
    ) as selected:
        generated.extend(selected)
        assert len(selected) == 1
        assert Path(selected[0]).is_file()

    assert generated
    assert not Path(generated[0]).exists()


def test_vision_attachment_paths_skips_non_visual_files(tmp_path):
    text_path = tmp_path / "notes.txt"
    text_path.write_text("notes", encoding="utf-8")

    with vision_attachment_paths(
        [
            {
                "success": True,
                "file_path": str(text_path),
                "requires_vision": True,
            }
        ],
        visual_semantics_requested=False,
    ) as selected:
        assert selected == []
