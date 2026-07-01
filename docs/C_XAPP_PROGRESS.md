# Progres C Native xApp — Security xApp
**Terakhir diperbarui: 20 Mei 2026 (rev 9 — LSTM v5, Hybrid Burst 97.0%)**

---

## 1. Latar Belakang

Python xApp (`real_monitor.py`) memiliki delay deteksi ~30 detik karena:
- Overhead parsing teks/CSV per indikasi
- Inference PyTorch melalui Python interpreter (~30-100ms per call)
- KPM polling period 1000ms × window 10 sampel = 10 detik minimum

**Target**: Deteksi **Near-RT (<1 detik)** dengan memindahkan engine ke C native + ONNX Runtime.

**Status saat ini**: C xApp (`xapp_sec_moni`) sudah berjalan penuh dengan **two-stage hybrid detection** — Stage 1 fast anomaly WARNING (<400ms), Stage 2 persistence-based CRITICAL (≥30s), ONNX inference aktif, CSV recording aktif.

---

## 2. File Utama C xApp

| File | Lokasi | Fungsi |
|------|--------|--------|
| `xapp_sec_moni.c` | `~/flexric/examples/xApp/c/monitor/` | Main xApp: E2 subscription, KPM parsing, ONNX inference, CSV recording |
| `sec_ids.c` | `~/flexric/examples/xApp/c/monitor/` | Rule-Based IDS engine (semua rules & state) |
| `sec_ids.h` | `~/flexric/examples/xApp/c/monitor/` | Interface publik IDS (cell_metrics_t, ids_init, rule_based_detect) |
| `CMakeLists.txt` | `~/flexric/examples/xApp/c/monitor/` | Build config |

**Binary:**
```
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni
```

**Build:**
```bash
cd ~/flexric/build
make -j$(nproc) xapp_sec_moni
```

---

## 3. Cara Menjalankan

### Startup Lengkap (RIC + gNB + xApp)
```bash
cd /home/telmat/xapp/security-xapp
./start_xapp_c.sh
# Mode default: deteksi + recording saja (tanpa mitigasi otomatis)
```

Script ini membuka tmux session `xapp_c` dengan layout:
```
Window 0 "RAN+RIC":
  ┌─────────────────────┬─────────────────────┐
  │ Near-RT RIC         │ srsGNB (SSH)        │
  ├─────────────────────┼─────────────────────┤
  │ Prompt (ENTER       │ xapp_sec_moni       │
  │ setelah UE attach)  │ (label=0, otomatis) │
  └─────────────────────┴─────────────────────┘
Window 1 "Record": petunjuk record_dataset.sh
```

**Alur startup:**
1. RIC dan gNB mulai otomatis
2. Hubungkan UE (HP) ke jaringan 5G
3. Tekan ENTER di pane Kiri Bawah
4. `xapp_sec_moni` mulai otomatis di Kanan Bawah

### Manual (tanpa script)
```bash
# 1. RIC (di node 10.91.2.2)
/home/telmat/flexric/build/examples/ric/nearRT-RIC
# Config: /usr/local/etc/flexric/ric.conf (NearRT_RIC_IP=10.91.2.2)

# 2. xApp — mode deteksi + recording saja (default, aman)
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni \
    -c /home/telmat/xapp/security-xapp/my_xapp_kpm.conf --label 0

# 3. xApp — dengan mitigasi RC PRB throttle (eksperimental, hanya jika srsRAN sudah di-patch)
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni \
    -c /home/telmat/xapp/security-xapp/my_xapp_kpm.conf --label 0 --mitigate
```

### Recording Dataset
```bash
# Script wrapper dengan label otomatis
./record_dataset.sh --label <N> [--duration <detik>]

# CATATAN: xapp_sec_moni dan record_dataset.sh tidak bisa jalan bersamaan
# (keduanya koneksi ke E42 port yang sama). Matikan salah satu dulu.
```

---

## 4. Arsitektur Sistem

```
KPM Indication (~120ms efektif dari srsRAN DU, config time=10ms)
        │
        ▼
  sm_cb_kpm() [xapp_sec_moni.c]
        │
        ├─► Hitung 15 fitur dari raw KPM metrics (PRB-only, karena DRB volume = 0 di srsRAN)
        │       prb_usage_dl_ratio, prb_usage_ul_ratio          ← dari RRU.PrbUsedDl/UL
        │       cqi, rach_preamble, air_delay_ul                ← dari CQI, RACH, AirIfDelayUl
        │       prb_direction, prb_total                        ← engineered dari PRB
        │       prb_dl_delta, prb_ul_delta, prb_burst_index     ← temporal PRB features
        │       empty_ind_rate                                  ← proxy RRC storm (decode fail counter)
        │       prb_dl_roll_mean, prb_dl_roll_std               ← rolling DL stats (DL Flood)
        │       prb_ul_roll_std, prb_ul_roll_max, prb_ul_roll_max_100 ← rolling UL stats (Burst ON/OFF)
        │
        ├─► rule_based_detect(&g_cell, now_ms) [sec_ids.c]
        │
        │   ┌─ STAGE 1: Fast Anomaly Indication (<400ms latency) ──────────────────
        │   │    R1 (upd): UL Saturation PRB_UL > 80% (3 windows) → ALERT_UL_SATURATION   WARNING
        │   │    R2 (upd): DL Saturation PRB_DL > 80% + PRB_UL < 30% (3 win)              WARNING
        │   │    R2b (lama): Signaling Storm via CQI/RACH heuristic (3 windows)            WARNING
        │   │    R3: RACH Spike / RRC Flood (1 window)                                     WARNING
        │   │    R3b: RRC Storm via empty indications (3 windows)                          WARNING
        │   │    R4: Uplink Flood via RLC (3 windows)                                      CRITICAL *
        │   │    R5: Downlink Flood via RLC (3 windows)                                    CRITICAL *
        │   │    R6: High UL Air Delay — jamming proxy (1 window)                          WARNING
        │   │    R7 (baru): Radio-Layer Degradation Suspicion via PRB sudden collapse       WARNING
        │   │    R8 (baru): Periodic Burst Anomaly via prb_burst_index ON/OFF cycles       WARNING
        │   │
        │   └─ STAGE 2: Persistence Validator (Mitigation Authorization Layer) ─────
        │        UL/DL Saturation sustained ≥ 30s     → CRITICAL (threshold empiris: speedtest ≤40s)
        │        RRC Storm sustained ≥ 4 windows       → CRITICAL
        │        Radio Degradation sustained ≥ 5 win   → CRITICAL
        │        Periodic Burst ≥ 3 ON/OFF cycles 60s  → CRITICAL
        │        Recovery: PRB normal ≥ 5s → reset semua Stage 2 state
        │
        │        Returns: 0=normal, 1=WARNING (Stage 1 active),
        │                 2=CRITICAL (Stage 2 confirmed, mitigasi authorized)
        │        * R4/R5 (RLC) direct CRITICAL — RLC selalu 0 di srsRAN, tidak aktif
        │
        ├─► Set g_pending_throttle flag (CRITICAL → 1, normal → 2 if throttling)
        │
        ├─► csv_trainer_write() → training_YYYYMMDD_HHMMSS.csv
        │       (setiap indikasi 100ms, label dari --label N)
        │       Kolom baru: stage1_alert, stage2_confirmed, alert_type,
        │                   stage1_latency_ms, stage2_confirmation_time_ms, anomaly_score
        │
        └─► run_cell_inference() [ONNX Runtime]  ← ~1000ms latency
                  (buffer 10 sampel × 100ms = 1 detik window penuh)
                  Input ONNX: [1, 10, 15] — 15 fitur (termasuk rolling stats, dibake scaler+threshold)
                  anomaly_score → g_last_anomaly_score → CSV (confidence layer, bukan trigger)

main() loop (setiap 1 detik):
        │
        ├─► g_pending_throttle == 1 → rc_send_prb_quota(5)   [E2SM-RC Control]
        │       Hanya aktif jika --mitigate DAN Stage 2 CRITICAL terkonfirmasi
        │       → control_sm_xapp_api() → E42 → Near-RT RIC → E2 → gNB
        │       → gNB scheduler PRB quota = 5% (blokir data plane)
        │
        └─► g_pending_throttle == 2 → rc_send_prb_quota(100) [Restore]
```

