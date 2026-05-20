#!/bin/bash
# =============================================================
# O-RAN Security xApp (C Version) — Startup Script WITH HYBRID MITIGATION
# =============================================================
# Prerequisite: tmux, sshpass, fuser
# Usage: ./start_xapp_c_mitigate.sh
#
# Mitigasi Hybrid (dua lapis):
#   Layer 1 (O-RAN native): E2SM-RC PRB Throttle via Near-RT RIC
#                           (O-RAN-compliant; terbatas oleh srsRAN RC Bug #468)
#   Layer 2 (iptables fallback): DROP UE subnet di Core node via SSH
#                           (aktif otomatis saat STAGE2-CRITICAL, dicabut saat script restart)
#
# Layout tmux:
#   Window 0 "RAN+RIC":
#     ┌──────────────────┬──────────────────┐
#     │ Pane 0           │ Pane 1           │
#     │ Near-RT RIC      │ srsGNB (SSH)     │
#     ├──────────────────┼──────────────────┤
#     │ Pane 2           │ Pane 3           │
#     │ Prompt / info    │ xapp_sec_moni    │
#     └──────────────────┴──────────────────┘
#   Window 1 "Record": Petunjuk record_dataset.sh
#   Window 2 "Mitigate": iptables watcher (otomatis)
# =============================================================

SESSION="xapp_c"
RIC_BIN="/home/telmat/flexric/build/examples/ric/nearRT-RIC"
XAPP_BIN="/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni"
XAPP_CONF="/home/telmat/sec-xapp/my_xapp_kpm.conf"
XAPP_DIR="/home/telmat/sec-xapp"

RAN_IP="10.91.2.1"
RAN_USER="telmat"
RAN_PASS="123"
RAN_GNB_DIR="/home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B"
RAN_GNB_BIN="./srsRAN_Project/build/apps/gnb/gnb"
RAN_GNB_CONF="configs/cots_n78_copied.yml"

CORE_IP="10.91.2.4"
CORE_USER="telmat"
CORE_PASS="123"
UE_SUBNET="10.45.0.0/16"

XAPP_LOG="/tmp/xapp_sec_moni.log"
PHASE2_FLAG="/tmp/xapp_c_phase2_start"
MITIGATE_FLAG="/tmp/xapp_iptables_active"
RECOVERY_FLAG="/tmp/xapp_recovery_start"
WATCHER_SCRIPT="/tmp/xapp_iptables_watcher.sh"

# =============================================================
# Cleanup — jalankan saat script exit
# =============================================================
cleanup() {
    echo "[cleanup] Killing gNB on RAN node ($RAN_IP)..."
    sshpass -p "$RAN_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
        "$RAN_USER@$RAN_IP" "sudo pkill -9 gnb 2>/dev/null" 2>/dev/null || true

    if [ -f "$MITIGATE_FLAG" ]; then
        echo "[cleanup] Removing iptables rules on Core ($CORE_IP)..."
        sshpass -p "$CORE_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
            "$CORE_USER@$CORE_IP" \
            "echo '$CORE_PASS' | sudo -S iptables -D FORWARD -d $UE_SUBNET -j DROP 2>/dev/null; \
             echo '$CORE_PASS' | sudo -S iptables -D FORWARD -s $UE_SUBNET -j DROP 2>/dev/null" \
            2>/dev/null || true
        rm -f "$MITIGATE_FLAG"
        echo "[cleanup] iptables rules removed."
    fi

    rm -f "$PHASE2_FLAG" "$RECOVERY_FLAG" "$WATCHER_SCRIPT"
    echo "[cleanup] Done."
}
trap cleanup EXIT

# =============================================================
# Dependency check
# =============================================================
for cmd in tmux sshpass fuser; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: '$cmd' tidak ditemukan. Install: sudo apt-get install -y $cmd psmisc"
        exit 1
    fi
done

# =============================================================
# Cleanup sesi lama + port
# =============================================================
tmux kill-session -t "$SESSION" 2>/dev/null
pkill -9 -f "nearRT-RIC" 2>/dev/null
pkill -9 -f "xapp_sec_moni" 2>/dev/null
fuser -k -9 36421/sctp 2>/dev/null
fuser -k -9 36422/sctp 2>/dev/null
rm -f "$PHASE2_FLAG" "$MITIGATE_FLAG" "$RECOVERY_FLAG" "$XAPP_LOG"

