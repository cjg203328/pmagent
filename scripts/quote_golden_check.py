#!/usr/bin/env python3
"""报价正确性门禁。

结构断言（ST-1..ST-7）不依赖业务真值，从 M1 起阻塞 CI。
真值断言（V-1..V-3）需要 `tests/golden/cases/` 下的真实报价单与人工确认答案
（PRD §12.5）。`cases/` 为空时明确 skip 并打印原因，**不得报告为已通过**——
沿用 `docs/dev/AGENTS.md` 对集成测试的诚实性要求。

退出码（CI 只认这个，所以「没验」必须与「验过」区分开）：

    0  结构断言全部通过
    1  结构断言失败（pytest 的退出码）
    2  真值断言不可用：样本存在但比对器未实现，或样本目录不存在
    3  真值断言被要求执行，但 `cases/` 为空——未验证，不是通过

用法：

    python scripts/quote_golden_check.py            # 结构断言
    python scripts/quote_golden_check.py --values   # 真值断言，需 cases/
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRUCTURE_TARGET = "tests/golden/test_structure.py"
CASES_DIR = ROOT / "tests" / "golden" / "cases"

EXIT_OK = 0
EXIT_STRUCTURE_FAILED = 1
EXIT_VALUES_UNAVAILABLE = 2
EXIT_VALUES_NOT_RUN = 3


def _run_structure() -> int:
    print("== 结构断言 ST-1..ST-7 ==")
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-cov",
            "-rs",
            STRUCTURE_TARGET,
        ],
        cwd=ROOT,
        text=True,
        check=False,
    )
    return process.returncode


def _display(path: Path) -> str:
    """Show a path relative to the repo when possible, absolute otherwise."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _existing_cases() -> list:
    if not CASES_DIR.is_dir():
        return []
    return sorted(path for path in CASES_DIR.iterdir() if path.is_dir())


def _run_values() -> int:
    cases = _existing_cases()
    if not cases:
        print("== 真值断言 V-1..V-3 ==")
        print(f"未执行：{_display(CASES_DIR)} 为空或不存在。")
        print(
            "原因：需要业务方提供 3-5 份真实报价单 + 人工确认的 expected.json"
            "（PRD §12.5 / tests/golden/README.md §3）。"
        )
        print("在拿到之前，「报价功能已验证」的结论不成立。")
        print(f"退出码 {EXIT_VALUES_NOT_RUN}：未验证，不是通过。")
        return EXIT_VALUES_NOT_RUN

    print(f"== 真值断言 V-1..V-3（{len(cases)} 个样本）==")
    print("注意：真值比对器尚未实现（依赖 cases/ 的 schema 定稿）。")
    print("当前仅确认样本存在，不作为通过信号。")
    return EXIT_VALUES_UNAVAILABLE


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="报价正确性门禁")
    parser.add_argument(
        "--values",
        action="store_true",
        help="运行真值断言（需要 tests/golden/cases/，为空时退出码 3）",
    )
    arguments = parser.parse_args(argv)

    if arguments.values:
        return _run_values()
    return _run_structure()


if __name__ == "__main__":
    raise SystemExit(main())
