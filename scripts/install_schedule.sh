#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_NAME="com.ganesh.jobsautomation.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$TARGET_DIR/$PLIST_NAME"

mkdir -p "$TARGET_DIR"
mkdir -p "$PROJECT_DIR/logs"
chmod +x "$PROJECT_DIR/scripts/scheduled_runner.sh"

# Unload previous instance if currently loaded
if launchctl list 2>/dev/null | grep -q "com.ganesh.jobsautomation"; then
    echo "[-] Unloading existing scheduled job..."
    launchctl unload "$TARGET_PLIST" 2>/dev/null || true
fi

# Render template plist with actual PROJECT_DIR and load it
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/scripts/$PLIST_NAME" > "$TARGET_PLIST"
launchctl load "$TARGET_PLIST"

echo "================================================================================"
echo "[+] SUCCESS: Jobs Automation schedule is active!"
echo "[+] Target times: 9:00 AM, 12:00 PM, and 3:00 PM."
echo "[+] Scheduled runner: $PROJECT_DIR/scripts/scheduled_runner.sh"
echo "[+] Run logs will stream to: $PROJECT_DIR/logs/scheduled_run.log"
echo "================================================================================"

