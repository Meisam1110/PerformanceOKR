#!/usr/bin/env bash
# macOS double-click launcher. Finder runs .command files in Terminal;
# everything else lives in start.sh.
exec "$(dirname "$0")/start.sh" "$@"
