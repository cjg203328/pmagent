#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "[ERROR] Python was not found."
    exit 1
fi

if ! "$PYTHON_BIN" -c "import streamlit, fastapi, uvicorn" >/dev/null 2>&1; then
    echo "Installing runtime dependencies..."
    "$PYTHON_BIN" -m pip install -e ".[api]" || exit 1
fi

[ -f .env ] || cp .env.example .env
mkdir -p data artpm_agent/logs

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
export ARTPM_API_HOST="${ARTPM_API_HOST:-127.0.0.1}"
export ARTPM_API_PORT="${ARTPM_API_PORT:-8765}"

echo "ArtPM Agent"
echo "  UI:  http://127.0.0.1:8501"
echo "  API: http://${ARTPM_API_HOST}:${ARTPM_API_PORT}/docs"
echo "Press Ctrl+C to stop both services."
echo

exec "$PYTHON_BIN" start_with_checks.py
