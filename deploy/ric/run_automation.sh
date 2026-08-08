#!/bin/bash
# Clean up any existing session
tmux kill-session -t xapp_c 2>/dev/null
pkill -9 -f "nearRT-RIC" 2>/dev/null
pkill -9 -f "xapp_sec_moni" 2>/dev/null
pkill -9 -f "xapp_sec_mitigate" 2>/dev/null
fuser -k -9 36421/sctp 2>/dev/null
fuser -k -9 36422/sctp 2>/dev/null

# Generate start_xapp_automated.sh from start_xapp_c_mitigate_bg.sh
sed -e 's/--no-cell --no-csv//g' start_xapp_c_mitigate_bg.sh > start_xapp_automated.sh
chmod +x start_xapp_automated.sh

# Run start_xapp_automated.sh in the background
bash ./start_xapp_automated.sh &

# Wait for RIC and gNB to initialize
echo "Waiting for RIC and gNB to initialize..."
sleep 10

# Automate inputs to Pane 2
echo "Sending Cell-level detection choice (3 - Hybrid)..."
tmux send-keys -t xapp_c:0.2 "3" Enter
sleep 2

echo "Sending Per-UE IDS choice (3 - gru-hybrid)..."
tmux send-keys -t xapp_c:0.2 "3" Enter
sleep 2

echo "Pressing ENTER to start the xApp..."
tmux send-keys -t xapp_c:0.2 Enter
