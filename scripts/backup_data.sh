#!/usr/bin/env bash
# 备份 data/ 数据库文件（Git Bash / *nix 便捷包装）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python "$SCRIPT_DIR/backup_data.py" "$@"
