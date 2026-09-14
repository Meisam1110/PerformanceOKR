#!/usr/bin/env bash
# Start the POLE OKR Tracker on macOS or Linux.
#
#   ./start.sh              from a terminal
#   double-click            from a file manager (see "Start OKR Tracker.command"
#                           on macOS, which is this script under a name Finder
#                           will run)
#
# The first run installs Flask and waitress into a private .venv; later runs
# start immediately. Any arguments are passed through to the app:
#
#   ./start.sh --port 9000
set -euo pipefail

cd "$(dirname "$0")"

# Pick an interpreter new enough to run the app (3.9+).
find_python() {
  local candidate
  for candidate in python3 python python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PYTHON=$(find_python); then
  echo
  echo "POLE OKR Tracker needs Python 3.9 or newer, and none was found."
  echo
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "Install it from https://www.python.org/downloads/ or with Homebrew:"
    echo "    brew install python"
  else
    echo "Install it with your package manager, for example:"
    echo "    sudo apt install python3 python3-venv    # Debian, Ubuntu"
    echo "    sudo dnf install python3                 # Fedora"
  fi
  echo
  read -r -p "Press Return to close. " _ || true
  exit 1
fi

# Keep the window open on failure so a double-click shows what went wrong.
if ! "$PYTHON" run.py "$@"; then
  status=$?
  echo
  echo "The tracker exited with status $status."
  read -r -p "Press Return to close. " _ || true
  exit "$status"
fi
