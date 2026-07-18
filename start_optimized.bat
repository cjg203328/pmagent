@echo off
REM ArtPM Agent 一键启动脚本（带优化功能）
REM 版本: v0.3.0

echo ========================================
echo ArtPM Agent 启动脚本 v0.3.0
echo ========================================
echo.

REM 检查 Python 环境
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

REM 检查是否首次运行
if not exist ".env" (
    echo [首次运行] 正在初始化配置...
    copy .env.example .env
    echo.
    echo [提示] 已创建 .env 配置文件
    echo [提示] 如需使用 AI 功能，请编辑 .env 填写 API Key
    echo.
)

REM 检查依赖
echo [检查] 正在检查依赖...
python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装依赖（首次运行需要几分钟）...
    python -m pip install -e . -q
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
    echo [完成] 依赖安装成功
)

REM 检查优化功能
echo.
echo [配置] 检查优化功能状态...
python -c "import os; from dotenv import load_dotenv; load_dotenv(); opts = ['CONNECTION_POOLING', 'LAZY_SKILL_LOADING', 'STREAMING_PROGRESS', 'FRIENDLY_ERRORS', 'ADAPTIVE_VECTORS']; [print(f'  {opt}: {'启用' if os.getenv(f'ENABLE_{opt}', 'false').lower() == 'true' else '禁用'}') for opt in opts]" 2>nul
if errorlevel 1 (
    echo   [跳过] 无法检测优化状态（dotenv 未安装）
)

echo.
echo [启动] 正在启动 ArtPM Agent...
echo [提示] 启动后自动打开浏览器访问 http://localhost:8501
echo [提示] 按 Ctrl+C 停止服务
echo.

REM 启动 Streamlit
python -m streamlit run artpm_agent/app.py --server.address 127.0.0.1 --server.headless false

pause
