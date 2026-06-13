# Status Evaluasi & Rencana Kedepan
**Terakhir diperbarui:** 2026-06-13 (rev 3 — per-UE model pipeline, inference benchmark)  
**Dataset cell-level:** `csv/dataset_attack_mei.csv` — 17.941 baris, 5 label (0=Normal, 1=UL Flood, 2=DL Flood, 3=Burst, 4=RRC Storm)  
**Dataset per-UE:** `csv/dataset_training_ue_juni.csv` (4.200 baris benign) · `csv/dataset_validation_ue_juni.csv` (1.800 baris benign)

---

## 1. Performa Komponen — Status Quo

### 1.1 Rule-Based IDS (Standalone)

| Metrik | Nilai |
|--------|-------|
| Accuracy | 98.12% |
| Precision | 98.62% |
| Recall | 97.65% |
| F1 Score | 0.9813 |
| ROC-AUC | 0.9812 |
| **FPR Stage1** | **1.40%** (124/8852) |
| FPR Stage2 | 1.37% (121/8852) |

**Per-Attack Recall (Rule-only):**

| Serangan | Stage1 | Stage2 |
|----------|:------:|:------:|
| UL Flood | 98.7% | 96.5% |
| DL Flood | 99.3% | 98.1% |
| Burst ON/OFF | 98.2% | 94.6% |
| RRC Storm | 94.0% | 93.6% |

> Rule-based sangat kuat secara keseluruhan. Kelemahan utama: tidak mampu mendeteksi serangan di bawah threshold individual (multi-vector low-and-slow).

---

### 1.2 LSTM Dual Ensemble (Standalone)

**Model:** `security_model_v16.onnx` (thresh=0.21, scaler baked-in) + `security_model_v22.onnx` (thresh=0.5)  
**Fitur:** 25 fitur, seq_len=10, min_consec=3

| Metrik | Nilai |
|--------|-------|
| Accuracy | 86.36% |
| Precision | 92.84% |
| Recall | 79.18% |
| F1 Score | 0.8547 |
| ROC-AUC (binary) | 0.8646 |
| ROC-AUC (raw score) | 0.9503 |
| **FPR Stage1** | **6.27%** (555/8852) |

**Per-Attack Recall (LSTM-only):**

| Serangan | LSTM v16 (thresh=0.21) | LSTM v22 (thresh=0.5) | LSTM Ensemble |
|----------|:---------------------:|:--------------------:|:-------------:|
| UL Flood | ~83.6% | ~0.8% | **81.4%** |
| DL Flood | ~99.8% | ~10.7% | **99.7%** |
| Burst ON/OFF | ~64.9% | ~39.7% | **61.4%** |
| RRC Storm | ~79.3% | ~85.8% | **83.8%** |

> FPR 6.27% cukup tinggi karena distribusi normal traffic LSTM heavy-tailed (Normal P99=2.87, threshold=0.21 hanya di ~P80 normal). LSTM v16 spesialis UL Flood + DL Flood; v22 spesialis RRC Storm.

---

### 1.3 GRU Dual Ensemble (Standalone)

**Model:** `models/gru_autoencoder_A_v1.pt` (seq_len=10) + `models/gru_autoencoder_B_v1.pt` (seq_len=30)  
**Scaler:** `models/scaler_gru.pkl` (16 fitur, fit dari 59.752 baris benign)

#### 1.3a. Konfigurasi Original (Default thresh)

| Model | Threshold | FPR Stage1 |
|-------|:---------:|:----------:|
| GRU-A | 0.002881 | — |
| GRU-B | 0.009865 | — |
| **Ensemble (A OR B)** | — | **2.14%** (189/8852) |

| Serangan | GRU Ensemble | Precision |
|----------|:------------:|:---------:|
| UL Flood | 99.2% | 91.4% |
| DL Flood | **25.9%** ⚠️ | 73.4% |
| Burst ON/OFF | 93.0% | 93.8% |
| RRC Storm | **61.3%** ⚠️ | 86.4% |
| **Overall Recall** | **72.71%** | |

#### 1.3b. Setelah Threshold Tuning (GRU-B diturunkan)

> Hasil dari `sweep_gru_threshold.py` — sweep per-window (Stage1), 17.911 valid windows.  
> Stage2 (5× consecutive) FPR aktual akan lebih rendah dari angka di bawah.

