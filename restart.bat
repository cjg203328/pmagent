@echo off
set PYTHONUTF8=1
title ArtPM - Restart

echo ===============================================================
echo                       ArtPM Restart
echo ===============================================================
echo.

echo [*] Stopping the service on port 8501...
powershell -NoProfile -Command "$connection = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($connection) { Stop-Process -Id $connection.OwningProcess -Force; Write-Output '[OK] Service stopped' } else { Write-Output '[OK] No running service found' }"

echo [*] Keeping databases and user configuration unchanged.
echo.
echo [*] Starting ArtPM...
echo Open in browser: http://localhost:8501
echo Press Ctrl+C to stop
echo ===============================================================

python -m streamlit run artpm_agent\app.py --server.headless true --server.address 127.0.0.1

pause