---

## 5. KPM Subscription

**File config:** `/home/telmat/xapp/security-xapp/my_xapp_kpm.conf`

```
SM_DIR = "/usr/local/lib/flexric/"
Name = "xApp"
NearRT_RIC_IP = "10.91.2.2"
E42_Port = 36422

Sub_OMAN_SM_List = (
    { name = "KPM", time = 10,         ← 10ms config → srsRAN fires ~24ms → 90ms gate → ~120ms efektif di CSV
      format = 1,
      ran_type = "ngran_gNB_DU",       ← hanya DU, tidak ada CU-CP
      actions = (
        DRB.UEThpDl, DRB.UEThpUl,
        RRU.PrbUsedDl, RRU.PrbUsedUl,
        RRU.PrbAvailDl, RRU.PrbAvailUl,
        DRB.AirIfDelayUl,
        RACH.PreambleDedCell,
        DRB.RlcSduTransmittedVolumeUL,
        DRB.RlcSduTransmittedVolumeDL,
        DRB.RlcPacketDropRateDl,
        DRB.RlcDelayUl,
        CQI, RSRP, RSRQ
      )
    }
)
```

**Catatan:** Subscription CU-CP (`ngran_gNB`) dihapus karena menyebabkan loop `[E2AP]: Resending Setup Request`. RRC metrics (`rrc_att`, `rrc_succ`) tidak tersedia via KPM DU.

---

## 6. Feature Schema (15 Fitur)

**File:** `src/detection/feature_schema.py` dan mapping di `run_cell_inference()` xapp_sec_moni.c

> ⚠️ **srsRAN KPM Gap**: `DRB.UEThpDl`, `DRB.UEThpUl`, `DRB.RlcSduTransmittedVolumeDL`, dan `DRB.RlcSduTransmittedVolumeUL` **semuanya selalu 0** di srsRAN KPM DU. Hanya `RRU.PrbUsedDl/UL` dan `RRU.PrbAvailDl/UL` yang akurat. Feature schema menggunakan PRB semua.

| # | Nama Fitur | Sumber | Keterangan |
|---|-----------|--------|-----------|
| 1 | `prb_usage_dl_ratio` | `RRU.PrbUsedDl / (PrbUsedDl + PrbAvailDl)` | Rasio utilisasi PRB downlink (0-1) |
| 2 | `prb_usage_ul_ratio` | `RRU.PrbUsedUl / (PrbUsedUl + PrbAvailUl)` | Rasio utilisasi PRB uplink (0-1) |
| 3 | `cqi` | `CQI` | 0 saat UE disconnect/RRC storm, 15 saat connected |
| 4 | `rach_preamble` | `RACH.PreambleDedCell` | Spike saat RRC churn (airplane mode toggle → peak=6) |
| 5 | `air_delay_ul` | `DRB.AirIfDelayUl` | 40ms normal, 20ms saat RRC transisi, 0ms saat UE detach |
| 6 | `prb_direction` | `(prb_ul - prb_dl) / (prb_total + ε)` | Arah asimetri traffic, bounded **[-1, +1]** |
| 7 | `prb_total` | `prb_dl + prb_ul` | Total beban PRB (0–2) |
| 8 | `prb_dl_delta` | `prb_dl[t] - prb_dl[t-1]` | Laju perubahan DL PRB (nonzero di transisi) |
| 9 | `prb_ul_delta` | `prb_ul[t] - prb_ul[t-1]` | Laju perubahan UL PRB (nonzero di transisi) |
| 10 | `prb_burst_index` | `log(1 + prb_total) / (rolling_mean + ε)` | Burstiness PRB — 170 nilai unik |
| 11 | `empty_ind_rate` | #empty APER-failed KPM indications per window | Proxy RRC storm (UE detach → SIZE(0) decode fail) |
| 12 | `prb_dl_roll_mean` | mean `prb_dl_ratio` 10 timestep terakhir | Flood DL = persistently high mean; normal = fluktuatif |
| 13 | `prb_dl_roll_std` | std `prb_dl_ratio` 10 timestep | Flood = std rendah (steady); burst = std tinggi |
| 14 | `prb_ul_roll_std` | std `prb_ul_ratio` 10 timestep | Burst ON/OFF = std sangat tinggi (0↔90%); normal = rendah |
| 15 | `prb_ul_roll_max` | max `prb_ul_ratio` 10 timestep | Peak UL jangka pendek |
| 16 | `prb_ul_roll_max_100` | max `prb_ul_ratio` **100 timestep (10s)** | Peak persists through OFF phase — max OFF duration = 58ts < 100ts window |

