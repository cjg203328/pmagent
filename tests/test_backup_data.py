"""
data/ 备份脚本测试
====================

通过子进程调用 scripts/backup_data.py，验证：
1. 备份到 data/backups/<时间戳>/ 且源文件不被删除
2. 仅备份 *.db（忽略其他文件）
3. 超过 --keep 时清理最旧备份目录
4. --list 能列出备份

全部使用临时目录，绝不触碰真实 data/。
"""
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS_DIR / "backup_data.py"


def _make_data(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    (d / "artpm.db").write_bytes(b"x" * 100)
    (d / "memory.db").write_bytes(b"y" * 50)
    (d / "notes.txt").write_text("ignore me")
    return d


def _run(data_dir: Path, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(data_dir), *extra],
        capture_output=True,
        text=True,
    )


def test_backup_creates_timestamped_copy_and_keeps_source(tmp_path):
    d = _make_data(tmp_path)
    out = _run(d)
    assert out.returncode == 0, out.stderr

    backups = sorted((d / "backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "artpm.db").read_bytes() == b"x" * 100
    assert (backups[0] / "memory.db").read_bytes() == b"y" * 50
    # notes.txt 不匹配 *.db，不应被备份
    assert not (backups[0] / "notes.txt").exists()
    # 源文件仍然存在
    assert (d / "artpm.db").exists()
    assert (d / "memory.db").exists()


def test_backup_prunes_oldest_beyond_keep(tmp_path):
    d = _make_data(tmp_path)
    for _ in range(5):
        _run(d, "--keep", "3")
    backups = sorted((d / "backups").iterdir())
    assert len(backups) == 3


def test_list_backups_reports_count(tmp_path):
    d = _make_data(tmp_path)
    _run(d)
    out = _run(d, "--list")
    assert out.returncode == 0
    assert "1" in out.stdout


def test_backup_no_db_files_is_safe(tmp_path):
    d = tmp_path / "empty_data"
    d.mkdir()
    out = _run(d)
    assert out.returncode == 0
    assert "未找到" in out.stdout
    assert not (d / "backups").exists()
