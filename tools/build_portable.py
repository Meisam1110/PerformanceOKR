"""Assemble a portable Windows folder that needs nothing installed.

The result is a directory (and a zip of it) holding python.org's official
Windows *embeddable* Python, the tracker, and its two dependencies. Copy it
anywhere -- a USB stick, a network share -- and double-click the launcher. No
installer, no admin rights, no PATH changes, and no custom-built executable for
corporate antivirus to distrust: every binary in it is a signed python.org
file.

Run it on any machine with a network connection; it does not have to be
Windows, because it only downloads and unpacks files:

    python tools/build_portable.py
    python tools/build_portable.py --python-version 3.11.9 --output dist

GitHub Actions runs this on every tagged release (.github/workflows/portable.yml)
and publishes the zip, so nobody has to build it by hand.
"""

from __future__ import annotations

import argparse
import compileall
import io
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Embeddable builds exist for 3.5+; this one is widely deployed and supported.
DEFAULT_PYTHON = "3.11.9"
DEFAULT_ARCH = "amd64"

BUNDLE_NAME = "OKR-Tracker-Portable"

#: Copied verbatim into the bundle.
APP_FILES = ("requirements.txt", "README.md")

GET_PIP = "https://bootstrap.pypa.io/get-pip.py"

#: The icon people actually double-click. A .vbs runs under wscript.exe, so no
#: console is ever created -- which also means Windows never prints its
#: "UNC paths are not supported. Defaulting to Windows directory." banner, the
#: one CMD emits before a .bat on a network share runs its first line.
VBS_LAUNCHER = r"""' Start the POLE OKR Tracker. Double-click this file.
'
' Nothing needs installing: the python folder beside this file is a complete
' self-contained Python. Your OKRs are stored in the data folder, or wherever
' okr-tracker.ini points.
Option Explicit

Dim shell, fso, appDir, pythonw, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' This file's own folder. It works as a UNC path (\\server\share\...) exactly
' as it does on a local disk, and nothing here depends on the working
' directory, so Windows never needs one it cannot use.
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = fso.BuildPath(fso.BuildPath(appDir, "python"), "pythonw.exe")

If Not fso.FileExists(pythonw) Then
  MsgBox "This folder is incomplete. Expected to find:" & vbCrLf & vbCrLf & _
         pythonw & vbCrLf & vbCrLf & _
         "Unzip the whole OKR Tracker folder, keeping it together, and run " & _
         "this file from inside it.", vbCritical, "POLE OKR Tracker"
  WScript.Quit 1
End If

' Chr(34) is a double quote: the path may contain spaces.
command = Chr(34) & pythonw & Chr(34) & " -m okr_tracker"

' 0 = no window at all, False = do not wait. The app opens your browser itself.
shell.Run command, 0, False
"""

LAUNCHER = r"""@echo off
rem Start the POLE OKR Tracker.
rem
rem Prefer "OKR Tracker.vbs" -- it starts with no console window at all. Use
rem this one if your IT policy blocks .vbs files.
rem
rem Nothing needs installing: the python folder beside this file is a complete
rem self-contained Python. Your OKRs are stored in the data folder, or wherever
rem okr-tracker.ini points.
setlocal

rem Deliberately no "cd" and no "pushd". On a network share this folder is a
rem UNC path (\\server\share\...), which CMD will not accept as a working
rem directory: it prints "UNC paths are not supported. Defaulting to Windows
rem directory." before this file runs its first line, and any relative path
rem would then resolve under C:\Windows. Every path below is absolute, built
rem from %~dp0 (this file's own folder), and the app locates its settings and
rem data from its own location rather than the working directory -- so whatever
rem CMD picked simply does not matter.
set "APP=%~dp0"

if not exist "%APP%python\pythonw.exe" goto :incomplete

rem pythonw.exe runs without a console window. Anything that goes wrong is
rem written to data\launcher.log and shown in a message box.
start "" "%APP%python\pythonw.exe" -m okr_tracker %*
exit /b 0

:incomplete
echo.
echo This folder is incomplete - python\pythonw.exe is missing.
echo.
echo Unzip the whole OKR Tracker folder, keeping it together, and run this
echo file from inside it.
echo.
pause
exit /b 1
"""

