#!/bin/bash
# =============================================================
# O-RAN Security xApp (C Version) — Startup Script
# =============================================================
# Prerequisite: tmux, sshpass, fuser
# Usage: ./start_xapp_c.sh
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
XAPP_CONF="/home/telmat/sec-xapp/deploy/ric/my_xapp_kpm.conf"
XAPP_DIR="/home/telmat/sec-xapp"

RAN_IP="10.91.2.1"
RAN_USER="telmat"
RAN_PASS="123"
RAN_GNB_DIR="/home/telmat/TA-Rizqi-Nabiel/O-RAN-Testbed-Automation/Next_Generation_Node_B"
RAN_GNB_BIN="./srsRAN_Project/build/apps/gnb/gnb"
RAN_GNB_CONF="configs/cots_n78_copied.yml"

PHASE2_FLAG="/tmp/xapp_c_phase2_start"
MODE_FILE="/tmp/xapp_detection_mode"
IDS_MODE_FILE="/tmp/xapp_ids_mode"
EXTRA_FLAGS_FILE="/tmp/xapp_extra_flags"

# =============================================================
# Cleanup — jalankan saat script exit (termasuk tmux kill-session)
# =============================================================
cleanup() {
    echo "[cleanup] Killing gNB on RAN node ($RAN_IP)..."
    sshpass -p "$RAN_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
        "$RAN_USER@$RAN_IP" "sudo pkill -9 gnb 2>/dev/null" 2>/dev/null || true
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
rm -f "$PHASE2_FLAG" "$MODE_FILE" "$IDS_MODE_FILE" "$EXTRA_FLAGS_FILE"
sleep 1

echo "=============================================="
echo "  O-RAN Security xApp (C) — Starting"
echo "  Attach: tmux attach -t $SESSION"
echo "=============================================="

# =============================================================
# Window 0: RAN + RIC — buat semua 4 pane dulu, baru isi
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

# Pane 2: Prompt — pilih mode + ids-mode + opsi tambahan + tunggu UE attach (Kiri Bawah)
tmux send-keys -t "$SESSION:0.2" \
    "echo '' && \
     echo '  ╔══════════════════════════════════════╗' && \
     echo '  ║   O-RAN Security xApp — Mode Setup   ║' && \
     echo '  ╚══════════════════════════════════════╝' && \
     echo '' && \
     echo '  [1/3] Cell-level detection (--mode):' && \
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
     echo '  [2/3] Per-UE IDS (--ids-mode):' && \
     echo '    1) rule-only     (dataset collection, tanpa ML)' && \
     echo '    2) lstm-hybrid   (Rule + LSTM-UE v4)' && \
     echo '    3) gru-hybrid    (Rule + GRU-UE v4)' && \
     echo '    4) lstm-only     (ablasi ML saja)' && \
     echo '    5) gru-only      (ablasi ML saja)' && \
     echo '' && \
     read -p '  >> Pilihan [1-5, default=1]: ' _ids_choice && \
     case \"\$_ids_choice\" in \
       2) echo 'lstm-hybrid' > '$IDS_MODE_FILE'; _ids_label=\"lstm-hybrid\" ;; \
       3) echo 'gru-hybrid'  > '$IDS_MODE_FILE'; _ids_label=\"gru-hybrid\" ;; \
       4) echo 'lstm-only'   > '$IDS_MODE_FILE'; _ids_label=\"lstm-only\" ;; \
       5) echo 'gru-only'    > '$IDS_MODE_FILE'; _ids_label=\"gru-only\" ;; \
       *) echo 'rule-only'   > '$IDS_MODE_FILE'; _ids_label=\"rule-only\" ;; \
     esac && \
     echo '' && \
     echo \"  Mode per-UE IDS: \$_ids_label\" && \
     echo '' && \
     echo '  [3/3] Opsi tambahan:' && \
     echo '    --no-cell  : nonaktifkan cell-level detection + cell CSV' && \
     echo '    --no-csv   : nonaktifkan semua penulisan training CSV' && \
     echo '    (kosongkan = aktifkan semua, default untuk dataset collection)' && \
     echo '' && \
     read -p '  >> Extra flags [contoh: --no-cell --no-csv, atau kosong]: ' _extra && \
     echo \"\$_extra\" > '$EXTRA_FLAGS_FILE' && \
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
     echo \"  Cell: \$_mode_label | Per-UE: \$_ids_label | Extra: \$_extra\" && \
     echo '  Pindah ke Window 1 (Record) untuk ganti label: Ctrl+B lalu 1'" Enter

# Pane 3: xapp_sec_moni — tunggu PHASE2_FLAG + baca mode, lalu mulai (Kanan Bawah)
tmux send-keys -t "$SESSION:0.3" \
    "echo '=== [Pane 3] xapp_sec_moni — menunggu UE attach ===' && \
     until [ -f '$PHASE2_FLAG' ]; do sleep 1; done && \
     _det_mode=\$(cat '$MODE_FILE' 2>/dev/null || echo hybrid) && \
     _ids_mode=\$(cat '$IDS_MODE_FILE' 2>/dev/null || echo rule-only) && \
     _extra=\$(cat '$EXTRA_FLAGS_FILE' 2>/dev/null || echo '') && \
     echo \"=== Memulai xapp_sec_moni | mode: \$_det_mode | ids-mode: \$_ids_mode | extra: \$_extra ===\" && \
     '$XAPP_BIN' -c '$XAPP_CONF' --label 0 --mode \"\$_det_mode\" --ids-mode \"\$_ids_mode\" \$_extra" Enter

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

# Kembali ke Window 0 saat attach
tmux select-window -t "$SESSION:0"

echo ""
echo "Menjalankan: tmux attach -t $SESSION"
echo ""
tmux attach -t "$SESSION"
