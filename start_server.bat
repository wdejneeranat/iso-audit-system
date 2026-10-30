@echo off
chcp 65001 >nul
echo ================================================================
echo   Starting SUNLOGIS ISO Audit & Document Control System...
echo   URL: http://localhost:8000
echo ================================================================
start "" "http://localhost:8000"
python -m uvicorn app:app --host 0.0.0.0 --port 8000
pause
