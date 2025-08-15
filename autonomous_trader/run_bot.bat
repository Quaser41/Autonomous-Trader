@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Auto-Trader

:: Jump to this script’s directory
cd /d "%~dp0"

:: Update repository to latest main branch
git fetch origin
git reset --hard origin/main

:: Ensure a logs directory
if not exist logs mkdir logs

:: Create timestamped log file (YYYY-MM-DD_HH-MM-SS)
for /f "tokens=1-3 delims=/ " %%a in ("%date%") do set ymd=%%c-%%a-%%b
set t=%time: =0%
set t=%t::=-%
for /f "tokens=1,2 delims=." %%h in ("%t%") do set hhmmss=%%h
set "LOGFILE=logs\bot_%ymd%_%hhmmss%.log"

echo ===============================================
echo Starting Autonomous Trader (logs -> %LOGFILE%)
echo Folder: %cd%
echo ===============================================

:: Verify Python
where python >nul 2>nul || (
  echo [ERROR] Python not found on PATH.
  pause
  exit /b 1
)

:: Create venv if needed
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv || (
    echo [ERROR] Failed to create virtual environment.
    pause & exit /b 1
  )
)

:: Activate venv
call .venv\Scripts\activate.bat || (
  echo [ERROR] Failed to activate virtual environment.
  pause & exit /b 1
)

:: Upgrade pip and install requirements
echo Upgrading pip...
python -m pip install --upgrade pip
if exist requirements.txt (
  echo Installing requirements...
  python -m pip install -r requirements.txt
) else (
  echo [WARN] requirements.txt not found. Skipping dependency install.
)

:: Force unbuffered output and mirror to log
set PYTHONUNBUFFERED=1
echo Launching bot...
powershell -NoProfile -ExecutionPolicy Bypass ^
  -Command "python -u main.py 2>&1 | Tee-Object -FilePath '%LOGFILE%'; exit \$LASTEXITCODE"

set EXITCODE=%ERRORLEVEL%
echo.
echo Bot exited with code %EXITCODE%
echo Logs saved to: %LOGFILE%
echo.
pause
endlocal
