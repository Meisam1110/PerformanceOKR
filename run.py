#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["Flask>=3.0,<4.0", "waitress>=3.0,<4.0"]
# ///
"""Launcher for the POLE OKR Tracker.

    python run.py

That is the whole install. On first run this creates a private .venv beside the
application, installs Flask and waitress into it, and restarts itself there;
afterwards it starts straight away. Nothing is added to your system Python, and
the step is skipped entirely if the dependencies are already available.

Every option of the full CLI works here too, and the CLI itself is available as
``python -m okr_tracker`` (or ``okr-tracker`` once installed):

    python run.py --port 9000
    python run.py --host 0.0.0.0 --passphrase "a shared secret"

Set OKR_NO_BOOTSTRAP=1 to manage dependencies yourself.
"""

import sys

MINIMUM_PYTHON = (3, 9)

# Checked before anything else is imported, so someone on an old interpreter
# gets a sentence they can act on rather than a traceback from deeper in.
if sys.version_info < MINIMUM_PYTHON:
    sys.exit(
        "POLE OKR Tracker needs Python {}.{} or newer, but this is Python {}.{}.\n"
        "Install a current version from https://www.python.org/downloads/ "
        "and run this again.".format(
            MINIMUM_PYTHON[0], MINIMUM_PYTHON[1],
            sys.version_info[0], sys.version_info[1],
        )
    )

from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent

# Allow running straight from a source checkout without installing first.
sys.path.insert(0, str(ROOT))

from okr_tracker.bootstrap import BootstrapError, ensure_dependencies  # noqa: E402


def main() -> int:
    try:
        # Returns only when this interpreter can already serve; otherwise it
        # installs and re-executes, so nothing below runs twice.
        ensure_dependencies(ROOT)
    except BootstrapError as error:
        print(f"\nSetup failed: {error}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nSetup cancelled.", file=sys.stderr)
        return 130

    from okr_tracker.__main__ import main as cli

    return cli()


if __name__ == "__main__":
    raise SystemExit(main())
