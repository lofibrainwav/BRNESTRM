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
printf '#!/bin/bash\nexec /bin/bash %q\n' "$HERE/run.sh" > "$APP/Contents/MacOS/BRNESTRM"
chmod +x "$APP/Contents/MacOS/BRNESTRM"
echo "created $APP"
