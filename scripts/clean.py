#!/usr/bin/env python3
"""
项目清理工具 - 清理缓存、日志和临时文件
"""
import shutil
from pathlib import Path
from datetime import datetime, timedelta


def clean_pycache(root_dir: Path) -> tuple[int, int]:
    """清理 __pycache__ 和 .pyc 文件"""
    dirs_removed = 0
    files_removed = 0

    for path in root_dir.rglob("__pycache__"):
        if path.is_dir():
            shutil.rmtree(path)
            dirs_removed += 1

    for path in root_dir.rglob("*.pyc"):
        if path.is_file():
            path.unlink()
            files_removed += 1

    return dirs_removed, files_removed


def clean_logs(root_dir: Path, days: int = 3) -> int:
    """清理旧日志文件（保留最近N天）"""
    cutoff_time = datetime.now() - timedelta(days=days)
    files_removed = 0

    log_dirs = [
        root_dir / "artpm_agent" / "logs",
        root_dir / ".cache",
    ]

    for log_dir in log_dirs:
        if not log_dir.exists():
            continue

        for log_file in log_dir.glob("*.log"):
            if log_file.stat().st_mtime < cutoff_time.timestamp():
                log_file.unlink()
                files_removed += 1

    # 清理根目录的日志文件
    for log_file in root_dir.glob("*.log"):
        if log_file.stat().st_mtime < cutoff_time.timestamp():
            log_file.unlink()
            files_removed += 1

    return files_removed


def clean_test_cache(root_dir: Path) -> int:
    """清理测试缓存"""
    dirs_removed = 0

    cache_dirs = [
        root_dir / ".pytest_cache",
        root_dir / ".ruff_cache",
        root_dir / "htmlcov",
    ]

    for cache_dir in cache_dirs:
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            dirs_removed += 1

    # 删除 coverage 文件
    for coverage_file in [root_dir / ".coverage", root_dir / "coverage.xml"]:
        if coverage_file.exists():
            coverage_file.unlink()
            dirs_removed += 1

    return dirs_removed


def get_dir_size(path: Path) -> int:
    """获取目录大小（字节）"""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except (PermissionError, OSError):
        pass
    return total


def format_size(bytes_size: int) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"


def main():
    """主函数"""
    root_dir = Path(__file__).parent.parent
    print(f"🧹 清理项目: {root_dir}")
    print(f"{'='*60}\n")

    # 记录初始大小
    initial_size = get_dir_size(root_dir)
    print(f"清理前项目大小: {format_size(initial_size)}\n")

    # 1. 清理 Python 缓存
    print("🔹 清理 Python 缓存...")
    dirs, files = clean_pycache(root_dir)
    print(f"   ✅ 删除 {dirs} 个 __pycache__ 目录和 {files} 个 .pyc 文件\n")

    # 2. 清理旧日志
    print("🔹 清理旧日志 (保留最近3天)...")
    log_count = clean_logs(root_dir, days=3)
    print(f"   ✅ 删除 {log_count} 个日志文件\n")

    # 3. 清理测试缓存
    print("🔹 清理测试缓存...")
    cache_count = clean_test_cache(root_dir)
    print(f"   ✅ 清理 {cache_count} 个缓存目录/文件\n")

    # 记录最终大小
    final_size = get_dir_size(root_dir)
    saved = initial_size - final_size
    print(f"{'='*60}")
    print(f"清理后项目大小: {format_size(final_size)}")
    print(f"节省空间: {format_size(saved)} ({saved / initial_size * 100:.1f}%)")
    print(f"{'='*60}\n")
    print("✨ 清理完成！")


if __name__ == "__main__":
    main()
