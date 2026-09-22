"""R-0 回归：内置工作流版本自增死循环。

症状（PRD §10 R-0，收缩迁移手册 §1）：每次启动 `workflow_definitions` +3 行，
实测 2026-09-19 单日从 111 涨到 123。

根因：checksum 把 `version`（以及 `tenant_id` / `workspace_id` / `profile_id`）
一起算进哈希。于是「内容变了 → 发布新版本 → 版本号变了 → checksum 又变了 →
下次启动仍判定为『已变更』」，自持循环。

这些断言全部只看**行数是否稳定**，不看打印或日志。
"""

from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path

import pytest

from artpm_agent.workflows.store import WorkflowStore
from artpm_agent.workflows.store_codec import (
    CHECKSUM_EXCLUDED_FIELDS,
    definition_checksum,
    definition_json,
)


def _definition_rows(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM workflow_definitions").fetchone()[0]


def _rows_per_workflow(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            "SELECT workflow_id, COUNT(*) FROM workflow_definitions GROUP BY workflow_id"
        ).fetchall()
    return dict(rows)


def _builtin_count() -> int:
    from artpm_agent.workflows.defaults import get_builtin_workflows

    return len(list(get_builtin_workflows()))


# ── 核心断言：重复启动不再增长 ─────────────────────────────────


def test_ensure_builtins_twice_does_not_add_rows(tmp_path):
    """手册 §1 步骤 2 指定的回归断言。"""
    path = tmp_path / "conversations.db"
    store = WorkflowStore(path)

    first = _definition_rows(path)
    assert first > 0

    store.ensure_builtins()
    second = _definition_rows(path)

    store.ensure_builtins()
    third = _definition_rows(path)

    assert second == first, f"第二次 ensure_builtins 新增了 {second - first} 行"
    assert third == first, f"第三次 ensure_builtins 新增了 {third - first} 行"


def test_reopening_store_does_not_add_rows(tmp_path):
    """真实症状是「每次启动 +3」——启动即构造 WorkflowStore。"""
    path = tmp_path / "conversations.db"

    WorkflowStore(path)
    baseline = _definition_rows(path)

    for _ in range(5):
        WorkflowStore(path)

    assert _definition_rows(path) == baseline


def test_repeated_startups_do_not_accumulate_per_workflow(tmp_path):
    """按 workflow_id 分别核对，能区分「整体不涨」和「互相抵消」。"""
    path = tmp_path / "conversations.db"

    WorkflowStore(path)
    baseline = _rows_per_workflow(path)
    assert set(baseline) and all(count >= 1 for count in baseline.values())

    for _ in range(4):
        WorkflowStore(path)

    after = _rows_per_workflow(path)
    assert after == baseline


# ── checksum 语义：只随业务内容变化 ────────────────────────────


def test_checksum_is_independent_of_version():
    """把 version 改掉不得改变 checksum，否则循环必然重现。"""
    from artpm_agent.workflows.defaults import get_builtin_workflows

    definition = next(iter(get_builtin_workflows()))

    original = definition_checksum(definition, max_bytes=256 * 1024)
    bumped = definition.model_copy(update={"version": definition.version + 17})
    assert definition_checksum(bumped, max_bytes=256 * 1024) == original


def test_checksum_is_independent_of_scope_fields():
    from artpm_agent.workflows.defaults import get_builtin_workflows

    definition = next(iter(get_builtin_workflows()))
    original = definition_checksum(definition, max_bytes=256 * 1024)

    moved = definition.model_copy(
        update={
            "tenant_id": "another-tenant",
            "workspace_id": "another-workspace",
            "profile_id": "another-profile",
        }
    )

    assert definition_checksum(moved, max_bytes=256 * 1024) == original


def test_checksum_changes_when_business_content_changes():
    """排除项不能排除过头——内容真变了必须体现出来。"""
    from artpm_agent.workflows.defaults import get_builtin_workflows

    definition = next(iter(get_builtin_workflows()))
    original = definition_checksum(definition, max_bytes=256 * 1024)

    renamed = definition.model_copy(update={"name": definition.name + "（改）"})
    assert definition_checksum(renamed, max_bytes=256 * 1024) != original


def test_excluded_fields_are_exactly_the_documented_four():
    """排除清单是契约的一部分；多排一个就可能漏掉真实变更。"""
    assert set(CHECKSUM_EXCLUDED_FIELDS) == {
        "version",
        "tenant_id",
        "workspace_id",
        "profile_id",
    }


def test_stored_json_still_contains_version_and_scope(tmp_path):
    """只有哈希被收窄；落库的 definition_json 必须保留完整字段。"""
    path = tmp_path / "conversations.db"
    WorkflowStore(path)

    with closing(sqlite3.connect(path)) as conn:
        raw = conn.execute(
            "SELECT definition_json FROM workflow_definitions LIMIT 1"
        ).fetchone()[0]

    import json

    payload = json.loads(raw)
    for field in ("version", "workspace_id", "profile_id"):
        assert field in payload, f"落库 JSON 丢了 {field}"


def test_definition_json_still_hashes_full_payload_limits(tmp_path):
    """哈希收窄后，存储体积上限仍必须作用于完整 JSON。"""
    from artpm_agent.workflows.defaults import get_builtin_workflows

    definition = next(iter(get_builtin_workflows()))
    full = definition_json(definition, max_bytes=256 * 1024)

    with pytest.raises(ValueError, match="exceeds"):
        definition_json(definition, max_bytes=16)

    assert "version" in full
