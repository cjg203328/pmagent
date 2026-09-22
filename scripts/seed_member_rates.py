#!/usr/bin/env python3
"""把 `cost_config.staff_levels` 播种进 `team_members.daily_cost`。

领域数据契约 §10 第 2 步。播种后 `staff_levels` 只作为历史默认值存在，
运行时不再读取；唯一费率来源是 `team_members.daily_cost`。

行为约束：

- 默认 **dry-run**，只打印计划；`--apply` 才写库。
- 只填充 `daily_cost IS NULL` 的行，已有费率一律不动。
- 档位映射不上（`skill_level` 为空或不在映射表内）的行**跳过并列出**，
  不猜费率——领域数据契约 §6 要求缺数据时返回 `needs_input`，
  在数据层就是把 NULL 留成 NULL。
- `cost_source` 一律写 `default`，因为这些数值来自配置文件而非合同。

用法::

    python scripts/seed_member_rates.py                    # 预演
    python scripts/seed_member_rates.py --apply            # 写入
    python scripts/seed_member_rates.py --apply --db <url> # 指定库
    python scripts/seed_member_rates.py --level-map senior=资深
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# English levels are what the ORM comment documents for team_members.skill_level;
# the Chinese five-tier names are what cost_config.staff_levels uses.
# 中间的 senior → 高级 而非 资深 属于待业务方确认的判定，可用 --level-map 覆盖。
DEFAULT_LEVEL_MAP: Dict[str, str] = {
    "junior": "初级",
    "初级": "初级",
    "intermediate": "中级",
    "中级": "中级",
    "middle": "中级",
    "senior": "高级",
    "中高级": "中高级",
    "高级": "高级",
    "资深": "资深",
}

COST_SOURCE_DEFAULT = "default"


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


def load_level_costs(config_path: Optional[str]) -> Dict[str, float]:
    """Read the seeding values from configuration, not from code constants."""
    from artpm_agent.config import get_config

    config = get_config(config_path) if config_path else get_config()
    raw = getattr(config, "config", {}) or {}
    levels = (raw.get("cost_config") or {}).get("staff_levels") or {}

    costs: Dict[str, float] = {}
    for level, payload in levels.items():
        if not isinstance(payload, dict):
            continue
        try:
            value = float(payload.get("daily_cost"))
        except (TypeError, ValueError):
            continue
        if value > 0:
            costs[str(level)] = value
    return costs


def parse_level_map(entries: Optional[Iterable[str]]) -> Dict[str, str]:
    mapping = dict(DEFAULT_LEVEL_MAP)
    for entry in entries or ():
        if "=" not in entry:
            raise SystemExit(f"--level-map 需要 KEY=VALUE 形式，收到: {entry}")
        key, value = entry.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or not value:
            raise SystemExit(f"--level-map 两侧都不能为空: {entry}")
        mapping[key] = value
    return mapping


def plan_seeding(
    members: Iterable[Any],
    level_costs: Dict[str, float],
    level_map: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split members into what can be seeded and what cannot, with reasons."""
    planned: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for member in members:
        name = getattr(member, "name", None) or f"#{getattr(member, 'id', '?')}"
        current = getattr(member, "daily_cost", None)
        if current is not None:
            skipped.append(
                {"id": member.id, "name": name, "reason": f"已有费率 {current}，不覆盖"}
            )
            continue

        raw_level = getattr(member, "skill_level", None)
        level = level_map.get(str(raw_level or "").strip()) or level_map.get(
            str(raw_level or "").strip().lower()
        )
        if not level:
            skipped.append(
                {
                    "id": member.id,
                    "name": name,
                    "reason": (
                        f"skill_level 为 {raw_level!r}，无法映射到费率档位；"
                        "补档位或先用 --level-map 指定"
                    ),
                }
            )
            continue

        cost = level_costs.get(level)
        if cost is None:
            skipped.append(
                {
                    "id": member.id,
                    "name": name,
                    "reason": f"配置中不存在档位 {level} 的 daily_cost",
                }
            )
            continue

        planned.append(
            {"id": member.id, "name": name, "level": level, "daily_cost": cost}
        )

    return planned, skipped


def apply_seeding(db_url: Optional[str], planned: List[Dict[str, Any]]) -> int:
    from artpm_agent.database.models import DatabaseManager, TeamMember

    if not planned:
        return 0

    effective_at = datetime.now()
    updated = 0
    with DatabaseManager(db_url) as db:
        session = db.get_session()
        try:
            for item in planned:
                member = (
                    session.query(TeamMember).filter(TeamMember.id == item["id"]).one()
                )
                if member.daily_cost is not None:
                    # Re-check inside the transaction; another writer may have
                    # filled the rate between planning and applying.
                    continue
                member.daily_cost = item["daily_cost"]
                member.cost_source = COST_SOURCE_DEFAULT
                member.cost_effective_at = effective_at
                updated += 1
            session.commit()
        finally:
            session.close()
    return updated


def main(argv: Optional[List[str]] = None) -> int:
    _stdout_utf8()
    parser = argparse.ArgumentParser(description="播种 team_members.daily_cost")
    parser.add_argument("--apply", action="store_true", help="实际写入，默认仅预演")
    parser.add_argument("--db", default=None, help="数据库 URL，默认使用项目库")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument(
        "--level-map",
        action="append",
        default=[],
        help="覆盖档位映射，可重复，例：--level-map senior=资深",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    level_costs = load_level_costs(args.config)
    if not level_costs:
        print("[seed] 配置中没有任何 daily_cost，无可播种。")
        return 1

    level_map = parse_level_map(args.level_map)

    from artpm_agent.database.models import DatabaseManager

    with DatabaseManager(args.db) as db:
        members = db.list_members(is_active=None)

    planned, skipped = plan_seeding(members, level_costs, level_map)

    if args.json:
        print(
            json.dumps(
                {"planned": planned, "skipped": skipped, "applied": not args.apply},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

    if not args.json:
        print(f"[seed] 候选档位费率: {level_costs}")
        print(f"[seed] 待播种 {len(planned)} 人，跳过 {len(skipped)} 人")
        for item in planned:
            print(
                f"  + {item['name']}: {item['level']} → {item['daily_cost']:g} 元/人天"
            )
        for item in skipped:
            print(f"  - {item['name']}: {item['reason']}")

    if not args.apply:
        if not args.json:
            print("[seed] 预演结束，未写入。加 --apply 执行。")
        return 0

    updated = apply_seeding(args.db, planned)
    print(f"[seed] 已写入 {updated} 行，cost_source='{COST_SOURCE_DEFAULT}'。")
    if skipped:
        print(f"[seed] {len(skipped)} 人仍无费率，报价时会要求补数据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
