"""Survive being run without a console, and still be diagnosable.

The portable Windows launcher starts the app with ``pythonw.exe`` so no black
console window appears. That has a sharp edge: under ``pythonw`` there is no
standard output at all -- ``sys.stdout`` and ``sys.stderr`` are ``None``, and
an ordinary ``print()`` raises ``AttributeError``. A crash would then be
completely silent, which is the worst possible behaviour for an app whose whole
promise is "double-click it".

So when there is no console:

* output is buffered from the first line, then written to a log file as soon as
  the data directory is known;
* an unhandled error also raises a native message box, so the user sees
  something actionable instead of nothing at all.
"""

from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

LOG_NAME = "launcher.log"

#: Keep the log from growing without bound across restarts.
MAX_LOG_BYTES = 512 * 1024

_buffer: io.StringIO | None = None
_log_path: Path | None = None


def headless() -> bool:
    """True when this process has no usable console (pythonw.exe)."""
    return sys.stdout is None or sys.stderr is None


def capture() -> None:
    """Start collecting output, before the log file's location is known.

    Called first thing so that nothing between startup and settings resolution
    can crash on a missing stdout.
    """
    global _buffer
    if not headless() or _buffer is not None:
        return
    _buffer = io.StringIO()
    sys.stdout = _buffer
    sys.stderr = _buffer


def redirect(data_dir: Path) -> None:
    """Send captured and future output to ``data_dir/launcher.log``."""
    global _log_path
    if _buffer is None:
        return

    path = data_dir / LOG_NAME
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.replace(path.with_suffix(".log.old"))
        handle = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return  # A read-only or unreachable folder: keep buffering in memory.

    handle.write(_buffer.getvalue())
    sys.stdout = handle
    sys.stderr = handle
    _log_path = path


def log_location() -> Path | None:
    """Where the log ended up, if anywhere."""
    return _log_path


def message_box(title: str, text: str) -> None:
    """Show a native Windows message box. A no-op everywhere else."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # MB_OK | MB_ICONERROR | MB_SETFOREGROUND
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 | 0x10000)
    except Exception:  # pragma: no cover - never let reporting hide the error
        pass


def report(error: BaseException) -> None:
    """Record an unhandled error, and surface it if there is no console."""
    print("".join(traceback.format_exception(type(error), error, error.__traceback__)))

    if not headless():
        return

    where = f"\n\nThe full details are in:\n{_log_path}" if _log_path else ""
    message_box(
        "POLE OKR Tracker could not start",
        f"{type(error).__name__}: {error}{where}\n\n"
        'Running "OKR Tracker (debug).bat" shows the same error in a window.',
    )
