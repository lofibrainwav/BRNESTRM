#!/usr/bin/env bash
# Start the BRNESTRM desktop shell (installs the pinned Electron on first run).
# BRNESTRM_CONSOLE_URL overrides the console address (loopback only; default http://127.0.0.1:8788/).
set -euo pipefail
cd "$(dirname "$0")"
[ -x node_modules/electron/dist/Electron.app/Contents/MacOS/Electron ] || npm ci --no-audit --no-fund
node harden-electron.cjs   # cookie encryption on, RunAsNode/NODE_OPTIONS/--inspect off (idempotent)
exec node_modules/electron/dist/Electron.app/Contents/MacOS/Electron .
