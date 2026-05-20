#!/bin/bash
# S1 — UL Flood: iperf3 UDP uplink 80M for 120 seconds.
# Usage: ul_flood.sh <adb_device_id>

set -euo pipefail
source "$(dirname "$0")/../attack_config.env"

DEV="${1:?Usage: ul_flood.sh <adb_device_id>}"

if [ "$DEV" = "$DEV1" ]; then
    SSH_PORT="$UE1_SSH_PORT"
else
    SSH_PORT="$UE2_SSH_PORT"
fi

echo "[UL_FLOOD] Starting on $DEV via SSH port $SSH_PORT — 80M UDP 120s"
ssh -p "$SSH_PORT" \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=5 \
    localhost \
    "iperf3 -u -c $TARGET_IP -p $IPERF_PORT -b 50M -t 120 --no-delay"
echo "[UL_FLOOD] Done."
