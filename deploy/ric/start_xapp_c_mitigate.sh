#!/bin/bash
# =============================================================
# O-RAN Security xApp (C Version) — Startup Script WITH E2SM-RC MITIGATION
# =============================================================
# Prerequisite: tmux, fuser, SSH key authorized on $RAN_USER@$RAN_IP, NOPASSWD sudo on RAN node
# Usage: ./start_xapp_c_mitigate.sh
#
# Mitigasi: E2SM-RC PRB Throttle via Near-RT RIC (O-RAN native)
#   srsRAN RC Bug #468: RESOLVED (patch merged May 2024)
#   Flag --mitigate: aktif — throttle max=5% saat CRITICAL, restore saat normal
#   Cooldown: 30s | Auto-restore: 10s setelah severity=0
#
# Layout tmux Window 0 "RAN+RIC":
#   ┌──────────────────┬──────────────────┐
#   │ Pane 0           │ Pane 1           │
#   │ Near-RT RIC      │ srsGNB (SSH)     │
#   ├──────────────────┼──────────────────┤
#   │ Pane 2           │ Pane 3           │
#   │ Prompt / info    │ xapp_sec_moni    │
#   └──────────────────┴──────────────────┘
#
# Window 1 "Record":
#   Petunjuk record_dataset.sh — jalankan manual setelah UE attach
# =============================================================

SESSION="xapp_c"
RIC_BIN="/home/telmat/flexric/build/examples/ric/nearRT-RIC"
XAPP_BIN="/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni"
MITIGATE_BIN="/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_mitigate"
XAPP_CONF="/home/telmat/sec-xapp/my_xapp_kpm.conf"
MITIGATE_CONF="/home/telmat/sec-xapp/my_xapp_mitigate.conf"
XAPP_DIR="/home/telmat/sec-xapp"

RAN_IP="${RAN_IP:-10.91.2.1}"
RAN_USER="${RAN_USER:-telmat}"
# Requires an SSH key already authorized on $RAN_USER@$RAN_IP (ssh-copy-id)
# and passwordless sudo (NOPASSWD) on the RAN node. No password is read
# from this script or the environment.
RAN_GNB_DIR="/home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B"
RAN_GNB_BIN="./srsRAN_Project/build/apps/gnb/gnb"
RAN_GNB_CONF="configs/cots_n78_copied.yml"

PHASE2_FLAG="/tmp/xapp_c_phase2_start"
MODE_FILE="/tmp/xapp_detection_mode"
IDS_MODE_FILE="/tmp/xapp_ids_mode"

# =============================================================
# Cleanup — jalankan saat script exit
# =============================================================
cleanup() {
    echo "[cleanup] Killing gNB on RAN node ($RAN_IP)..."
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
        "$RAN_USER@$RAN_IP" "sudo -n pkill -9 gnb 2>/dev/null; sudo -n killall -9 gnb 2>/dev/null" 2>/dev/null || true
    echo "[cleanup] Done."
}
trap cleanup INT TERM

# =============================================================
# Dependency check
# =============================================================
for cmd in tmux fuser; do
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
pkill -9 -f "xapp_sec_mitigate" 2>/dev/null
fuser -k -9 36421/sctp 2>/dev/null
fuser -k -9 36422/sctp 2>/dev/null
rm -f "$PHASE2_FLAG" "$MODE_FILE" "$IDS_MODE_FILE"
sleep 1

echo "=============================================="
echo "Resetting subscriber profile to SST=1..."
echo "=============================================="
bash /home/telmat/sec-xapp/change_subscriber_slice.sh 001013310000103 1


echo "=============================================="
echo "  O-RAN Security xApp (C) + E2SM-RC MITIGATE"
echo "  Mitigasi: E2SM-RC PRB Throttle (O-RAN native)"
echo "  RC Bug #468: RESOLVED — --mitigate aktif"
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
# sleep 10: beri waktu RIC selesai init + bind port 36421 sebelum gNB konek
tmux send-keys -t "$SESSION:0.1" \
    "echo '=== [Pane 1] srsGNB via SSH ===' && sleep 10 && \
     ssh '$RAN_USER@$RAN_IP' \
     'cd $RAN_GNB_DIR && sudo -n pkill -9 gnb 2>/dev/null; sudo -n killall -9 gnb 2>/dev/null; sleep 1; \
      sudo -n stdbuf -oL $RAN_GNB_BIN -c $RAN_GNB_CONF 2>&1 | tee /tmp/gnb.log'" Enter

