@echo off
chcp 65001 >nul 2>&1
REM ArtPM Agent Windows 启动脚本

echo   ArtPM Agent starting...
echo.

REM ---- 查找有依赖的 Python（优先 E:\python，再走 PATH）----
set "PYTHON_CMD="
where /q "E:\python\python.exe" 2>nul && (
    set "PYTHON_CMD=E:\python\python.exe"
) || (
    where /q "python" 2>nul && set "PYTHON_CMD=python"
)

if "%PYTHON_CMD%"=="" (
    echo [ERROR] No Python found.
    pause & exit /b 1
)

REM 验证依赖可用
"%PYTHON_CMD%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python at "%PYTHON_CMD%" is missing streamlit.
    echo         Run:  pip install -r artpm_agent\requirements.txt
    pause & exit /b 1
)

for %%A in ("%PYTHON_CMD%") do set "PY_VER=%%~dpA"
echo   Python: %PYTHON_CMD%
call "%PYTHON_CMD%" --version

REM ---- 环境准备 ----
if not exist .env copy .env.example .env >nul 2>&1
if not exist data mkdir data >nul 2>&1
if not exist artpm_agent\logs mkdir artpm_agent\logs >nul 2>&1

REM ---- 关键：设置 PYTHONPATH 让多页面导入不报错 ----
set "PYTHONPATH=%~dp0"

echo.
echo   http://localhost:8501
echo.

"%PYTHON_CMD%" -m streamlit run artpm_agent\app.py --server.address 127.0.0.1 --server.port 8501

pause
