#!/bin/bash
# ==============================================================================
# Restore Standard Laptop Mode for macOS
# Restores normal power-saving battery & sleep behavior for when traveling.
# ==============================================================================

if [ "$EUID" -ne 0 ]; then
    echo "[!] Please run this script with sudo: sudo bash scripts/restore_laptop_mode.sh"
    exit 1
fi

echo "================================================================="
echo "       RESTORING STANDARD MACOS LAPTOP POWER SETTINGS"
echo "================================================================="

# Re-enable standard sleep behavior
pmset -a disablesleep 0
pmset -b sleep 10 displaysleep 5
pmset -c sleep 30 displaysleep 15

echo ""
echo "[✓] Standard Laptop Mode RESTORED successfully!"
echo "    • Sleep Mode: RE-ENABLED (System sleeps normally when idle or lid closed)"
echo "    • Battery: Optimized for travel and standard laptop use"
echo "================================================================="