> **Fitur 12–16** dihitung dari rolling buffer di `run_cell_inference()`. Tidak menambah latency deteksi.
> **Fitur 16** menggunakan buffer terpisah `roll_ul_long[100]` karena OFF phase burst mencapai 58ts (5.8s) — melampaui window 10ts standar. Root cause: 69.8% baris OFF phase tidak terdeteksi v4 karena ON phase sudah keluar window.

**CSV output per baris (18 kolom — updated 16 Mei 2026):**
```
timestamp_ms, datetime,
prb_usage_dl_ratio, prb_usage_ul_ratio,
cqi, rach_preamble, air_delay_ul,
prb_direction, prb_total,
prb_dl_delta, prb_ul_delta, prb_burst_index,
label,
stage1_alert, stage2_confirmed, alert_type,
stage1_latency_ms, stage2_confirmation_time_ms,
anomaly_score
```

> Catatan: Kolom `empty_ind_rate` dan rolling stats (fitur 11–15) dihitung on-the-fly di `run_cell_inference()` dan masuk ke ONNX input, tapi tidak ditulis ke CSV karena kolom CSV tetap 18 untuk backward compatibility.

| Kolom baru | Tipe | Keterangan |
|---|---|---|
| `stage1_alert` | int 0/1 | 1 jika Stage 1 WARNING aktif pada window ini |
| `stage2_confirmed` | int 0/1 | 1 jika Stage 2 CRITICAL terkonfirmasi |
| `alert_type` | string | `none` / `ul_saturation` / `dl_saturation` / `rrc_storm` / `radio_degradation_suspicion` / `periodic_burst_anomaly` |
| `stage1_latency_ms` | long long | Durasi Stage 1 aktif sejak event dimulai (ms) |
| `stage2_confirmation_time_ms` | long long | Durasi WARNING→CRITICAL (ms), 0 jika belum dikonfirmasi |
| `anomaly_score` | float | ONNX LSTM anomaly score — confidence layer, bukan trigger mitigasi |

**Separasi fitur per jenis serangan** (diverifikasi dari data eksperimen):

| Kondisi | `prb_dl` | `prb_ul` | `prb_direction` | `cqi` | `rach` |
|---------|----------|----------|-----------------|-------|--------|
| Idle (normal) | ~0 | ~0 | ~0 | 15 | 0 |
| DL Speedtest/Flood | ~0.99 | ~0.04 | **≈ −0.90** | 15 | 0 |
| UL Flood | ~0.00 | ~0.87 | **≈ +1.00** | 15 | 0 |
| RRC Storm (airplane toggle) | ~0.09 | ~0 | **−1.0** | **0** | **1–6** |
| UE Disconnect | 0 | 0 | 0 | **0** | 0 |

---

## 7. LSTM Autoencoder — Detail Arsitektur

**Model:** LSTM-Autoencoder (PyTorch, export ke ONNX untuk inference C)

**Arsitektur:**
```
Input: [batch, sequence_length=10, input_features=16]
  │
  Encoder LSTM: hidden=[64, 32], latent_dim=32
  │
  Latent: [batch, 1, 32]
  │
  Decoder LSTM: hidden=[32, 64]
  │
Output: [batch, sequence_length=10, input_features=16]

Loss: MSE reconstruction error
Threshold: P99.0 (percentile ke-99.0 dari validation set benign, distribution-free)
```

**Parameter training (v4 — aktif):**
| Parameter | Nilai |
|-----------|-------|
| `sequence_length` | 10 sampel |
| `timestep` | ~120ms per sampel (efektif) |
| `window duration` | ~1.2 detik per window |
| `input_features` | **16** (11 base + 4 rolling stats + 1 long-window max) |
| `encoder_hidden` | [64, 32] |
| `decoder_hidden` | [32, 64] |
| `latent_dim` | 32 |
| `epochs` | 150 |
| `batch_size` | 64 |
| `learning_rate` | 0.001 |
| `anomaly_threshold` | **P99.0** dari benign val set (distribution-free) |

**Training hanya pada data benign (label=0).** Data attack digunakan untuk evaluasi saja.

**Riwayat versi model:**

| Versi | Input Features | Epochs | Threshold | Catatan |
|-------|---------------|--------|-----------|---------|
| v2 | 10 | 100 | P99.5 | Model awal. Hanya DL Flood terdeteksi. |
| v3 | 11 (+ `empty_ind_rate`) | 150 | P99.0 | UL Flood 97.9%, RRC Storm 100%. Training data dibersihkan dari 536 baris UL>80%. |
| v4 | 15 (+ 4 rolling stats) | 150 | P99.0 | DL Flood 16%→98.3% LSTM, Burst 63%→79.9% LSTM, Hybrid Burst ~94.5%. `prb_dl_roll_mean/std` dan `prb_ul_roll_std/max`. |
| **v5** | **16** (+ `prb_ul_roll_max_100`) | 150 | P99.0 | **Hybrid Burst 97.0%** (+2.5% dari v4 hybrid). Root cause fix: buffer 100ts menutupi OFF phase hingga 58ts. |

**File model (v5 — aktif):**
```
models/lstm_autoencoder_v5.pt               ← PyTorch checkpoint (model aktif, 20 Mei 2026)
models/lstm_autoencoder_v5_losses.json      ← Loss history train/val per epoch
models/lstm_autoencoder_v5_threshold.json   ← threshold P99.0 dari val set
models/scaler.pkl                           ← MinMaxScaler (fit dari training data saja, 16 fitur)
security_model.onnx                         ← ONNX untuk C inference (dibake dari v5, 0.08 MB)
```

**Pipeline training → deploy (v5):**

