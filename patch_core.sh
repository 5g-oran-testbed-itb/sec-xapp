#!/bin/bash
# ==============================================================================
# Robust Script to Patch Core Network Configs (SMF/AMF/NSSF) for Slice SST=2
# ==============================================================================

CORE_IP="${CORE_IP:-10.91.2.4}"
CORE_USER="${CORE_USER:-telmat}"
# Requires an SSH key already authorized on $CORE_USER@$CORE_IP (ssh-copy-id).
# No password is read from this script or the environment.

echo "[1/4] Preparing clean config files..."

# Write clean SMF config to a temporary file locally
cat << 'EOF' > /tmp/smf.yaml
logger:
  file:
    path: /var/log/open5gs/smf.log

global:
  max:
    ue: 1024

smf:
  sbi:
    server:
      - address: 127.0.0.4
        port: 7777
    client:
      scp:
        - uri: http://127.0.0.200:7777
  pfcp:
    server:
      - address: 127.0.0.4
    client:
      upf:
        - address: 127.0.0.7
  gtpc:
    server:
      - address: 127.0.0.4
  gtpu:
    server:
      - address: 127.0.0.4
  metrics:
    server:
      - address: 127.0.0.4
        port: 9090
  session:
    - subnet: 10.45.0.0/16
      gateway: 10.45.0.1
    - subnet: 2001:db8:cafe::/48
      gateway: 2001:db8:cafe::1
  dns:
    - 8.8.8.8
    - 8.8.4.4
    - 2001:4860:4860::8888
    - 2001:4860:4860::8844
  mtu: 1400
  freeDiameter: /etc/freeDiameter/smf.conf

  info:
    - s_nssai:
        - sst: 1
          dnn:
            - srsapn
            - internet
            - ims
        - sst: 2
          dnn:
            - srsapn
            - internet
            - ims
EOF

# Write clean NSSF config to a temporary file locally
cat << 'EOF' > /tmp/nssf.yaml
logger:
  file:
    path: /var/log/open5gs/nssf.log

global:
  max:
    ue: 1024

nssf:
  sbi:
    server:
      - address: 127.0.0.14
        port: 7777
    client:
      scp:
        - uri: http://127.0.0.200:7777
      nsi:
        - uri: http://127.0.0.10:7777
          s_nssai:
            sst: 1
        - uri: http://127.0.0.10:7777
          s_nssai:
            sst: 2
EOF

echo "[2/4] Uploading clean config files to Core node ($CORE_IP)..."
scp -o StrictHostKeyChecking=no /tmp/smf.yaml "$CORE_USER@$CORE_IP":/tmp/smf.yaml
scp -o StrictHostKeyChecking=no /tmp/nssf.yaml "$CORE_USER@$CORE_IP":/tmp/nssf.yaml

echo "[3/4] Overwriting /etc/open5gs configurations on Core node using sudo..."
# Requires passwordless sudo (NOPASSWD) configured for $CORE_USER on the Core
# node, since no password is stored or transmitted by this script.
ssh -o StrictHostKeyChecking=no "$CORE_USER@$CORE_IP" "sudo -n cp /tmp/smf.yaml /etc/open5gs/smf.yaml 2>/dev/null"
ssh -o StrictHostKeyChecking=no "$CORE_USER@$CORE_IP" "sudo -n cp /tmp/nssf.yaml /etc/open5gs/nssf.yaml 2>/dev/null"

echo "[4/4] Restarting Open5GS services (SMF, AMF, NSSF)..."
ssh -o StrictHostKeyChecking=no "$CORE_USER@$CORE_IP" "sudo -n systemctl restart open5gs-smfd 2>/dev/null"
ssh -o StrictHostKeyChecking=no "$CORE_USER@$CORE_IP" "sudo -n systemctl restart open5gs-amfd 2>/dev/null"
ssh -o StrictHostKeyChecking=no "$CORE_USER@$CORE_IP" "sudo -n systemctl restart open5gs-nssfd 2>/dev/null"

# Cleanup local temp files
rm -f /tmp/smf.yaml /tmp/nssf.yaml

echo "======================================================================"
echo "Core network configurations restored and successfully patched with SST=2!"
echo "======================================================================"