# Bersihkan iptables rules sesi lama di Core (idempotent — tidak error jika rule tidak ada)
sshpass -p "$CORE_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
    "$CORE_USER@$CORE_IP" \
    "echo '$CORE_PASS' | sudo -S iptables -D FORWARD -d $UE_SUBNET -j DROP 2>/dev/null; \
     echo '$CORE_PASS' | sudo -S iptables -D FORWARD -s $UE_SUBNET -j DROP 2>/dev/null; \
     echo '$CORE_PASS' | sudo -S iptables -D FORWARD -d $UE_SUBNET -j DROP 2>/dev/null; \
     echo '$CORE_PASS' | sudo -S iptables -D FORWARD -s $UE_SUBNET -j DROP 2>/dev/null" \
    2>/dev/null || true
sleep 1

# =============================================================
# Tulis iptables watcher script ke /tmp
# =============================================================
cat > "$WATCHER_SCRIPT" << 'WATCHER_EOF'
#!/bin/bash
XAPP_LOG="$1"
CORE_IP="$2"
CORE_USER="$3"
CORE_PASS="$4"
UE_SUBNET="$5"
MITIGATE_FLAG="$6"
RECOVERY_FLAG="$7"

_ts() { date '+%H:%M:%S'; }

apply_iptables() {
    echo "[watcher] $(_ts) STAGE2-CRITICAL — menerapkan iptables di $CORE_IP..."
    sshpass -p "$CORE_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 \
        "$CORE_USER@$CORE_IP" \
        "echo '$CORE_PASS' | sudo -S iptables -I FORWARD -d $UE_SUBNET -j DROP 2>/dev/null; \
         echo '$CORE_PASS' | sudo -S iptables -I FORWARD -s $UE_SUBNET -j DROP 2>/dev/null"
    if [ $? -eq 0 ]; then
        touch "$MITIGATE_FLAG"
        echo "[watcher] $(_ts) iptables DROP applied — UE subnet $UE_SUBNET BLOCKED."
    else
        echo "[watcher] $(_ts) WARN: SSH ke Core ($CORE_IP) gagal — iptables tidak diterapkan."
    fi
}

remove_iptables() {
    echo "[watcher] $(_ts) Recovery 30s confirmed — removing iptables di $CORE_IP..."
    sshpass -p "$CORE_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 \
        "$CORE_USER@$CORE_IP" \
        "echo '$CORE_PASS' | sudo -S iptables -D FORWARD -d $UE_SUBNET -j DROP 2>/dev/null; \
         echo '$CORE_PASS' | sudo -S iptables -D FORWARD -s $UE_SUBNET -j DROP 2>/dev/null"
    rm -f "$MITIGATE_FLAG" "$RECOVERY_FLAG"
    echo "[watcher] $(_ts) iptables removed — jaringan UE dipulihkan."
}

echo "[watcher] $(_ts) Memantau $XAPP_LOG..."
echo "[watcher] $(_ts) Core: $CORE_USER@$CORE_IP | UE subnet: $UE_SUBNET"
echo "[watcher] $(_ts) Trigger: STAGE2-CRITICAL → DROP (permanen sampai script restart)"
echo ""

tail -F "$XAPP_LOG" 2>/dev/null | while IFS= read -r line; do
    if [[ "$line" == *"STAGE2-CRITICAL"* ]]; then
        if [ ! -f "$MITIGATE_FLAG" ]; then
            apply_iptables
        fi
    fi
done
WATCHER_EOF
chmod +x "$WATCHER_SCRIPT"

echo "=============================================="
echo "  O-RAN Security xApp (C) + HYBRID MITIGATE"
echo "  Layer 1: E2SM-RC PRB Throttle (O-RAN native)"
echo "  Layer 2: iptables DROP UE subnet ($UE_SUBNET)"
echo "  Core: $CORE_USER@$CORE_IP"
echo "  Attach: tmux attach -t $SESSION"
echo "=============================================="

# =============================================================
# Window 0: RAN + RIC — 4 pane
# =============================================================
tmux new-session -d -s "$SESSION" -n "RAN+RIC"
tmux split-window -t "$SESSION:0.0" -h
tmux split-window -t "$SESSION:0.0" -v
tmux split-window -t "$SESSION:0.1" -v
tmux select-layout -t "$SESSION:0" tiled

# Pane 0: Near-RT RIC (Kiri Atas)
tmux send-keys -t "$SESSION:0.0" \
    "echo '=== [Pane 0] Near-RT RIC ===' && sleep 1 && '$RIC_BIN'" Enter

