#!/usr/bin/env bash
# Start the BRNESTRM Ego Lite screen bridge if it is not already answering.
#   ops/run-ego-screen-bridge.sh            start (idempotent)
#   ops/run-ego-screen-bridge.sh --restart  stop the running bridge, then start
#   ops/run-ego-screen-bridge.sh --if-ego-running  start only when Ego Lite is already open
#                                           (for a periodic launchd job: never opens the app itself)
# Settings (all optional): BRNESTRM_EGO_BRIDGE_PORT (8794), BRNESTRM_EGO_BRIDGE_SECRET_FILE
# (~/.brnestrm/ego-bridge.secret), BRNESTRM_EGO_PROFILE (Profile 1), BRNESTRM_EGO_SPACE.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${BRNESTRM_EGO_BRIDGE_PORT:-8794}"
SECRET_FILE="${BRNESTRM_EGO_BRIDGE_SECRET_FILE:-$HOME/.brnestrm/ego-bridge.secret}"
PROFILE="${BRNESTRM_EGO_PROFILE:-Profile 1}"
SPACE="${BRNESTRM_EGO_SPACE:-BRNESTRM console screen}"
EGO="${EGO_BROWSER:-$(command -v ego-browser || echo "$HOME/.local/bin/ego-browser")}"

# The secret goes to curl through a private header file, never on its command line (visible in ps).
HDR="$(umask 077; mktemp "${TMPDIR:-/tmp}/ego-bridge-hdr.XXXXXX")"
trap 'rm -f "$HDR"' EXIT
write_header() { [ -f "$SECRET_FILE" ] && { read -r SECRET < "$SECRET_FILE"; printf 'x-bridge-secret: %s\n' "$SECRET" > "$HDR"; unset SECRET; }; }
health() { write_header && curl -fsS --noproxy '*' -m 3 -H @"$HDR" "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; }

if [ "${1:-}" = "--if-ego-running" ]; then
  health && exit 0
  pgrep -x "ego lite" >/dev/null 2>&1 || exit 0
fi
if [ "${1:-}" = "--restart" ] && health; then
  write_header && curl -fsS --noproxy '*' -m 5 -X POST -H @"$HDR" "http://127.0.0.1:$PORT/shutdown" >/dev/null || true
  for _ in $(seq 1 20); do health || break; sleep 0.25; done
fi
if health; then echo "bridge already running on 127.0.0.1:$PORT"; exit 0; fi

CONFIG=$(python3 -c 'import json,sys; print(json.dumps({"port": int(sys.argv[1]), "secretFile": sys.argv[2], "profile": sys.argv[3], "space": sys.argv[4]}))' "$PORT" "$SECRET_FILE" "$PROFILE" "$SPACE")
{ printf 'globalThis.__BRNESTRM_BRIDGE__ = %s;\n' "$CONFIG"; cat "$HERE/ego-screen-bridge.mjs"; } | "$EGO" nodejs >/dev/null 2>&1 &
for _ in $(seq 1 60); do health && { echo "bridge running on 127.0.0.1:$PORT (profile $PROFILE)"; exit 0; }; sleep 0.5; done
echo "bridge did not start; run: $EGO nodejs < $HERE/ego-screen-bridge.mjs  to see the error" >&2
exit 1
