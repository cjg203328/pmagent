"""Tests for Phase 2 ConsolidationService and its schema migration."""

import sqlite3
from pathlib import Path

import pytest

from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore
from artpm_agent.memory.consolidation import (
    ConsolidationReport,
    ConsolidationScheduler,
    ConsolidationService,
)


def _new_store(tmp_path: Path) -> WorkspaceKnowledgeStore:
    return WorkspaceKnowledgeStore(
        tmp_path / "kb.db", enable_vector_search=False
    )


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def test_schema_migration_adds_consolidation_columns(tmp_path: Path):
    store = _new_store(tmp_path)
    with store._connect() as conn:
        res_cols = _columns(conn, "knowledge_resources")
        ver_cols = _columns(conn, "knowledge_versions")
    assert {"confidence", "consolidation_status", "supersedes", "last_hit"} <= res_cols
    assert {"source_episode", "confidence"} <= ver_cols
    # 旧调用仍可正常入库
    r = store.ingest_resource(
        title="旧调用兼容", searchable_text="一段普通知识文本", resource_type="document"
    )
    assert r["id"]
    assert r["confidence"] == 1.0
    assert r["consolidation_status"] == "active"


def test_dedup_marks_duplicates_superseded(tmp_path: Path):
    store = _new_store(tmp_path)
    store.ingest_resource(
        title="知识A", searchable_text="客户要求周五前交付全部素材", resource_type="lesson"
    )
    store.ingest_resource(
        title="知识A副本", searchable_text="客户要求周五前交付全部素材", resource_type="lesson"
    )
    svc = ConsolidationService(store)
    report = svc.consolidate(dry_run=False)
    assert report.duplicates_merged == 1
    assert report.superseded == 1
    superseded = list(
        store.iter_active_resources(consolidation_status="superseded")
    )
    assert len(superseded) == 1
    assert superseded[0]["supersedes"] is None or superseded[0]["supersedes"] != ""


def test_decay_reduces_confidence_for_stale_resources(tmp_path: Path):
    store = _new_store(tmp_path)
    store.ingest_resource(
        title="冷知识", searchable_text="很久没人用到的知识点", resource_type="lesson"
    )
    svc = ConsolidationService(store, decay_factor=0.9, confidence_floor=0.2)
    report = svc.consolidate(dry_run=False)
    assert report.decayed >= 1
    assert report.confidence_updated >= 1
    res = next(iter(store.iter_active_resources()))
    assert res["confidence"] == pytest.approx(0.9, abs=1e-6)


def test_reinforce_raises_confidence_for_recent_hits(tmp_path: Path):
    store = _new_store(tmp_path)
    rid = store.ingest_resource(
        title="热知识", searchable_text="经常被用到的知识点", resource_type="lesson"
    )["id"]
    store.set_confidence(rid, 0.7)
    store.record_hit(rid)  # 刚刚命中
    svc = ConsolidationService(store, reinforce_step=0.1)
    report = svc.consolidate(dry_run=False)
    assert report.reinforced >= 1
    res = next(iter(store.iter_active_resources()))
    assert res["confidence"] == pytest.approx(0.8, abs=1e-6)


def test_mutation_accessors(tmp_path: Path):
    store = _new_store(tmp_path)
    rid = store.ingest_resource(
        title="M", searchable_text="mutation test", resource_type="lesson"
    )["id"]
    assert store.record_hit(rid) is True
    assert store.set_confidence(rid, 0.5) is True
    assert store.mark_consolidation_status(rid, "conflict") is True
    assert store.set_supersedes(rid, "other-id") is True
    assert store.set_source_episode(rid, "ep-1") is True
    res = next(iter(store.iter_active_resources()))
    assert res["confidence"] == 0.5
    assert res["consolidation_status"] == "conflict"
    assert res["supersedes"] == "other-id"
    assert res["source_episode"] == "ep-1"


def test_scheduler_throttles_runs(tmp_path: Path):
    store = _new_store(tmp_path)
    store.ingest_resource(
        title="S", searchable_text="scheduler test", resource_type="lesson"
    )
    svc = ConsolidationService(store)
    sched = ConsolidationScheduler(tmp_path / "consolidation.db")
    ws = store.DEFAULT_WORKSPACE_ID
    assert sched.should_run(workspace_id=ws) is True
    report = ConsolidationReport(resources_scanned=1)
    sched.mark_run(ws, report)
    assert sched.should_run(workspace_id=ws, interval_minutes=60 * 12) is False
    assert sched.last_run(ws) is not None


def test_dry_run_does_not_write(tmp_path: Path):
    store = _new_store(tmp_path)
    store.ingest_resource(
        title="D", searchable_text="完全相同的文本", resource_type="lesson"
    )
    store.ingest_resource(
        title="D2", searchable_text="完全相同的文本", resource_type="lesson"
    )
    svc = ConsolidationService(store)
    report = svc.consolidate(dry_run=True)
    assert report.duplicates_merged == 1  # 统计仍发生
    superseded = list(
        store.iter_active_resources(consolidation_status="superseded")
    )
    assert superseded == []  # 但不写回
