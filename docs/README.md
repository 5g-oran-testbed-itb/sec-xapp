# Security xApp — O-RAN Near-RT RIC

**Versi:** C Native (rev 7, 10 Mei 2026)  
**Platform:** FlexRIC + srsRAN + Open5GS — Testbed Fisik 5G SA (band n78, QAM256)

Sistem deteksi anomali Near-RT untuk jaringan O-RAN berbasis LSTM-Autoencoder + Rule-Based IDS, diimplementasikan sebagai xApp C native di Near-RT RIC. Mampu mendeteksi serangan layer data, kontrol, dan fisik dalam <300ms (rule-based) atau <1 detik (LSTM).

---

## Topologi Testbed

```
┌─────────────────────────────────────────────────┐
│           CONTROLLER (Laptop)                   │
│  ~/xapp/security-scripts/                       │
│  ├── attacks/   (ul_flood, dl_flood, ...)       │
│  ├── helpers/   (switch_label, check_devices)   │
│  └── ATTACK_RUNBOOK.md                          │
│                                                 │
│  ADB ──── USB ──► UE-1 (Oppo Reno 8)           │
│  ADB ──── USB ──► UE-2 (Oppo Reno 8)           │
│  SSH ──────────►  RIC (10.91.2.2)              │
└─────────────────────────────────────────────────┘
                    ↕ 5G NR n78 + QAM256
┌─────────────────────────────────────────────────┐
│           O-RAN TESTBED (LAN 10.91.2.0/24)     │
│  srsRAN gNB      10.91.2.1   E2 Agent          │
│  Near-RT RIC     10.91.2.2   FlexRIC           │
│  └─ xapp_sec_moni ← /tmp/xapp_label            │
│  Open5GS Core    10.91.2.4   UPF: 10.45.0.0/24 │
└─────────────────────────────────────────────────┘
```

| Node | IP | Software | Path |
|------|----|----------|------|
| gNB (RAN) | `10.91.2.1` | srsRAN (gNB + E2 Agent) | `~/TA-Rizqi-Nabiel/.../srsRAN_Project` |
| Near-RT RIC | `10.91.2.2` | FlexRIC + xapp_sec_moni | `~/flexric/` |
| Core | `10.91.2.4` | Open5GS | `~/core/` |
| xApp source | `10.91.2.2` | C native | `~/flexric/examples/xApp/c/monitor/` |

---

## Arsitektur Sistem

```
KPM Indication (~120ms efektif dari srsRAN DU)
        │
        ▼
  sm_cb_kpm()  [xapp_sec_moni.c]
        │
        ├─► Hitung 10 fitur dari raw KPM
        │
        ├─► rule_based_detect()  ← ~100–300ms latency
        │         ├─ Rule 1: PRB Overload >90% (3 windows, CRITICAL)
        │         ├─ Rule 2: Signaling Storm heuristic (CQI/RACH guard)
        │         ├─ Rule 3: RACH Spike
        │         ├─ Rule 4: UL Flood (3 windows)
        │         ├─ Rule 5: DL Flood (3 windows)
        │         └─ Rule 6: High UL Delay (jamming proxy)
        │
        ├─► csv_trainer_write()
        │       ├─ maybe_reload_label()  ← baca /tmp/xapp_label (stat mtime cache)
        │       └─ tulis training_YYYYMMDD_HHMMSS.csv
        │
        └─► run_inference()  [ONNX Runtime C API]
                  LSTM-Autoencoder window 10 × 120ms = ~1.2 detik
                  Threshold P99.5 = 0.005035  →  score > 0.5 = anomali

main() loop (setiap 1 detik):
        └─► g_pending_throttle → rc_send_prb_quota() via E2SM-RC
```

---

## File Utama

