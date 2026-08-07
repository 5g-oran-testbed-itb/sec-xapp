#!/bin/bash
# ==============================================================================
# Helper Script to Sync gNB configuration to Remote Node
# ==============================================================================
set -e

GNB_IP="${GNB_IP:-10.91.2.1}"
GNB_USER="${GNB_USER:-telmat}"
# Requires an SSH key already authorized on $GNB_USER@$GNB_IP (ssh-copy-id).
# No password is read from this script or the environment.

echo "======================================================================"
echo "[SYNC] Transferring cots_n78_copied.yml to remote gNB ($GNB_IP)..."
echo "======================================================================"

scp -o StrictHostKeyChecking=no \
  /home/telmat/sec-xapp/cots_n78_copied.yml \
  "$GNB_USER@$GNB_IP":/home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B/configs/cots_n78_copied.yml

echo "======================================================================"
echo "[SYNC] Sync completed successfully."
echo "======================================================================"
