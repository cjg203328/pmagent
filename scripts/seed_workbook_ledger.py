#!/usr/bin/env python3
"""把真实月度人天分配表导入 ``projects / tasks / team_members / task_assignments``。

业务表目前是空的：8 个业务技能、周报和三栏工作台都在读零行表，所以命中也只会
给出空结果。这张工作簿就是缺失的数据源。

行为约束：

- 默认 **dry-run**，只打印导入计划与统计；``--apply`` 才写库。
- 幂等：按 ``(tenant, workspace, project, 月份, 任务标识)`` 去重，重跑不产生重复行。
- **不猜单位**：``商务人天``/``已分配人天`` 是人天，而 ``tasks.estimated_hours`` 是小时。
  换算系数由 ``--hours-per-day`` 显式给出（默认 8），并把原始人天写进
  ``tasks.description``，保证每个数字都能追回表里的来源行。
- 进度词映射不上时**列出来**并按 ``待开始`` 处理，不静默归类。
- 人名列 → ``team_members``；每人占该任务已分配人天的比例 → ``task_assignments.
  workload_ratio``（表里没有小时列，比例 × 该任务人天可无损还原到人天）。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# 表头用词在月份之间会变（早期「甲方工作室」，后期「项目」），所以按别名归一。
PROJECT_ALIASES = ("甲方工作室", "项目", "客户", "需求方")
TASK_ALIASES = ("任务名称", "任务", "资产名称", "名称")
STAGE_ALIASES = ("环节", "工序", "阶段")
PROGRESS_ALIASES = ("当前进度", "进度状态", "状态")
ESTIMATED_ALIASES = ("商务人天", "报价人天", "预估人天")
ACTUAL_ALIASES = ("已分配人天", "实际人天", "投入人天")
PRICE_ALIASES = ("单价", "人天单价")
DEADLINE_ALIASES = ("需求ddl", "需求DDL", "交付日期", "截止日期")
# 这些是元信息列，不能被当成人名。
METRIC_HEADER_WORDS = (
    "人天",
    "单价",
    "进度",
    "ddl",
    "日期",
    "任务",
    "工作室",
    "项目",
    "环节",
    "工序",
    "阶段",
    "未分配",
    "历史",
    "绩效",
    "月份",
    "备注",
    "合计",
    "状态",
    "场景",
    "目标",
    "达成",
    "绩效",
)

STATUS_MAP = {
    "进行中": "进行中",
    "已开始": "进行中",
    "制作中": "进行中",
    "返修": "进行中",
    "返工": "进行中",
    "修改中": "进行中",
    "已提交": "待审核",
    "待审核": "待审核",
    "待验收": "待审核",
    "已验收": "已完成",
    "已回收": "已完成",
    "回收": "已完成",
    "已通过": "已完成",
    "已完成": "已完成",
    "完成": "已完成",
    "未开始": "待开始",
    "未通过": "进行中",
    "已取消": "已取消",
    "取消": "已取消",
}

_MONTH_SHEET = re.compile(r"^(?P<year>\d{2})\s*年\s*(?P<month>1[0-2]|0?[1-9])\s*月$")


@dataclass
class TaskRow:
    month: str
    project: str
    name: str
    identity: str
    status: str
    estimated_days: float | None
    actual_days: float | None
    unit_price: float | None
    deadline: date | None
    source_row: int
    people: dict[str, float] = field(default_factory=dict)
    unmapped_status: str | None = None

    def window(self) -> tuple[date, date]:
        first = date(int(self.month[:4]), int(self.month[5:]), 1)
        following = (
            date(first.year + 1, 1, 1)
            if first.month == 12
            else date(first.year, first.month + 1, 1)
        )
        return first, following - timedelta(days=1)

    @property
    def progress(self) -> int:
        """A deriveable fill percentage, never an invented one.

        The sheet records 商务人天 and 已分配人天 but no completion percentage, so
        progress mirrors投入比例 and stays 100 only for terminal states.
        """
        if self.status == "已完成":
            return 100
        if self.status == "待开始":
            return 0
        if not self.estimated_days:
            return 0
        return max(
            0, min(99, round(100 * (self.actual_days or 0) / self.estimated_days))
        )


@dataclass
class Plan:
    tasks: list[TaskRow]
    projects: list[str]
    people: list[str]
    unmapped_statuses: Counter[str]
    skipped_rows: int
    months: list[str]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if not text:
        return None
    match = re.search(r"(20\d{2})[-/.年]\s*(\d{1,2})[-/.月]\s*(\d{1,2})", text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _header_index(row: Iterable[Any]) -> int | None:
    """Find the header row: the one naming the task column."""
    values = [_text(value) for value in row]
    for alias in TASK_ALIASES:
        if alias in values:
            return values.index(alias)
    return None


def _column_map(values: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, name in enumerate(values):
        if not name:
            continue
        for kind, aliases in (
            ("project", PROJECT_ALIASES),
            ("task", TASK_ALIASES),
            ("stage", STAGE_ALIASES),
            ("status", PROGRESS_ALIASES),
            ("estimated", ESTIMATED_ALIASES),
            ("actual", ACTUAL_ALIASES),
            ("price", PRICE_ALIASES),
            ("deadline", DEADLINE_ALIASES),
        ):
            if name in aliases and kind not in mapping:
                mapping[kind] = index
                break
    return mapping


def _people_columns(
    values: list[str], mapping: dict[str, int]
) -> list[tuple[int, str]]:
    used = set(mapping.values())
    people: list[tuple[int, str]] = []
    for index, name in enumerate(values):
        if index in used or not name:
            continue
        lowered = name.lower()
        if any(word.lower() in lowered for word in METRIC_HEADER_WORDS):
            continue
        if len(name) > 6:  # 人名不会比这更长；更长的列是说明性表头
            continue
        people.append((index, name))
    return people


def _read_sheet(worksheet: Any, month: str, plan: Plan) -> None:
    rows = [list(row) for row in worksheet.iter_rows(values_only=True)]
    header_at: int | None = None
    for index, row in enumerate(rows[:8]):
        if _header_index(row) is not None:
            header_at = index
            break
    if header_at is None:
        plan.skipped_rows += 1
        return
    values = [_text(value) for value in rows[header_at]]
    mapping = _column_map(values)
    people_columns = _people_columns(values, mapping)
    project_column = mapping.get("project")

    current_project = ""
    current_task = ""
    for offset, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        cell = lambda kind: (  # noqa: E731
            row[mapping[kind]] if kind in mapping and mapping[kind] < len(row) else None
        )
        raw_task = _text(cell("task"))
        project_text = _text(row[project_column]) if project_column is not None else ""
        # 合并单元格：项目分组必须前向填充，任务标识只在有值时更新。表尾的
        # 「目标/达成」块只在人名列有数字，若把任务名也填充下去，一张月表会
        # 凭空多出上百条重复任务（实测 5,956 条 vs 实际约 1,700 条）。
        if project_text and not project_text.isdigit():
            current_project = project_text
        if raw_task:
            current_task = raw_task
        stage = _text(cell("stage"))
        if not raw_task and not stage:
            if any(_text(value) for value in row):
                plan.skipped_rows += 1
            continue
        identity = f"{current_task}｜{stage}" if stage else current_task
        raw_status = _text(cell("status"))
        status = STATUS_MAP.get(raw_status)
        if status is None:
            if raw_status:
                plan.unmapped_statuses[raw_status] += 1
            status = "待开始"
        people: dict[str, float] = {}
        for index, name in people_columns:
            if index >= len(row):
                continue
            days = _number(row[index])
            if days:
                people[name] = days
        plan.tasks.append(
            TaskRow(
                month=month,
                project=current_project or "未归组",
                name=identity,
                identity=identity,
                status=status,
                estimated_days=_number(cell("estimated")),
                actual_days=_number(cell("actual")),
                unit_price=_number(cell("price")),
                deadline=_as_date(cell("deadline")),
                source_row=offset,
                people=people,
                unmapped_status=raw_status
                if status == "待开始" and raw_status
                else None,
            )
        )


def _collapse_duplicates(tasks: list[TaskRow]) -> list[TaskRow]:
    """Merge rows sharing a project/month/task key into one ledger entry."""
    merged: dict[tuple[str, str, str], TaskRow] = {}
    for task in tasks:
        key = (task.project, task.month, task.identity)
        existing = merged.get(key)
        if existing is None:
            merged[key] = task
            continue
        for name, days in task.people.items():
            existing.people[name] = existing.people.get(name, 0.0) + days
        for field_name in ("estimated_days", "actual_days"):
            value = getattr(task, field_name)
            if value:
                setattr(
                    existing, field_name, (getattr(existing, field_name) or 0.0) + value
                )
        if task.status == "进行中":
            existing.status = task.status
    return list(merged.values())


def build_plan(workbook: Any) -> Plan:
    plan = Plan(
        tasks=[],
        projects=[],
        people=[],
        unmapped_statuses=Counter(),
        skipped_rows=0,
        months=[],
    )
    for worksheet in workbook.worksheets:
        match = _MONTH_SHEET.match(_text(worksheet.title))
        if match is None:
            continue
        month = f"{2000 + int(match.group('year')):04d}-{int(match.group('month')):02d}"
        plan.months.append(month)
        reset = getattr(worksheet, "reset_dimensions", None)
        if callable(reset):
            reset()
        _read_sheet(worksheet, month, plan)
    plan.tasks = _collapse_duplicates(plan.tasks)
    plan.projects = sorted({task.project for task in plan.tasks})
    names: set[str] = set()
    for task in plan.tasks:
        names.update(task.people)
    plan.people = sorted(names)
    return plan


def _apply(
    db_url: str, plan: Plan, hours_per_day: int, tenant: str, workspace: str
) -> None:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from artpm_agent.database import models as m

    engine = create_engine(db_url)
    projects: dict[str, int] = {}
    members: dict[str, int] = {}
    seen_tasks: set[tuple[str, str, str]] = set()
    new_tasks = new_assignments = reused_tasks = 0
    with Session(engine) as session:
        for row in session.execute(
            select(m.Project).where(
                m.Project.tenant_id == tenant, m.Project.workspace_id == workspace
            )
        ).scalars():
            projects[row.project_name] = int(row.id)
        for row in session.execute(
            select(m.TeamMember).where(
                m.TeamMember.tenant_id == tenant,
                m.TeamMember.workspace_id == workspace,
            )
        ).scalars():
            members[row.name] = int(row.id)

        for name in plan.projects:
            if name in projects:
                continue
            total = sum(
                (task.estimated_days or 0.0) * (task.unit_price or 0.0)
                for task in plan.tasks
                if task.project == name
            )
            project = m.Project(
                project_name=name,
                client=name,
                status="进行中",
                quote_amount=round(total, 2),
                created_at=datetime.now(),
                tenant_id=tenant,
                workspace_id=workspace,
                notes="由月度人天分配表导入；quote_amount=Σ(商务人天×单价)，单位元",
            )
            session.add(project)
            session.flush()
            projects[name] = int(project.id)

        for name in plan.people:
            if name in members:
                continue
            member = m.TeamMember(
                name=name,
                role="美术",
                is_active=1,
                created_at=datetime.now(),
                tenant_id=tenant,
                workspace_id=workspace,
            )
            session.add(member)
            session.flush()
            members[name] = int(member.id)

        for task in plan.tasks:
            key = (task.project, task.month, task.identity)
            if key in seen_tasks:
                continue
            seen_tasks.add(key)
            record_name = f"[{task.month}] {task.identity}"
            start, due = task.window()
            # A closed historical month must not pose as live work: giving its
            # rows a past due date turned every archived task into a fake
            # 逾期 item (the first import reported 140 such "risks").
            current_month = date.today().strftime("%Y-%m")
            live = task.month >= current_month
            existing = session.execute(
                select(m.Task).where(
                    m.Task.tenant_id == tenant,
                    m.Task.workspace_id == workspace,
                    m.Task.project_id == projects[task.project],
                    m.Task.task_name == record_name,
                )
            ).first()
            if existing is not None:
                reused_tasks += 1
                continue
            record = m.Task(
                project_id=projects[task.project],
                task_name=record_name,
                task_type="美术外包",
                status=task.status,
                progress=task.progress,
                assignee_id=(
                    members[max(task.people, key=task.people.get)]
                    if task.people
                    else None
                ),
                estimated_hours=(
                    round(task.estimated_days * hours_per_day, 2)
                    if task.estimated_days is not None
                    else None
                ),
                actual_hours=(
                    round(task.actual_days * hours_per_day, 2)
                    if task.actual_days is not None
                    else None
                ),
                start_date=start,
                due_date=(task.deadline or due) if live else None,
                completed_date=due if task.status == "已完成" else None,
                # ``created_at`` doubles as the "new this week" key in the
                # weekly report; stamping it with the import time made all
                # 1,921 rows look newly created and drowned the real signal.
                created_at=datetime.combine(start, datetime.min.time()),
                tenant_id=tenant,
                workspace_id=workspace,
                description=(
                    f"来源行 {task.source_row}｜人天口径：商务 {task.estimated_days}、"
                    f"已分配 {task.actual_days}；1 人天 = {hours_per_day} 小时；"
                    "起止日期按所属月份推导"
                ),
            )
            session.add(record)
            session.flush()
            new_tasks += 1
            total_people = sum(task.people.values())
            if total_people > 0:
                for name, days in task.people.items():
                    session.add(
                        m.TaskAssignment(
                            task_id=int(record.id),
                            staff_id=members[name],
                            workload_ratio=round(days / total_people, 4),
                            assigned_at=datetime.now(),
                            tenant_id=tenant,
                            workspace_id=workspace,
                        )
                    )
                    new_assignments += 1
        session.commit()
    print(
        f"写入完成：新建任务 {new_tasks}、复用 {reused_tasks}、"
        f"派单 {new_assignments}、项目池 {len(projects)}、成员池 {len(members)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", help="月度人天分配表 .xlsx 路径")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只预演）")
    parser.add_argument("--db", default="sqlite:///data/artpm.db")
    parser.add_argument("--tenant", default="local")
    parser.add_argument("--workspace", default="local-default")
    parser.add_argument("--hours-per-day", type=int, default=8)
    parser.add_argument("--limit-projects", type=int, default=0, help="只导前 N 个项目")
    args = parser.parse_args(argv)

    from artpm_agent.utils.spreadsheet_io import safe_load_workbook

    source = Path(args.workbook).expanduser().resolve()
    if not source.is_file():
        print(f"找不到工作簿: {source}", file=sys.stderr)
        return 2
    workbook = safe_load_workbook(source, read_only=True, data_only=True)
    try:
        plan = build_plan(workbook)
    finally:
        workbook.close()

    if args.limit_projects:
        keep = set(plan.projects[: args.limit_projects])
        plan.tasks = [task for task in plan.tasks if task.project in keep]
        plan.projects = sorted(keep)
        plan.unmapped_statuses = Counter(
            task.unmapped_status for task in plan.tasks if task.unmapped_status
        )
        names = {name for task in plan.tasks for name in task.people}
        plan.people = sorted(names)

    print(f"月份表 {len(plan.months)} 张：{', '.join(plan.months[:3])} … 等")
    print(
        f"任务行 {len(plan.tasks)}｜项目 {len(plan.projects)}｜成员 {len(plan.people)}"
    )
    print(f"跳过（有内容但无任务名）{plan.skipped_rows} 行")
    print("状态分布:", dict(Counter(task.status for task in plan.tasks)))
    if plan.unmapped_statuses:
        print("映射不上的进度词（按待开始处理，请确认）:", dict(plan.unmapped_statuses))
    priced = [task for task in plan.tasks if task.unit_price and task.estimated_days]
    print(
        f"人天→小时按 1 人天 = {args.hours_per_day} 小时；"
        f"可计价任务 {len(priced)}，Σ(商务人天×单价) = "
        f"{sum(t.unit_price * t.estimated_days for t in priced):,.0f} 元"
    )
    print("项目样例:", ", ".join(plan.projects[:8]))
    print("成员样例:", ", ".join(plan.people[:12]))
    if not args.apply:
        print("\n[dry-run] 未写入。确认后用 --apply")
        return 0
    _apply(args.db, plan, args.hours_per_day, args.tenant, args.workspace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
