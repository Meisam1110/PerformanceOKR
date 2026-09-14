# PyInstaller spec: build a single-file executable of the OKR Tracker.
#
#     python -m pip install -r requirements.txt pyinstaller
#     python -m PyInstaller packaging/okr-tracker.spec
#
# The result lands in dist/ as one file per platform (okr-tracker,
# okr-tracker.exe, or the macOS binary). Run it and the app opens in the
# default browser; the workspace is stored in the user's data directory, not
# inside the executable, so replacing the binary never touches the data.
#
# PyInstaller must run on the platform you are targeting -- it does not
# cross-compile.

from pathlib import Path

# SPECPATH is injected by PyInstaller and points at this file's directory.
ROOT = Path(SPECPATH).parent
PACKAGE = ROOT / "okr_tracker"

# The front end is data, not importable code, so it has to be listed here or
# the bundled app would start with no interface.
datas = [
    (str(PACKAGE / "templates"), "okr_tracker/templates"),
    (str(PACKAGE / "static"), "okr_tracker/static"),
]

analysis = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["waitress", "waitress.server"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "playwright", "pytest"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="okr-tracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
