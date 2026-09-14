#!/usr/bin/env python3
"""Double-click launcher for the POLE OKR Tracker.

Starts the local server and opens the workspace in your browser:

    python run.py

Every option of the full CLI works here too, and the CLI itself is available as
``python -m okr_tracker`` (or ``okr-tracker`` once installed):

    python run.py --port 9000
    python run.py --host 0.0.0.0 --passphrase "a shared secret"
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running straight from a source checkout without installing first.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from okr_tracker.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
