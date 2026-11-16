#!/bin/bash
# ==============================================================================
# Scheduled Runner for Multi-Portal Jobs Automation
# Runs automatically via macOS launchd at 8:30 AM, 1:30 PM, and 4:30 PM.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/scheduled_run.log"
LOCK_FILE="/tmp/jobs_automation_scheduled.lock"

mkdir -p "$LOG_DIR"

# Ensure only one instance runs at a time (Concurrency Safety)
if [ -f "$LOCK_FILE" ]; then
    PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if [ -n "$PID" ] && ps -p "$PID" > /dev/null 2>&1; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Another job run is active (PID: $PID). Skipping this cycle." >> "$LOG_FILE"
        exit 0
    fi
fi

# Acquire process lock
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT INT TERM

echo "" >> "$LOG_FILE"
echo "================================================================================" >> "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting automated job application run..." >> "$LOG_FILE"
echo "================================================================================" >> "$LOG_FILE"

cd "$PROJECT_DIR" || exit 1

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Python binary not found at $PYTHON_BIN" >> "$LOG_FILE"
    exit 1
fi

# Enforce headless mode in background so browser windows don't disrupt current desktop work
export BROWSER_MODE="headless"
export PYTHONUNBUFFERED="1"

# Run the full multi-platform daily pipeline
"$PYTHON_BIN" daily_pipeline.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "" >> "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pipeline finished with exit code: $EXIT_CODE" >> "$LOG_FILE"
echo "================================================================================" >> "$LOG_FILE"

# Send macOS desktop notification
if [ $EXIT_CODE -eq 0 ]; then
    osascript -e 'display notification "Job pipeline completed successfully." with title "Jobs Automation" sound name "Glass"' 2>/dev/null || true
else
    osascript -e 'display notification "Job pipeline finished with notes. Check logs." with title "Jobs Automation" sound name "Basso"' 2>/dev/null || true
fi

# Send automated email status report via Resend API
if [ -f "$PROJECT_DIR/scripts/send_status_email.js" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sending email status report via Resend..." >> "$LOG_FILE"
    node "$PROJECT_DIR/scripts/send_status_email.js" >> "$LOG_FILE" 2>&1 || true
fi

exit $EXIT_CODE

