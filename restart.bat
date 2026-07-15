@echo off
REM ArtPM Agent - Windows 重启脚本

echo 🔄 重启 ArtPM Agent...
echo.

REM 停止现有进程
echo 停止现有进程...
taskkill /F /IM streamlit.exe >nul 2>&1
if errorlevel 1 (
    echo   没有找到运行中的进程
) else (
    echo   ✅ 进程已停止
)
timeout /t 2 /nobreak >nul

REM 检查环境
echo.
echo 检查环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 错误: 未找到Python
    pause
    exit /b 1
)
echo   ✅ Python 环境正常

REM 检查项目安装
python -c "import artpm_agent" >nul 2>&1
if errorlevel 1 (
    echo   ⚠️  项目未安装，正在安装...
    pip install -e . --quiet
)
echo   ✅ 项目已安装 开发模式

REM 检查配置
if not exist .env (
    echo   ⚠️  创建 .env 配置...
    copy .env.example .env >nul
)
echo   ✅ 配置文件存在

REM 检查数据目录
if not exist data mkdir data
if not exist artpm_agent\logs mkdir artpm_agent\logs
echo   ✅ 数据目录已就绪

REM 启动应用
echo.
echo 🚀 启动应用...
echo   访问地址: http://localhost:8501
echo   按 Ctrl+C 停止应用
echo.

python -m streamlit run artpm_agent\app.py --server.address 127.0.0.1 --server.port 8501

pause
