"""Contract tests for tolerant workbook loading.

Some exporters write ``xl/styles.xml`` entries that Excel and WPS accept but
openpyxl rejects, for example an empty ``<fill/>`` node. The loader must repair
that shape in memory and still surface the original error when the repair does
not apply.
"""

from __future__ import annotations

import re
import zipfile

from openpyxl import Workbook, load_workbook
import pytest

from artpm_agent.utils.spreadsheet_io import repair_workbook_styles, safe_load_workbook


def _write_workbook(path):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Quote"
    worksheet.append(["Asset", "Qty", "Cost"])
    worksheet.append(["Character Model", 2, 1200])
    workbook.save(path)


def _inject_empty_fill(path):
    """Add a self-closing <fill/> node, mirroring non-strict exporters."""

    with zipfile.ZipFile(path) as archive:
        members = [(item, archive.read(item.filename)) for item in archive.infolist()]
    styles = {item.filename: payload for item, payload in members}["xl/styles.xml"]
    styles = styles.decode("utf-8")
    styles, updated = re.subn(
        r'<fills count="(\d+)">',
        lambda match: f'<fills count="{int(match.group(1)) + 1}">',
        styles,
        count=1,
    )
    assert updated == 1, "openpyxl output did not contain a countable <fills> node"
    styles = styles.replace("</fills>", "<fill/></fills>", 1)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item, payload in members:
            archive.writestr(
                item,
                styles.encode("utf-8") if item.filename == "xl/styles.xml" else payload,
            )

    assert updated == 1, "openpyxl output did not contain a countable <fills> node"
    styles = styles.replace("</fills>", "<fill/></fills>", 1)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item, payload in members:
            archive.writestr(
                item,
                styles.encode("utf-8") if item.filename == "xl/styles.xml" else payload,
            )


def test_repair_workbook_styles_replaces_empty_fill_nodes(tmp_path):
    path = tmp_path / "quote.xlsx"
    _write_workbook(path)
    _inject_empty_fill(path)

    with pytest.raises(TypeError):
        load_workbook(path, read_only=True, data_only=True)

    repaired, changed = repair_workbook_styles(path.read_bytes())

    assert changed is True
    patched = tmp_path / "repaired.xlsx"
    patched.write_bytes(repaired)
    workbook = load_workbook(patched, read_only=True, data_only=True)
    try:
        assert workbook["Quote"]["A2"].value == "Character Model"
    finally:
        workbook.close()


def test_repair_workbook_styles_reports_no_change_for_clean_files(tmp_path):
    path = tmp_path / "quote.xlsx"
    _write_workbook(path)

    _repaired, changed = repair_workbook_styles(path.read_bytes())

    assert changed is False


def test_safe_load_workbook_reads_malformed_styles_from_disk(tmp_path):
    path = tmp_path / "quote.xlsx"
    _write_workbook(path)
    _inject_empty_fill(path)

    workbook = safe_load_workbook(path, read_only=True, data_only=True)
    try:
        assert workbook["Quote"]["B2"].value == 2
    finally:
        workbook.close()


def test_safe_load_workbook_reads_malformed_styles_from_stream(tmp_path):
    path = tmp_path / "quote.xlsx"
    _write_workbook(path)
    _inject_empty_fill(path)

    with path.open("rb") as handle:
        workbook = safe_load_workbook(
            handle,
            source_name=path.name,
            read_only=True,
            data_only=True,
        )
    try:
        assert workbook["Quote"]["A2"].value == "Character Model"
    finally:
        workbook.close()


def test_safe_load_workbook_leaves_the_source_file_untouched(tmp_path):
    path = tmp_path / "quote.xlsx"
    _write_workbook(path)
    _inject_empty_fill(path)
    before = path.read_bytes()

    workbook = safe_load_workbook(path, read_only=True, data_only=True)
    workbook.close()

    assert path.read_bytes() == before


def test_safe_load_workbook_reraises_when_repair_cannot_apply(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not a zip archive")

    with pytest.raises(Exception) as error:
        safe_load_workbook(path, read_only=True)

    assert not isinstance(error.value, FileNotFoundError)