#: A console version, for when something goes wrong and the output is wanted.
DIAGNOSTIC = r"""@echo off
rem Same as the normal launcher, but keeps a console window open so you can
rem read what happens. Use this if the tracker does not start.
rem
rem If Windows printed "UNC paths are not supported" above, that is CMD talking
rem about network paths before this file ran. It is harmless here: every path
rem below is absolute, so the tracker does not care which working directory
rem CMD settled on.
setlocal
set "APP=%~dp0"

echo Application folder: %APP%
echo.

if not exist "%APP%python\python.exe" (
  echo This folder is incomplete - python\python.exe is missing.
  echo Unzip the whole OKR Tracker folder, keeping it together.
  pause
  exit /b 1
)

"%APP%python\python.exe" -m okr_tracker %*
echo.
echo ---------------------------------------------
echo The tracker stopped. The message above says why.
pause
"""

CONFIG = """; POLE OKR Tracker settings.
;
; This file travels with the application folder. Relative paths are measured
; from this folder, so the whole thing stays portable.

[tracker]

; Where the workspace and its backups are kept.
;
; "data" keeps everything inside this folder, so putting the folder on a shared
; drive gives the whole team ONE shared workspace -- which is usually what you
; want for team OKRs. Concurrent edits from different machines are safe: saves
; are serialised with a lock file, and a save that loses a race is reported in
; the app rather than overwriting someone.
;
; To give each person their OWN private workspace instead, comment this line
; out; the app then stores data under each user's Windows profile.
data_dir = data

; The local address the app serves on. Change the port only if 8765 is taken.
host = 127.0.0.1
port = 8765

; How often each browser checks for other people's changes, in seconds.
poll_seconds = 15

; How many previous versions of the workspace to keep in data\\backups.
backup_count = 30

; Uncomment to serve a workspace that nobody can change.
; read_only = true
"""

README = r"""POLE OKR Tracker
================

Double-click "OKR Tracker.vbs". Your browser opens on the tracker.

That is all. Nothing is installed, and you do not need administrator rights
or Python -- everything this app needs is inside this folder.

The first person to open it gets a sample workspace. Sign in as
"POLE administrator" with the password shown on the entry screen, then change
it under Administration > Working profiles.


What is in this folder
----------------------

  OKR Tracker.vbs          start the tracker   <-- use this one
  OKR Tracker.bat          same, if your IT policy blocks .vbs files
  OKR Tracker (debug).bat  same, but keeps a window open showing any error
  okr-tracker.ini          settings - where data is kept, which port to use
  data\                    your workspace and its backups
  python\                  a self-contained Python, used only by this app
  okr_tracker\             the application


Sharing with your team
----------------------

Put this whole folder on a shared drive and have everyone run it from there.
Because okr-tracker.ini keeps the data inside the folder, everyone sees and
edits the SAME workspace, with nothing to set up.

Simultaneous edits are safe. Saves are serialised with a lock file that works
across machines, and if two people save at once the second is told rather than
silently overwriting the first. Each browser picks up other people's changes
within about 15 seconds.

To give each person their own private workspace instead, open okr-tracker.ini
in Notepad and put a ";" in front of the "data_dir = data" line.


"UNC paths are not supported" -- what that means
------------------------------------------------

If you use "OKR Tracker.bat" from a network drive, Windows may print:

    CMD.EXE was started with the above path as the current directory.
    UNC paths are not supported.  Defaulting to Windows directory.

That is Windows talking about network paths (\\server\share\...) before the
launcher runs a single line, and it is harmless: the tracker uses absolute
paths throughout and does not care which folder Windows settled on. It starts
normally.

To not see the message at all, use "OKR Tracker.vbs", which starts without a
console window.


If something goes wrong
-----------------------

Run "OKR Tracker (debug).bat". It keeps the window open and shows the error.
There is also a log in data\launcher.log.

  Nothing happens at all          Your antivirus may be blocking files on a
                                  network drive. Copy this folder to your own
                                  Desktop and run it from there.

  "port 8765 is already in use"   Another program has the port. Open
                                  okr-tracker.ini in Notepad and change
                                  "port = 8765" to "port = 8770".

  The browser does not open       Open it yourself and go to:
                                  http://127.0.0.1:8765


Backups
-------

Every save copies the previous workspace into data\backups (the last 30 are
kept). To take your own copy, use "Data & files" inside the app, or just copy
data\workspace.json somewhere safe.
"""


def log(message: str) -> None:
    print(f"build_portable: {message}", flush=True)


def fetch(url: str) -> bytes:
    log(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=180) as response:
        return response.read()


def embeddable_url(version: str, arch: str) -> str:
    return (
        f"https://www.python.org/ftp/python/{version}/"
        f"python-{version}-embed-{arch}.zip"
    )


