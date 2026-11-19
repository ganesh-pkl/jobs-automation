#!/bin/bash
# ==============================================================================
# Setup Server Mode for macOS (Jobs Automation)
# Enables true 24/7 server execution: keeps the Mac running, Wi-Fi connected,
# and launchd scheduled jobs firing even with the LID CLOSED and DISPLAY OFF.
# ==============================================================================

if [ "$EUID" -ne 0 ]; then
    echo "[!] Please run this script with sudo: sudo bash scripts/setup_server_mode.sh"
    exit 1
fi

echo "================================================================="
echo "       ENABLING MACOS SERVER MODE FOR JOBS AUTOMATION"
echo "================================================================="

# 1. Disable system sleep completely (behaves like an always-on cloud server)
pmset -a disablesleep 1

# 2. Turn off display after 3 minutes of inactivity (protects screen & saves power)
pmset -a displaysleep 3

# 3. Keep TCP connections active
pmset -a tcpkeepalive 1

# 4. Wake on network access
pmset -a womp 1

echo ""
echo "[✓] Server Mode ENABLED successfully!"
echo "    • Sleep Mode: DISABLED (Mac will stay fully active 24/7 like a server)"
echo "    • Lid Behavior: Closing the lid will NOT interrupt background jobs"
echo "    • Display: Will turn completely OFF/black after 3 minutes to conserve power"
echo "    • Scheduled Jobs: 9:00 AM, 12:00 PM, and 3:00 PM will trigger automatically"
echo ""
echo "NOTE: Keep your MacBook connected to the charger so the battery does not drain."
echo "To revert back to standard laptop sleep mode later, run:"
echo "    sudo bash scripts/restore_laptop_mode.sh"
echo "================================================================="
