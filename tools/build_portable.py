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

LAUNCHER = """@echo off
rem Start the POLE OKR Tracker. Double-click this file.
rem
rem Nothing needs installing: the python folder beside this file is a complete
rem self-contained Python. Your OKRs are stored in the data folder, or wherever
rem okr-tracker.ini points.
setlocal

rem pushd rather than "cd /d": this folder is often on a network share, and CMD
rem refuses a UNC path as a working directory, silently landing in C:\\Windows.
pushd "%~dp0" 2>nul
if errorlevel 1 goto :no_folder

rem pythonw.exe runs without a console window. Errors go to the log file that
rem the app names in data\\launcher.log.
start "" "python\\pythonw.exe" -m okr_tracker %*
popd
exit /b 0

:no_folder
echo.
echo Could not open the folder this file is in:
echo   %~dp0
echo.
echo Copy the whole OKR Tracker folder to your computer and run it from there.
echo.
pause
exit /b 1
"""

#: A console version, for when something goes wrong and output is wanted.
DIAGNOSTIC = """@echo off
rem Same as the normal launcher, but keeps a console window open so you can
rem read what happens. Use this if the tracker does not start.
setlocal
pushd "%~dp0" 2>nul
if errorlevel 1 (
  echo Could not open "%~dp0".
  pause
  exit /b 1
)
"python\\python.exe" -m okr_tracker %*
echo.
echo ---------------------------------------------
echo The tracker stopped. The message above says why.
popd
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

README = """POLE OKR Tracker - portable
===========================

Double-click "OKR Tracker.bat" (or the "OKR Tracker" shortcut). Your browser
opens on the tracker. That is all: nothing is installed, and you do not need
administrator rights or Python.

The first person to open it gets a sample workspace. Sign in as
"POLE administrator" with the password shown on the entry screen, then change
it under Administration > Working profiles.

What is in this folder
----------------------

  OKR Tracker.bat          start the tracker
  OKR Tracker (debug).bat  same, but keeps a window open showing any error
  okr-tracker.ini          settings - where data is kept, which port to use
  data\\                    your workspace and its backups
  python\\                  a self-contained Python, used only by this app
  okr_tracker\\             the application

Sharing with your team
----------------------

Put this whole folder on a shared drive and have everyone run it from there.
Because okr-tracker.ini keeps the data inside the folder, everyone sees and
edits the SAME workspace. Simultaneous edits are safe: saves are serialised,
and if two people save at once the second is told rather than silently
overwriting the first.

Everyone's browser rechecks for other people's changes every 15 seconds.

Backups
-------

Every save copies the previous workspace into data\\backups (the last 30 are
kept). To take your own copy, use "Data & files" in the app, or just copy
data\\workspace.json somewhere safe.

If something goes wrong
-----------------------

Run "OKR Tracker (debug).bat" - it keeps the window open and shows the error.

The most common problems:

  "port 8765 is already in use"   Someone else's program has the port. Change
                                  "port" in okr-tracker.ini to 8770 and retry.

  Nothing happens on double-click Your antivirus may be blocking .bat files
                                  from a network drive. Copy the folder to your
                                  own Desktop and run it from there.
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


def write_support_files(bundle: Path) -> None:
    (bundle / "OKR Tracker.bat").write_text(LAUNCHER, encoding="utf-8")
    (bundle / "OKR Tracker (debug).bat").write_text(DIAGNOSTIC, encoding="utf-8")
    (bundle / "okr-tracker.ini").write_text(CONFIG, encoding="utf-8")
    (bundle / "READ ME FIRST.txt").write_text(README, encoding="utf-8")
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
