#!/bin/bash
PLIST_NAME="com.ganesh.jobsautomation.plist"
TARGET_PLIST="$HOME/Library/LaunchAgents/$PLIST_NAME"

if [ -f "$TARGET_PLIST" ]; then
    echo "[-] Unloading and removing LaunchAgent schedule..."
    launchctl unload "$TARGET_PLIST" 2>/dev/null || true
    rm -f "$TARGET_PLIST"
    echo "[+] LaunchAgent schedule successfully uninstalled."
else
    echo "[!] Schedule plist was not found in $HOME/Library/LaunchAgents."
fi

