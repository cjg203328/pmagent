"""Phase 4 — 元记忆（Meta Memory）.

让 Agent 知道「自己懂什么 / 不懂什么 / 把握多大」，并在遇到知识缺口时
主动给出可执行建议：联网检索（search）或向用户澄清（ask_user）。

与其它子系统一样，本模块对回合只读、只产出建议；任何对外动作（如真正
发起联网检索）仍由调用方走既有能力/风险治理闸门，元记忆本身不做越权操作。

设计来源：MEMORY_EVOLUTION_DESIGN.md · Phase 4（修 G7：无元记忆）。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .strategy_store import StrategyStore, get_default_strategy_store

_DEFAULT_META_DB = "data/meta_memory.db"

_URL_RE = re.compile(
    r"https?://|www\.|\b[\w-]+\.(com|cn|org|io|ai|net|dev)\b",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _extract_topic(text: str, limit: int = 48) -> str:
    text = (text or "").strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    if not text:
        return "(空输入)"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _is_external_topic(text: str) -> bool:
    """Heuristic: 含 URL / 域名等外部信号 → 倾向联网检索。"""
    return bool(_URL_RE.search(text or ""))


@dataclass
class KnowledgeGap:
    """一个被识别出的知识缺口。"""

    topic: str
    kind: str  # unknown | low_confidence | no_strategy
    suggested_action: str  # search | ask_user
    detail: str
    confidence: float = 0.0


@dataclass
class MetaMemoryReport:
    """一次元记忆分析的结果。"""

    known_topics: List[str] = field(default_factory=list)
    unknown_topics: List[str] = field(default_factory=list)
    gaps: List[KnowledgeGap] = field(default_factory=list)
    suggested_actions: List[str] = field(default_factory=list)

    def has_gaps(self) -> bool:
        return bool(self.gaps)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "known_topics": self.known_topics,
            "unknown_topics": self.unknown_topics,
            "gaps": [
                {
                    "topic": g.topic,
                    "kind": g.kind,
                    "suggested_action": g.suggested_action,
                    "detail": g.detail,
                    "confidence": g.confidence,
                }
                for g in self.gaps
            ],
            "suggested_actions": self.suggested_actions,
        }


class MetaMemory:
    """分析「已知/未知/缺口」，给出主动检索或澄清建议。"""

    def __init__(
        self,
        *,
        confidence_threshold: float = 0.5,
        low_retrieval_threshold: float = 0.1,
    ):
        self.confidence_threshold = confidence_threshold
        self.low_retrieval_threshold = low_retrieval_threshold

    def analyze(
        self,
        user_input: str,
        *,
        retrieved_block: str = "",
        knowledge_store: Optional[Any] = None,
        strategy_store: Optional[StrategyStore] = None,
        feedback_store: Optional[Any] = None,
        meta_store: Optional["MetaMemoryStore"] = None,
    ) -> MetaMemoryReport:
        report = MetaMemoryReport()
        q = (user_input or "").strip()
        if not q:
            return report

        topic = _extract_topic(q)
        retrieval_empty = not (retrieved_block or "").strip()

        best_conf = 1.0
        has_hits = False
        if knowledge_store is not None and hasattr(knowledge_store, "search"):
            try:
                hits = knowledge_store.search(q, limit=3)
                if hits:
                    has_hits = True
                    best_conf = max(
                        float(h.get("confidence", 1.0)) for h in hits
                    )
            except Exception:  # noqa: BLE001 - 元记忆分析绝不应打断回合
                pass

        # 1) 已知：检索有结果且置信度达标
        if not retrieval_empty and best_conf >= self.confidence_threshold:
            report.known_topics.append(topic)
            return report

        # 2) 低置信度知识：检索到了，但把握不足 → 向用户澄清/补充来源
        if has_hits and best_conf < self.confidence_threshold:
            report.gaps.append(
                KnowledgeGap(
                    topic=topic,
                    kind="low_confidence",
                    suggested_action="ask_user",
                    detail=(
                        "已检索到相关知识，但置信度偏低"
                        f"（{best_conf:.2f}），建议向用户澄清或补充权威来源"
                    ),
                    confidence=best_conf,
                )
            )
            report.suggested_actions.append("ask_user")

        # 3) 未知：完全没检索到 → 外部线索则联网，否则追问
        if retrieval_empty:
            action = "search" if _is_external_topic(q) else "ask_user"
            report.unknown_topics.append(topic)
            report.gaps.append(
                KnowledgeGap(
                    topic=topic,
                    kind="unknown",
                    suggested_action=action,
                    detail=(
                        "未检索到相关记忆/知识，建议"
                        + ("主动联网检索" if action == "search" else "向用户澄清需求")
                    ),
                    confidence=0.0,
                )
            )
            report.suggested_actions.append(action)

        # 4) 无策略覆盖：若某能力从未沉淀过优化策略，温和提示
        if strategy_store is not None:
            try:
                if not strategy_store.active():
                    report.gaps.append(
                        KnowledgeGap(
                            topic=topic,
                            kind="no_strategy",
                            suggested_action="ask_user",
                            detail=(
                                "该主题尚无已采纳的优化策略，"
                                "建议先与用户确认期望的处理方式"
                            ),
                            confidence=0.0,
                        )
                    )
                    report.suggested_actions.append("ask_user")
            except Exception:  # noqa: BLE001
                pass

        if meta_store is not None and report.has_gaps():
            try:
                meta_store.record_gaps(report.gaps)
            except Exception:  # noqa: BLE001
                pass

        return report


def format_meta_memory_context(report: MetaMemoryReport) -> str:
    """把缺口建议格式化为可注入系统提示的块；无缺口时返回空串。"""
    if not report.gaps:
        return ""
    lines: List[str] = []
    for g in report.gaps:
        if g.suggested_action == "search":
            lines.append(
                f"- 知识缺口（{g.kind}）：建议主动联网检索「{g.topic}」"
                f"[{g.detail}]"
            )
        else:
            lines.append(
                f"- 知识缺口（{g.kind}）：建议向用户澄清「{g.topic}」"
                f"[{g.detail}]"
            )
    return "【元记忆：知识缺口与建议】\n" + "\n".join(lines)


# ---- 持久化的「已知未知」清单（让 Agent 跨会话记得自己不懂什么） --------


class MetaMemoryStore:
    """记录被反复遇到的知识缺口，形成可观测的「已知未知」列表。"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(
            db_path or _DEFAULT_META_DB
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
                CREATE TABLE IF NOT EXISTS meta_gaps (
                    topic TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    suggested_action TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1
                )
                """
            )

    def record_gaps(self, gaps: List[KnowledgeGap]) -> None:
        now = _utc_now()
        with self._connect() as conn:
            for g in gaps:
                row = conn.execute(
                    "SELECT * FROM meta_gaps WHERE topic = ?", (g.topic,)
                ).fetchone()
                if row is None:
                    conn.execute(
                        """
                        INSERT INTO meta_gaps(
                            topic, kind, suggested_action, detail,
                            first_seen, last_seen, seen_count
                        ) VALUES (?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            g.topic,
                            g.kind,
                            g.suggested_action,
                            g.detail,
                            now,
                            now,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE meta_gaps
                        SET kind = ?, suggested_action = ?, detail = ?,
                            last_seen = ?, seen_count = seen_count + 1
                        WHERE topic = ?
                        """,
                        (
                            g.kind,
                            g.suggested_action,
                            g.detail,
                            now,
                            g.topic,
                        ),
                    )

    def top_gaps(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM meta_gaps ORDER BY seen_count DESC, last_seen DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


# ---- 惰性单例 ------------------------------------------------------------


_DEFAULT_META_MEMORY: Optional[MetaMemory] = None
_DEFAULT_META_STORE: Optional[MetaMemoryStore] = None


def get_default_meta_memory() -> MetaMemory:
    global _DEFAULT_META_MEMORY
    if _DEFAULT_META_MEMORY is None:
        _DEFAULT_META_MEMORY = MetaMemory()
    return _DEFAULT_META_MEMORY


def get_default_meta_memory_store() -> MetaMemoryStore:
    global _DEFAULT_META_STORE
    if _DEFAULT_META_STORE is None:
        _DEFAULT_META_STORE = MetaMemoryStore()
    return _DEFAULT_META_STORE
