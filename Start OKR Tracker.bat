@echo off
rem Start the POLE OKR Tracker from source. Double-click this file.
rem
rem This launcher needs Python installed. For a version that needs nothing at
rem all, use the portable folder -- see README.md, "Install".
rem
rem The first run installs Flask and waitress into a private .venv; later runs
rem start immediately. Arguments are passed through, so a shortcut to
rem   "Start OKR Tracker.bat" --port 9000
rem works as well.
setlocal

rem Deliberately no "cd" or "pushd" here. When this folder is on a network
rem share, CMD cannot use its UNC path (\\server\share\...) as a working
rem directory: it prints "UNC paths are not supported. Defaulting to Windows
rem directory." before this file runs its first line, and any relative path
rem would then resolve under C:\Windows. So every path below is absolute, built
rem from %~dp0 (this file's own folder), and the app locates its settings and
rem data from its own location rather than from the working directory. That
rem makes the working directory irrelevant, whatever CMD chose.
set "APP=%~dp0"

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

%PYTHON% "%APP%run.py" %*
if errorlevel 1 goto :failed
exit /b 0

:no_python
echo.
echo POLE OKR Tracker needs Python 3.9 or newer, and none was found.
echo.
echo Either install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" in the installer), or use the portable
echo folder, which needs nothing installed.
echo.
pause
exit /b 1

:failed
echo.
echo The tracker exited with an error.
pause
exit /b 1