def unpack_python(version: str, arch: str, target: Path) -> None:
    """Unpack the embeddable distribution and let it import our code."""
    payload = fetch(embeddable_url(version, arch))
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(target)

    # The embeddable build ships with `import site` disabled, which also
    # disables site-packages. Enabling it is what lets pip-installed
    # dependencies and the bundled application be imported at all.
    for pth in target.glob("python*._pth"):
        lines = pth.read_text(encoding="utf-8").splitlines()
        patched = [line for line in lines if line.strip() != "#import site"]
        if "import site" not in patched:
            patched.append("import site")
        # The bundle root, so `python\python.exe -m okr_tracker` finds the app.
        if ".." not in patched:
            patched.insert(0, "..")
        pth.write_text("\n".join(patched) + "\n", encoding="utf-8")
        log(f"enabled site imports in {pth.name}")


def install_dependencies(python_dir: Path, requirements: Path) -> None:
    """Install Flask and waitress into the embedded Python's site-packages.

    The embeddable build has no pip, so get-pip.py bootstraps one. pip is then
    installed into the bundle as well, which costs a little space and makes the
    folder repairable later without a network round trip.
    """
    interpreter = python_dir / "python.exe"
    site_packages = python_dir / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32" and interpreter.exists():
        get_pip = python_dir / "get-pip.py"
        get_pip.write_bytes(fetch(GET_PIP))
        subprocess.run(
            [str(interpreter), str(get_pip), "--no-warn-script-location"],
            check=True,
        )
        get_pip.unlink()
        subprocess.run(
            [
                str(interpreter), "-m", "pip", "install",
                "--no-warn-script-location", "-r", str(requirements),
            ],
            check=True,
        )
        return

    # Building the Windows bundle from Linux or macOS: we cannot run the
    # bundled python.exe, so install the dependencies with this interpreter's
    # pip straight into the bundle. Flask and waitress are pure Python, so the
    # result is identical -- which is the whole reason those two were chosen.
    log("host is not Windows: installing pure-Python dependencies with local pip")
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--no-compile", "--target", str(site_packages),
            "-r", str(requirements),
        ],
        check=True,
    )


def copy_application(bundle: Path) -> None:
    package = bundle / "okr_tracker"
    if package.exists():
        shutil.rmtree(package)
    shutil.copytree(
        ROOT / "okr_tracker",
        package,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for name in APP_FILES:
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, bundle / name)
    log(f"copied the application ({sum(1 for _ in package.rglob('*'))} files)")


def write_windows_text(path: Path, text: str) -> None:
    """Write a file with CRLF line endings, the way Windows wants them.

    Batch and VBScript files are read by Windows itself rather than by Python,
    and LF-only line endings make some Windows versions mis-parse them. Written
    with an explicit open() rather than write_text(newline=...) so the build
    script still runs on Python 3.9.
    """
    with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(text)


def write_support_files(bundle: Path) -> None:
    write_windows_text(bundle / "OKR Tracker.vbs", VBS_LAUNCHER)
    write_windows_text(bundle / "OKR Tracker.bat", LAUNCHER)
    write_windows_text(bundle / "OKR Tracker (debug).bat", DIAGNOSTIC)
    write_windows_text(bundle / "okr-tracker.ini", CONFIG)
    write_windows_text(bundle / "READ ME FIRST.txt", README)
    (bundle / "data").mkdir(exist_ok=True)
    (bundle / "data" / ".gitkeep").write_text("", encoding="utf-8")


def make_zip(bundle: Path) -> Path:
    archive = bundle.with_suffix(".zip")
    if archive.exists():
        archive.unlink()
    log(f"compressing to {archive.name}")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for path in sorted(bundle.rglob("*")):
            if "__pycache__" in path.parts:
                continue
            out.write(path, Path(bundle.name) / path.relative_to(bundle))
    return archive


def build(version: str, arch: str, output: Path, compress: bool) -> Path:
    bundle = output / BUNDLE_NAME
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    unpack_python(version, arch, bundle / "python")
    install_dependencies(bundle / "python", ROOT / "requirements.txt")
    copy_application(bundle)
    write_support_files(bundle)

    # Byte-compiling shaves a second off the first launch from a slow share.
    compileall.compile_dir(str(bundle / "okr_tracker"), quiet=2, force=True)

    size = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
    log(f"bundle ready: {bundle} ({size / 1_048_576:.1f} MB)")

    if compress:
        archive = make_zip(bundle)
        log(f"archive: {archive} ({archive.stat().st_size / 1_048_576:.1f} MB)")
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-version", default=DEFAULT_PYTHON)
    parser.add_argument("--arch", default=DEFAULT_ARCH, choices=["amd64", "win32", "arm64"])
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--no-zip", action="store_true", help="leave the folder uncompressed")
    args = parser.parse_args()

    try:
        build(args.python_version, args.arch, args.output, compress=not args.no_zip)
    except (OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"build_portable: failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