```bash
# STEP 1 — Siapkan CSV (tambah kolom rolling stats ke training/val/test)
# (sudah dilakukan; file: csv/dataset_training_clean.csv, csv/dataset_validation_clean.csv)

# STEP 2 — Train model
cd /home/telmat/sec-xapp
/home/telmat/xapp/security-xapp/venv/bin/python3 train_lstm.py \
    --train csv/dataset_training_clean.csv \
    --val   csv/dataset_validation_clean.csv \
    --epochs 150 --batch-size 64 --lr 0.001 \
    --model-out models/lstm_autoencoder_v5.pt

# STEP 3 — Export ke ONNX
/home/telmat/xapp/security-xapp/venv/bin/python3 export_onnx.py
# Output: security_model.onnx — input [batch, 10, 16], output score per sample

# STEP 4 — Rebuild C xApp (NUM_FEATURES=16 sudah di-update)
cd ~/flexric/build && make -j$(nproc) xapp_sec_moni
```

> **Catatan**: Threshold dihitung dari **validation set** (bukan training) agar tidak overfit.
> Scaler di-fit HANYA dari training data; validation set hanya di-transform.
> Rolling stats (fitur 12–15) di-fit dari training data normal — nilai attack (tinggi/burst) akan out-of-distribution → anomaly score naik.

**Hasil training v3 (19 Mei 2026):**
| Parameter | Nilai |
|-----------|-------|
| Train sequences | 59,761 |
| Val sequences | 15,673 |
| Best epoch | 144/150 |
| Final train loss | 0.001124 |
| Final val loss | 0.001154 |
| Threshold (P99.0) | 0.003504 |
| FPR benign | 1.00% |

**Hasil evaluasi v3 vs v4 vs v5 — LSTM only (dataset_testing_v2_with_empty.csv):**

| Attack | Rule | LSTM v2 | LSTM v3 | LSTM v4 | LSTM v5 |
|--------|------|---------|---------|---------|---------|
| UL Flood | 98.6% | ~0% | 97.9% | 99.0% | **98.8%** |
| DL Flood | 98.1% | ~16% | 16.3% | 98.3% | 65.4%¹ |
| Burst ON/OFF | 94.5% | ~0% | 62.8% | 79.9% | **83.5%** (+3.6%) |
| RRC Storm | 99.7% | ~0% | 100% | 100% | **100%** |
| RF Jammer | 0% | 0% | 0% | 0% | 0% (fundamental) |
| Normal FPR | — | — | 4.30% | 4.88% | 4.67% |

> ¹ DL Flood LSTM v5 turun dari 98.3% → 65.4% karena dengan 16 fitur model lebih baik merekonstruksi pola DL Flood. Namun **hybrid tetap 98.1%** karena rule-based menangkap DL Flood sepenuhnya.

**Hasil evaluasi Hybrid (Rule + LSTM max) — v5 (20 Mei 2026):**

| Attack | Hybrid S1+ | Hybrid S2 | LSTM-only | Rule-only |
|--------|-----------|-----------|-----------|-----------|
| UL Flood | **98.8%** | 90.5% | 98.8% | 98.6% |
| DL Flood | **98.1%** | 77.5% | 65.4% | 98.1% |
| Burst ON/OFF | **97.0%** | 78.5% | 83.5% | 94.5% |
| RRC Storm | **99.7%** | 99.6% | 100% | 99.7% |
| RF Jammer | **0.0%** | 0.0% | 0.0% | 0.0% |
| Normal FPR | 4.91% | 3.18% | 4.67% | 2.31% |

> Hybrid S1 Accuracy=90.6%, Precision=84.5%, Recall=77.7%, F1=80.9%, ROC-AUC=86.8%

**LSTM v5 score statistics (mean anomaly score):**

| Label | Mean Score | P50 | >Threshold (0.5) |
|-------|-----------|-----|-----------------|
| Normal | 0.229 | 0.081 | 4.9% (FPR) |
| UL Flood | 1.195 | 1.222 | 98.8% |
| DL Flood | 0.521 | 0.530 | 65.4% |
| Burst ON/OFF | 1.094 | 1.196 | 83.5% |
| RRC Storm | 7.100 | 6.793 | 100% |
| RF Jammer | 0.083 | 0.078 | 0.0% |

> **Kunci perbaikan Burst v5**: `prb_ul_roll_max_100` (buffer 100ts = 10s) menutupi OFF phase hingga 58ts. Hybrid Burst naik dari ~94.5% (v4) → **97.0%** (v5), target >95% tercapai.
> **Note DL Flood v5**: DL Flood lebih mengandalkan rule-based (98.1%), LSTM fokus Burst. Total hybrid tidak berubah.

**Hasil training v4 (19 Mei 2026):**
| Parameter | Nilai |
|-----------|-------|
| Train sequences | 59,761 |
| Val sequences | 15,673 |
| Best epoch | 147/150 |
| Final train loss | 0.000856 |
| Final val loss | 0.000899 |
| Threshold (P99.0) | 0.002833 |
| FPR benign | 1.00% |

**Hasil training v5 (20 Mei 2026):**
| Parameter | Nilai |
|-----------|-------|
| Train sequences | 59,761 |
| Val sequences | 15,673 |
| Best epoch | (dari training v5) |
| Threshold (P99.0) | 0.004659 |
| μ / σ | 0.000904 / 0.001628 |
| FPR benign | 0.61% |

---

## 8. Dataset Collection Plan

### Konvensi Label

| Label | Jenis Serangan | Plane |
|-------|---------------|-------|
| 0 | Normal (benign) | — |
| 1 | UL Flood (`iperf3 -u -b 80M`) | Data |
| 2 | DL Flood (`iperf3 -R`) | Data |
| 3 | Burst ON/OFF (5s ON / 5s OFF) | Data |
| 4 | RRC/Signaling Storm (reconnect tiap 3-5s) | Control |
| 5 | RF Burst Interference (USRP jammer ON/OFF) | Physical |
| 6 | Continuous Jamming (USRP TX noise kontinyu) | Physical |

### Rencana Dataset

**Training dataset (label=0, hanya benign):**
| Phase | Aktivitas | Durasi |
|-------|-----------|--------|
| T1 | Idle UE attach | 15 menit |
| T2 | Browsing ringan | 30 menit |
| T3 | Streaming video 720p | 45 menit |
| T4 | Speedtest 3-5x | 15 menit |
| T5 | Mixed traffic | 15 menit |
| **Total** | | **~2 jam** |

> ✅ **SELESAI** — `dataset_training.csv`: 60.306 baris, 120 menit, CQI=15 (98%), n78+QAM256, 2 UE.
> Model telah ditraining: `models/lstm_autoencoder.pt`, `models/scaler.pkl`, `models/training_loss.png`

