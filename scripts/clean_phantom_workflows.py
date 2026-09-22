#!/usr/bin/env python3
"""清理内置工作流的幻影版本（收缩迁移手册 §1 步骤 3–5）。

背景：checksum 曾把 `version` 也算进哈希，导致「发布新版本 → 版本号变化 →
checksum 变化 → 下次启动仍判定为已变更」的自持循环，每次启动每个内置工作流
+1 行。修复后循环已停止，但历史累积的重复行仍在库里（实测 3 个内置 × 41 版）。

本脚本把这些行收拢为**每个内置工作流一行**，让版本号重新如实反映业务内容。

安全设计：

- 默认 **dry-run**，只打印将要删除的行；`--apply` 才写库。
- 删除前逐项核对手册 §1 的前置条件，任一不满足即中止：
    * 待删行必须全部 `source='builtin'` 且 `read_only=1`；
    * 不得存在用户自建工作流（`source!='builtin'`）；
    * 不得有 `workflow_runs` 引用（引用会被删版本则中止）。
- 同步清理 `workflow_overrides` 的孤儿行。
- 只操作 `-workflows` 指定的库，不碰其他 SQLite 文件。

用法::

    python scripts/clean_phantom_workflows.py                 # 预演
    python scripts/clean_phantom_workflows.py --apply         # 写入
    python scripts/clean_phantom_workflows.py --db <path>     # 指定库
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "conversations.db"


def _stdout_utf8() -> None:
    """Make console output UTF-8 on Windows.

    Uses `reconfigure` rather than wrapping `sys.stdout.buffer`: replacing the
    stream breaks callers that already own it (pytest's capture), and the
    wrapper's close-on-GC surfaces later as "I/O operation on closed file".
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def survey(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Collect every fact the safety checks need, in one read pass."""
    q = conn.execute
    tables = {
        row[0]
        for row in q("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    data: Dict[str, Any] = {"tables": tables}
    if "workflow_definitions" not in tables:
        return data

    data["context"] = [
        dict(row)
        for row in q(
            "SELECT tenant_id, workspace_id, profile_id, source, read_only, "
            "COUNT(*) AS rows FROM workflow_definitions "
            "GROUP BY tenant_id, workspace_id, profile_id, source, read_only"
        ).fetchall()
    ]
    data["per_workflow"] = [
        dict(row)
        for row in q(
            "SELECT workflow_id, COUNT(*) AS rows, MIN(version) AS min_v, "
            "MAX(version) AS max_v FROM workflow_definitions "
            "GROUP BY workflow_id ORDER BY workflow_id"
        ).fetchall()
    ]
    # 幻影行 = 同一 workflow 在版本号 > 1 的重复副本。回滚到「每个内置一行」
    # 而不是「保留最新一版」，因为保留 v41 会让人误以为改过 41 次，
    # 而实际上 41 个版本的内容完全相同（仅 version 字段不同）。
    data["keep"] = [
        dict(row)
        for row in q(
            "SELECT tenant_id, workspace_id, profile_id, workflow_id, MIN(version) AS version "
            "FROM workflow_definitions GROUP BY tenant_id, workspace_id, profile_id, workflow_id"
        ).fetchall()
    ]
    data["to_delete"] = [
        dict(row)
        for row in q(
            "SELECT tenant_id, workspace_id, profile_id, workflow_id, version, source, read_only "
            "FROM workflow_definitions WHERE version > 1 ORDER BY workflow_id, version"
        ).fetchall()
    ]
    if "workflow_runs" in tables:
        data["runs"] = q("SELECT COUNT(*) FROM workflow_runs").fetchone()[0]
        data["runs_at_risk"] = _runs_on_superseded_versions(q)
    if "workflow_overrides" in tables:
        data["overrides"] = q("SELECT COUNT(*) FROM workflow_overrides").fetchone()[0]
        # 注意列名不对称：definitions 用 `version`，overrides 用 `workflow_version`。
        data["orphan_overrides"] = q(
            "SELECT COUNT(*) FROM workflow_overrides o WHERE NOT EXISTS ("
            "  SELECT 1 FROM workflow_definitions d"
            "  WHERE d.tenant_id=o.tenant_id AND d.workspace_id=o.workspace_id"
            "    AND d.profile_id=o.profile_id AND d.workflow_id=o.workflow_id"
            "    AND d.version=o.workflow_version)"
        ).fetchone()[0]
    return data


def _retained_versions(q) -> set:
    """(scope, workflow_id, version) triples that survive the cleanup.

    Must mirror `prune_stale_builtin_rows`: per scope each builtin keeps exactly
    one row — the lowest version whose content matches the builtin in source
    today. Treating every identical-content copy as a survivor would let a run
    that points at a doomed version pass the check as safe, which is precisely
    what the manual forbids.

    When no row matches the current content, the store will republish the
    builtin at its source version, so that version is the one that survives.
    """
    from artpm_agent.workflows.defaults import get_builtin_workflows
    from artpm_agent.workflows.store import WorkflowStore

    expected_checksum: Dict[str, str] = {}
    expected_version: Dict[str, int] = {}
    for definition in get_builtin_workflows():
        expected_checksum[definition.id] = WorkflowStore._definition_checksum(
            definition
        )
        expected_version[definition.id] = definition.version

    rows = q(
        """
        SELECT tenant_id, workspace_id, profile_id, workflow_id, version, checksum
        FROM workflow_definitions WHERE source = 'builtin'
        """
    ).fetchall()

    def _key(row) -> tuple:
        return (
            row["tenant_id"],
            row["workspace_id"],
            row["profile_id"],
            row["workflow_id"],
        )

    survivors: Dict[tuple, int] = {}
    for row in rows:
        if expected_checksum.get(row["workflow_id"]) != row["checksum"]:
            continue
        current = survivors.get(_key(row))
        if current is None or row["version"] < current:
            survivors[_key(row)] = row["version"]

    for row in rows:
        key = _key(row)
        if key in survivors:
            continue
        source_version = expected_version.get(row["workflow_id"])
        if source_version is not None:
            survivors[key] = source_version

    return {
        (key[0], key[1], key[2], key[3], version) for key, version in survivors.items()
    }


def _runs_on_superseded_versions(q) -> int:
    """Count runs pointing at a version that cleanup would delete.

    Runs referencing the retained canonical version are normal and must not
    block the cleanup — counting all runs would make the step impossible to run.
    """
    retained = _retained_versions(q)
    at_risk = 0
    for row in q(
        """
        SELECT tenant_id, workspace_id, profile_id, workflow_id, workflow_version
        FROM workflow_runs
        """
    ).fetchall():
        key = (
            row["tenant_id"],
            row["workspace_id"],
            row["profile_id"],
            row["workflow_id"],
            int(row["workflow_version"]),
        )
        if key not in retained:
            at_risk += 1
    return at_risk


def check_preconditions(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """手册 §1 步骤 3 与步骤 5 的机器化检查。"""
    problems: List[str] = []

    to_delete = data.get("to_delete") or []
    if not to_delete:
        problems.append("没有待清理的重复版本，无需执行（可能是新库或已清理）")

    wrong_source = [row for row in to_delete if row.get("source") != "builtin"]
    if wrong_source:
        problems.append(
            f"{len(wrong_source)} 行待删记录的 source 不是 builtin，"
            "删除会波及用户自建工作流"
        )

    not_read_only = [row for row in to_delete if int(row.get("read_only") or 0) != 1]
    if not_read_only:
        problems.append(f"{len(not_read_only)} 行待删记录 read_only 不为 1")

    custom = [row for row in data.get("context", []) if row.get("source") != "builtin"]
    if custom:
        problems.append(f"库中存在用户自建工作流（{custom}），需人工确认后再执行")

    at_risk = data.get("runs_at_risk")
    if at_risk:
        problems.append(
            f"有 {at_risk} 个 workflow_run 引用了将被删除的版本；"
            "手册 §1 步骤 5 要求此时停止"
        )

    # 每个 workflow 至少保留一行
    keep_keys = {
        (r["tenant_id"], r["workspace_id"], r["profile_id"], r["workflow_id"])
        for r in data.get("keep") or []
    }
    delete_keys = {
        (r["tenant_id"], r["workspace_id"], r["profile_id"], r["workflow_id"])
        for r in to_delete
    }
    lost = delete_keys - keep_keys
    if lost:
        problems.append(f"{len(lost)} 个工作流在删除后会一行不剩：{sorted(lost)}")

    return (not problems), problems


def current_builtin_checksums() -> Dict[str, str]:
    """Semantic checksum of every builtin as it exists in source today."""
    from artpm_agent.workflows.defaults import get_builtin_workflows
    from artpm_agent.workflows.store import WorkflowStore

    checksums: Dict[str, str] = {}
    for definition in get_builtin_workflows():
        checksums[definition.id] = WorkflowStore._definition_checksum(definition)
    return checksums


def prune_stale_builtin_rows(conn: sqlite3.Connection) -> int:
    """Keep exactly one builtin row per scope: the one matching source today.

    After the loop fix, historical rows fall into two kinds:

    * rows whose content equals the current builtin (canonical), and
    * rows carrying superseded content — the phantom duplicates.

    Only the canonical row per scope survives. When no canonical row exists yet
    (the source moved on after the duplicates were written), the caller runs the
    store once to publish it, then calls this again to drop the stale ones.
    """
    expected = current_builtin_checksums()
    removed = 0

    rows = conn.execute(
        """
        SELECT tenant_id, workspace_id, profile_id, workflow_id, version, checksum
        FROM workflow_definitions
        WHERE source = 'builtin'
        ORDER BY workflow_id, version
        """
    ).fetchall()

    keep: Dict[tuple, int] = {}
    for row in rows:
        wanted = expected.get(row["workflow_id"])
        if wanted is None or row["checksum"] != wanted:
            continue
        key = (
            row["tenant_id"],
            row["workspace_id"],
            row["profile_id"],
            row["workflow_id"],
        )
        previous = keep.get(key)
        if previous is None or row["version"] < previous:
            keep[key] = row["version"]

    for row in rows:
        key = (
            row["tenant_id"],
            row["workspace_id"],
            row["profile_id"],
            row["workflow_id"],
        )
        if keep.get(key) == row["version"]:
            continue
        cursor = conn.execute(
            """
            DELETE FROM workflow_definitions
            WHERE tenant_id = ? AND workspace_id = ? AND profile_id = ?
              AND workflow_id = ? AND version = ? AND source = 'builtin'
              AND read_only = 1
            """,
            (
                row["tenant_id"],
                row["workspace_id"],
                row["profile_id"],
                row["workflow_id"],
                row["version"],
            ),
        )
        removed += cursor.rowcount

    return removed


def settle_builtins(path: Path) -> Dict[str, int]:
    """Collapse builtin rows to one canonical row per scope.

    Two phases, because a builtin's source can change after the duplicates were
    written (measured here: the stored v1 predates the retry fields on steps):

    1. drop every row that is not the canonical current content;
    2. open the store once so it publishes the current content as one row —
       a no-op when the canonical row survived phase 1.

    Afterwards further startups change nothing, which the caller verifies.
    """
    from artpm_agent.workflows.store import WorkflowStore

    conn = _connect(path, read_only=False)
    try:
        pruned = prune_stale_builtin_rows(conn)
        conn.commit()
    finally:
        conn.close()

    WorkflowStore(path)

    conn = _connect(path, read_only=False)
    try:
        pruned += prune_stale_builtin_rows(conn)
        conn.commit()
    finally:
        conn.close()

    return {"pruned": pruned}


def apply_cleanup(
    path: Path, conn: sqlite3.Connection, data: Dict[str, Any]
) -> Dict[str, int]:
    """Delete duplicates, settle builtins to one row each, drop orphan overrides."""
    to_delete = data.get("to_delete") or []
    deleted_defs = 0
    deleted_overrides = 0

    with conn:
        for row in to_delete:
            cursor = conn.execute(
                """
                DELETE FROM workflow_definitions
                WHERE tenant_id = ? AND workspace_id = ? AND profile_id = ?
                  AND workflow_id = ? AND version = ? AND source = 'builtin'
                  AND read_only = 1
                """,
                (
                    row["tenant_id"],
                    row["workspace_id"],
                    row["profile_id"],
                    row["workflow_id"],
                    row["version"],
                ),
            )
            deleted_defs += cursor.rowcount

        if "workflow_overrides" in data.get("tables", set()):
            cursor = conn.execute(
                """
                DELETE FROM workflow_overrides
                WHERE NOT EXISTS (
                    SELECT 1 FROM workflow_definitions d
                    WHERE d.tenant_id = workflow_overrides.tenant_id
                      AND d.workspace_id = workflow_overrides.workspace_id
                      AND d.profile_id = workflow_overrides.profile_id
                      AND d.workflow_id = workflow_overrides.workflow_id
                      AND d.version = workflow_overrides.workflow_version
                )
                """
            )
            deleted_overrides += cursor.rowcount

    settled = settle_builtins(path)

    # overrides 可能因上一段收敛而出现新的孤儿
    conn = _connect(path, read_only=False)
    try:
        with conn:
            cursor = conn.execute(
                """
                DELETE FROM workflow_overrides
                WHERE NOT EXISTS (
                    SELECT 1 FROM workflow_definitions d
                    WHERE d.tenant_id = workflow_overrides.tenant_id
                      AND d.workspace_id = workflow_overrides.workspace_id
                      AND d.profile_id = workflow_overrides.profile_id
                      AND d.workflow_id = workflow_overrides.workflow_id
                      AND d.version = workflow_overrides.workflow_version
                )
                """
            )
        deleted_overrides += cursor.rowcount
    finally:
        conn.close()

    return {
        "definitions": deleted_defs,
        "overrides": deleted_overrides,
        "pruned": settled["pruned"],
    }


def main(argv: Optional[List[str]] = None) -> int:
    _stdout_utf8()
    parser = argparse.ArgumentParser(description="清理内置工作流幻影版本")
    parser.add_argument("--apply", action="store_true", help="实际写入，默认仅预演")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="工作流库路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    path = Path(args.db)
    if not path.is_file():
        print(f"[clean] 库不存在: {path}")
        return 1

    conn = _connect(path, read_only=True)
    try:
        data = survey(conn)
    finally:
        conn.close()

    if "workflow_definitions" not in data.get("tables", set()):
        print(f"[clean] {path} 中没有 workflow_definitions 表，无需处理。")
        return 0

    ok, problems = check_preconditions(data)

    if args.json:
        print(
            json.dumps(
                {
                    "db": str(path),
                    "per_workflow": data.get("per_workflow"),
                    "to_delete": len(data.get("to_delete") or []),
                    "orphan_overrides": data.get("orphan_overrides"),
                    "runs": data.get("runs"),
                    "ok": ok,
                    "problems": problems,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"[clean] 库: {path}")
        for row in data.get("per_workflow", []):
            print(
                f"  {row['workflow_id']}: {row['rows']} 行"
                f"（version {row['min_v']}–{row['max_v']}）"
            )
        print(
            f"[clean] 待删 {len(data.get('to_delete') or [])} 行，"
            f"孤儿 override {data.get('orphan_overrides', 0)} 行，"
            f"workflow_runs {data.get('runs', 0)} 行"
        )

    if not ok:
        print("[clean] 前置检查未通过，未做任何修改：")
        for item in problems:
            print(f"  ! {item}")
        return 1

    if not args.apply:
        print("[clean] 预演结束，未写入。加 --apply 执行。")
        return 0

    conn = _connect(path, read_only=False)
    try:
        result = apply_cleanup(path, conn, data)
    finally:
        conn.close()

    print(
        f"[clean] 已删除 {result['definitions']} 行重复定义、"
        f"{result['pruned']} 行陈旧副本、"
        f"{result['overrides']} 行孤儿 override。"
    )

    conn = _connect(path, read_only=True)
    try:
        after = survey(conn)
    finally:
        conn.close()
    for row in after.get("per_workflow", []):
        print(f"  {row['workflow_id']}: {row['rows']} 行（version {row['max_v']}）")

    # 清理后必须让启动变成空操作：再开一次 store，行数不得变化。
    from artpm_agent.workflows.store import WorkflowStore

    WorkflowStore(path)
    conn = _connect(path, read_only=True)
    try:
        settled = survey(conn)
    finally:
        conn.close()

    total_after = sum(row["rows"] for row in after.get("per_workflow", []))
    total_settled = sum(row["rows"] for row in settled.get("per_workflow", []))
    if total_settled != total_after:
        print(
            f"[clean] 警告：重新打开后行数从 {total_after} 变成 {total_settled}，"
            "说明版本仍会自增。请检查 checksum 逻辑。"
        )
        return 1
    print(f"[clean] 重新打开库后行数稳定在 {total_settled}，版本不再自增。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