```
sec-xapp/
├── start_xapp_c.sh              ← startup tmux (RIC + gNB + xApp)
├── record_dataset.sh            ← recording dengan label manual
├── my_xapp_kpm.conf             ← KPM subscription config (E42: 10.91.2.2:36422)
│
├── train_lstm.py                ← training LSTM-Autoencoder (train/val terpisah)
├── export_onnx.py               ← export PyTorch → security_model.onnx
├── plot_training_evaluation.py  ← generate evaluation plot
│
├── security_model.onnx          ← model aktif untuk C inference (84 KB)
├── security_model.onnx.data     ← ONNX weights
│
├── dataset_training.csv         ← 60.306 baris benign (120 menit, 2 UE)
├── dataset_validation.csv       ← 15.756 baris benign (31.5 menit)
│
├── models/
│   ├── lstm_autoencoder_v2.pt          ← PyTorch checkpoint (model aktif)
│   ├── lstm_autoencoder_v2_threshold.json  ← P99.5=0.005035, FPR=0.50%
│   ├── lstm_autoencoder_v2_losses.json ← loss history per epoch
│   ├── scaler.pkl               ← MinMaxScaler (fit dari training saja)
│   ├── training_evaluation_v2.png
│   └── lstm_autoencoder_v2/     ← ONNX serialization PyTorch
│
├── src/detection/
│   ├── lstm_autoencoder.py      ← definisi arsitektur LSTM-Autoencoder
│   ├── feature_schema.py        ← daftar 10 fitur dan preprocessing
│   └── detector.py              ← inference wrapper Python (untuk analisis)
│
├── logs/
│   ├── alerts.log               ← log deteksi anomali
│   └── scenario_events.log      ← audit trail tiap ganti label serangan
│
├── gnb_usrp.yaml                ← gNB config (USRP B205 mini)
├── cots_n78_copied.yml          ← gNB config (COTS UE, band n78, aktif)
├── config/config.yaml           ← config jaringan (IP, credentials)
└── requirements.txt             ← Python deps (PyTorch, ONNX, sklearn)
```

**Binary C xApp** (di-build terpisah, bukan di folder ini):
```
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni
```

---

## Cara Menjalankan

### Startup Normal

```bash
cd ~/sec-xapp
./start_xapp_c.sh
# Attach: tmux attach -t xapp_c
```

Membuka tmux session `xapp_c`:
```
Window 0 "RAN+RIC":
  ┌─────────────────────┬─────────────────────┐
  │ Near-RT RIC         │ srsGNB (SSH)        │
  ├─────────────────────┼─────────────────────┤
  │ Prompt — tekan ENTER│ xapp_sec_moni       │
  │ setelah UE attach   │ (hot-label aktif)   │
  └─────────────────────┴─────────────────────┘
Window 1 "Record": petunjuk record_dataset.sh
```

Alur:
1. Tunggu `E2AP listening on :36421` di Pane Near-RT RIC
2. Tunggu E2 Setup sukses di Pane gNB
3. Hubungkan UE ke jaringan 5G
4. Tekan ENTER di Pane kiri bawah → xapp_sec_moni mulai otomatis

### Manual (tanpa script)

```bash
# RIC
/home/telmat/flexric/build/examples/ric/nearRT-RIC

# xApp — detection + recording (default, aman)
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni \
    -c /home/telmat/xapp/security-xapp/my_xapp_kpm.conf

# xApp — dengan mitigasi RC PRB throttle (opt-in, lihat Known Issues)
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni \
    -c /home/telmat/xapp/security-xapp/my_xapp_kpm.conf --mitigate
```

### Build xApp

```bash
cd ~/flexric/build
make -j$(nproc) xapp_sec_moni
```

---

## Hot-Label Switching

Label CSV dapat diganti **tanpa restart xApp** via file `/tmp/xapp_label` di RIC. xApp membaca file ini setiap write cycle (~120ms) menggunakan `stat()` mtime cache — tidak ada overhead fopen kecuali file berubah.

### Format file label

```
<label>,<scenario>,<attacker_ue>,<epoch_ms>
```

Contoh:
```
0,baseline,none,1746861115000
1,ul_flood,UE1,1746861235123
0,recovery,none,1746861480000
```

### Dari controller (laptop)

```bash
cd ~/xapp/security-scripts
source attack_config.env

./helpers/switch_label.sh 0 baseline none
./helpers/switch_label.sh 1 ul_flood UE1
./helpers/switch_label.sh 0 recovery none
```

Setiap perubahan label dicatat ke `logs/scenario_events.log`:
```
epoch_ms,event,label,scenario,attacker_ue,details
1746861235123,START,1,ul_flood,UE1,
```

---

## Skenario Serangan

Semua script ada di `~/xapp/security-scripts/`. Panduan lengkap: `~/xapp/security-scripts/ATTACK_RUNBOOK.md`.