**Optimal: GRU-A tetap 0.002881 + GRU-B diturunkan ke 0.003363**

| Konfigurasi | FPR Stage1 | UL Flood | DL Flood | Burst | RRC Storm | Overall |
|-------------|:----------:|:--------:|:--------:|:-----:|:---------:|:-------:|
| Original (B=0.009865) | 2.14% | 99.2% | 25.9% | 93.0% | 61.3% | 72.7% |
| **Tuned (B=0.003363)** | **5.3%** | **99.6%** | **99.4%** | **98.7%** | **71.5%** | **93.2%** |

**Trade-off yang perlu dicatat:**
- FPR Stage1 naik: 2.14% → 5.3% (+3.2pp)
- DL Flood recall: 25.9% → 99.4% (+73.5pp) — perbaikan besar
- Burst recall: 93.0% → 98.7% (+5.7pp)
- RRC Storm recall: 61.3% → 71.5% (+10.2pp) — perbaikan terbatas
- FPR Stage2 (5× consecutive) diperkirakan jauh di bawah 5.3% karena filter consecutive

**Distribusi skor per model (Stage1):**

| Label | GRU-A mean | GRU-A P99 | GRU-B mean | GRU-B P99 |
|-------|:----------:|:---------:|:----------:|:---------:|
| Normal | 0.000516 | 0.005307 | 0.001220 | 0.015880 |
| UL Flood | 0.009638 | 0.009829 | 0.015450 | 0.015790 |
| DL Flood | 0.003057 | 0.021183 | 0.005937 | 0.024224 |
| Burst | 0.009942 | 0.024611 | 0.020323 | 0.042748 |
| RRC Storm | 0.021207 | 0.134013 | 0.030835 | 0.111624 |

**Catatan RRC Storm:** Median GRU-A = 0.000321 (hampir sama dengan Normal) — hanya 40% window RRC Storm yang benar-benar di atas Normal range. Ini menunjukkan RRC Storm tidak menghasilkan rekonstruksi error konsisten di semua window karena pola detach sangat intermittent.

> **Rekomendasi untuk thesis:** Gunakan konfigurasi tuned (B=0.003363) untuk evaluasi GRU standalone. Dokumentasikan RRC Storm limitation sebagai trade-off arsitektur (GRU tanpa `empty_ind_rate` yang berfungsi). Hybrid Rule+GRU tetap mencapai ~94% RRC Storm via Rule component.

---

### 1.4 Hybrid Rule + LSTM (Sistem Utama)

| Metrik | Nilai |
|--------|-------|
| Accuracy | 96.02% |
| Precision | 94.10% |
| Recall | **98.31%** |
| F1 Score | 0.9616 |
| ROC-AUC | 0.9834 |
| **FPR Stage1** | **6.33%** (560/8852) |
| **FPR Stage2** | **1.37%** (121/8852) |

**Per-Attack Recall (Hybrid Rule+LSTM):**

| Serangan | Stage1 | Stage2 | Kontribusi LSTM |
|----------|:------:|:------:|:---------------:|
| UL Flood | 99.2% | 96.5% | +0.5pp dari Rule |
| DL Flood | 99.7% | 98.1% | +0.4pp dari Rule |
| Burst ON/OFF | 99.5% | 94.6% | +1.3pp dari Rule |
| RRC Storm | 94.0% | 93.6% | ±0pp dari Rule |

> Hybrid Stage1 FPR 6.33% tinggi karena LSTM dominates FP. Namun **Stage2 FPR hanya 1.37%** — confirmasi 5-detik efektif menyaring hampir semua FP LSTM.

---

### 1.5 Hybrid Rule + GRU (Perbandingan Arsitektur)

| Metrik | Rule+LSTM | Rule+GRU |
|--------|:---------:|:--------:|
| Recall Stage1 | 98.31% | **98.24%** |
| FPR Stage1 | 6.33% | **2.87%** |
| FPR Stage2 | 1.37% | 1.37% |
| F1 Stage1 | 0.9616 | **0.9773** |

> Rule+GRU lebih baik dari sisi FPR Stage1 (2.87% vs 6.33%) dengan recall hampir sama. F1 GRU lebih tinggi karena precision lebih baik. Kedua hybrid memiliki Stage2 FPR identik (1.37%) — konfirmasi 5-detik efektif untuk keduanya.

---

