"""Lightweight reflection over edit feedback - the 'reinforcement' loop.

This is NOT model training. It mines the correction samples collected by
``EditFeedbackStore`` and surfaces: (1) which edit verbs the interpreter gets
wrong most often, and (2) concrete, readable suggestions a maintainer (or a
future prompt-tuner) can act on. As more real corrections accumulate, the
report gets sharper - that is the learning signal.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from artpm_agent.editing.feedback_store import EditFeedbackStore


def reflect(store: EditFeedbackStore, learned_rule_count: int = 0) -> dict[str, Any]:
    rows = store.all()
    if not rows:
        return {
            "total": 0,
            "accepted": 0,
            "rejected": 0,
            "accept_rate": None,
            "op_accept": {},
            "learned_rules": learned_rule_count,
            "suggestions": ["暂无反馈样本。使用「👎 或 我手动改了」记录的纠错会在这里形成学习信号。"],
        }

    accepted = sum(1 for r in rows if r.get("accepted"))
    op_total: Counter[str] = Counter()
    op_rejected: Counter[str] = Counter()
    rejected_samples: list[dict[str, Any]] = []
    correction_texts: list[str] = []

    for r in rows:
        for op in r.get("ops", []) or ["<none>"]:
            op_total[op] += 1
            if not r.get("accepted"):
                op_rejected[op] += 1
        if not r.get("accepted"):
            rejected_samples.append(r)
            if r.get("correction"):
                correction_texts.append(r["correction"])

    op_accept = {
        op: {
            "total": op_total[op],
            "rejected": op_rejected.get(op, 0),
            "reject_rate": (
                op_rejected.get(op, 0) / op_total[op] if op_total[op] else 0
            ),
        }
        for op in op_total
    }

    suggestions = _build_suggestions(
        op_accept, rejected_samples, correction_texts, learned_rule_count
    )

    return {
        "total": len(rows),
        "accepted": accepted,
        "rejected": len(rows) - accepted,
        "accept_rate": accepted / len(rows),
        "op_accept": op_accept,
        "learned_rules": learned_rule_count,
        "suggestions": suggestions,
    }


def _build_suggestions(
    op_accept: dict[str, Any],
    rejected: list[dict[str, Any]],
    corrections: list[str],
    learned_rule_count: int = 0,
) -> list[str]:
    out: list[str] = []
    weak = sorted(
        (
            (op, info)
            for op, info in op_accept.items()
            if info["total"] >= 2 and info["reject_rate"] >= 0.4
        ),
        key=lambda kv: kv[1]["reject_rate"],
        reverse=True,
    )
    if weak:
        names = "、".join(f"{op}（拒绝率 {info['reject_rate']*100:.0f}%）" for op, info in weak)
        out.append(
            f"以下编辑类型误判偏高，建议优先打磨解释器规则：{names}。"
        )
    else:
        out.append("目前各编辑类型拒绝率可控，解释器表现稳定。")

    if corrections:
        # surface the most recent corrections verbatim as a tuning backlog
        recent = corrections[-5:]
        out.append("近期用户纠正样本（可直接用于优化提示词/规则）：")
        for text in recent:
            out.append(f"  - {text}")

    # recurring correction themes
    theme_counter = _theme_clusters(corrections)
    if theme_counter:
        themes = "、".join(f"{t}×{c}" for t, c in theme_counter.most_common(3))
        out.append(f"高频纠正主题：{themes}。")

    if corrections and learned_rule_count == 0:
        out.append(
            "已积累纠正样本，但尚未沉淀为免费规则。点击「🧠 从反馈中学习」"
            "可把高频说法蒸馏成正则规则，之后同类指令将不再调用 AI。"
        )

    return out


def _theme_clusters(corrections: list[str]) -> Counter[str]:
    keywords = ["单价", "数量", "总价", "名称", "日期", "交付", "客户", "行", "列", "替换", "删除"]
    counter: Counter[str] = Counter()
    for text in corrections:
        for kw in keywords:
            if kw in text:
                counter[kw] += 1
    return counter
