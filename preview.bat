@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYCMD=python"
where python >nul 2>nul || set "PYCMD=py -3"
echo Starting preview at http://127.0.0.1:8000/
echo Close this window to stop the server.
%PYCMD% run.py serve --open
pause
