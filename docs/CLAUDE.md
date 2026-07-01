# Security xApp — AI Assistant Master Guide

**Terakhir diperbarui:** 10 Mei 2026 (rev 8 — C Native, hot-label, attack orchestration)  
**Working directory ini:** `~/sec-xapp` (clean copy dari `~/xapp/security-xapp/`)

---

## Konteks Penting untuk AI

### Apa yang berubah sejak versi lama

Versi lama (Python xApp, `~/xapp/security-xapp/`) sudah **digantikan sepenuhnya** oleh C native xApp. File Python seperti `real_monitor.py`, `dashboard.py`, `src/mitigation/mitigator.py`, `rrc_log_streamer.py` **tidak digunakan lagi** dan tidak ada di direktori ini.

**Mitigasi** sekarang dilakukan native di C via E2SM-RC (bukan iptables Python). Flag `--mitigate` opt-in.

**Label CSV** sekarang hot-swappable via `/tmp/xapp_label` di RIC — tidak perlu restart xApp untuk ganti skenario serangan.

---

## Topologi Testbed (10.91.2.0/24)

| Node | IP | Software | Lokasi |
|------|----|----------|--------|
| gNB (RAN) | `10.91.2.1` | srsRAN (gNB + E2 Agent) | `~/TA-Rizqi-Nabiel/.../srsRAN_Project` |
| Near-RT RIC | `10.91.2.2` | FlexRIC + xapp_sec_moni | `~/flexric/` |
| Core | `10.91.2.4` | Open5GS | `~/core/` |
| Controller | laptop | ADB + SSH orchestration | `~/xapp/security-scripts/` |

Interface kritis: E2AP `:36421` (RAN→RIC), E42 `:36422` (xApp→RIC), N3 GTP-U `:2152`, ogstun `10.45.0.0/24` (IP UE)

---

## File Utama di ~/sec-xapp

| File | Fungsi |
|------|--------|
| `start_xapp_c.sh` | Startup tmux: RIC + gNB (SSH) + xapp_sec_moni |
| `my_xapp_kpm.conf` | KPM subscription — `ngran_gNB_DU` only, E42: 10.91.2.2:36422 |
| `security_model.onnx` | ONNX model aktif (84 KB, MinMaxScaler + threshold P99.5 dibake) |
| `train_lstm.py` | Training LSTM-Autoencoder (train/val terpisah) |
| `export_onnx.py` | Export PyTorch → ONNX |
| `plot_training_evaluation.py` | Generate evaluation plot |
| `dataset_training.csv` | 60.306 baris benign, 120 menit, 2 UE |
| `dataset_validation.csv` | 15.756 baris benign, 31.5 menit |
| `models/lstm_autoencoder_v2.pt` | PyTorch checkpoint aktif |
| `models/lstm_autoencoder_v2_threshold.json` | P99.5=0.005035, FPR=0.50% |
| `models/scaler.pkl` | MinMaxScaler (fit dari training saja, rach_max=6) |
| `src/detection/lstm_autoencoder.py` | Definisi arsitektur LSTM |
| `src/detection/feature_schema.py` | 10 fitur + preprocessing |

**Binary C xApp** (bukan di sini, di-build dari flexric):
```
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni
```
**Source C xApp:**
```
/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c  (1440+ baris)
```

**Build:**
```bash
cd ~/flexric/build && make -j$(nproc) xapp_sec_moni
```

---

## Cara Menjalankan

### Normal (tanpa mitigasi — untuk dataset collection)
```bash
./start_xapp_c.sh
# → tmux attach -t xapp_c
# xapp_sec_moni berjalan tanpa --mitigate (detection-only, default)
```

### Dengan mitigasi (live demo)
```bash
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni \
    -c /home/telmat/xapp/security-xapp/my_xapp_kpm.conf --mitigate
```

### Toggle mitigasi
| Mode | Command | Kapan |
|------|---------|-------|
| Detection-only | tanpa `--mitigate` | Dataset collection, testing |
| Dengan mitigasi | `--mitigate` | Live demo |

---

## Hot-Label Switching

