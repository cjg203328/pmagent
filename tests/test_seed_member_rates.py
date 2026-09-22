"""费率播种脚本测试：只填未标定的人，已有费率和不认识的档位一律不猜。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SPEC = importlib.util.spec_from_file_location(
    "seed_member_rates", PROJECT_ROOT / "scripts" / "seed_member_rates.py"
)
assert SPEC and SPEC.loader
seed = importlib.util.module_from_spec(SPEC)
sys.modules["seed_member_rates"] = seed
SPEC.loader.exec_module(seed)

from artpm_agent.database.models import DatabaseManager  # noqa: E402


LEVEL_COSTS = {"初级": 300.0, "中级": 500.0, "高级": 1000.0}


class FakeMember:
    def __init__(self, member_id, name, skill_level=None, daily_cost=None):
        self.id = member_id
        self.name = name
        self.skill_level = skill_level
        self.daily_cost = daily_cost


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'seed.db'}"


# ── 计划阶段 ──────────────────────────────────────────────────


def test_member_without_rate_is_planned():
    planned, skipped = seed.plan_seeding(
        [FakeMember(1, "张三", "senior")], LEVEL_COSTS, seed.DEFAULT_LEVEL_MAP
    )

    assert skipped == []
    assert planned == [{"id": 1, "name": "张三", "level": "高级", "daily_cost": 1000.0}]


def test_member_with_existing_rate_is_never_overwritten():
    planned, skipped = seed.plan_seeding(
        [FakeMember(1, "张三", "senior", daily_cost=1234.0)],
        LEVEL_COSTS,
        seed.DEFAULT_LEVEL_MAP,
    )

    assert planned == []
    assert "已有费率" in skipped[0]["reason"]


def test_member_with_blank_level_is_skipped_not_guessed():
    """真实库里那名成员 skill_level 为空，必须跳过而不是套一个档位。"""
    planned, skipped = seed.plan_seeding(
        [FakeMember(1, "王五", None)], LEVEL_COSTS, seed.DEFAULT_LEVEL_MAP
    )

    assert planned == []
    assert "无法映射" in skipped[0]["reason"]


def test_member_with_unmapped_level_is_skipped():
    planned, skipped = seed.plan_seeding(
        [FakeMember(1, "李四", "principal")], LEVEL_COSTS, seed.DEFAULT_LEVEL_MAP
    )

    assert planned == []
    assert "principal" in skipped[0]["reason"]


def test_member_with_level_missing_from_config_is_skipped():
    planned, skipped = seed.plan_seeding(
        [FakeMember(1, "赵六", "中高级")],
        {"初级": 300.0},
        seed.DEFAULT_LEVEL_MAP,
    )

    assert planned == []
    assert "不存在档位" in skipped[0]["reason"]


def test_english_level_is_matched_case_insensitively():
    planned, _ = seed.plan_seeding(
        [FakeMember(1, "张三", "SENIOR")], LEVEL_COSTS, seed.DEFAULT_LEVEL_MAP
    )
    assert planned[0]["level"] == "高级"


def test_level_map_override_redirects_a_level():
    mapping = seed.parse_level_map(["senior=资深"])
    planned, _ = seed.plan_seeding(
        [FakeMember(1, "张三", "senior")],
        {**LEVEL_COSTS, "资深": 1500.0},
        mapping,
    )
    assert planned[0]["level"] == "资深"
    assert planned[0]["daily_cost"] == 1500.0


def test_level_map_rejects_malformed_entry():
    with pytest.raises(SystemExit):
        seed.parse_level_map(["senior"])


# ── 配置读取 ──────────────────────────────────────────────────


def test_load_level_costs_reads_five_tiers_from_default_config():
    costs = seed.load_level_costs(None)

    assert set(costs) == {"初级", "中级", "中高级", "高级", "资深"}
    assert costs["中级"] == 500.0
    assert all(value > 0 for value in costs.values())


# ── 落库 ──────────────────────────────────────────────────────


def test_apply_writes_rate_source_and_effective_date(tmp_path):
    url = _db_url(tmp_path)
    with DatabaseManager(url) as db:
        db.create_member({"name": "张三", "skill_level": "senior"})

    planned, _ = seed.plan_seeding(
        [FakeMember(1, "张三", "senior")], LEVEL_COSTS, seed.DEFAULT_LEVEL_MAP
    )
    updated = seed.apply_seeding(url, planned)

    assert updated == 1
    with DatabaseManager(url) as db:
        member = db.list_members(is_active=None)[0]
        assert member.daily_cost == 1000.0
        assert member.cost_source == "default"
        assert member.cost_effective_at is not None


def test_apply_is_idempotent(tmp_path):
    url = _db_url(tmp_path)
    with DatabaseManager(url) as db:
        db.create_member({"name": "张三", "skill_level": "senior"})

    planned, _ = seed.plan_seeding(
        [FakeMember(1, "张三", "senior")], LEVEL_COSTS, seed.DEFAULT_LEVEL_MAP
    )
    assert seed.apply_seeding(url, planned) == 1

    with DatabaseManager(url) as db:
        members = db.list_members(is_active=None)
    replanned, skipped = seed.plan_seeding(members, LEVEL_COSTS, seed.DEFAULT_LEVEL_MAP)

    assert replanned == []
    assert "已有费率" in skipped[0]["reason"]
    assert seed.apply_seeding(url, replanned) == 0


def test_apply_with_empty_plan_touches_nothing(tmp_path):
    url = _db_url(tmp_path)
    with DatabaseManager(url) as db:
        db.create_member({"name": "王五"})

    assert seed.apply_seeding(url, []) == 0

    with DatabaseManager(url) as db:
        member = db.list_members(is_active=None)[0]
        assert member.daily_cost is None
        assert member.cost_source is None
        assert member.cost_effective_at is None


def test_member_without_level_keeps_null_rate_after_run(tmp_path):
    """端到端：档位为空的人跑完播种仍是 NULL，报价时会走 needs_input。"""
    url = _db_url(tmp_path)
    with DatabaseManager(url) as db:
        db.create_member({"name": "王五"})

    with DatabaseManager(url) as db:
        members = db.list_members(is_active=None)
    planned, skipped = seed.plan_seeding(members, LEVEL_COSTS, seed.DEFAULT_LEVEL_MAP)
    seed.apply_seeding(url, planned)

    assert planned == []
    assert len(skipped) == 1
    with DatabaseManager(url) as db:
        member = db.list_members(is_active=None)[0]
        assert member.daily_cost is None
