from __future__ import annotations

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"

from artpm_agent.utils.chat_attachments import (
    ChatAttachmentStore,
    normalize_chat_submission,
    chat_submission_audio,
    select_conversation_attachments,
)


class FakeUploadedFile(io.BytesIO):
    def __init__(self, name: str, data: bytes, mime_type: str = "") -> None:
        super().__init__(data)
        self.name = name
        self.type = mime_type
        self.size = len(data)


def test_save_files_uses_uuid_names_and_returns_resolvable_metadata(tmp_path):
    store = ChatAttachmentStore(tmp_path / "attachments")
    uploaded = FakeUploadedFile("报价单.XLSX", b"workbook", "application/xlsx")
    uploaded.seek(2)

    attachments = store.save_files("conversation_1", [uploaded])

    assert uploaded.tell() == 2
    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment["name"] == "报价单.XLSX"
    assert attachment["original_name"] == "报价单.XLSX"
    assert attachment["extension"] == "xlsx"
    assert attachment["size"] == 8
    assert attachment["sha256"] == hashlib.sha256(b"workbook").hexdigest()
    assert "报价单" not in attachment["stored_path"]
    assert attachment["stored_path"].startswith("conversation_1/")

    resolved = store.resolve_paths(attachments)
    assert Path(resolved[0]).read_bytes() == b"workbook"
    assert not list((tmp_path / "attachments").rglob("*.part"))


def test_original_path_components_are_not_used_for_storage(tmp_path):
    store = ChatAttachmentStore(tmp_path / "attachments")

    attachment = store.save_files(
        "conversation", [FakeUploadedFile(r"..\..\客户报价.csv", b"a,b\n1,2")]
    )[0]

    assert attachment["name"] == "客户报价.csv"
    assert Path(store.resolve_paths([attachment])[0]).parent.name == "conversation"


@pytest.mark.parametrize("conversation_id", ["../escape", "nested/path", "", "."])
def test_conversation_id_rejects_path_traversal(tmp_path, conversation_id):
    store = ChatAttachmentStore(tmp_path / "attachments")

    with pytest.raises(ValueError):
        store.save_files(conversation_id, [FakeUploadedFile("note.txt", b"data")])


def test_resolve_paths_rejects_escape_and_missing_files(tmp_path):
    root = tmp_path / "attachments"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    store = ChatAttachmentStore(root)

    with pytest.raises(ValueError, match="escapes"):
        store.resolve_paths([{"stored_path": "../outside.txt"}])
    with pytest.raises(ValueError, match="relative"):
        store.resolve_paths([{"stored_path": str(outside.resolve())}])
    with pytest.raises(FileNotFoundError):
        store.resolve_paths([{"stored_path": "conversation/missing.txt"}])


def test_save_files_enforces_extension_count_and_size_limits(tmp_path):
    store = ChatAttachmentStore(
        tmp_path / "attachments",
        max_files=2,
        max_file_size=4,
        max_total_size=6,
    )

    with pytest.raises(ValueError, match="Unsupported"):
        store.save_files("c1", [FakeUploadedFile("script.exe", b"exe")])
    with pytest.raises(ValueError, match="at most"):
        store.save_files(
            "c1",
            [
                FakeUploadedFile("a.txt", b"a"),
                FakeUploadedFile("b.txt", b"b"),
                FakeUploadedFile("c.txt", b"c"),
            ],
        )
    with pytest.raises(ValueError, match="exceeds 4"):
        store.save_files("c1", [FakeUploadedFile("large.txt", b"12345")])
    with pytest.raises(ValueError, match="batch exceeds 6"):
        store.save_files(
            "c1",
            [FakeUploadedFile("a.txt", b"1234"), FakeUploadedFile("b.md", b"567")],
        )

    assert not list((tmp_path / "attachments").rglob("*.*"))


def test_failed_batch_removes_files_created_earlier_in_the_batch(tmp_path):
    store = ChatAttachmentStore(
        tmp_path / "attachments",
        max_file_size=4,
        max_total_size=8,
    )

    with pytest.raises(ValueError):
        store.save_files(
            "conversation",
            [FakeUploadedFile("ok.txt", b"1234"), FakeUploadedFile("bad.txt", b"12345")],
        )

    assert not (tmp_path / "attachments" / "conversation").exists()