xApp membaca `/tmp/xapp_label` di RIC setiap ~120ms via `stat()` mtime cache.
Label berubah **tanpa restart xApp** — E2 session dan LSTM buffer tidak reset.

**Format:** `<label>,<scenario>,<attacker_ue>,<epoch_ms>`

**Dari controller:**
```bash
cd ~/xapp/security-scripts
./helpers/switch_label.sh 0 baseline none
./helpers/switch_label.sh 1 ul_flood UE1
./helpers/switch_label.sh 0 recovery none
```

**Audit trail** otomatis ke `logs/scenario_events.log`:
```
epoch_ms,event,label,scenario,attacker_ue,details
1746861235123,START,1,ul_flood,UE1,
```

---

## Attack Orchestration (di controller, bukan di sini)

Script serangan ada di **`~/xapp/security-scripts/`** (bukan di `~/sec-xapp`).

```
~/xapp/security-scripts/
├── attack_config.env          ← DEV1, DEV2, RIC_HOST, IPERF_PORT, dll
├── attacks/
│   ├── ul_flood.sh            ← S1: iperf3 UL 80M/120s via SSH→Termux
│   ├── dl_flood.sh            ← S2: iperf3 DL 100M/120s -R
│   ├── burst_onoff.sh         ← S3: randomized ON/OFF 120s
│   ├── signaling_storm.sh     ← S4: ADB airplane-mode toggle
│   └── jammer_burst.sh        ← S5: wrapper usrp_ssb_jamming.py
├── helpers/
│   ├── switch_label.sh        ← SSH → /tmp/xapp_label di RIC
│   ├── mark_event.sh          ← log manual event
│   ├── check_devices.sh       ← preflight: ADB + SSH + ping
│   └── setup_termux_ssh.sh    ← ADB port-forward Termux
└── ATTACK_RUNBOOK.md          ← panduan S0–S5 step-by-step
```

**Workflow dataset testing:**
```bash
cd ~/xapp/security-scripts
source attack_config.env
./helpers/setup_termux_ssh.sh
./helpers/check_devices.sh

./helpers/switch_label.sh 0 baseline none; sleep 120
./helpers/switch_label.sh 1 ul_flood UE1
./attacks/ul_flood.sh $DEV1 &; sleep 120; wait
./helpers/switch_label.sh 0 recovery none; sleep 60
# ... dst per ATTACK_RUNBOOK.md
```

---

## Skenario Serangan

| Label | Skenario | Expected KPI |
|-------|----------|--------------|
| 0 | Baseline / Recovery | PRB ≈ 0, CQI=15 |
| 1 | UL Flood (`iperf3 -u -b 80M -t 120`) | PRB_UL≈90%, prb_direction≈+1.0 |
| 2 | DL Flood (`iperf3 -u -R -b 100M -t 120`) | PRB_DL≈90%, prb_direction≈−1.0 |
| 3 | Burst ON/OFF (3–7s ON, 2–6s OFF) | prb_burst_index spike berulang |
| 4 | RRC Storm (airplane toggle 5–10s interval) | `empty_ind_rate` spike (≥2/window) — CQI tetap 15 di srsRAN (keep-last) |
| 5 | RF Burst Jammer (USRP B205 mini) | CQI drop, air_delay_ul naik |

---

## Feature Schema (10 Fitur)

> ⚠️ `DRB.UEThpDl/UL` dan `DRB.RlcSduVolume*` **selalu 0** di srsRAN KPM DU. PRB-only.

| # | Fitur | Sumber |
|---|-------|--------|
| 1 | `prb_usage_dl_ratio` | PrbUsedDl/(PrbUsedDl+PrbAvailDl) |
| 2 | `prb_usage_ul_ratio` | PrbUsedUl/(PrbUsedUl+PrbAvailUl) |
| 3 | `cqi` | CQI — **selalu 15 di srsRAN** (keep-last policy, tidak reset saat UE detach) |
| 4 | `rach_preamble` | RACH.PreambleDedCell — spike saat airplane toggle (max=6) |
| 5 | `air_delay_ul` | DRB.AirIfDelayUl — 40ms normal, 0ms saat detach |
| 6 | `prb_direction` | (prb_ul−prb_dl)/(prb_total+ε) bounded [−1,+1] |
| 7 | `prb_total` | prb_dl + prb_ul |
| 8 | `prb_dl_delta` | prb_dl[t] − prb_dl[t−1] |
| 9 | `prb_ul_delta` | prb_ul[t] − prb_ul[t−1] |
| 10 | `prb_burst_index` | log(1+prb_total)/(rolling_mean+ε) — fitur terbaik LSTM |

