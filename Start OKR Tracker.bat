@echo off
rem Start the POLE OKR Tracker on Windows. Double-click this file.
rem
rem This is the "run from source" launcher and it needs Python installed. For a
rem version that needs nothing at all, use OKR Tracker.exe -- see README.md,
rem "One icon, no Python".
rem
rem The first run installs Flask and waitress into a private .venv; later runs
rem start immediately. Arguments are passed through, so a shortcut to
rem   "Start OKR Tracker.bat" --port 9000
rem works as well.
setlocal

rem pushd, not "cd /d": when this file sits on a network share the folder is a
rem UNC path (\\server\share\...), which CMD refuses as a working directory and
rem silently drops you in C:\Windows. pushd maps a temporary drive letter to it
rem instead, so the launcher works from a mapped drive and a UNC path alike.
pushd "%~dp0" 2>nul
if errorlevel 1 goto :no_folder
if /i "%CD%"=="%WINDIR%" goto :no_folder

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

if not defined PYTHON goto :no_python

%PYTHON% run.py %*
if errorlevel 1 goto :failed

popd
exit /b 0

:no_folder
echo.
echo Could not open the folder this file is in:
echo   %~dp0
echo.
echo If it is on a network drive, map it to a drive letter first
echo (File Explorer: right-click the folder, "Map network drive"),
echo then run this file from the mapped drive.
echo.
pause
exit /b 1

:no_python
echo.
echo POLE OKR Tracker needs Python 3.9 or newer, and none was found.
echo.
echo Either install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" in the installer), or use
echo OKR Tracker.exe, which needs nothing installed.
echo.
popd
pause
exit /b 1

:failed
echo.
echo The tracker exited with an error.
popd
pause
exit /b 1