| Label | Skenario | Script | Expected KPI |
|-------|----------|--------|--------------|
| 0 | Baseline normal | — | PRB ≈ 0, CQI = 15 |
| 1 | UL Flood | `attacks/ul_flood.sh $DEV` | PRB_UL ≈ 90%, prb_direction ≈ +1.0 |
| 2 | DL Flood | `attacks/dl_flood.sh $DEV` | PRB_DL ≈ 90%, prb_direction ≈ −1.0 |
| 3 | Burst ON/OFF | `attacks/burst_onoff.sh $DEV` | prb_burst_index spike berulang |
| 4 | RRC/Signaling Storm | `attacks/signaling_storm.sh $DEV` | CQI → 0, RACH spike |
| 5 | RF Burst Jammer | `attacks/jammer_burst.sh` | CQI drop, air_delay_ul naik |

**Struktur sesi per skenario (role swapping):**
```
Phase A (2 mnt): kedua UE benign               [label=0]
Phase B (2 mnt): UE-1 attacker, UE-2 benign   [label=N]
Phase C (1 mnt): recovery                      [label=0]
Phase D (2 mnt): UE-2 attacker, UE-1 benign   [label=N]
Phase E (1 mnt): recovery                      [label=0]
```

---

## Feature Schema (10 Fitur KPM)

> ⚠️ `DRB.UEThpDl/UL` dan `DRB.RlcSduVolumeDL/UL` **selalu 0** di srsRAN KPM DU. Hanya PRB yang reliabel.

| # | Fitur | Sumber | Range |
|---|-------|--------|-------|
| 1 | `prb_usage_dl_ratio` | `PrbUsedDl / (PrbUsedDl + PrbAvailDl)` | 0–1 |
| 2 | `prb_usage_ul_ratio` | `PrbUsedUl / (PrbUsedUl + PrbAvailUl)` | 0–1 |
| 3 | `cqi` | `CQI` | 0 (detach) / 15 (connected) |
| 4 | `rach_preamble` | `RACH.PreambleDedCell` | 0–6 (spike saat RRC churn) |
| 5 | `air_delay_ul` | `DRB.AirIfDelayUl` | 0ms (detach) / 40ms (normal) |
| 6 | `prb_direction` | `(prb_ul − prb_dl) / (prb_total + ε)` | −1 (pure DL) … +1 (pure UL) |
| 7 | `prb_total` | `prb_dl + prb_ul` | 0–2 |
| 8 | `prb_dl_delta` | `prb_dl[t] − prb_dl[t−1]` | nonzero di transisi |
| 9 | `prb_ul_delta` | `prb_ul[t] − prb_ul[t−1]` | nonzero di transisi |
| 10 | `prb_burst_index` | `log(1 + prb_total) / (rolling_mean + ε)` | fitur terbaik LSTM |

**Separasi per skenario:**

| Kondisi | `prb_dl` | `prb_ul` | `prb_direction` | `cqi` | `rach` |
|---------|----------|----------|-----------------|-------|--------|
| Idle | ≈ 0 | ≈ 0 | ≈ 0 | 15 | 0 |
| DL Flood | ≈ 0.99 | ≈ 0.04 | ≈ −0.90 | 15 | 0 |
| UL Flood | ≈ 0.00 | ≈ 0.87 | ≈ +1.00 | 15 | 0 |
| RRC Storm | ≈ 0.09 | ≈ 0 | −1.0 | 0 | 1–6 |

---

## LSTM-Autoencoder

**Arsitektur:**
```
Input: [batch, sequence=10, features=10]
  Encoder LSTM: hidden [64, 32] → latent_dim=32
  Decoder LSTM: hidden [32, 64]
Output: [batch, sequence=10, features=10]
Loss: MSE reconstruction error
```

**Model aktif (v2, 10 Mei 2026):**

| Parameter | Nilai |
|-----------|-------|
| Training sequences | 60.297 |
| Validation sequences | 15.756 |
| Best epoch | 98 / 100 |
| Final train loss | 0.001287 |
| Final val loss | 0.001267 |
| Threshold (P99.5) | 0.005035 |
| FPR benign | 0.50% |

Training **hanya pada data benign (label=0)**. Threshold dihitung dari validation set.

**Pipeline training → deploy:**

```bash
cd ~/sec-xapp

# 1. Training
./venv/bin/python3 train_lstm.py \
    --train dataset_training.csv \
    --val   dataset_validation.csv \
    --model-out models/lstm_autoencoder_v2.pt

# 2. Evaluation plot
./venv/bin/python3 plot_training_evaluation.py \
    --train dataset_training.csv \
    --val   dataset_validation.csv \
    --model models/lstm_autoencoder_v2.pt

# 3. Export ke ONNX
./venv/bin/python3 export_onnx.py

# 4. Rebuild xApp
cd ~/flexric/build && make -j$(nproc) xapp_sec_moni
```

