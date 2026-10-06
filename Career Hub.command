#!/bin/zsh
# Double-click to open (or bring back) Danbi's Career Hub.
# The server is owned by the launchd agent com.danbi.careerhub (installed with
# `python3 career.py hub install`; starts at login, restarts if it stops).
# This script only (re)starts that agent — it never runs a second copy.
cd "$(dirname "$0")"
AGENT="gui/$(id -u)/com.danbi.careerhub"
PLIST="$HOME/Library/LaunchAgents/com.danbi.careerhub.plist"
if ! curl -s -m 2 http://127.0.0.1:7768/health >/dev/null; then
  if launchctl print "$AGENT" >/dev/null 2>&1; then
    launchctl kickstart -k "$AGENT"
  elif [ -f "$PLIST" ]; then
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
  else
    python3 career.py hub install
    exit 0
  fi
  for i in {1..20}; do curl -s -m 1 http://127.0.0.1:7768/health >/dev/null && break; sleep 0.5; done
fi
open http://127.0.0.1:7768/
