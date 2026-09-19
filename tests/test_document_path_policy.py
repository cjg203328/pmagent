from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from artpm_agent.security.document_paths import TrustedDocumentRoots
from artpm_agent.skills.skill_router import DocumentClassifierParser, SkillRouter
from artpm_agent.tenancy import TenantContext


def _parser(*roots: Path) -> DocumentClassifierParser:
    return DocumentClassifierParser(
        {"document_roots": TrustedDocumentRoots.from_paths(*roots)}
    )


def test_document_parser_allows_current_attachment_and_artifact_roots(tmp_path):
    attachment_root = tmp_path / "chat_attachments" / "conversation-a"
    artifact_root = tmp_path / "artifacts" / "tenant-a" / "workspace-a"
    attachment_root.mkdir(parents=True)
    artifact_root.mkdir(parents=True)
    upload = attachment_root / "brief.txt"
    artifact = artifact_root / "report.md"
    upload.write_text("uploaded brief", encoding="utf-8")
    artifact.write_text("generated report", encoding="utf-8")
    parser = _parser(attachment_root.parent, artifact_root)

    upload_result = parser.run({"file_path": "conversation-a/brief.txt"})
    artifact_result = parser.run({"file_path": str(artifact)})

    assert upload_result["success"] is True
    assert upload_result["raw_text"] == "uploaded brief"
    assert artifact_result["success"] is True
    assert artifact_result["raw_text"] == "generated report"


def test_document_parser_rejects_traversal_and_cross_workspace_paths(tmp_path):
    current_root = tmp_path / "artifacts" / "tenant-a" / "workspace-a"
    other_root = tmp_path / "artifacts" / "tenant-a" / "workspace-b"
    current_root.mkdir(parents=True)
    other_root.mkdir(parents=True)
    secret = other_root / "private.txt"
    secret.write_text("cross-workspace secret", encoding="utf-8")
    parser = _parser(current_root)

    traversal = current_root / "nested" / ".." / ".." / "workspace-b" / "private.txt"
    traversal_result = parser.run({"file_path": str(traversal)})
    cross_workspace_result = parser.run({"file_path": str(secret)})

    assert traversal_result["error_code"] == "document_path_not_allowed"
    assert cross_workspace_result["error_code"] == "document_path_not_allowed"


def test_document_parser_rejects_missing_and_non_file_targets(tmp_path):
    root = tmp_path / "workspace"
    directory = root / "folder"
    directory.mkdir(parents=True)
    parser = _parser(root)

    missing = parser.run({"file_path": str(root / "missing.txt")})
    non_file = parser.run({"file_path": str(directory)})

    assert missing["error_code"] == "document_not_found"
    assert non_file["error_code"] == "document_not_file"


def test_document_parser_rejects_symlink_escape(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret", encoding="utf-8")
    link = root / "linked.txt"
    try:
        os.symlink(outside, link)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlinks unavailable: {error}")

    result = _parser(root).run({"file_path": str(link)})

    assert result["error_code"] == "document_path_not_allowed"


def test_document_parser_rejects_parent_symlink_escape(tmp_path):
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("outside secret", encoding="utf-8")
    link = root / "linked-directory"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"directory symlinks unavailable: {error}")

    result = _parser(root).run({"file_path": str(link / "secret.txt")})

    assert result["error_code"] == "document_path_not_allowed"


def test_document_parser_ignores_forged_scope_and_root_inputs(tmp_path):
    current_root = tmp_path / "workspace-a"
    other_root = tmp_path / "workspace-b"
    current_root.mkdir()
    other_root.mkdir()
    target = other_root / "private.txt"
    target.write_text("do not disclose", encoding="utf-8")

    result = _parser(current_root).run(
        {
            "file_path": str(target),
            "trusted_root": str(other_root),
            "document_roots": [str(other_root)],
            "workspace_id": "workspace-b",
        }
    )

    assert result["error_code"] == "document_path_not_allowed"


def test_document_path_errors_do_not_disclose_path_or_contents(tmp_path):
    root = tmp_path / "workspace-a"
    secret_dir = tmp_path / "internal-customer-42"
    root.mkdir()
    secret_dir.mkdir()
    target = secret_dir / "payroll.txt"
    target.write_text("salary=999999", encoding="utf-8")

    result = _parser(root).run({"file_path": str(target)})
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["error_code"] == "document_path_not_allowed"
    assert str(target) not in serialized
    assert "internal-customer-42" not in serialized
    assert "salary=999999" not in serialized


def test_document_parser_fails_closed_without_server_roots(tmp_path):
    target = tmp_path / "brief.txt"
    target.write_text("brief", encoding="utf-8")

    result = DocumentClassifierParser().run({"file_path": str(target)})

    assert result["error_code"] == "document_path_not_allowed"


def test_skill_router_derives_roots_from_trusted_tenant_context(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    tenant_context = TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
    )
    current_root = tmp_path / "artifacts" / "tenant-a" / "workspace-a"
    other_root = tmp_path / "artifacts" / "tenant-a" / "workspace-b"
    current_root.mkdir(parents=True)
    other_root.mkdir(parents=True)
    current = current_root / "current.txt"
    other = other_root / "other.txt"
    current.write_text("current workspace", encoding="utf-8")
    other.write_text("other workspace", encoding="utf-8")
    router = SkillRouter({}).for_tenant(tenant_context)

    allowed = router.execute_skill(
        "document_classifier_parser",
        {"file_path": str(current)},
    )
    denied = router.execute_skill(
        "document_classifier_parser",
        {
            "file_path": str(other),
            "workspace_id": "workspace-a",
            "trusted_root": str(other_root),
        },
    )

    assert allowed["success"] is True
    assert denied["error_code"] == "document_path_not_allowed"