def test_empty_file_and_invalid_constructor_values_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        ChatAttachmentStore(tmp_path / "a", allowed_extensions=[])
    with pytest.raises(ValueError):
        ChatAttachmentStore(tmp_path / "b", max_files=0)

    store = ChatAttachmentStore(tmp_path / "c")
    with pytest.raises(ValueError, match="empty"):
        store.save_files("conversation", [FakeUploadedFile("empty.txt", b"")])


def test_remove_conversation_only_removes_its_own_directory(tmp_path):
    store = ChatAttachmentStore(tmp_path / "attachments")
    first = store.save_files("first", [FakeUploadedFile("first.txt", b"first")])
    second = store.save_files("second", [FakeUploadedFile("second.txt", b"second")])

    store.remove_conversation("first")
    store.remove_conversation("first")

    assert not (tmp_path / "attachments" / "first").exists()
    assert Path(store.resolve_paths(second)[0]).read_bytes() == b"second"
    with pytest.raises(FileNotFoundError):
        store.resolve_paths(first)


def test_remove_files_rolls_back_only_the_new_attachment_batch(tmp_path):
    store = ChatAttachmentStore(tmp_path / "attachments")
    existing = store.save_files(
        "conversation",
        [FakeUploadedFile("existing.txt", b"keep")],
    )
    transient = store.save_files(
        "conversation",
        [FakeUploadedFile("transient.txt", b"remove")],
    )

    store.remove_files(transient)

    assert Path(store.resolve_paths(existing)[0]).read_bytes() == b"keep"
    with pytest.raises(FileNotFoundError):
        store.resolve_paths(transient)


def test_normalize_chat_submission_handles_string_mapping_and_object():
    first = FakeUploadedFile("a.txt", b"a")
    second = FakeUploadedFile("b.md", b"b")

    assert normalize_chat_submission(None) == ("", [])
    assert normalize_chat_submission("  hello  ") == ("hello", [])
    assert normalize_chat_submission({"text": "  inspect ", "files": [first]}) == (
        "inspect",
        [first],
    )
    assert normalize_chat_submission(
        SimpleNamespace(text=" continue ", files=(first, second))
    ) == ("continue", [first, second])

    with pytest.raises(TypeError):
        normalize_chat_submission({"text": 123, "files": []})


def test_recorded_audio_is_separate_from_persistent_attachments():
    audio = FakeUploadedFile("recording.wav", b"RIFF")

    assert chat_submission_audio({"text": "", "files": [], "audio": audio}) is audio
    assert chat_submission_audio(SimpleNamespace(text="", files=[], audio=audio)) is audio
    assert chat_submission_audio("text only") is None
    assert normalize_chat_submission(
        {"text": "", "files": [], "audio": audio}
    ) == ("", [])


def test_select_conversation_attachments_prefers_current_files():
    current = [{"id": "current", "stored_path": "c/current.txt"}]
    old = [{"id": "old", "stored_path": "c/old.txt"}]
    messages = [{"role": "user", "metadata": {"attachments": old}}]

    selected = select_conversation_attachments("普通问题", current, messages)

    assert selected == current
    assert selected is not current


def test_select_conversation_attachments_reuses_only_referenced_recent_files():
    older = [{"id": "older", "stored_path": "c/older.xlsx"}]
    recent = [{"id": "recent", "stored_path": "c/recent.xlsx"}]
    messages = [
        {"role": "user", "metadata": {"attachments": older}},
        {"role": "assistant", "metadata": {"attachments": [{"id": "ignored"}]}},
        {"role": "user", "metadata": {"attachments": recent}},
    ]

    assert select_conversation_attachments("你好", [], messages) == []
    assert select_conversation_attachments("继续分析这份报价", [], messages) == recent
    assert select_conversation_attachments("项目利润怎么样", None, messages) == []
    assert select_conversation_attachments("看看这些文件", None, messages) == recent
    assert select_conversation_attachments("继续", None, messages) == []
