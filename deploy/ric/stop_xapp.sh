#!/bin/bash
SESSION="xapp_c"
RAN_IP="10.91.2.1"
RAN_USER="telmat"
RAN_PASS="123"

echo "=============================================="
echo "Stopping all RIC, xApp, and gNB processes..."
echo "=============================================="

# Kill local xapp and RIC processes
echo "Killing local RIC & xApp processes..."
tmux kill-session -t "$SESSION" 2>/dev/null
pkill -9 -f "nearRT-RIC" 2>/dev/null
pkill -9 -f "xapp_sec_moni" 2>/dev/null
pkill -9 -f "xapp_sec_mitigate" 2>/dev/null
fuser -k -9 36421/sctp 2>/dev/null
fuser -k -9 36422/sctp 2>/dev/null

# Kill remote gNB processes
echo "Killing remote gNB on RAN node ($RAN_IP)..."
sshpass -p "$RAN_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
    "$RAN_USER@$RAN_IP" "echo '$RAN_PASS' | sudo -S pkill -9 gnb 2>/dev/null; echo '$RAN_PASS' | sudo -S killall -9 gnb 2>/dev/null" 2>/dev/null || true

echo "Checking remote process status..."
sshpass -p "$RAN_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
    "$RAN_USER@$RAN_IP" "ps aux | grep gnb | grep -v grep || echo 'Remote gNB successfully killed!'"

echo "=============================================="
echo "Done!"
echo "=============================================="
