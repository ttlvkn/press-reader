@echo off
chcp 65001 >nul
cd /d "%~dp0"
git add -A
git commit -m "update" 2>nul
git push
echo.
echo ============================================
echo Pushed. Check the Actions tab on GitHub.
echo ============================================
pause
