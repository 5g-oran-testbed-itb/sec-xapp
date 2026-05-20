#!/bin/bash
# =============================================================
# Dataset Recording Script — Security xApp
# Usage: ./record_dataset.sh --label <N> [--duration <seconds>]
#
# Label Convention:
#   0 = Normal (benign)
#   1 = UL Flood       (iperf3 -u -b 80M dari UE)
#   2 = DL Flood       (iperf3 -R)
#   3 = Burst ON/OFF   (5s ON / 5s OFF)
#   4 = RRC Storm      (reconnect tiap 3-5s)
#   5 = RF Burst       (USRP jammer ON/OFF periodik)
#   6 = Continuous Jamming (USRP TX noise kontinyu)
# =============================================================

XAPP_BIN="/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni"
CONF="/home/telmat/xapp/security-xapp/my_xapp_kpm.conf"

LABEL=""
DURATION=""

# Parse argumen
while [[ $# -gt 0 ]]; do
    case "$1" in
        --label)
            LABEL="$2"; shift 2 ;;
        --duration)
            DURATION="$2"; shift 2 ;;
        *)
            echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [[ -z "$LABEL" ]]; then
    echo "Usage: $0 --label <0-6> [--duration <seconds>]"
    echo ""
    echo "  0 = Normal (benign)"
    echo "  1 = UL Flood"
    echo "  2 = DL Flood"
    echo "  3 = Burst ON/OFF"
    echo "  4 = RRC Storm"
    echo "  5 = RF Burst Interference"
    echo "  6 = Continuous Jamming"
    exit 1
fi

LABEL_NAMES=("Normal" "UL Flood" "DL Flood" "Burst ON/OFF" "RRC Storm" "RF Burst" "Continuous Jamming")
LABEL_NAME="${LABEL_NAMES[$LABEL]:-Unknown}"

echo "=============================================="
echo "  Dataset Recording — Label $LABEL: $LABEL_NAME"
if [[ -n "$DURATION" ]]; then
    echo "  Duration: ${DURATION}s"
fi
echo "=============================================="
echo ""

if [[ -n "$DURATION" ]]; then
    timeout "$DURATION" "$XAPP_BIN" -c "$CONF" --label "$LABEL"
    echo ""
    echo "[record_dataset] Selesai setelah ${DURATION}s."
else
    "$XAPP_BIN" -c "$CONF" --label "$LABEL"
fi