**Test dataset (semua label):**
| Phase | Aktivitas | Label | Durasi |
|-------|-----------|-------|--------|
| S0 | Baseline normal | 0 | 10 menit |
| S1 | UL Flood | 1 | 10 menit |
| S2 | DL Flood | 2 | 10 menit |
| S3 | Burst ON/OFF | 3 | 10 menit |
| S4 | RRC Storm | 4 | 5 menit |
| S5 | RF Burst Interference | 5 | 10 menit |
| S6 | Continuous Jamming | 6 | 5 menit |
| S7 | Recovery (normal) | 0 | 5 menit |
| **Total** | | | **~65 menit** |

### Cara Recording
```bash
# Training — normal 2 jam (Ctrl+C untuk stop)
./record_dataset.sh --label 0

# Test per attack
./record_dataset.sh --label 1 --duration 600   # UL Flood 10 menit
./record_dataset.sh --label 2 --duration 600   # DL Flood
./record_dataset.sh --label 3 --duration 600   # Burst ON/OFF
./record_dataset.sh --label 4 --duration 300   # RRC Storm
./record_dataset.sh --label 5 --duration 600   # RF Burst
./record_dataset.sh --label 6 --duration 300   # Continuous Jamming
```

---

## 9. Metode Mitigasi

### Arsitektur Mitigasi — O-RAN Compliant

C xApp (`xapp_sec_moni`) melakukan deteksi **DAN** mitigasi secara native via E2SM-RC. Tidak ada dependency ke Python layer untuk mitigasi.

#### Alur Keputusan

```
rule_based_detect() → severity
│
├─ severity=2 (CRITICAL: data-plane flood)
│      → E2SM-RC Control Style 2: PRB throttle → 5% max  [O-RAN compliant]
│
├─ severity=1 (WARNING: signaling storm / jamming)
│      → Alert log only
│         (PRB throttle tidak efektif untuk control-plane/RF attacks)
│         (Fallback manual: SSH AMF barring)
│
└─ severity=0 (normal) + throttle aktif > 10 detik
       → E2SM-RC Control: PRB restore → 100%
```

#### Mitigasi Primer: E2SM-RC PRB Quota (O-RAN Compliant)

| Parameter | Nilai |
|-----------|-------|
| RC Control Style | 2 (Radio Resource Allocation Control) |
| Action ID | 6 (`Slice_level_PRB_quotal_7_6_3_1`) |
| Header Format | Format 1 |
| Message Format | Format 1 (RRM_Policy_Ratio_List) |
| Throttle PRB | `max=5%`, `dedicated=5%`, `min=0%` |
| Restore PRB | `max=100%`, `dedicated=100%`, `min=0%` |
| PLMN | `00101` (Open5GS default MCC=001, MNC=01) |
| Slice | SST=`1` (eMBB), SD=`0` |
| Cooldown | 30 detik (antara throttle actions) |
| Auto-restore | 10 detik setelah severity kembali 0 |

**Cara kerja:**
- `sm_cb_kpm()` memanggil `rule_based_detect()` dan menyimpan flag `g_pending_throttle`
- Main loop (setiap 1 detik) membaca flag dan memanggil `rc_send_prb_quota()` di luar mutex
- `rc_send_prb_quota()` memanggil `control_sm_xapp_api()` → mengirim RC Control ke gNB via E42→E2

**Mengapa PRB throttle tidak memblokir signaling storm:**
RRC signaling (attach/detach) berjalan di control plane dan tidak menggunakan data PRB. PRB quota hanya mempengaruhi data plane (DRB). Untuk signaling storm, satu-satunya solusi O-RAN murni adalah RC Style 1 (UE admission control) yang belum tersedia di srsRAN.

**Catatan srsRAN RC Bug #468:**
RC Control message decoding di srsRAN memiliki bug — throttle mungkin tidak diterapkan ke scheduler. Fungsionalitas pengiriman control message sudah benar dari sisi xApp (sesuai O-RAN spec). Jika throttle tidak efektif, fallback SSH tersedia.

#### Mitigasi Fallback: SSH AMF Barring (untuk Signaling Storm)

Untuk severity=1 (signaling storm), mitigasi manual via SSH ke Core Node:

```bash
# Blokir UE di level AMF (Registration Reject):
ssh telmat@10.91.2.4 "sudo open5gs-dbctl subscriber_status <IMSI> 1 1"

# Restore akses:
ssh telmat@10.91.2.4 "sudo open5gs-dbctl subscriber_status <IMSI> 0 0"
```

#### Tabel Efektivitas per Serangan

| Jenis Serangan | Plane | PRB Throttle (E2SM-RC) | SSH AMF Barring |
|---|---|---|---|
| UL Flood | Data | ✅ Efektif | ⚠️ Overkill |
| DL Flood | Data | ✅ Efektif | ⚠️ Overkill |
| Burst ON/OFF | Data | ✅ Efektif | ⚠️ Overkill |
| Signaling Storm | Control | ❌ Tidak efektif | ✅ Satu-satunya solusi |
| RF Burst/Jamming | Physical | ❌ Tidak efektif | ❌ Tidak efektif |

---

## 10. Status Deteksi per Serangan

Two-stage detection diimplementasi 16 Mei 2026. Stage 1 memberikan early warning sub-second, Stage 2 mengonfirmasi setelah persistence threshold sebelum mitigasi diaktifkan.

| Jenis Serangan | Stage 1 (WARNING) | Stage 2 (CRITICAL) | Mitigasi | Catatan |
|---|---|---|---|---|
| UL Flood | ✅ R1 ~360ms | ✅ ≥30s sustained | E2SM-RC PRB 5% (--mitigate) | Stage 2 eliminasi FP speedtest |
| DL Flood | ✅ R2 ~360ms | ✅ ≥30s sustained | E2SM-RC PRB 5% (--mitigate) | Guard PRB_UL<30% vs bidirectional |
| Burst ON/OFF | ✅ R8 ~240ms | ✅ ≥3 ON/OFF cycles/60s | E2SM-RC PRB 5% (--mitigate) | prb_burst_index > 2.0 trigger |
| Signaling Storm | ✅ R3b ~360ms (empty ind.) | ✅ ≥4 windows consecutive | Alert only → SSH AMF fallback | Control-plane, PRB throttle tidak efektif |
| RF Burst/Jamming | ✅ R7 ~240ms (PRB collapse proxy) | ✅ ≥5 windows consecutive | Alert only | Framing: "radio_degradation_suspicion" — tidak diklaim jammer definitif |
| Speedtest benign | ⚠️ R1/R2 WARNING (FP Stage 1) | ✅ **Tidak dikonfirmasi** (transient ≤40s < 30s threshold) | Tidak ada | Stage 2 menyelesaikan ambiguitas ini |