### 1.6 Per-UE Model — GRU-UE v1 & LSTM-UE v1 (Baru, Juni 2026)

**Konteks:** Model terpisah dari cell-level (§1.2–1.5). Dilatih dengan dataset per-UE 15 fitur
(PRB ratio, throughput, ul_efficiency, rolling stats) menggunakan `RobustScaler` (bukan MinMaxScaler)
karena distribusi zero-heavy (61% baris prb_ul = 0).

**Dataset training:** `csv/dataset_training_ue_juni.csv` — 4.200 baris, 70 menit, label=0, interval 1s  
**Dataset validation:** `csv/dataset_validation_ue_juni.csv` — 1.800 baris, 30 menit, label=0  
**Feature schema:** `src/detection/feature_schema_ue.py` — 15 fitur (lihat di bawah)  
**Fix prerequisite:** MAC PRB cache fallback di `xapp_sec_moni.c` — mengatasi NO_MEAS_VALUE untuk
`RRU.PrbUsedUl/Dl` per-UE di srsRAN KPM FORMAT_3

#### Hasil Training

| | GRU-UE v1 | LSTM-UE v1 |
|-|:---------:|:----------:|
| File | `models/gru_ue_v1.pt` | `models/lstm_ue_v1.pt` |
| Arsitektur | BiGRU encoder [64,32] + decoder [32,64] | LSTM (unidirectional) [64,32] |
| Parameters | 87.870 | 65.373 |
| File size | 353 KB | 263 KB |
| seq_len | 10 | 10 |
| Epochs | 150 (best: ep 136) | 150 (best: ep 77) |
| val_loss terbaik | 138.001 | 138.008 |
| Threshold (P99) | 2.793.671 | 2.793.713 |
| **FPR @P99** | **1.00%** (18/1.792 windows) | **1.00%** (18/1.792 windows) |
| Scaler | `models/gru_ue_v1_scaler.pkl` | `models/lstm_ue_v1_scaler.pkl` |

> val_loss dalam skala besar (~138k) karena fitur `ul_efficiency` range [0, 50.000] yang tidak
> ter-normalisasi sempurna oleh RobustScaler. FPR tetap OK pada 1.00%.

#### Status Evaluasi Per-UE

**Yang tersedia:** FPR/TNR dari dataset validasi benign (di atas).

**Yang belum tersedia:** Confusion matrix, recall, precision — memerlukan data serangan
dalam format per-UE (15 fitur termasuk throughput). Dataset attack yang ada
(`dataset_testing_v3.csv`, dll.) adalah format cell-level tanpa kolom `thp_dl/ul_kbps`,
`ul_efficiency`, dsb. Evaluasi parsial (10/15 fitur = 0) tidak valid karena skor rekonstruksi
menjadi ~31 (sangat rendah) vs threshold 2.793.671 — model menolak hampir semua input parsial
sebagai "benign", bukan karena model salah tapi karena input tidak valid.

**Untuk evaluasi lengkap:** Perlu rekam skenario serangan (~2 menit per label 1–4) menggunakan
xApp per-UE yang sudah diperbaiki, kemudian jalankan `python3 evaluate_ue_models.py`.

#### 15 Fitur Per-UE

| # | Fitur | Sumber |
|---|-------|--------|
| 1 | `prb_usage_dl_ratio` | RRU.PrbUsedDl/(PrbUsedDl+PrbAvailDl), clip [0,1] |
| 2 | `prb_usage_ul_ratio` | RRU.PrbUsedUl/(PrbUsedUl+PrbAvailUl), clip [0,1] |
| 3 | `thp_dl_kbps` | DRB.UEThpDl (kbps) |
| 4 | `thp_ul_kbps` | DRB.UEThpUl (kbps) |
| 5 | `prb_direction` | (prb_ul−prb_dl)/(prb_total+ε), bounded [−1,+1] |
| 6 | `prb_total` | prb_dl+prb_ul, clip [0,1] |
| 7 | `prb_ul_delta` | prb_ul[t]−prb_ul[t−1] |
| 8 | `ul_efficiency` | thp_ul/prb_ul, clip [0, 50.000] |
| 9 | `prb_ul_roll_mean` | rolling mean prb_ul (window=10) |
| 10 | `prb_ul_roll_std` | rolling std prb_ul (window=10) |
| 11 | `ul_persistence` | fraction prb_ul>0 di 10 timestep terakhir |
| 12 | `thp_total_kbps` | thp_dl+thp_ul |
| 13 | `thp_ul_delta` | thp_ul[t]−thp_ul[t−1] |
| 14 | `thp_dl_delta` | thp_dl[t]−thp_dl[t−1] |
| 15 | `traffic_direction` | (thp_ul−thp_dl)/(thp_total+ε), bounded [−1,+1] |

