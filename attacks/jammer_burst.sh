#!/bin/bash
# S5 — RF Burst Jammer: wrapper around usrp_ssb_jamming.py.
# Transmits Gaussian noise at gNB DL center frequency (Band 3, 1842.5 MHz) to
# degrade UE CQI.  Each burst = JAMMER_BURST_SEC ON, JAMMER_SLEEP_SEC OFF.
# Usage: jammer_burst.sh [total_duration_hint_seconds]

set -euo pipefail
source "$(dirname "$0")/../attack_config.env"

TOTAL="${1:-120}"
SCRIPT_DIR="$(dirname "$0")/.."

# Calculate burst count so total ≈ TOTAL seconds
# total ≈ BURSTS * BURST_SEC + (BURSTS-1) * SLEEP_SEC
BURSTS="${JAMMER_BURSTS:-8}"
BURST_SEC="${JAMMER_BURST_SEC:-10}"
SLEEP_SEC="${JAMMER_SLEEP_SEC:-8}"

echo "[JAMMER] Starting RF burst on ${JAMMER_HOST}"
echo "  freq=${USRP_FREQ} Hz  rate=${USRP_RATE}  gain=${USRP_GAIN} dB"
echo "  ${BURSTS} bursts × ${BURST_SEC}s ON / ${SLEEP_SEC}s OFF"
ssh -o BatchMode=yes "$JAMMER_HOST" \
    "python3 /home/telmat/xapp/security-scripts/usrp_ssb_jamming.py \
        --freq ${USRP_FREQ} \
        --rate ${USRP_RATE} \
        --gain ${USRP_GAIN} \
        --bursts ${BURSTS} \
        --sleep ${SLEEP_SEC} \
        --duration ${BURST_SEC}"
echo "[JAMMER] Done."