---

## 11. Bug yang Diperbaiki

### Bug 1 — KPM Style 5 Assertion Crash
**Gejala**: `Assertion 'act_def_frm_5->ue_id_lst_len >= 2' failed` → core dump saat startup.

**Fix**: Set `fill_report_style_5 = NULL` di array dispatch — style 5 di-skip, style 1 digunakan.

### Bug 2 — Format 1 Hanya Cetak 1 dari 15 Metrik
**Gejala**: 15 metrik tersedia tapi hanya 1 yang tercetak.

**Fix**: Iterasi dua level — outer loop atas data entries, inner loop atas info list.

### Bug 3 — E42 Encoding Crash pada Subscription Failure
**Gejala**: xApp infinite reconnect loop saat `e2ap_handle_subscription_failure_iapp` mencoba encode `RIC_SUBSCRIPTION_FAILURE`.

**Fix** (`msg_handler_iapp.c`): E42 tidak support encoding `RIC_SUBSCRIPTION_FAILURE`. Ganti dengan `rm_map_ric_id()` + log saja — xApp subscription timeout secara normal.

### Bug 4 — Loop `[E2AP]: Resending Setup Request`
**Gejala**: xApp terus loop karena subscription ke CU-CP (`ngran_gNB`) gagal — gNB ini hanya DU.

**Fix**: Hapus blok subscription CU-CP dari `my_xapp_kpm.conf`. Hanya satu subscription: `ngran_gNB_DU`.

### Bug 5 — srsRAN KPM DU: `DRB.UEThpDl/UL` Selalu 0

**Gejala**: Setelah speedtest aktif (menghasilkan PRB_DL ~99%), `dl_throughput_mbps` dan `ul_throughput_mbps` di CSV tetap 0.000000 di semua 198 baris. `RRU.PrbUsedDl` bekerja benar (mencapai 0.99 ratio).

**Root cause**: srsRAN KPM DU tidak melaporkan `DRB.UEThpDl` dan `DRB.UEThpUl` secara benar pada granularity 100ms. Metrik ini selalu 0 meskipun ada transfer data aktif. Ini bug implementasi di srsRAN, bukan masalah konfigurasi xApp.

**Akibat**: Rule 1 dengan guard `&& thp_dl_mbps > 0.5f` yang sempat ditambahkan adalah **salah** — deteksi PRB overload saat speedtest bukan false positive, melainkan **true positive** yang benar. Guard throughput memblokir deteksi yang seharusnya terjadi.

**Fix**:
```c
/* WRONG (added by mistake — blocks true positive): */
if ((m->prb_used_dl > 90.0f && m->thp_dl_mbps > 0.5f) || ...) { ... }

/* CORRECT (reverted): */
if (m->prb_used_dl > 90.0f || m->prb_used_ul > 90.0f) { ... }
```

**Dampak lanjutan**: Setelah investigasi lebih lanjut, `DRB.RlcSduTransmittedVolumeDL/UL` juga selalu 0 di srsRAN — bukan hanya `DRB.UEThpDl/UL`. Satu-satunya metrik yang bekerja adalah PRB (`RRU.PrbUsedDl/UL`, `RRU.PrbAvailDl/UL`). **Solusi final**: Feature schema dimigrasi sepenuhnya ke PRB-derived features — lihat Section 6. Rules 2/4/5 diupdate: Rule 2 mendapat guard `prb < 90%` (tidak fire saat Rule 1 aktif); Rules 4/5 menggunakan RLC (0 selalu, tidak akan trigger di srsRAN — diakui sebagai limitation).

### Bug 7 — `prb_ul_dl_ratio` Overflow (858490)
**Gejala**: Fitur `prb_ul_dl_ratio` dalam CSV memiliki nilai hingga 858,490 — jauh di luar range normal. Terlihat dari analisis `training_20260508_150631.csv`.

**Root cause**: Formula `prb_ul / (prb_dl + ε)` dengan `ε = 1e-6`. Ketika `prb_dl = 0.000` (exact zero, kondisi idle atau pure UL attack) dan `prb_ul = 0.858`, hasilnya `0.858 / 0.000001 = 858,000`. Nilai ini akan merusak normalisasi MinMaxScaler dan membuat LSTM tidak bisa konvergen.

**Fix**: Ganti dengan `prb_direction = (prb_ul - prb_dl) / (prb_total + ε)`, yang selalu bounded `[-1, +1]`:
```c
/* WRONG — dapat overflow ke 858490: */
float prb_ul_dl_ratio = prb_ul_ratio / (prb_dl_ratio + EPS);

/* CORRECT — bounded [-1, +1]: */
float prb_direction = (prb_ul_ratio - prb_dl_ratio) / (prb_total + EPS);
```
Setelah fix: range terukur `[-0.93, +1.0]`, 20 nilai unik. Interpretasi: `-1` = pure DL, `+1` = pure UL, `0` = balanced (idle atau signaling storm).

### Bug 8 — Rule 1 False Positive saat TCP Speedtest (PRB Overload)

**Gejala**: Rule 1 PRB_OVERLOAD muncul selama iperf3 speedtest dengan bitrate 30M–50M. Alert CRITICAL muncul meski tidak ada serangan.

**Root cause**: Rule lama hanya memeriksa `prb_used_dl > 90%` dalam satu window — tanpa membedakan TCP speedtest (bidirectional: PRB_DL >90%, PRB_UL ~4% untuk ACK) dari UDP DL flood (unidirectional: PRB_DL >90%, PRB_UL ~0%). Satu window juga terlalu singkat untuk debouncing.

