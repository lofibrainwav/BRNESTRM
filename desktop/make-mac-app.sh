#!/usr/bin/env bash
# Create ~/Applications/BRNESTRM.app, a double-clickable launcher for desktop/run.sh.
# It only points at this folder; nothing is copied, signed or installed system-wide.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="${1:-$HOME/Applications/BRNESTRM.app}"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>BRNESTRM</string>
  <key>CFBundleIdentifier</key><string>local.brnestrm.desktop-launcher</string>
  <key>CFBundleExecutable</key><string>BRNESTRM</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST
# Output goes to a log; if the shell cannot start, say so instead of failing silently.
cat > "$APP/Contents/MacOS/BRNESTRM" <<LAUNCH
#!/bin/bash
LOG="\$HOME/Library/Logs/brnestrm-desktop.log"
echo "--- \$(date) start" >> "\$LOG"
/bin/bash $(printf %q "$HERE/run.sh") >> "\$LOG" 2>&1 || /usr/bin/osascript -e 'display alert "BRNESTRM could not start" message "Details: ~/Library/Logs/brnestrm-desktop.log"'
LAUNCH
chmod +x "$APP/Contents/MacOS/BRNESTRM"
echo "created $APP"
