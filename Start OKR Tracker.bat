@echo off
rem Start the POLE OKR Tracker on Windows. Double-click this file.
rem
rem The first run installs Flask and waitress into a private .venv; later runs
rem start immediately. Arguments are passed through, so a shortcut to
rem   "Start OKR Tracker.bat" --port 9000
rem works as well.
setlocal
cd /d "%~dp0"

set "PYTHON="

rem The py launcher ships with python.org installs and picks the newest runtime.
where py >nul 2>&1 && (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PYTHON=py -3"
)

if not defined PYTHON (
  where python >nul 2>&1 && (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PYTHON=python"
  )
)

if not defined PYTHON (
  echo.
  echo POLE OKR Tracker needs Python 3.9 or newer, and none was found.
  echo.
  echo Install it from https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" in the installer, then run this file again.
  echo.
  pause
  exit /b 1
)

%PYTHON% run.py %*
if errorlevel 1 (
  echo.
  echo The tracker exited with an error.
  pause
  exit /b 1
)
