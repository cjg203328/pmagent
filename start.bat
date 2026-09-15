@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "ARTPM_PORT=8501"
if not defined ARTPM_API_HOST set "ARTPM_API_HOST=127.0.0.1"
if not defined ARTPM_API_PORT set "ARTPM_API_PORT=8765"

set "PYTHON_CMD="
if exist "E:\python\python.exe" (
    set "PYTHON_CMD=E:\python\python.exe"
) else (
    where /q python >nul 2>&1 && set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python was not found.
    pause
    exit /b 1
)

"%PYTHON_CMD%" -c "import streamlit, fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] The selected Python environment is missing runtime dependencies.
    echo         Run: "%PYTHON_CMD%" -m pip install -e ".[api]"
    pause
    exit /b 1
)

if not exist .env copy .env.example .env >nul 2>&1
if not exist data mkdir data >nul 2>&1
if not exist artpm_agent\logs mkdir artpm_agent\logs >nul 2>&1

REM Stop only a stale Streamlit instance belonging to this repository.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\prepare_streamlit_port.ps1" -Port %ARTPM_PORT%
if errorlevel 1 (
    echo [ERROR] Port %ARTPM_PORT% is unavailable. ArtPM Agent was not started.
    pause
    exit /b 1
)

set "PYTHONPATH=%~dp0"
echo.
echo ArtPM Agent
echo   UI:  http://127.0.0.1:%ARTPM_PORT%
echo   API: http://%ARTPM_API_HOST%:%ARTPM_API_PORT%/docs
echo.

"%PYTHON_CMD%" start_with_checks.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