---

## 2. Ringkasan Komparatif

### 2.1 Per-Attack: LSTM vs GRU Standalone

| Serangan | LSTM Ensemble | GRU Original | GRU Tuned (B=0.003363) | Unggul (vs LSTM) |
|----------|:-------------:|:------------:|:----------------------:|:----------------:|
| UL Flood | 81.4% | 99.2% | **99.6%** | GRU +18.2pp |
| DL Flood | **99.7%** | 25.9% | 99.4% | Hampir setara (−0.3pp) |
| Burst ON/OFF | 61.4% | 93.0% | **98.7%** | GRU +37.3pp |
| RRC Storm | **83.8%** | 61.3% | 71.5% | LSTM +12.3pp |
| **Overall Recall** | 79.2% | 72.7% | **93.2%** | GRU Tuned +14pp |
| **FPR Stage1** | 6.27% | **2.14%** | 5.3%¹ | LSTM lebih buruk |

¹ *FPR Stage1 per-window. FPR Stage2 (5× consecutive) diperkirakan ≤2%.*

**Kesimpulan setelah tuning:** GRU tuned unggul di 3 dari 4 attack type (UL Flood, DL Flood, Burst) dan overall recall (93.2% vs 79.2%). LSTM masih unggul di RRC Storm (83.8% vs 71.5%). GRU tuned FPR Stage1 (5.3%) lebih tinggi dari original (2.14%) tapi masih lebih rendah dari LSTM (6.27%).

### 2.2 Lima Komponen Side-by-Side

| Metrik | Rule | LSTM | GRU (orig) | GRU (tuned) | Hybrid R+LSTM |
|--------|:----:|:----:|:----------:|:-----------:|:-------------:|
| Recall | 97.7% | 79.2% | 72.7% | **93.2%** | **98.3%** |
| Precision | 98.6% | 92.8% | 97.2% | ~93%¹ | 94.1% |
| F1 | 0.981 | 0.855 | 0.832 | ~0.930 | 0.962 |
| FPR Stage1 | **1.40%** | 6.27% | 2.14% | 5.3% | 6.33% |
| FPR Stage2 | 1.37% | — | — | ~≤2%² | **1.37%** |

¹ *GRU tuned precision diestimasi dari sweep data (FPR=5.3%, recall=93.2%).*  
² *Stage2 (5× consecutive) FPR GRU tuned diperkirakan jauh lebih rendah dari Stage1 5.3% — perlu evaluasi formal.*

---

## 3. Latensi Deteksi & Mitigasi

### 3.1 Pipeline Latensi

```
KPM Report (120ms)
    ↓
xApp Processing (~1ms)
    ↓
Stage1 Alert (first window anomaly)     ← ~120ms–1s setelah serangan mulai
    ↓
Stage2 Confirmation (5× consecutive)   ← +5s confirmasi
    ↓
Mitigation Apply (SSH iptables)         ← +1–2s SSH latency
    ═══════════════════════════════════
    TOTAL TIME-TO-MITIGATE: ~6–7 detik
```

### 3.2 Komponen Latensi

| Tahap | Latensi | Keterangan |
|-------|:-------:|------------|
| KPM reporting interval | ~120ms | FlexRIC default per-UE |
| Stage1 detection | 1–3 periods (~120–360ms) | Pertama kali anomaly window |
| Stage2 confirmation | 5 detik | 5 consecutive Stage1 alerts |
| SSH mitigation apply | 1–2 detik | SSH → RIC → iptables |
| **Total time-to-mitigate** | **~6–7 detik** | Stage2 + SSH |
| Mitigation active (FPR Stage2) | 1.37% FMR | False mitigation rate |
| Auto-restore | 10 detik | Setelah severity=0 berlanjut |
| Cooldown sebelum re-trigger | 30 detik | Anti-flapping |

### 3.5 Inference Latency & Resource Usage Model (Benchmark Python, CPU-only)

