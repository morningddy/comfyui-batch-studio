@echo off
cd /d "%~dp0"
set PY=python
%PY% --version >nul 2>nul
if errorlevel 1 set PY=py -3
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.8+ and add it to PATH.
  pause
  exit /b 1
)
"%PY%" menu.py img
