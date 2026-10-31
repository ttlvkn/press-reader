@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYCMD=python"
where python >nul 2>nul || set "PYCMD=py -3"
%PYCMD% -c "import requests,feedparser,jinja2,yaml" 2>nul || (
  echo Installing dependencies...
  %PYCMD% -m pip install -r requirements.txt
)
echo.
echo ================ FETCH / TRANSLATE / BUILD ================
%PYCMD% run.py all
echo.
echo Done. Run preview.bat to read it in your browser.
pause