---

## Mitigasi

### Primer: E2SM-RC PRB Throttle (O-RAN Compliant)

Diaktifkan dengan flag `--mitigate` (default OFF karena srsRAN RC Bug #468).

| Parameter | Nilai |
|-----------|-------|
| RC Control Style | 2 (Radio Resource Allocation) |
| Throttle | max=5%, dedicated=5%, min=0% |
| Restore | max=100%, dedicated=100% |
| Cooldown | 30 detik antar throttle |
| PLMN | 00101 (MCC=001, MNC=01) |
| Auto-restore | 10 detik setelah severity kembali 0 |

### Fallback: SSH AMF Barring (Signaling Storm)

```bash
# Blokir UE
ssh telmat@10.91.2.4 "sudo open5gs-dbctl subscriber_status <IMSI> 1 1"
# Restore
ssh telmat@10.91.2.4 "sudo open5gs-dbctl subscriber_status <IMSI> 0 0"
```

### Efektivitas

| Serangan | Plane | PRB Throttle | SSH AMF |
|----------|-------|-------------|---------|
| UL / DL Flood | Data | ✅ Efektif | ⚠️ Overkill |
| Burst ON/OFF | Data | ✅ Efektif | ⚠️ Overkill |
| Signaling Storm | Control | ❌ Tidak efektif | ✅ Satu-satunya |
| RF Jamming | Physical | ❌ | ❌ |

---

## Performa Deteksi

| Serangan | Rule-Based | LSTM | Latency | Severity |
|----------|-----------|------|---------|----------|
| UL Flood | ✅ | ✅ | ~300ms | CRITICAL |
| DL Flood | ✅ | ✅ | ~300ms | CRITICAL |
| Burst ON/OFF | ✅ | ✅ | ~100ms | CRITICAL |
| Signaling Storm | ✅ (RACH proxy) | ⚠️ Parsial | ~300ms | WARNING |
| RF Burst | ⚠️ (air_delay proxy) | ⚠️ Parsial | — | WARNING |

---

## Recording Dataset

Dataset collection menggunakan **hot-label switching** — label berubah tanpa restart xApp.

```bash
cd ~/xapp/security-scripts
./helpers/switch_label.sh 0 baseline none   # mulai record normal
./helpers/switch_label.sh 1 ul_flood UE1   # saat attack UL aktif
./helpers/switch_label.sh 0 recovery none  # setelah attack selesai
```

**Dataset aktif:**

| File | Baris | Durasi | Keterangan |
|------|-------|--------|------------|
| `dataset_training.csv` | 60.306 | 120 mnt | Benign (label=0), 2 UE |
| `dataset_validation.csv` | 15.756 | 31.5 mnt | Benign (label=0) |

**Ambil data dari RIC:**
```bash
scp telmat@10.91.2.2:~/xapp/security-xapp/training_*.csv ./
scp telmat@10.91.2.2:~/xapp/security-xapp/logs/scenario_events.log ./logs/
awk -F, 'NR>1{print $NF}' training_*.csv | sort | uniq -c   # cek distribusi label
```

---

## Known Issues

| Issue | Keterangan |
|-------|------------|
| **srsRAN RC Bug #468** | gNB crash setelah menerima RC Control. `--mitigate` opt-in saja. |
| **DRB metrics selalu 0** | `DRB.UEThpDl/UL`, `DRB.RlcSduVolume*` tidak dilaporkan srsRAN. Feature schema PRB-only. |
| **ONNX IR version mismatch** | Warning saat startup (IR v10 vs max v9). Score tetap dihitung, rule-based yang jadi acuan utama. |
| **TCP speedtest FP Rule 1** | Guard bidirectional (PRB_UL < 3% untuk DL flood) mencegah FP saat TCP speedtest. |
| **Signaling Storm mitigation** | PRB throttle tidak efektif control-plane. Fallback: SSH AMF barring. |

---

## Next Steps

- [ ] Kumpulkan dataset test per attack label 1–5 dengan role swapping (gunakan `ATTACK_RUNBOOK.md`)
- [ ] Validasi IDS: iperf3 30M/50M → pastikan tidak ada FP Rule 1/2
- [ ] Evaluasi metrik: detection latency, TPR, FPR, ROC-AUC dari test dataset
- [ ] Validasi RC throttle: UL flood → cek PRB UE turun setelah deteksi
- [ ] Tuning threshold Rule-Based IDS dari data eksperimen nyata