# Pane 2: Prompt — pilih mode + ids-mode + tunggu UE attach (Kiri Bawah)
tmux send-keys -t "$SESSION:0.2" \
    "echo '' && \
     echo '  ╔══════════════════════════════════════════╗' && \
     echo '  ║  O-RAN Security xApp — MITIGATE Mode    ║' && \
     echo '  ║  E2SM-RC PRB Throttle (--mitigate aktif)║' && \
     echo '  ╚══════════════════════════════════════════╝' && \
     echo '' && \
     echo '  [1/2] Cell-level detection (--mode):' && \
     echo '    1) Rule-Based IDS only  (Stage 1 saja)' && \
     echo '    2) LSTM only            (Stage 2 saja)' && \
     echo '    3) Hybrid               (Rule + LSTM, default)' && \
     echo '' && \
     read -p '  >> Pilihan [1/2/3, default=3]: ' _mode_choice && \
     case \"\$_mode_choice\" in \
       1) echo rule   > '$MODE_FILE'; _mode_label=\"Rule-Based Only\" ;; \
       2) echo lstm   > '$MODE_FILE'; _mode_label=\"LSTM Only\" ;; \
       *) echo hybrid > '$MODE_FILE'; _mode_label=\"Hybrid (Rule+LSTM)\" ;; \
     esac && \
     echo '' && \
     echo \"  Mode cell-level: \$_mode_label\" && \
     echo '' && \
     echo '  [2/2] Per-UE IDS (--ids-mode):' && \
     echo '    1) rule-only     (rule saja, tanpa ML per-UE)' && \
     echo '    2) lstm-hybrid   (Rule + LSTM-UE v4)' && \
     echo '    3) gru-hybrid    (Rule + GRU-UE v4, rekomendasi)' && \
     echo '    4) lstm-only     (ablasi ML saja)' && \
     echo '    5) gru-only      (ablasi ML saja)' && \
     echo '' && \
     read -p '  >> Pilihan [1-5, default=3]: ' _ids_choice && \
     case \"\$_ids_choice\" in \
       1) echo 'rule-only'   > '$IDS_MODE_FILE'; _ids_label=\"rule-only\" ;; \
       2) echo 'lstm-hybrid' > '$IDS_MODE_FILE'; _ids_label=\"lstm-hybrid\" ;; \
       4) echo 'lstm-only'   > '$IDS_MODE_FILE'; _ids_label=\"lstm-only\" ;; \
       5) echo 'gru-only'    > '$IDS_MODE_FILE'; _ids_label=\"gru-only\" ;; \
       *) echo 'gru-hybrid'  > '$IDS_MODE_FILE'; _ids_label=\"gru-hybrid\" ;; \
     esac && \
     echo '' && \
     echo \"  Mode per-UE IDS: \$_ids_label\" && \
     echo '' && \
     echo '  Checklist sebelum ENTER:' && \
     echo '  [1] RIC: tunggu \"E2AP listening on :36421\" (Pane 0)' && \
     echo '  [2] gNB: tunggu E2 Setup sukses (Pane 1)' && \
     echo '  [3] Hubungkan UE (HP) ke jaringan 5G' && \
     echo '' && \
     read -p '  >> Tekan ENTER setelah UE attach... ' && \
     touch '$PHASE2_FLAG' && \
     echo '' && \
     echo \"  xapp_sec_moni mulai di Pane 3.\" && \
     echo \"  Cell: \$_mode_label | Per-UE: \$_ids_label | Mitigasi: E2SM-RC aktif\" && \
     echo '  Pindah ke Window 1 (Record) untuk ganti label: Ctrl+B lalu 1'" Enter

# Pane 3: xapp_sec_moni + --mitigate (Kanan Bawah)
tmux send-keys -t "$SESSION:0.3" \
    "echo '=== [Pane 3] xapp_sec_moni + E2SM-RC MITIGATE — menunggu UE attach ===' && \
     until [ -f '$PHASE2_FLAG' ]; do sleep 1; done && \
     _det_mode=\$(cat '$MODE_FILE' 2>/dev/null || echo hybrid) && \
     _ids_mode=\$(cat '$IDS_MODE_FILE' 2>/dev/null || echo gru-hybrid) && \
     echo \"=== Memulai xapp_sec_moni | mode: \$_det_mode | ids-mode: \$_ids_mode | --mitigate ===\" && \
     '$XAPP_BIN' -c '$XAPP_CONF' --label 0 --mode \"\$_det_mode\" --ids-mode \"\$_ids_mode\" --mitigate" Enter

# Fokus ke Pane 2 (prompt) saat attach
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
# Window 2: xapp_sec_mitigate — E2SM-RC mitigation (IPC server)
# Start BEFORE xapp_sec_moni (it's the socket server)
# =============================================================
tmux new-window -t "$SESSION" -n "Mitigate"
tmux send-keys -t "$SESSION:2" \
    "echo '=== [Window 2] xapp_sec_mitigate — E2SM-RC Mitigation ===' && \
     echo '  Menunggu Near-RT RIC siap (Window 0 Pane 0)...' && \
     sleep 5 && \
     '$MITIGATE_BIN' \
       -c '$MITIGATE_CONF' --ue_f1ap 1 --mcc 001 --mnc 01 --sst 1" Enter

# Kembali ke Window 0 saat attach
tmux select-window -t "$SESSION:0"

echo ""
echo "Menjalankan: tmux attach -t $SESSION"
echo ""
tmux attach -t "$SESSION"
