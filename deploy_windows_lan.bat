@echo off
chcp 65001 >nul
echo ======================================================================
echo   SUNLOGIS ISO Audit System - LAN / Production Deployment
echo ======================================================================

REM Open Windows Firewall Port 8000 (if running as Administrator)
netsh advfirewall firewall show rule name="ISO_Audit_Port_8000" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Opening Windows Firewall TCP Port 8000 for LAN access...
    netsh advfirewall firewall add rule name="ISO_Audit_Port_8000" dir=in action=allow protocol=TCP localport=8000 >nul 2>&1
)

echo.
echo [INFO] เครื่องอื่นๆ ในวงแลน (LAN/Wi-Fi) สามารถเข้าใช้งานผ่าน IP ต่อไปนี้:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    echo   --^> http:%%a:8000
)
echo   --^> http://localhost:8000
echo ======================================================================
echo.

python -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
pause
