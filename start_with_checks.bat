@echo off
chcp 65001 >nul
echo.
echo ====================================================================
echo ArtPM Agent - 启动前配置检查
echo ====================================================================
echo.

REM 运行配置检查
python -m artpm_agent.tools.check_config
set CHECK_RESULT=%ERRORLEVEL%

echo.
if %CHECK_RESULT% NEQ 0 (
    echo ❌ 配置检查未通过，建议修复错误后再启动
    set /p CONTINUE="是否仍要继续启动? (y/N): "
    if /i NOT "%CONTINUE%"=="y" (
        echo 已取消启动
        exit /b 1
    )
)

echo.
echo ====================================================================
echo 启动 ArtPM Agent
echo ====================================================================
echo.

REM 启动 Streamlit 应用
streamlit run artpm_agent/app.py

exit /b %ERRORLEVEL%
