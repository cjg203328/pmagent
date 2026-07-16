@echo off
chcp 65001 >nul 2>&1
REM ArtPM Agent - Windows restart entry point

echo   Restarting ArtPM Agent...
call "%~dp0start.bat"