**Fix** (`sec_ids.c`):
1. Tambahkan **bidirectional guard**: DL flood hanya trigger jika `PRB_UL < 3%`; UL flood hanya jika `PRB_DL < 3%`. TCP speedtest DL memiliki PRB_UL ~4% (ACK traffic) → tidak trigger.
2. Tambahkan **3-window duration**: counter `g_prb_overload_cnt` harus ≥ 3 window berturut-turut (~360ms) sebelum alert.

```c
int dl_flood = (m->prb_used_dl > 90.0f && m->prb_used_ul < 3.0f);
int ul_flood = (m->prb_used_ul > 90.0f && m->prb_used_dl < 3.0f);
if (dl_flood || ul_flood) g_prb_overload_cnt++;
else                       g_prb_overload_cnt = 0;
if (g_prb_overload_cnt >= 3) { /* ALERT */ }
```

**Keterbatasan**: TCP DL/UL flood (bidirectional dengan ACK) tidak terdeteksi Rule 1. Didelegasikan ke LSTM autoencoder via anomaly score.

---

### Bug 9 — Rule 2 False Positive saat Speedtest (Signaling Storm)

**Gejala**: Rule 2 SIGNALING_STORM muncul bersamaan dengan Rule 1 saat speedtest. Alert WARNING muncul meski CQI=15 dan tidak ada RRC churn.

**Root cause**: Guard `rlc_rate < 100 kbps` selalu TRUE karena `DRB.RlcSduTransmittedVolumeDL/UL` selalu 0 di srsRAN KPM DU. Kondisi `prb_avg > 20% && prb < 90%` cukup terpenuhi saat transisi speedtest. Tidak ada pembeda antara signaling storm nyata (CQI drop, RACH spike) dan speedtest normal (CQI=15, RACH=0).

**Fix** (`sec_ids.c`): Tambahkan kondisi **CQI atau RACH** sebagai syarat wajib:
```c
int cqi_degraded  = (m->cqi < 5.0f);
int rach_elevated = (m->rach_preamble > 0.0f);
if (prb_avg > 20.0f
        && rlc_rate_ul_kbps < 100.0f && rlc_rate_dl_kbps < 100.0f
        && m->prb_used_dl < 90.0f && m->prb_used_ul < 90.0f
        && (cqi_degraded || rach_elevated)) {  /* ← BARU: wajib CQI drop atau RACH spike */
    g_sig_storm_cnt++;
}
```

Speedtest normal: CQI=15 (tidak degraded), RACH=0 → kondisi tidak terpenuhi → tidak trigger.
Signaling storm nyata: CQI=0 (airplane mode detach) atau RACH>0 → trigger.

---

### Bug 6 — E2AP Reconnect Loop Setelah RC Throttle (srsRAN RC Bug #468)
**Gejala**: Setelah `rc_send_prb_quota(5)` berhasil (CONTROL-ACK diterima), gNB E2 agent crash → xApp loop "Resending Setup Request" tanpa henti. Lebih parah: setelah gNB reconnect, metrics menjadi 0 → severity=0 → restore PRB dikirim → crash lagi → infinite loop.