# Pane 1: srsGNB via SSH (Kanan Atas)
tmux send-keys -t "$SESSION:0.1" \
    "echo '=== [Pane 1] srsGNB via SSH ===' && sleep 3 && \
     sshpass -p '$RAN_PASS' ssh '$RAN_USER@$RAN_IP' \
     'cd $RAN_GNB_DIR && sudo pkill -9 gnb 2>/dev/null; sleep 1; \
      sudo stdbuf -oL $RAN_GNB_BIN -c $RAN_GNB_CONF 2>&1 | tee /tmp/gnb.log'" Enter

# Pane 2: Prompt — tunggu UE attach (Kiri Bawah)
tmux send-keys -t "$SESSION:0.2" \
    "echo '' && \
     echo '  Checklist sebelum ENTER:' && \
     echo '  [1] RIC: tunggu \"E2AP listening on :36421\" (Pane 0)' && \
     echo '  [2] gNB: tunggu E2 Setup sukses (Pane 1)' && \
     echo '  [3] Hubungkan UE (HP) ke jaringan 5G' && \
     echo '' && \
     read -p '  >> Tekan ENTER setelah UE attach... ' && \
     touch '$PHASE2_FLAG' && \
     echo '' && \
     echo '  xapp_sec_moni mulai di Pane 3 (Layer 1: E2SM-RC).' && \
     echo '  iptables watcher mulai di Window 2 (Layer 2: fallback).' && \
     echo '  Ctrl+B 2 → lihat watcher | Ctrl+B 1 → Record'" Enter

# Pane 3: xapp_sec_moni + tee ke log (Kanan Bawah)
# --mitigate (E2SM-RC PRB throttle) dinonaktifkan — Layer 1 menyebabkan E2 timeout
# Layer 2 (iptables via watcher) tetap aktif via STAGE2-CRITICAL log trigger
tmux send-keys -t "$SESSION:0.3" \
    "echo '=== [Pane 3] xapp_sec_moni — menunggu UE attach ===' && \
     until [ -f '$PHASE2_FLAG' ]; do sleep 1; done && \
     echo '=== Memulai xapp_sec_moni (label=0) — iptables mitigasi via watcher ===' && \
     stdbuf -oL '$XAPP_BIN' -c '$XAPP_CONF' --label 0 2>&1 | tee '$XAPP_LOG'" Enter

tmux select-pane -t "$SESSION:0.2"

# =============================================================
# Window 1: Record — petunjuk manual
# =============================================================
tmux new-window -t "$SESSION" -n "Record"
tmux send-keys -t "$SESSION:1" \
    "cd '$XAPP_DIR' && \
     echo '' && \
     echo '  RECORD DATASET — ./record_dataset.sh' && \
     echo '' && \
     echo '  CATATAN: Matikan xapp_sec_moni di Pane 3 (Window 0) sebelum record.' && \
     echo '           Keduanya tidak bisa pakai port E42 bersamaan.' && \
     echo '' && \
     echo '  Label:' && \
     echo '    0 = Normal          ./record_dataset.sh --label 0' && \
     echo '    1 = UL Flood        ./record_dataset.sh --label 1 --duration 600' && \
     echo '    2 = DL Flood        ./record_dataset.sh --label 2 --duration 600' && \
     echo '    3 = Burst ON/OFF    ./record_dataset.sh --label 3 --duration 600' && \
     echo '    4 = RRC Storm       ./record_dataset.sh --label 4 --duration 300' && \
     echo '    5 = RF Burst        ./record_dataset.sh --label 5 --duration 600' && \
     echo '    6 = Jamming         ./record_dataset.sh --label 6 --duration 300' && \
     echo ''" Enter

# =============================================================
# Window 2: Mitigate — iptables watcher otomatis
# =============================================================
tmux new-window -t "$SESSION" -n "Mitigate"
tmux send-keys -t "$SESSION:2" \
    "echo '=== [Window 2] iptables Watcher — Layer 2 Hybrid Mitigation ===' && \
     echo '  Menunggu UE attach dan xapp mulai...' && \
     until [ -f '$PHASE2_FLAG' ]; do sleep 1; done && \
     sleep 3 && \
     '$WATCHER_SCRIPT' \
       '$XAPP_LOG' '$CORE_IP' '$CORE_USER' '$CORE_PASS' \
       '$UE_SUBNET' '$MITIGATE_FLAG' '$RECOVERY_FLAG'" Enter

# Kembali ke Window 0 saat attach
tmux select-window -t "$SESSION:0"

echo ""
echo "Menjalankan: tmux attach -t $SESSION"
echo ""
tmux attach -t "$SESSION"