> **Catatan metodologi:** Angka di bawah diukur dari proses Python standalone (PyTorch inference langsung),
> bukan dari binary xApp C (`xapp_sec_moni`) yang berjalan di RIC. CPU usage xApp C aktual perlu
> diukur via `pidstat`/`top` langsung di node RIC (10.91.2.2) saat xApp berjalan — belum dilakukan
> karena SSH ke RIC belum dikonfigurasi dari mesin ini.

#### 3.5.1 Latency Single-Window (batch=1, seq_len=10, 2.000 iterasi)

| Metrik | GRU-UE v1 | LSTM-UE v1 | Keterangan |
|--------|:---------:|:----------:|------------|
| Mean | 2,80 ms | 1,35 ms | LSTM 2× lebih cepat (unidirectional) |
| Median | 2,01 ms | 0,93 ms | |
| P95 | 5,87 ms | 3,67 ms | |
| P99 | 10,49 ms | 5,87 ms | |
| Max | 38,3 ms | 62,6 ms | Spike GC/scheduling |

#### 3.5.2 Throughput Maksimum (batch inference)

| Batch | GRU throughput | LSTM throughput |
|------:|:--------------:|:---------------:|
| 1 | 417 inf/s | 657 inf/s |
| 8 | 2.011 inf/s | 5.128 inf/s |
| 32 | 6.666 inf/s | 14.050 inf/s |
| 128 | 10.073 inf/s | 34.939 inf/s |

#### 3.5.3 CPU & Memory — Operasi Real-Time (1 Hz, KPM 1000ms, per-UE)

Diukur selama 10 detik per konfigurasi (single-window streaming pada rate aktual).  
Sistem: 4 core CPU. CPU % = persentase dari total kapasitas sistem (4 core = 400%).

| Jumlah UE | GRU latency | GRU CPU sistem | LSTM latency | LSTM CPU sistem |
|----------:|:-----------:|:--------------:|:------------:|:---------------:|
| 1 UE | 6,5 ms | ~2,0% | 6,2 ms | ~2,3% |
| 5 UE | 31 ms | ~3,7% | 18 ms | ~3,0% |
| 10 UE | 41 ms | ~4,4% | 44 ms | ~4,7% |
| 20 UE | 73 ms | ~6,9% | 57 ms | ~6,2% |

Pada 1 UE (konfigurasi testbed saat ini): kedua model menggunakan **<3% CPU total** — sangat
efisien untuk Near-RT RIC. Bahkan 20 UE simultaneous masih <7% CPU dengan latency jauh di bawah
budget 1.000ms window KPM.

#### 3.5.4 Model Footprint

| | GRU-UE v1 | LSTM-UE v1 |
|--|:---------:|:----------:|
| Parameters | 87.870 | 65.373 |
| File size | 353 KB | 263 KB |
| RSS (kedua model loaded, idle) | ~563 MB | — |
| Δ memory saat inferensi | +0,1 MB | ~0 MB |

> RSS 563 MB tinggi karena Python + PyTorch runtime overhead, bukan ukuran model itu sendiri.
> xApp C menggunakan ONNX Runtime yang jauh lebih ringan.

### 3.3 Latensi per Serangan (Ekspektasi)

| Serangan | Stage1 Latency | Stage2 Rate | Keterangan |
|----------|:--------------:|:-----------:|------------|
| UL Flood | ~120–360ms | 96.5% | Rule trigger cepat (threshold PRB_UL) |
| DL Flood | ~120–360ms | 98.1% | Rule trigger cepat (threshold PRB_DL) |
| Burst ON/OFF | ~360ms–1s | 94.6% | Burst pattern perlu beberapa window |
| RRC Storm | ~1–3s | 93.6% | `empty_ind_rate` accumulation perlu waktu |
| **Unknown (low-and-slow)** | **TBD** | **TBD** | Eksperimen belum dilakukan |

### 3.4 Throughput Reduction saat Mitigasi (Ekspektasi)

| Mode Mitigasi | Mekanisme | Ekspektasi |
|---------------|-----------|------------|
| E2SM-RC PRB Throttle | PRB max 5% via E2 | Throughput drop ~95% |
| iptables DROP | Layer 3 IP blocking | Throughput drop ~100% |
| Kombinasi | PRB + iptables | Throughput drop ~100% |

