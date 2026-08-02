@echo off
setlocal
chcp 65001 >nul 2>&1
REM ArtPM Agent - Windows restart entry point

echo   Restarting ArtPM Agent...
if not defined ARTPM_API_PORT set "ARTPM_API_PORT=8765"

REM Stop only the listener that identifies itself as this API service.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$port=[int]$env:ARTPM_API_PORT; $health='http://127.0.0.1:'+$port+'/health';" ^
  "$isArtPm=$false; try { $payload=Invoke-RestMethod -Uri $health -TimeoutSec 2; $isArtPm=($payload.service -eq 'artpm-agent-api') } catch {}" ^
  "if ($isArtPm) { Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Write-Host ('  Stopping API PID '+$_); Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } }"

call "%~dp0start.bat"
exit /b %ERRORLEVEL%
