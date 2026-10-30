@echo off
chcp 65001 >nul
echo ======================================================================
echo   Update & Push SUNLOGIS ISO Audit System to GitHub
echo   Repository: https://github.com/wdejneeranat/iso-audit-system.git
echo ======================================================================
echo.

set /p COMMIT_MSG="ระบุข้อความการอัปเดต (กด Enter เพื่อใช้ค่าเริ่มต้น 'Update ISO Audit System'): "
if "%COMMIT_MSG%"=="" set COMMIT_MSG=Update ISO Audit System (%date% %time%)

git remote get-url origin >nul 2>&1
if errorlevel 1 (
    git remote add origin https://github.com/wdejneeranat/iso-audit-system.git
)

git add .
git commit -m "%COMMIT_MSG%"
git push -u origin main

echo.
echo ======================================================================
echo   อัปเดตโค้ดขึ้น GitHub สำเร็จเรียบร้อย!
echo ======================================================================
pause
