#!/bin/bash
# S2 — DL Flood: iperf3 UDP downlink 100M for 120 seconds (-R reverse mode).
# Usage: dl_flood.sh <adb_device_id>

set -euo pipefail
source "$(dirname "$0")/../attack_config.env"

DEV="${1:?Usage: dl_flood.sh <adb_device_id>}"

if [ "$DEV" = "$DEV1" ]; then
    SSH_PORT="$UE1_SSH_PORT"
else
    SSH_PORT="$UE2_SSH_PORT"
fi

echo "[DL_FLOOD] Starting on $DEV via SSH port $SSH_PORT — 100M UDP DL 120s"
ssh -p "$SSH_PORT" \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=5 \
    localhost \
    "iperf3 -u -R -c $TARGET_IP -p $IPERF_PORT -b 100M -t 120 --no-delay"
echo "[DL_FLOOD] Done."
