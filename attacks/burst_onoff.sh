#!/bin/bash
# S3 — Burst ON/OFF: randomized UDP bursts (3-7s ON, 2-6s OFF) for 120 seconds total.
# Usage: burst_onoff.sh <adb_device_id>

set -euo pipefail
source "$(dirname "$0")/../attack_config.env"

DEV="${1:?Usage: burst_onoff.sh <adb_device_id>}"

if [ "$DEV" = "$DEV1" ]; then
    SSH_PORT="$UE1_SSH_PORT"
else
    SSH_PORT="$UE2_SSH_PORT"
fi

echo "[BURST] Starting on $DEV via port $SSH_PORT — randomized ON/OFF for 120s total"

ssh -p "$SSH_PORT" \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=5 \
    localhost \
    "bash -s" << 'REMOTE'
TARGET_IP="10.45.0.1"
IPERF_PORT="5202"
END_TIME=$(( $(date +%s) + 120 ))
CYCLE=1
while [ $(date +%s) -lt $END_TIME ]; do
    ON=$(( RANDOM % 5 + 3 ))
    OFF=$(( RANDOM % 5 + 2 ))
    REMAINING=$(( END_TIME - $(date +%s) ))
    [ $ON -gt $REMAINING ] && ON=$REMAINING
    [ $ON -le 0 ] && break
    echo "[BURST] Cycle $CYCLE: ON=${ON}s OFF=${OFF}s"
    iperf3 -u -c "$TARGET_IP" -p "$IPERF_PORT" -b 80M -t "$ON" --no-delay 2>&1 | tail -1
    sleep "$OFF"
    CYCLE=$(( CYCLE + 1 ))
done
echo "[BURST] Done."
REMOTE

echo "[BURST] SSH session complete."
