@echo off
chcp 65001 >nul
echo ======================================================================
echo   Push SUNLOGIS ISO Audit System to GitHub
echo ======================================================================
echo.
set /p REPO_URL="กรุณาวาง URL ของ GitHub Repository (เช่น https://github.com/yourname/iso-audit-system.git): "

if "%REPO_URL%"=="" (
    echo [ERROR] กรุณาระบุ GitHub Repository URL
    pause
    exit /b 1
)

git init
git branch -M main
git add .
git commit -m "Initial commit: SUNLOGIS ISO Audit & Document Control System"
git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%
git push -u origin main

echo.
echo ======================================================================
echo   อัปโหลดโปรเจกต์ขึ้น GitHub สำเร็จเรียบร้อย!
echo ======================================================================
pause
