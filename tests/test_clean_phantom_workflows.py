"""幻影工作流版本清理脚本的测试。

用**真实的内置定义源**构造库，因此「收敛到每内置一行」这条断言同时验证了：
清理逻辑与 `definitions` 源内容一致，而不是把行删到刚好剩下某个旧快照。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "clean_phantom_workflows.py"

SPEC = importlib.util.spec_from_file_location("clean_phantom_workflows", SCRIPT)
assert SPEC and SPEC.loader
cleaner = importlib.util.module_from_spec(SPEC)
sys.modules["clean_phantom_workflows"] = cleaner
SPEC.loader.exec_module(cleaner)

from artpm_agent.workflows.defaults import get_builtin_workflows  # noqa: E402
from artpm_agent.workflows.store import WorkflowStore  # noqa: E402


BUILTIN_IDS = [definition.id for definition in get_builtin_workflows()]


def _make_phantom_db(path: Path, extra_versions: int = 3) -> Path:
    """Build a DB shaped like the bug's aftermath: N duplicate rows per builtin."""
    WorkflowStore(path)
    with sqlite3.connect(path) as conn:
        for definition in get_builtin_workflows():
            for offset in range(1, extra_versions + 1):
                conn.execute(
                    """
                    INSERT INTO workflow_definitions(
                        tenant_id, workspace_id, profile_id, workflow_id, version,
                        source, read_only, definition_json, checksum, created_at
                    )
                    SELECT tenant_id, workspace_id, profile_id, workflow_id, ?,
                           source, read_only, definition_json, checksum, created_at
                    FROM workflow_definitions
                    WHERE workflow_id = ? ORDER BY version LIMIT 1
                    """,
                    (definition.version + offset, definition.id),
                )
            conn.execute(
                """
                INSERT INTO workflow_overrides(
                    tenant_id, workspace_id, profile_id, workflow_id,
                    workflow_version, enabled, priority, created_at, updated_at
                )
                SELECT tenant_id, workspace_id, profile_id, workflow_id, ?,
                       1, 5, created_at, updated_at
                FROM workflow_overrides
                WHERE workflow_id = ? ORDER BY workflow_version LIMIT 1
                """,
                (definition.version + extra_versions, definition.id),
            )
    return path


