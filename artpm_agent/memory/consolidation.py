"""Phase 2 — 知识炼化（Knowledge Consolidation）.

对 ``WorkspaceKnowledgeStore`` 中的知识做增量维护：语义去重、矛盾标注、
版本化（supersedes 关联）、置信度衰减/强化。所有写回都走 store 既有的
公共写入接口（record_hit / set_confidence / mark_consolidation_status /
set_supersedes），不触碰知识库既有查询与编排逻辑。

设计来源：MEMORY_EVOLUTION_DESIGN.md · Phase 2（修 G4：知识库只增不炼）。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set
from urllib.parse import urlparse

from .workspace_knowledge_store import WorkspaceKnowledgeStore

_DEFAULT_CONSOLIDATION_DB = "data/consolidation.db"

# 极性标记：用于矛盾检测的粗粒度信号
_POSITIVE_MARKERS = (
    "采用", "推荐", "应当", "建议", "正确", "有效", "优先", "需要", "必须",
    "应", "要", "可以", "适合", "最佳实践",
)
_NEGATIVE_MARKERS = (
    "避免", "不要", "禁止", "错误", "无效", "不宜", "不应", "不可", "禁用",
    "反对", "缺陷", "问题", "风险", "切勿", "禁止",
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _normalize_text(text: str) -> str:
    text = (text or "").casefold()
    text = re.sub(r"\s+", "", text)
    return text


def _tokens(text: str) -> Set[str]:
    return set(_TOKEN_RE.findall(text or ""))


def _topic_key(meta: Dict[str, Any]) -> Optional[str]:
    """从 metadata 抽取一个可比较的「主题键」（如 client / category）。"""
    for key in ("client", "customer", "category", "project", "scope"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return f"{key}:{value.strip().casefold()}"
    return None


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class ConsolidationReport:
    """一次炼化运行的产出统计，便于审计与可观测。"""

    resources_scanned: int = 0
    duplicates_merged: int = 0
    superseded: int = 0
    contradictions_flagged: int = 0
    decayed: int = 0
    reinforced: int = 0
    confidence_updated: int = 0
    conflicts: List[Dict[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "resources_scanned": self.resources_scanned,
            "duplicates_merged": self.duplicates_merged,
            "superseded": self.superseded,
            "contradictions_flagged": self.contradictions_flagged,
            "decayed": self.decayed,
            "reinforced": self.reinforced,
            "confidence_updated": self.confidence_updated,
            "conflicts": self.conflicts,
            "notes": self.notes,
        }


class ConsolidationService:
    """周期性/触发式地对知识库做去重、矛盾标注、版本化与衰减。"""

    def __init__(
        self,
        knowledge_store: WorkspaceKnowledgeStore,
        *,
        decay_days: int = 90,
        reinforce_days: int = 30,
        decay_factor: float = 0.9,
        reinforce_step: float = 0.05,
        confidence_floor: float = 0.2,
        normalize_dedup: bool = True,
        detect_contradictions: bool = True,
        contradiction_jaccard: float = 0.6,
    ):
        self.store = knowledge_store
        self.decay_days = decay_days
        self.reinforce_days = reinforce_days
        self.decay_factor = decay_factor
        self.reinforce_step = reinforce_step
        self.confidence_floor = confidence_floor
        self.normalize_dedup = normalize_dedup
        self.detect_contradictions = detect_contradictions
        self.contradiction_jaccard = contradiction_jaccard

    # ---- 主入口 ----------------------------------------------------------

    def consolidate(
        self,
        workspace_id: str = WorkspaceKnowledgeStore.DEFAULT_WORKSPACE_ID,
        *,
        dry_run: bool = False,
    ) -> ConsolidationReport:
        """扫描并炼化指定工作区的知识库。

        dry_run=True 时只统计、不写回，适合先观察影响面。
        """
        report = ConsolidationReport()
        resources = list(
            self.store.iter_active_resources(workspace_id=workspace_id)
        )
        report.resources_scanned = len(resources)
        if not resources:
            return report

        by_hash: Dict[str, List[dict]] = {}
        by_norm: Dict[str, List[dict]] = {}
        for res in resources:
            by_hash.setdefault(res["content_hash"], []).append(res)
            if self.normalize_dedup:
                norm = _normalize_text(res["searchable_text"])
                if norm:
                    by_norm.setdefault(norm, []).append(res)

        # 1) 去重：同一内容（精确 hash 或归一化文本）只保留最新一条
        handled_superseded: Set[str] = set()
        for group in list(by_hash.values()) + list(by_norm.values()):
            if len(group) < 2:
                continue
            # 已被标记为重复的资源不再担任 canonical，避免状态冲突
            candidates = [
                r for r in group if r["id"] not in handled_superseded
            ]
            if len(candidates) < 2:
                continue
            canonical = max(
                candidates, key=lambda r: (r["updated_at"], r["id"])
            )
            for dup in candidates:
                if dup["id"] == canonical["id"]:
                    continue
                if dup["id"] in handled_superseded:
                    continue
                handled_superseded.add(dup["id"])
                report.duplicates_merged += 1
                if not dry_run:
                    self.store.mark_consolidation_status(
                        dup["id"], "superseded"
                    )
                    self.store.set_supersedes(canonical["id"], dup["id"])
                    report.superseded += 1

        # 2) 矛盾检测（保守：高重叠 + 极性相反）
        if self.detect_contradictions:
            self._detect_contradictions(
                resources, report, dry_run, handled_superseded
            )

        # 3) 衰减 / 强化：基于 last_hit 调整置信度
        now = datetime.now(timezone.utc)
        for res in resources:
            if res["consolidation_status"] == "superseded":
                continue
            conf = float(res["confidence"])
            last_hit = _parse_ts(res["last_hit"])
            new_conf = self._adjusted_confidence(conf, last_hit, now)
            if new_conf != conf:
                report.confidence_updated += 1
                if new_conf < conf:
                    report.decayed += 1
                else:
                    report.reinforced += 1
                if not dry_run:
                    self.store.set_confidence(res["id"], new_conf)

        report.notes.append(
            "consolidation完成："
            f"扫描{report.resources_scanned}条，"
            f"去重{report.duplicates_merged}条，"
            f"矛盾{report.contradictions_flagged}条，"
            f"置信度更新{report.confidence_updated}条"
        )
        return report

    # ---- 内部逻辑 --------------------------------------------------------

    def _adjusted_confidence(
        self,
        conf: float,
        last_hit: Optional[datetime],
        now: datetime,
    ) -> float:
        if last_hit is None:
            age_days = self.decay_days + 1  # 从未命中 → 直接衰减
        else:
            age_days = (now - last_hit).total_seconds() / 86400.0

        if age_days <= self.reinforce_days:
            # 近期被命中 → 小幅强化，向 1.0 靠拢
            return min(1.0, conf + self.reinforce_step)
        if age_days >= self.decay_days:
            # 久未命中 → 衰减，但有下限
            return max(self.confidence_floor, conf * self.decay_factor)
        return conf

    def _detect_contradictions(
        self,
        resources: List[dict],
        report: ConsolidationReport,
        dry_run: bool,
        handled_superseded: Set[str],
    ) -> None:
        active = [
            r
            for r in resources
            if r["consolidation_status"] == "active"
            and r["id"] not in handled_superseded
        ]
        seen_pairs: Set[frozenset] = set()
        for i in range(len(active)):
            a = active[i]
            a_tokens = _tokens(a["searchable_text"])
            a_topic = _topic_key(a["metadata"])
            a_pos = any(m in a["searchable_text"] for m in _POSITIVE_MARKERS)
            a_neg = any(m in a["searchable_text"] for m in _NEGATIVE_MARKERS)
            if not (a_pos or a_neg):
                continue
            for j in range(i + 1, len(active)):
                b = active[j]
                pair = frozenset((a["id"], b["id"]))
                if pair in seen_pairs:
                    continue
                # 同一主题键或较高 token 重叠才进入矛盾判定
                b_topic = _topic_key(b["metadata"])
                same_topic = (
                    a_topic is not None and a_topic == b_topic
                )
                overlap = _jaccard(a_tokens, _tokens(b["searchable_text"]))
                if not (same_topic or overlap >= self.contradiction_jaccard):
                    continue
                b_pos = any(m in b["searchable_text"] for m in _POSITIVE_MARKERS)
                b_neg = any(m in b["searchable_text"] for m in _NEGATIVE_MARKERS)
                # 一条正向、一条负向 → 疑似矛盾
                if (a_pos and b_neg) or (a_neg and b_pos):
                    seen_pairs.add(pair)
                    report.contradictions_flagged += 1
                    report.conflicts.append(
                        {
                            "a": a["id"],
                            "a_title": a["title"],
                            "b": b["id"],
                            "b_title": b["title"],
                            "reason": "同一主题下出现相反极性标记",
                        }
                    )
                    if not dry_run:
                        self.store.mark_consolidation_status(a["id"], "conflict")
                        self.store.mark_consolidation_status(b["id"], "conflict")


# ---- 轻量调度器（周期性触发，复用 reflection 的模式） --------------------


class ConsolidationScheduler:
    """按时间间隔/新增长知识触发一次炼化；状态落在独立 db，避免污染知识库。"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(
            db_path or _DEFAULT_CONSOLIDATION_DB
        ).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS consolidation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    ran_at TEXT NOT NULL,
                    resources_scanned INTEGER NOT NULL DEFAULT 0,
                    duplicates_merged INTEGER NOT NULL DEFAULT 0,
                    contradictions_flagged INTEGER NOT NULL DEFAULT 0,
                    confidence_updated INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def last_run(self, workspace_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ran_at FROM consolidation_runs "
                "WHERE workspace_id = ? ORDER BY id DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        return row["ran_at"] if row else None

    def should_run(
        self,
        *,
        workspace_id: str,
        interval_minutes: int = 60 * 12,
        min_new_resources: int = 10,
        current_resource_count: int = 0,
        last_seen_count: int = 0,
    ) -> bool:
        last = self.last_run(workspace_id)
        if last is None:
            return True
        last_ts = _parse_ts(last)
        if last_ts is None:
            return True
        elapsed = (
            datetime.now(timezone.utc) - last_ts
        ).total_seconds() / 60.0
        if elapsed >= interval_minutes:
            return True
        if current_resource_count - last_seen_count >= min_new_resources:
            return True
        return False

    def mark_run(
        self,
        workspace_id: str,
        report: ConsolidationReport,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO consolidation_runs(
                    workspace_id, ran_at, resources_scanned,
                    duplicates_merged, contradictions_flagged, confidence_updated
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    _utc_now(),
                    report.resources_scanned,
                    report.duplicates_merged,
                    report.contradictions_flagged,
                    report.confidence_updated,
                ),
            )

    def run_if_due(
        self,
        service: ConsolidationService,
        *,
        workspace_id: str = WorkspaceKnowledgeStore.DEFAULT_WORKSPACE_ID,
        interval_minutes: int = 60 * 12,
        min_new_resources: int = 10,
        current_resource_count: int = 0,
        last_seen_count: int = 0,
        dry_run: bool = False,
    ) -> Optional[ConsolidationReport]:
        if not self.should_run(
            workspace_id=workspace_id,
            interval_minutes=interval_minutes,
            min_new_resources=min_new_resources,
            current_resource_count=current_resource_count,
            last_seen_count=last_seen_count,
        ):
            return None
        report = service.consolidate(workspace_id=workspace_id, dry_run=dry_run)
        if not dry_run:
            self.mark_run(workspace_id, report)
        return report
