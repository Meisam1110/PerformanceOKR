"""POLE OKR Tracker - a browser OKR workspace with a Python backend."""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__", "create_app", "Settings"]


def __getattr__(name: str):
    # Imported lazily so `python -m okr_tracker --help` and the console script
    # can report a missing Flask clearly instead of raising on import.
    if name == "create_app":
        from .app import create_app

        return create_app
    if name == "Settings":
        from .config import Settings

        return Settings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
