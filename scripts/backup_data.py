#!/usr/bin/env python3
"""
data/ 数据库备份脚本
====================

将 data/ 下的数据库文件（默认 *.db）复制到 data/backups/<时间戳>/，
保留最近 N 份备份目录，清理最旧的；**绝不删除源文件**。

用法::

    python scripts/backup_data.py                  # 备份 <repo>/data 下所有 *.db
    python scripts/backup_data.py --keep 10        # 保留最近 10 份
    python scripts/backup_data.py --data-dir X     # 指定数据目录
    python scripts/backup_data.py --list           # 仅列出已有备份
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_KEEP = 10
DB_GLOB = "*.db"


def backup(data_dir: Path, keep: int) -> Path | None:
    """备份 data_dir 下的 *.db 到 data_dir/backups/<时间戳>/，并清理最旧备份。

    Returns:
        新建的备份目录路径；若无匹配文件则返回 None。
    """
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        raise SystemExit(f"数据目录不存在: {data_dir}")

    sources = sorted(data_dir.glob(DB_GLOB))
    if not sources:
        print(f"[backup] 未找到匹配 {DB_GLOB} 的文件 (目录: {data_dir})")
        return None

    backups_root = data_dir / "backups"
    backups_root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backups_root / stamp
    # 同一秒内多次运行也可能碰撞：保证目录名唯一，绝不覆盖已有快照
    counter = 1
    while dest.exists():
        dest = backups_root / f"{stamp}_{counter}"
        counter += 1
    dest.mkdir(parents=True, exist_ok=True)

    copied = []
    for src in sources:
        shutil.copy2(src, dest / src.name)
        copied.append(src.name)

    print(f"[backup] 已备份 {len(copied)} 个文件 -> {dest}")
    for name in copied:
        print(f"           - {name}")

    prune(backups_root, keep)
    return dest


def prune(backups_root: Path, keep: int) -> None:
    """保留最近 keep 份备份目录，清理其余（按目录名升序=时间升序）。"""
    if keep <= 0:
        return
    existing = sorted(
        (p for p in backups_root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    excess = existing[:-keep] if len(existing) > keep else []
    for old in excess:
        shutil.rmtree(old)
        print(f"[prune] 已清理最旧备份: {old.name}")


def list_backups(data_dir: Path) -> None:
    backups_root = data_dir.resolve() / "backups"
    if not backups_root.is_dir():
        print("[list] 暂无备份")
        return
    items = sorted(
        (p for p in backups_root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    print(f"[list] 共 {len(items)} 份备份:")
    for p in items:
        files = [f.name for f in p.glob(DB_GLOB)]
        print(f"  {p.name}/  ({len(files)} files)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backup data/ database files")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="数据目录 (默认: <repo>/data)",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help="保留最近 N 份备份 (默认: 10)",
    )
    parser.add_argument("--list", action="store_true", help="仅列出已有备份")
    args = parser.parse_args(argv)

    if args.list:
        list_backups(args.data_dir)
        return 0

    backup(args.data_dir, args.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