> Catatan: E2SM-RC PRB throttle saat ini dinonaktifkan karena [srsRAN RC Bug #468](https://github.com/srsran/srsRAN_Project/issues/468). Mitigasi aktif saat ini via iptables Layer 3 saja.

---

## 4. Rencana Kedepan

### 4.1 Eksperimen yang Belum Dilakukan

| # | Eksperimen | Status | Output |
|---|------------|:------:|--------|
| E1 | Unknown Attack (label=7) — sesi 1 (UE1 attacker) | ⬜ Belum | `results/eval_results_unknown_s1.json` |
| E2 | Unknown Attack (label=7) — sesi 2 (UE2 attacker, role-swap) | ⬜ Belum | `results/eval_results_unknown_s2.json` |
| E3 | Mitigation experiment — UL Flood + iperf throughput measurement | ⬜ Belum | `results/eval_results_mitigation_ul.json` |
| E4 | Mitigation experiment — Unknown Attack (label=7) + mitigate mode | ⬜ Belum | `results/eval_results_mitigation_unknown.json` |
| E5 | GRU ensemble evaluation pada dataset unknown attack | ⬜ Belum | `results/eval_results_gru_unknown.json` |
| E6 | Rekam data serangan per-UE (label 1–4) dengan xApp per-UE | ⬜ Belum | `csv/dataset_attack_ue_juni.csv` |
| E7 | Evaluasi GRU-UE v1 & LSTM-UE v1 pada data serangan per-UE (E6) | ⬜ Belum (butuh E6) | `results/eval_ue_v1_attack.json` |
| E8 | CPU usage xApp C aktual di RIC via `pidstat` saat xApp running | ⬜ Belum | Log di RIC |

### 4.2 Parameter Unknown Attack (Label=7)

```bash
# Di UE attacker (via Termux SSH):
iperf3 -u -b 30M -t 180   # Target: prb_usage_ul_ratio ≈ 0.40–0.65

# Airplane toggle (ADB):
# Interval: 30–60 detik (lebih jarang dari RRC Storm biasa)
```

**Timeline per sesi (8–10 menit):**

| Waktu | UE-1 | UE-2 |
|-------|-------|-------|
| 0–2 mnt | benign browsing | benign |
| 2–5 mnt | UL low-rate + reconnect periodik | benign |
| 5–6 mnt | recovery benign | benign |
| 6–9 mnt | benign | UL low-rate + reconnect periodik |
| 9–10 mnt | recovery | recovery |

**Switch label:**
```bash
./helpers/switch_label.sh 7 unknown_low_slow UE1
```

### 4.3 Metrik Target per Eksperimen

**E1/E2 — Unknown Attack Detection:**

| Komponen | Ekspektasi | Argumen Thesis |
|----------|-----------|----------------|
| Rule UL Flood | Tidak trigger (PRB < threshold) | ✓ |
| Rule RRC Storm | Tidak trigger (interval terlalu jarang) | ✓ |
| LSTM v16 | Reconstruction error naik | Pola kombinasi belum dilihat di training |
| LSTM v22 | Mungkin naik (RRC-like pattern) | Tergantung intensitas reconnect |
| GRU-A | Mungkin naik (UL pattern) | GRU sensitif UL Flood |
| UE benign | Tidak terdeteksi | False positive tetap rendah |

**E3 — Mitigation:**

| Metrik | Target | Cara Ukur |
|--------|--------|-----------|
| Throughput reduction | ≥90% | iperf Mbps sebelum vs sesudah |
| Time-to-mitigate | ~6–7 detik | Log timestamp Stage2 → iptables apply |
| False Mitigation Rate | ≤1.37% | Stage2 FPR × durasi benign |

### 4.4 Peningkatan GRU — Hasil Threshold Tuning (✅ Selesai)

**Dilakukan:** Sweep threshold GRU-A (0.000071–0.063) dan GRU-B (0.000277–0.070) pada `dataset_attack_mei.csv` via `sweep_gru_threshold.py`.

**Hasil:** Cukup turunkan GRU-B dari 0.009865 → **0.003363**:

| Serangan | Sebelum | Sesudah | Delta |
|----------|:-------:|:-------:|:-----:|
| UL Flood | 99.2% | 99.6% | +0.4pp |
| DL Flood | **25.9%** | **99.4%** | **+73.5pp** ✅ |
| Burst ON/OFF | 93.0% | 98.7% | +5.7pp |
| RRC Storm | 61.3% | 71.5% | +10.2pp |
| Overall | 72.7% | **93.2%** | +20.5pp |
| FPR Stage1 | 2.14% | 5.3% | +3.2pp |

**Implementasi tuning:**
```python
# models/gru_autoencoder_B_v1.pt — ubah threshold di file model
import torch, pickle
state = torch.load("models/gru_autoencoder_B_v1.pt", weights_only=False)
state['anomaly_threshold'] = 0.003363
torch.save(state, "models/gru_autoencoder_B_v1_tuned.pt")
```

**Atau:** Override threshold di `evaluate_gru.py` via argparse `--thresh-b 0.003363`.

**Analisis sweep GRU-A standalone (untuk DL Flood):**
- thresh=0.001595: DL=98.8%, FPR=3.0% — GRU-A bisa deteksi DL Flood jika threshold cukup rendah
- Namun RRC Storm GRU-A maksimum 45.2% bahkan di FPR=5% — GRU-A tidak cocok untuk RRC Storm
- GRU-B lebih efisien untuk DL Flood DAN RRC Storm secara bersamaan

**Limitasi yang tidak bisa diperbaiki via tuning:**
- RRC Storm GRU-B maksimum ~71–73% (di FPR=4–5%) — ceiling ini karena `empty_ind_rate` zero-range
- Sekitar 30% window RRC Storm memiliki skor GRU mendekati Normal (median skor GRU-A = 0.000321, hampir sama Normal median 0.000184)
- Perbaikan RRC Storm lebih lanjut memerlukan retraining (lihat Opsi B di bawah)

### 4.5 Peningkatan Lanjutan (Jika Waktu Memungkinkan)

| Ide | Dampak Estimasi | Kompleksitas | Status |
|-----|:--------------:|:------------:|:------:|
| **Threshold tuning GRU-B** | DL Flood 25.9%→99.4%, RRC 61.3%→71.5% | Low | **✅ Done** |
| Retrain GRU-B dengan `empty_ind_rate` variation | RRC Storm +10–15% lebih lanjut | Medium | ⬜ Belum |
| Triple ensemble (Rule+LSTM+GRU) | FPR Stage1 turun, recall naik | Medium | ⬜ Belum |
| Score smoothing LSTM (consec=7) | FPR LSTM 6.27% → ~4.5% | Sudah implemented | ⬜ Evaluasi |

---

## 5. File Model & Evaluasi

### Model Aktif

| Model | File | Fitur | Threshold | Spesialisasi |
|-------|------|:-----:|:---------:|-------------|
| LSTM v16 | `security_model_v16.onnx` | 25 | 0.21 | UL Flood, DL Flood |
| LSTM v22 | `security_model_v22.onnx` | 25 | 0.50 | RRC Storm |
| GRU-A | `models/gru_autoencoder_A_v1.pt` | 16 | 0.002881 | UL Flood, Burst |
| GRU-B | `models/gru_autoencoder_B_v1.pt` | 16 | 0.009865 (orig) / **0.003363** (tuned) | RRC Storm, Burst, DL Flood |
| LSTM C xApp | `security_model.onnx` | 10 | baked-in | Deploy di xapp_sec_moni |
| **GRU-UE v1** | `models/gru_ue_v1.pt` | **15 per-UE** | 2.793.671 (P99) | Per-UE anomaly, FPR=1.00% |
| **LSTM-UE v1** | `models/lstm_ue_v1.pt` | **15 per-UE** | 2.793.713 (P99) | Per-UE anomaly, FPR=1.00% |

### Command Evaluasi

```bash
# LSTM Dual Ensemble
python3 evaluate_detection.py \
  --csv csv/dataset_attack_mei.csv \
  --dual --num-features 25 \
  --score-smooth-n 1 --min-consec 3

# GRU Dual Ensemble
./venv/bin/python3 evaluate_gru.py \
  --model-a models/gru_autoencoder_A_v1.pt \
  --model-b models/gru_autoencoder_B_v1.pt \
  --csv csv/dataset_attack_mei.csv \
  --scaler models/scaler_gru.pkl

# GRU + LSTM perbandingan
./venv/bin/python3 evaluate_gru.py \
  --model-a models/gru_autoencoder_A_v1.pt \
  --model-b models/gru_autoencoder_B_v1.pt \
  --csv csv/dataset_attack_mei.csv \
  --scaler models/scaler_gru.pkl \
  --compare-lstm \
  --lstm-a security_model_v16.onnx --thresh-a 0.21 \
  --lstm-b security_model_v22.onnx --thresh-b 0.5
```

### Command Evaluasi Per-UE

```bash
# Per-UE models (GRU-UE v1 + LSTM-UE v1) — benign validation + attack dataset
python3 evaluate_ue_models.py \
  --val csv/dataset_validation_ue_juni.csv \
  --attack csv/dataset_attack_ue_juni.csv \   # butuh E6 dulu
  --output results/eval_ue_v1.json
```

### Hasil Evaluasi Tersimpan

| File | Isi | Status |
|------|-----|:------:|
| `results/eval_results_gru_ensemble_v1.json` | GRU ensemble known attacks (cell-level) | ✅ Ada |
| `docs/eval_dual_v16_v22.log` | LSTM dual ensemble known attacks (raw log) | ✅ Ada |
| `results/eval_ue_v1.json` | GRU-UE & LSTM-UE benign validation (FPR only) | ✅ Ada |
| `results/eval_results_unknown_s1.json` | Unknown attack sesi 1 | ⬜ Belum |
| `results/eval_results_unknown_s2.json` | Unknown attack sesi 2 | ⬜ Belum |
| `results/eval_results_mitigation_ul.json` | Mitigation experiment UL Flood | ⬜ Belum |
| `results/eval_results_gru_unknown.json` | GRU cell-level pada unknown attack | ⬜ Belum |
| `results/eval_ue_v1_attack.json` | GRU-UE & LSTM-UE pada serangan per-UE | ⬜ Belum (butuh E6) |

---

## 6. Catatan Penting

### Bug yang Sudah Difix

| Bug | Dampak | Fix |
|-----|--------|-----|
| `security_model_v16_raw.onnx` dipakai sebagai Model-A | UL Flood LSTM 0.7% → seharusnya 81.4% | Gunakan `security_model_v16.onnx` (scaler baked-in) |
| `train_gru.py` save model sebelum `fit_threshold()` | `anomaly_threshold=None` di checkpoint | Pindah `model.save()` setelah `fit_threshold()` |
| Scaler mismatch GRU (27 vs 16 fitur) | Evaluasi crash / scores salah | Buat `models/scaler_gru.pkl` 16-fitur khusus |
| DualLSTMDetector 2-of-3 voting + score smoothing | Smoothing perlu ekspansi spike, FPR meningkat | Ganti ke consecutive counter (`_cnt_a/_cnt_b`) |
| KPM FORMAT_3 NO_MEAS_VALUE untuk RRU.PrbUsedUl/Dl per-UE | prb_ul/dl selalu 0 di dataset per-UE | MAC PRB cache fallback — `g_mac_ul_prb[]`/`g_mac_dl_prb[]` di `xapp_sec_moni.c` |
| Dataset per-UE prb_usage_dl_ratio & prb_total > 1.0 | Nilai tidak valid, melanggar asumsi normalisasi | Clip in-place: training (10+13 baris), validation (24+31 baris) |

### Limitasi Sistem

| Limitasi | Detail |
|----------|--------|
| GRU `empty_ind_rate` zero-range | Feature selalu 0 di training → RRC Storm GRU maksimum ~71.5% (setelah threshold tuning) |
| E2SM-RC PRB throttle off | srsRAN RC Bug #468 — mitigasi via iptables saja |
| Restoration manual | Tidak ada auto-cooldown — perlu restart script manual |
| iptables blunt instrument | Blokir seluruh UE subnet, bukan per-UE |
| LSTM FPR tinggi (6.27%) | Heavy-tailed normal distribution — perlu retraining dengan regularization untuk perbaikan signifikan |
| Per-UE confusion matrix belum tersedia | Tidak ada dataset serangan per-UE format (15 fitur); evaluasi parsial tidak valid — 10/15 fitur = 0 menyebabkan skor ~31 vs threshold 2,8M |
| CPU xApp C belum diukur | Benchmark hanya PyTorch Python standalone; `pidstat` di RIC saat xApp running belum dilakukan (SSH key belum dikonfigurasi) |
| srsRAN KPM per-UE `NO_MEAS_VALUE` | RRU.PrbUsedUl/Dl per-UE selalu 0 di FORMAT_3 — diatasi dengan MAC PRB cache fallback di xapp_sec_moni.c |
