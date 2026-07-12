@echo off
set PYTHONUTF8=1
title ArtPM

echo.
echo ===============================================================
echo                            ArtPM
echo        Professional AI Project Management Tool
echo ===============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.8+ first.
    pause
    exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.8 or newer is required.
    pause
    exit /b 1
)

echo [OK] Python found
echo.

echo [*] Checking dependencies...
python -c "import streamlit, pandas, plotly, sqlalchemy, openpyxl, dotenv, requests" 2>nul
if errorlevel 1 (
    echo [*] Installing dependencies...
    python -m pip install -r requirements.txt -q
    if errorlevel 1 (
        echo [ERROR] Installation failed
        echo Run manually: python -m pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo [OK] Installed
) else (
    echo [OK] Dependencies ready
)
echo.

if not exist "data" mkdir data >nul 2>&1
if not exist "temp" mkdir temp >nul 2>&1

if not exist ".env" (
    echo [*] Creating config...
    echo OPENAI_API_KEY= > .env
    echo ANTHROPIC_API_KEY= >> .env
    echo ZHIPU_API_KEY= >> .env
    echo. >> .env
    echo LLM_PROVIDER=anthropic >> .env
    echo LLM_MODEL=claude-3-5-sonnet-20241022 >> .env
    echo MEMORY_DB_PATH=./data/memory.db >> .env
    echo [OK] Config created. The app can run offline; edit .env to enable LLM chat
    echo.
)

echo [*] Starting ArtPM...
echo ===============================================================
echo Open in browser: http://localhost:8501
echo Press Ctrl+C to stop
echo ===============================================================
echo.

python -m streamlit run artpm_agent\app.py --server.headless true --server.address 127.0.0.1

pause
