#!/bin/bash
# ==============================================================================
# Helper Script for Dynamic Slice Migration (Solusi A)
# ==============================================================================
# Usage: ./change_subscriber_slice.sh <IMSI> <SST>
# Example: ./change_subscriber_slice.sh 001013310000103 2   (Mitigate to SST=2)
#          ./change_subscriber_slice.sh 001013310000103 1   (Restore to SST=1)

IMSI=$1
SST=$2
CORE_IP="10.91.2.4"
CORE_USER="telmat"
CORE_PASS="123"

if [ -z "$IMSI" ] || [ -z "$SST" ]; then
    echo "Usage: $0 <IMSI> <SST>"
    exit 1
fi

echo "======================================================================"
echo "[SLICE_CONTROL] Starting slice migration for IMSI: $IMSI -> SST: $SST"
echo "======================================================================"

# 1. Update MongoDB on the Open5GS Core machine
echo "[1/2] Updating subscriber profile in MongoDB on Open5GS Core ($CORE_IP)..."
sshpass -p "$CORE_PASS" ssh -o StrictHostKeyChecking=no "$CORE_USER@$CORE_IP" "
if command -v mongosh &> /dev/null; then
  mongosh open5gs --eval 'db.subscribers.updateOne({imsi:\"$IMSI\"}, {\$set: {\"slice.0.sst\": $SST}})'
else
  mongo open5gs --eval 'db.subscribers.updateOne({imsi:\"$IMSI\"}, {\$set: {\"slice.0.sst\": $SST}})'
fi
"

# 2. Restart AMF and SMF services to force the UE to reconnect with a clean state
echo "[2/2] Restarting Open5GS AMF, SMF, and UPF services to enforce slice updates..."
sshpass -p "$CORE_PASS" ssh -o StrictHostKeyChecking=no "$CORE_USER@$CORE_IP" "sudo systemctl restart open5gs-amfd open5gs-smfd open5gs-upfd"

echo "======================================================================"
echo "[SLICE_CONTROL] Slice migration completed successfully."
echo "======================================================================"