def _counts(path: Path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        defs = conn.execute("SELECT COUNT(*) FROM workflow_definitions").fetchone()[0]
        overrides = conn.execute("SELECT COUNT(*) FROM workflow_overrides").fetchone()[
            0
        ]
        per = dict(
            conn.execute(
                "SELECT workflow_id, COUNT(*) FROM workflow_definitions GROUP BY workflow_id"
            ).fetchall()
        )
    return defs, overrides, per


# ── 前置检查 ──────────────────────────────────────────────────


def test_dry_run_leaves_database_untouched(tmp_path):
    db = _make_phantom_db(tmp_path / "conversations.db")
    before = _counts(db)

    code = cleaner.main(["--db", str(db)])

    assert code == 0
    assert _counts(db) == before


def test_apply_collapses_to_one_row_per_builtin(tmp_path):
    db = _make_phantom_db(tmp_path / "conversations.db")
    assert _counts(db)[0] > len(BUILTIN_IDS)

    code = cleaner.main(["--db", str(db), "--apply"])

    assert code == 0
    defs, _, per = _counts(db)
    assert defs == len(BUILTIN_IDS)
    assert set(per) == set(BUILTIN_IDS)
    assert all(count == 1 for count in per.values())


def test_settled_database_survives_repeated_startups(tmp_path):
    """清理的验收判据：之后每次启动都不再新增行。"""
    db = _make_phantom_db(tmp_path / "conversations.db")
    cleaner.main(["--db", str(db), "--apply"])

    baseline = _counts(db)[0]
    for _ in range(4):
        WorkflowStore(db)

    assert _counts(db)[0] == baseline


def test_orphan_overrides_are_removed(tmp_path):
    db = _make_phantom_db(tmp_path / "conversations.db")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO workflow_overrides(tenant_id, workspace_id, profile_id,"
            " workflow_id, workflow_version, enabled, priority, created_at, updated_at)"
            " VALUES('local','local-default','local-default','quote_assessment',999,1,5,'x','x')"
        )

    cleaner.main(["--db", str(db), "--apply"])

    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        orphans = conn.execute(
            "SELECT COUNT(*) FROM workflow_overrides o WHERE NOT EXISTS ("
            " SELECT 1 FROM workflow_definitions d"
            " WHERE d.tenant_id=o.tenant_id AND d.workspace_id=o.workspace_id"
            "   AND d.profile_id=o.profile_id AND d.workflow_id=o.workflow_id"
            "   AND d.version=o.workflow_version)"
        ).fetchone()[0]
    assert orphans == 0


def test_custom_workflow_blocks_cleanup(tmp_path):
    """存在用户自建工作流时必须中止——盲删会波及用户内容。"""
    db = _make_phantom_db(tmp_path / "conversations.db")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO workflow_definitions(tenant_id, workspace_id, profile_id,"
            " workflow_id, version, source, read_only, definition_json, checksum, created_at)"
            " SELECT tenant_id, workspace_id, profile_id, 'custom_x', 1, 'custom', 0,"
            " definition_json, 'deadbeef', created_at FROM workflow_definitions LIMIT 1"
        )

    before = _counts(db)
    code = cleaner.main(["--db", str(db), "--apply"])

    assert code == 1
    assert _counts(db) == before


def _insert_run(path: Path, run_id: str = "r1", workflow_version: int = 2) -> None:
    """Insert a run row shaped like the store's own insert.

    Column list copied from `store.create_run` so it cannot drift from the real
    schema. `workflow_version` defaults to a **superseded** version: a run on
    the retained canonical version is normal traffic and must not block cleanup,
    so the blocker fixture has to point at a version that would be deleted.
    """
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO workflow_runs(id, idempotency_key, workflow_id,"
            " workflow_version, tenant_id, workspace_id, profile_id,"
            " conversation_id, turn_id, status, state_version, current_step,"
            " definition_snapshot_json, input_json, context_json, outputs_json,"
            " error, created_at, updated_at)"
            " VALUES(?,?,'quote_assessment',?,'local','local-default','local-default',"
            " 'conv-test',NULL,'pending',0,0,'{}','{}','{}','{}',NULL,'now','now')",
            (run_id, f"key-{run_id}", workflow_version),
        )


def test_run_on_retained_version_does_not_block_cleanup(tmp_path):
    """引用保留版本的 run 是正常流量，不该让清理永远无法执行。"""
    db = _make_phantom_db(tmp_path / "conversations.db")
    _insert_run(db, "r-retained", workflow_version=1)

    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        data = cleaner.survey(conn)

    assert data["runs"] == 1
    assert data["runs_at_risk"] == 0


def test_workflow_runs_block_cleanup(tmp_path):
    """手册 §1 步骤 5：有 run 引用**将被删除的**版本时必须停止。"""
    db = _make_phantom_db(tmp_path / "conversations.db")
    _insert_run(db, workflow_version=2)

    before = _counts(db)
    code = cleaner.main(["--db", str(db), "--apply"])

    assert code == 1
    assert _counts(db) == before


def test_check_preconditions_reports_missing_duplicates(tmp_path):
    db = tmp_path / "conversations.db"
    WorkflowStore(db)

    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        data = cleaner.survey(conn)

    ok, problems = cleaner.check_preconditions(data)

    assert ok is False
    assert any("没有待清理" in item for item in problems)


def test_missing_database_is_reported_not_crashed(tmp_path):
    code = cleaner.main(["--db", str(tmp_path / "nope.db")])
    assert code == 1


def test_database_without_workflow_tables_is_a_no_op(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()

    code = cleaner.main(["--db", str(db)])

    assert code == 0


def test_survey_counts_duplicates_per_workflow(tmp_path):
    db = _make_phantom_db(tmp_path / "conversations.db", extra_versions=2)

    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        data = cleaner.survey(conn)

    assert len(data["to_delete"]) == 2 * len(BUILTIN_IDS)
    assert {row["workflow_id"] for row in data["per_workflow"]} == set(BUILTIN_IDS)


def test_cli_exit_codes_are_nonzero_on_blockers(tmp_path):
    """端到端：子进程返回码必须能当门禁信号用。"""
    db = _make_phantom_db(tmp_path / "conversations.db")
    _insert_run(db, "r2")

    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--apply"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert process.returncode == 1
    assert "前置检查未通过" in process.stdout
