"""S-2b 知识 owner 维度：同 workspace 内 A 的 private 不得出现在 B 的检索结果。

对应 [`docs/operations/收缩迁移手册.md`](../docs/operations/收缩迁移手册.md) §2b 第 5 步，
以及 PRD §12.2「前期个人、后续团队」。

这一维度的意义只在**读取路径真的过滤**时才成立，所以断言全部落在 `search()` 上，
而不是「列存在」这种形式检查。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore


@pytest.fixture()
def store(tmp_path: Path) -> WorkspaceKnowledgeStore:
    return WorkspaceKnowledgeStore(tmp_path / "knowledge.db")


def _ingest(store, *, title, text, source_id, visibility="workspace", owner=None):
    return store.ingest_resource(
        title=title,
        searchable_text=text,
        resource_type="note",
        source_type="manual",
        source_id=source_id,
        visibility=visibility,
        owner_principal_id=owner,
    )


# ── 迁移与默认值 ──────────────────────────────────────────────


def test_migration_adds_owner_columns(store, tmp_path):
    with closing(sqlite3.connect(tmp_path / "knowledge.db")) as conn:
        for table in ("knowledge_resources", "knowledge_rules"):
            columns = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert "owner_principal_id" in columns
            assert "visibility" in columns


def test_existing_rows_default_to_workspace_shared(store, tmp_path):
    """旧行必须保持原语义（共享），否则升级即改变可见范围。"""
    _ingest(store, title="老资料", text="升级前就存在的资料", source_id="legacy")

    with closing(sqlite3.connect(tmp_path / "knowledge.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT visibility, owner_principal_id FROM knowledge_resources"
        ).fetchone()

    assert row["visibility"] == "workspace"
    assert row["owner_principal_id"] is None


def test_private_ingest_stores_owner(store):
    record = _ingest(
        store,
        title="个人笔记",
        text="私有内容 zebra",
        source_id="priv-1",
        visibility="private",
        owner="user-a",
    )

    assert record["visibility"] == "private"
    assert record["owner_principal_id"] == "user-a"


def test_workspace_ingest_drops_owner(store):
    """共享行的 owner 无意义，留着会让人误以为存在个人归属。"""
    record = _ingest(
        store,
        title="共享资料",
        text="共享内容 zebra",
        source_id="shared-1",
        visibility="workspace",
        owner="user-a",
    )

    assert record["visibility"] == "workspace"
    assert record["owner_principal_id"] is None


# ── 隔离（核心判据） ─────────────────────────────────────────


def test_private_resource_is_invisible_to_other_principal(store):
    """手册 §2b 第 5 步：A 的 private 不得出现在 B 的检索结果中。"""
    _ingest(
        store,
        title="A 的个人笔记",
        text="私有内容 zebra",
        source_id="priv-1",
        visibility="private",
        owner="user-a",
    )

    assert [item["title"] for item in store.search("zebra", principal_id="user-a")] == [
        "A 的个人笔记"
    ]
    assert store.search("zebra", principal_id="user-b") == []


def test_private_resource_is_invisible_to_anonymous_caller(store):
    """没有身份就不该看到别人的私有内容。"""
    _ingest(
        store,
        title="A 的个人笔记",
        text="私有内容 zebra",
        source_id="priv-1",
        visibility="private",
        owner="user-a",
    )

    assert store.search("zebra") == []


def test_workspace_resource_is_visible_to_everyone(store):
    _ingest(store, title="共享资料", text="共享内容 zebra", source_id="shared-1")

    for principal in ("user-a", "user-b", None):
        titles = [
            item["title"] for item in store.search("zebra", principal_id=principal)
        ]
        assert titles == ["共享资料"], principal


def test_both_visibilities_returned_to_owner(store):
    """本人应同时看到自己的私有内容与共享内容。"""
    _ingest(
        store,
        title="A 的个人笔记",
        text="zebra 私有",
        source_id="priv-1",
        visibility="private",
        owner="user-a",
    )
    _ingest(store, title="共享资料", text="zebra 共享", source_id="shared-1")

    titles = sorted(
        item["title"] for item in store.search("zebra", principal_id="user-a")
    )
    assert titles == ["A 的个人笔记", "共享资料"]


def test_two_principals_do_not_see_each_other(store):
    _ingest(
        store,
        title="A 的笔记",
        text="zebra A",
        source_id="priv-a",
        visibility="private",
        owner="user-a",
    )
    _ingest(
        store,
        title="B 的笔记",
        text="zebra B",
        source_id="priv-b",
        visibility="private",
        owner="user-b",
    )

    assert [item["title"] for item in store.search("zebra", principal_id="user-a")] == [
        "A 的笔记"
    ]
    assert [item["title"] for item in store.search("zebra", principal_id="user-b")] == [
        "B 的笔记"
    ]


def test_private_rule_is_invisible_to_other_principal(store):
    """规则与资料同属知识，隔离要求一致。"""
    rule = store.propose_rule(
        "私有规则 zebra 只有 A 可见",
        visibility="private",
        owner_principal_id="user-a",
    )
    store.confirm_rule(
        rule["id"],
        confirmed_by="user-a",
        confirmation_token="token-a",
    )

    assert store.search("zebra", principal_id="user-a")
    assert store.search("zebra", principal_id="user-b") == []
    assert store.search("zebra") == []


def test_consolidation_scan_respects_owner(store):
    """扫描若不过滤，私有内容会被卷进共享的合并提案。"""
    _ingest(
        store,
        title="A 的个人笔记",
        text="私有内容 zebra",
        source_id="priv-1",
        visibility="private",
        owner="user-a",
    )

    seen_by_a = [
        item["title"] for item in store.iter_active_resources(principal_id="user-a")
    ]
    seen_by_b = [
        item["title"] for item in store.iter_active_resources(principal_id="user-b")
    ]
    seen_anon = [item["title"] for item in store.iter_active_resources()]

    assert seen_by_a == ["A 的个人笔记"]
    assert seen_by_b == []
    assert seen_anon == []


def test_iter_active_resources_still_returns_shared(store):
    _ingest(store, title="共享资料", text="共享内容", source_id="shared-1")

    for principal in ("user-a", None):
        titles = [
            item["title"]
            for item in store.iter_active_resources(principal_id=principal)
        ]
        assert titles == ["共享资料"], principal


# ── 写入校验 ──────────────────────────────────────────────────


def test_private_requires_owner(store):
    """private 无 owner 会让内容对所有人（含作者）不可见，必须拒绝。"""
    with pytest.raises(ValueError, match="owner_principal_id"):
        _ingest(
            store,
            title="无主私有",
            text="内容",
            source_id="priv-x",
            visibility="private",
        )


def test_unknown_visibility_is_rejected(store):
    with pytest.raises(ValueError, match="visibility"):
        _ingest(
            store,
            title="奇怪的可见性",
            text="内容",
            source_id="priv-y",
            visibility="大家都看",
            owner="user-a",
        )


def test_private_rule_requires_owner(store):
    with pytest.raises(ValueError, match="owner_principal_id"):
        store.propose_rule("无主私有规则", visibility="private")


def test_versioning_cannot_silently_change_visibility(store):
    """可见范围变化必须显式发生，不能随版本写入顺带改掉。"""
    first = _ingest(
        store,
        title="A 的笔记",
        text="第一版内容",
        source_id="priv-1",
        visibility="private",
        owner="user-a",
    )

    with pytest.raises(ValueError, match="cannot change across versions"):
        store.ingest_resource(
            title="A 的笔记",
            searchable_text="第二版内容",
            resource_type="note",
            source_type="manual",
            resource_id=first["id"],
            visibility="workspace",
        )


def test_same_visibility_versioning_still_works(store):
    """正常追加版本不受上述限制影响。"""
    first = _ingest(
        store,
        title="A 的笔记",
        text="第一版内容",
        source_id="priv-1",
        visibility="private",
        owner="user-a",
    )

    second = store.ingest_resource(
        title="A 的笔记",
        searchable_text="第二版内容",
        resource_type="note",
        source_type="manual",
        resource_id=first["id"],
        visibility="private",
        owner_principal_id="user-a",
    )

    assert second["current_version"] == 2
    assert second["visibility"] == "private"
    assert [
        item["title"] for item in store.search("第二版", principal_id="user-a")
    ] == ["A 的笔记"]
    assert store.search("第二版", principal_id="user-b") == []