---

## LSTM-Autoencoder

- **Model aktif:** `models/lstm_autoencoder_v2.pt` — best epoch 98/100, FPR=0.50%
- **Threshold:** P99.5 = 0.005035 (dari validation set, bukan training)
- **ONNX:** `security_model.onnx` — MinMaxScaler + threshold dibake, input raw features
- **Input ONNX:** `[batch, 10, 10]` (10 timestep × 10 fitur) — tidak perlu preprocessing di C
- **Scaler:** rach_preamble max=6 (domain-known, bukan dari data)

**Pipeline training → deploy:**
```bash
# 1. Train
./venv/bin/python3 train_lstm.py \
    --train dataset_training.csv --val dataset_validation.csv \
    --model-out models/lstm_autoencoder_v2.pt

# 2. Export ONNX
./venv/bin/python3 export_onnx.py   # → security_model.onnx

# 3. Rebuild xApp
cd ~/flexric/build && make -j$(nproc) xapp_sec_moni
```

---

## Mitigasi C xApp

**Primer: E2SM-RC PRB Throttle** (opt-in via `--mitigate`)
- Style 2, Action 6, Throttle: max=5% → Restore: max=100%
- Cooldown 30s, auto-restore 10s setelah severity=0
- PLMN: 00101, SST=1 (eMBB), SD=0

**Fallback: SSH AMF Barring** (manual, untuk signaling storm)
```bash
ssh telmat@10.91.2.4 "sudo open5gs-dbctl subscriber_status <IMSI> 1 1"
```

**Efektivitas:**
- UL/DL Flood, Burst → E2SM-RC efektif
- Signaling Storm → SSH AMF (PRB throttle tidak efektif control-plane)
- RF Jamming → tidak bisa dimitigasi via E2

---

## Known Issues

| Issue | Detail |
|-------|--------|
| ~~srsRAN RC Bug #468~~ | **Resolved** (patch merged Mei 2024, gckopper/wdgj). E2SM-RC PRB throttle aktif di `start_xapp_c_mitigate.sh` via `--mitigate`. |
| **DRB metrics selalu 0** | srsRAN KPM DU tidak melaporkan throughput. PRB-only feature. |
| **ONNX IR version mismatch** | Warning IR v10 vs max v9 saat startup — tidak mempengaruhi fungsionalitas. |
| **TCP speedtest FP** | Rule 1 punya bidirectional guard (PRB_UL<3% untuk DL flood). |
| **Signaling storm mitigation** | PRB throttle tidak efektif control-plane — fallback SSH AMF. |
| **CQI keep-last (srsRAN bug)** | srsRAN KPM DU tidak mereset CQI ke 0 saat UE detach — CQI selalu 15. Rule 2 (cqi<5) tidak bekerja. Gunakan Rule 3b via `empty_ind_rate` sebagai gantinya. |
| **srsRAN SIZE(0) MeasurementData** | srsRAN mengirim KPM Indication dengan 0 measurement record saat UE detach, melanggar ASN.1 SIZE(1..65535). FlexRIC decoder menolak pesan ini. Kegagalan decode ini dimanfaatkan sebagai proxy signal RRC storm (`empty_ind_rate`). |

---

## Pasca Sesi Testing — Ambil Data dari RIC

```bash
scp telmat@10.91.2.2:~/xapp/security-xapp/training_*.csv ~/sec-xapp/
scp telmat@10.91.2.2:~/xapp/security-xapp/logs/scenario_events.log ~/sec-xapp/logs/
awk -F, 'NR>1{print $NF}' training_*.csv | sort | uniq -c
```
