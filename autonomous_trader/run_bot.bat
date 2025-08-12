@echo off
setlocal EnableExtensions EnableDelayedExpansion

:: 1) Jump to this folder
cd /d "%~dp0"

:: 2) Make a logs dir
if not exist logs mkdir logs

:: 3) Timestamped log name (sanitize colons in %time%)
for /f "tokens=1-3 delims=/ " %%a in ("%date%") do (
  set ymd=%%c-%%a-%%b
)
set t=%time: =0%
set t=%t::=-%
for /f "tokens=1,2 delims=." %%h in ("%t%") do set hhmmss=%%h
set LOGFILE=logs\bot_%ymd%_%hhmmss%.log

echo ===============================================
echo Starting Autonomous Trader (logs -> %LOGFILE%)
echo Folder: %cd%
echo ===============================================

:: 4) Ensure Python exists
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found on PATH.
  echo Install Python 3.10+ from python.org and re-run.
  pause
  exit /b 1
)

:: 5) Create venv if missing
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
  )
)

:: 6) Activate venv
call .venv\Scripts\activate
if errorlevel 1 (
  echo [ERROR] Failed to activate virtual environment.
  pause
  exit /b 1
)

:: 7) Upgrade pip and install deps (safe retry)
echo Upgrading pip...
python -m pip install --upgrade pip

if exist requirements.txt (
  echo Installing requirements...
  python -m pip install -r requirements.txt
) else (
  echo [WARN] requirements.txt not found. Skipping dependency install.
)

:: 8) Run the bot, mirror output to console + log using PowerShell Tee-Object
echo Launching bot...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { python 'main.py' 2>&1 | Tee-Object -FilePath '%LOGFILE%'; exit $LASTEXITCODE } catch { $_ | Out-String | Tee-Object -FilePath '%LOGFILE%'; exit 1 }"

set EXITCODE=%ERRORLEVEL%
echo.
echo Bot exited with code %EXITCODE%
echo Logs saved to: %LOGFILE%
echo.
pause
endlocal
