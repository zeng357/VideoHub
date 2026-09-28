@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ===== Video Downloader Launcher =====
set "PYTHON="

rem Step 1: prefer Python 3.10 with dependencies installed
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"

rem Step 2: fallback to py launcher
if not defined PYTHON (
  py -3.10 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PYTHON=py -3.10"
)

rem Step 3: fallback to python on PATH with required modules
if not defined PYTHON (
  python -c "import tkinter, requests, DrissionPage" >nul 2>nul
  if not errorlevel 1 set "PYTHON=python"
)

if not defined PYTHON (
  echo [ERROR] Python environment with dependencies was not found.
  echo Please install dependencies first:
  echo   pip install DrissionPage requests aiohttp pycryptodome
  pause
  exit /b 1
)

echo Using Python: %PYTHON%
%PYTHON% gui.py
if errorlevel 1 (
  echo.
  echo [ERROR] The program exited with an error. See messages above.
  pause
)