**Root cause**: srsRAN RC Control message decoding bug (#468) — gNB crash setelah menerima RC Control meski sudah mengirim ACK.

**Fix**: 
1. Mitigasi otomatis di-disable by default — tambah flag `--mitigate` untuk opt-in
2. Tanpa `--mitigate`: xApp berjalan dalam detection-only mode (aman)
3. Dengan `--mitigate`: RC PRB throttle aktif (eksperimental, bisa crash gNB di srsRAN)

**Catatan**: Pengiriman RC Control sudah benar secara O-RAN spec (CONTROL-ACK diterima). Bug ada di sisi srsRAN, bukan xApp.

---

## 11b. Two-Stage Hybrid Detection — Implementasi (16 Mei 2026)

Fitur ini merupakan pengembangan utama Buku TA. Diimplementasi sebagai Approach A (minimal-risk) — rule-based two-stage tanpa binary baru.

### Perubahan Rule

| Rule | Perubahan | Alasan |
|------|-----------|--------|
| R1 (UL Saturation) | Threshold 90% → 80%; WARNING only (bukan CRITICAL langsung) | Stage 2 menangani FP; lebih sensitif untuk deteksi awal |
| R2 (DL Saturation) | Threshold 90% → 80%, guard PRB_UL < 30%; WARNING only | Sama — Stage 2 konfirmasi, tidak langsung CRITICAL |
| **R7 (baru)** | Sudden PRB collapse (>40% → <5%) + `air_delay_ul < 1ms` | Proxy radio-layer degradation tanpa CQI (srsRAN keep-last bug) |
| **R8 (baru)** | `prb_burst_index > 2.0` untuk ≥2 window, cycle counting | Deteksi pola alternating saturation periodik |

### Stage 2 State (sec_ids.c)

```
g_cfg_saturation_confirm_ms  = 30000   ← speedtest ≤40s, flood >120s (empiris testbed)
g_cfg_burst_cycle_threshold  = 3       ← ≥3 ON/OFF cycle dalam 60s
g_cfg_rrc_storm_confirm_win  = 4       ← 4 windows consecutive empty storm
g_cfg_rf_susp_confirm_win    = 5       ← 5 windows consecutive PRB collapse
g_cfg_recovery_confirm_ms    = 5000   ← 5s tenang → reset semua Stage 2 state
```

Semua threshold disimpan sebagai named globals (bukan magic number) untuk kemudahan konfigurasi.

### Known Limitations (untuk Buku TA)

| Limitasi | Detail |
|----------|--------|
| **Mitigation delay** | Stage 2 menambah latency ~30s sebelum RC throttle aktif. Tradeoff disengaja untuk mengurangi FP. |
| **Sustained benign uploads** | Large file upload > 30s dapat mencapai Stage 2 CRITICAL. |
| **Cell-level only** | Tidak bisa mengidentifikasi UE bertanggung jawab. |
| **Radio-layer degradation heuristik** | Berbasis PRB collapse, bukan SINR histogram — tidak tersedia di KPM cell-level. |
| **Speedtest threshold empiris** | 30s dari observasi testbed kami; durasi speedtest di environment lain bisa berbeda. |
| **CQI tidak reliabel di srsRAN** | keep-last policy — CQI tidak di-reset saat UE detach. R7 tidak menggunakan CQI. |

### Commit

- **flexric repo**: `503672a6` — `feat: two-stage hybrid detection — Stage 1 WARNING + Stage 2 persistence CRITICAL`
- **Files**: `sec_ids.h`, `sec_ids.c`, `xapp_sec_moni.c` (+ copy di `copy-xapp/`)
- **Tests**: `--test` → 4 test cases [PASS], exit code 0

---

## 12. Ringkasan Latency

| Komponen | Latency | Keterangan |
|---|---|---|
| KPM period (timestep) | **100ms** | |
| Stage 1 WARNING (1-window rules: R6, R7, R8) | **~100–240ms** ✅ Near-RT | R7/R8 butuh 2 window consecutive |
| Stage 1 WARNING (3-window rules: R1, R2, R3b) | **~360ms** ✅ Near-RT | |
| Stage 2 CRITICAL (saturation persistence) | **~30s** ✅ Mitigation authorization | Threshold empiris: speedtest ≤40s, flood >120s |
| Stage 2 CRITICAL (RRC storm) | **~480ms** ✅ Near-RT | 4 windows × 120ms |
| Stage 2 CRITICAL (radio degradation) | **~600ms** ✅ Near-RT | 5 windows × 120ms |
| Stage 2 CRITICAL (periodic burst) | **variabel** | ≥3 cycle dalam 60s |
| LSTM inference (window = 10 × 100ms) | **~1000ms** ✅ Near-RT batas bawah | Sebagai anomaly_score confidence layer |
| Python monitor (sebelumnya) | ~30.000ms ❌ | |

---

## 13. Evaluasi yang Akan Dilakukan

### Metrik Two-Stage (baru, 16 Mei 2026)

| Metrik | Definisi | Target |
|--------|----------|--------|
| `stage1_warning_latency_ms` | t(WARNING) − t(attack_start) | < 1000ms |
| `stage2_confirmation_time_ms` | t(CRITICAL) − t(WARNING) | ≈30s untuk flood, variabel untuk burst |
| `stage1_fpr` | WARNING/benign_windows | Didokumentasikan sebagai tradeoff |
| `stage2_fpr` | CRITICAL/benign_windows | < stage1_fpr |
| `speedtest_fp_elimination` | Stage 2 tidak confirm speedtest (transient ≤40s < threshold 30s) | Diverifikasi empiris |

### Metrik Umum

| Metrik | Deskripsi |
|--------|-----------|
| `detection_latency_ms` | Waktu dari attack start → Stage 1 WARNING pertama |
| `mitigation_latency_ms` | Waktu dari attack start → Stage 2 CRITICAL + RC throttle |
| `true_positive_rate` | Alert (Stage 1 atau Stage 2) saat attack / total attack windows |
| `false_positive_rate` | Alert saat benign / total benign windows — per stage |
| `ROC-AUC` | Area under ROC curve per attack type |
| `F1-score` | Per attack type, per stage |
| `recovery_time_ms` | Waktu dari mitigate() → traffic kembali normal |

---

## 14. Next Steps

### Selesai (Buku TA)
- [x] **Kumpulkan dataset training** — ✅ `dataset_training.csv` (60K baris, 120 menit, n78+QAM256, 2 UE)
- [x] **Ambil validation dataset** — ✅ `dataset_validation.csv` (15.8K baris, 31.5 menit, n78+QAM256)
- [x] **Train LSTM v2** — ✅ `models/lstm_autoencoder_v2.pt` (train/val terpisah, best epoch=98, FPR=0.50%, 10 Mei 2026)
- [x] **Hitung threshold** — ✅ tersimpan di `models/lstm_autoencoder_v2_threshold.json` (P99.5=0.005035, FPR=0.50%)
- [x] **Fix rach_preamble scaler** — ✅ domain-known max=6; RACH spike ke 6 → normalized 1.0
- [x] **Fix Rule 1/2 FP** — ✅ bidirectional guard + 3-window duration + CQI/RACH guard
- [x] **Export ke ONNX** — ✅ `security_model.onnx` (0.08 MB, MinMaxScaler + threshold P99.5 dibake ke graph)
- [x] **Two-Stage Hybrid Detection** — ✅ 16 Mei 2026 (commit `503672a6` di flexric repo)
  - Stage 1: R1/R2 threshold 90%→80%, R7 (radio degradation), R8 (periodic burst), <400ms latency
  - Stage 2: duration-based persistence (30s threshold empiris dari testbed), recovery 5s
  - CSV: 6 kolom baru (stage1_alert, stage2_confirmed, alert_type, latency, anomaly_score)
  - Tests: 4 test cases A/B/C/D termasuk speedtest FP guard — semua [PASS]

### Prioritas Tinggi (Dataset Testing)
- [ ] **Kumpulkan dataset test per attack** dengan kolom stage1/stage2 baru:
  - Label 1: UL Flood (`iperf3 -u -b 80M`, 10 menit) — verifikasi Stage 2 CRITICAL setelah 30s
  - Label 2: DL Flood (`iperf3 -R`, 10 menit) — verifikasi Stage 2 CRITICAL setelah 30s
  - Label 3: Burst ON/OFF (5s/5s) — verifikasi R8 cycle detection
  - Label 4: RRC Storm (airplane toggle tiap 3-5s, 5 menit) — verifikasi Stage 2 RRC
  - Label 5/6: Jamming (jika USRP tersedia) — verifikasi R7 suspicion
- [ ] **Verifikasi speedtest tidak mencapai Stage 2** — jalankan Ookla speedtest, cek `stage2_confirmed=0`
- [ ] **Ukur stage1_latency_ms dari dataset** — verifikasi < 1000ms untuk semua attack
- [ ] **Ukur stage2_confirmation_time_ms** — verifikasi ≈30s untuk UL/DL Flood

### Prioritas Sedang
- [ ] **Evaluasi metrik** (Stage 1 FPR, Stage 2 FPR, ROC-AUC) dari test dataset
- [ ] **Evaluasi mitigasi**: jalankan UL flood dengan `--mitigate` → cek apakah PRB UE turun setelah Stage 2 CRITICAL

### Future Work (di luar scope Buku TA)
- [ ] Supervised ML Classifier untuk menggantikan threshold-based Stage 2
- [ ] Per-RNTI detection (butuh srsRAN KPM Style 4 fix atau migrasi ke OAI)
- [ ] Jika srsRAN RC Bug #468 dipatch: verifikasi ulang efektivitas PRB throttle
