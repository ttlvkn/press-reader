@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYCMD=python"
where python >nul 2>nul || set "PYCMD=py -3"
%PYCMD% run.py check
pause
