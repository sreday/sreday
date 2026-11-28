#!/bin/sh
# The scheduled catch-up (Mac): `python -m live_factory catchup` at every login and every 3 hours, so a talk the
# day's YouTube quota (or a restart) left behind goes up without anyone keeping a window open. Log: local/catchup.log.
# Undo: launchctl bootout gui/$(id -u)/com.sreday.live-factory.catchup && rm ~/Library/LaunchAgents/com.sreday.live-factory.catchup.plist
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL=com.sreday.live-factory.catchup
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$HERE/local"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HERE/env/bin/python</string><string>-m</string><string>live_factory</string><string>catchup</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUTF8</key><string>1</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>10800</integer>
  <key>StandardOutPath</key><string>$HERE/local/catchup.log</string>
  <key>StandardErrorPath</key><string>$HERE/local/catchup.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "scheduled: $LABEL (at login + every 3 h), log $HERE/local/catchup.log"
