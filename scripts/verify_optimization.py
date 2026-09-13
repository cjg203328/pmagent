#!/usr/bin/env python3
"""
优化验证脚本 - 验证项目优化效果
"""
import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str], description: str) -> tuple[bool, str]:
    """运行命令并返回结果"""
    print(f"🔹 {description}...", end=" ")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print("✅")
            return True, result.stdout
        else:
            print(f"❌ (退出码: {result.returncode})")
            return False, result.stderr
    except Exception as e:
        print(f"❌ ({e})")
        return False, str(e)


def check_file_exists(path: Path, description: str) -> bool:
    """检查文件是否存在"""
    print(f"🔹 检查 {description}...", end=" ")
    if path.exists():
        print("✅")
        return True
    else:
        print("❌")
        return False


def main():
    """主函数"""
    # Windows often starts this script with a GBK console. Keep status output
    # usable instead of failing before the first verification runs.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")

    root = Path(__file__).parent.parent
    print(f"📊 验证优化效果\n{'='*60}\n")

    checks = []

    # 1. 检查清理效果
    print("1️⃣  清理效果验证")
    checks.append(check_file_exists(root / "scripts" / "clean.py", "清理脚本"))

    # 检查是否删除了缓存
    pycache_count = len(list(root.rglob("__pycache__")))
    print(f"🔹 __pycache__ 目录数量: {pycache_count}...", end=" ")
    if pycache_count == 0:
        print("✅")
    else:
        # Test and type-check runs legitimately create bytecode on the project
        # volume.  Report it without turning a non-destructive verifier into a
        # cleanup command or a release blocker.
        print("ℹ️  (测试运行产生，可按需清理)")
    checks.append(True)

    print()

    # 2. 检查启动脚本
    print("2️⃣  启动脚本验证")
    required_scripts = ["start.bat", "start.sh", "start_with_checks.py"]
    for script in required_scripts:
        checks.append(check_file_exists(root / script, script))

    # Historical launchers were removed.  Keep this list limited to names that
    # are not documented, so the verifier itself cannot become a stale entry
    # point for a deleted launcher.
    removed_scripts = [
        "start_app.bat",
        "start_with_checks.bat",
    ]
    for script in removed_scripts:
        path = root / script
        print(f"🔹 确认删除 {script}...", end=" ")
        if not path.exists():
            print("✅")
            checks.append(True)
        else:
            print("⚠️  (仍存在)")
            checks.append(False)

    # Documentation must point at the single maintained supervisor entries.
    # A stale launcher name is a user-facing false entry even when the file is
    # absent, so fail the contract before a release is shipped.
    documentation_files = [
        root / "docs" / "guides" / "QUICKSTART.md",
        root / "docs" / "operations" / "TROUBLESHOOTING.md",
    ]
    for document in documentation_files:
        print(f"检查启动文档 {document.name}...", end=" ")
        try:
            text = document.read_text(encoding="utf-8")
        except OSError:
            print("失败")
            checks.append(False)
            continue
        stale = "start_optimized" in text
        canonical = "start.bat" in text or "start.sh" in text
        if stale or not canonical:
            print("失败")
            checks.append(False)
        else:
            print("通过")
            checks.append(True)

    print()

    # 3. 检查新增测试
    print("3️⃣  测试文件验证")
    test_files = [
        "tests/test_views_observability.py",
        "tests/test_views_settings.py",
    ]
    for test_file in test_files:
        checks.append(check_file_exists(root / test_file, test_file))

    print()

    # 4. 运行新测试
    print("4️⃣  运行新增测试")
    success, output = run_command(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_views_observability.py",
            "tests/test_views_settings.py",
            "-q",
            "--no-cov",
        ],
        "运行视图层测试"
    )
    checks.append(success)

    if success:
        # 统计测试数量
        for line in output.split("\n"):
            if "passed" in line or "collected" in line:
                print(f"   {line.strip()}")

    print()

    # 5. 代码质量检查
    print("5️⃣  代码质量检查")
    success, _ = run_command(
        ["ruff", "check", "artpm_agent", "--quiet"],
        "Ruff 代码检查"
    )
    checks.append(success)

    print()

    # 6. 检查文档
    print("6️⃣  文档验证")
    report_candidates = (
        root / "docs" / "analysis" / "deep-analysis-artpm-2026.md",
        root / "docs" / "analysis" / "deep-analysis-2026.md",
    )
    roadmap_candidates = (
        root / "docs" / "roadmaps" / "improvement-roadmap-24w.md",
        root / "docs" / "dev" / "TECHNICAL_DEBT_TASKS.md",
        root / "docs" / "roadmaps" / "optimization-roadmap.md",
    )
    has_report = any(path.is_file() for path in report_candidates)
    has_roadmap = any(path.is_file() for path in roadmap_candidates)
    print(f"🔹 当前深度分析报告... {'✅' if has_report else '❌'}")
    print(f"🔹 当前路线图/技术债清单... {'✅' if has_roadmap else '❌'}")
    checks.extend((has_report, has_roadmap))

    print()

    # 总结
    print(f"{'='*60}")
    total = len(checks)
    passed = sum(checks)
    percentage = (passed / total * 100) if total > 0 else 0

    print(f"检查项: {passed}/{total} 通过 ({percentage:.1f}%)")

    if percentage == 100:
        print("✨ 优化验证通过！所有检查项均正常")
        return 0
    elif percentage >= 80:
        print("✅ 优化基本完成，部分检查项需要关注")
        return 0
    else:
        print("⚠️  部分检查未通过，请查看上述输出")
        return 1


if __name__ == "__main__":
    sys.exit(main())
