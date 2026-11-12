#!/usr/bin/env bash
# Start the BRNESTRM desktop shell (installs the pinned Electron on first run).
# BRNESTRM_CONSOLE_URL overrides the console address (loopback only; default http://127.0.0.1:8788/).
set -euo pipefail
cd "$(dirname "$0")"
BIN=node_modules/electron/dist/Electron.app/Contents/MacOS/Electron
[ -d node_modules/electron ] || npm ci --no-audit --no-fund
# Some npm setups skip install scripts, so fetch the pinned Electron binary explicitly when missing.
[ -x "$BIN" ] || node node_modules/electron/install.js
node harden-electron.cjs   # cookie encryption on, RunAsNode/NODE_OPTIONS/--inspect off (idempotent)
exec "$BIN" .
