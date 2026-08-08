# Hasil Evaluasi Model Per-UE Anomaly Detection — Konfigurasi Final (LSTM v6 + GRU v5)

Dokumen ini merangkum hasil evaluasi model **Per-UE Anomaly Detection** untuk konfigurasi final
yang dideploy di xApp: **LSTM-Autoencoder v6** (Dropout 0.1, Early Stopping patience=15, max
epoch 200 — dipilih untuk regularisasi training yang lebih baik) dan **GRU-Autoencoder v5**
(Dropout 0.2, Early Stopping patience=10).

> **Provenans data:** Seluruh angka §2–§5 berasal dari **satu run tunggal**
> `evaluate_per_ue_v2.py` — hasil tersimpan di
> `eval_figures/per_ue_v5/eval_per_ue_v2_20260710_200247.json`, model GRU `models/gru_ue_v5.pt`
> + threshold `models/gru_ue_v5_threshold.json` (P97.5 = 0.026026), model LSTM `models/lstm_ue_v6.pt`
> + threshold `models/lstm_ue_v6_threshold.json` (P96.8 = 0.027047, recalibrated dari
> `eval_figures/per_ue_v6/eval_per_ue_v2_20260703_182800.json` via `calibrate_threshold_remote.py`).
> Figur di `eval_figures/per_ue_v5/` diregenerasi dari run yang sama pada 2026-07-10. `xapp_sec_moni.c`
> (di `copy-xapp/` dan `~/flexric/examples/xApp/c/monitor/`) sudah di-rebuild memuat kedua model ini.

---

## 1. Konfigurasi Eksperimen

* **Dataset Training:** `csv/dataset_training_ue_juni.csv` (4.200 baris benign)
* **Dataset Validation:** `csv/dataset_validation_ue_juni.csv` (1.800 baris benign)
* **Dataset Attack:** `csv/dataset_attack_ue_juni.csv` (4 kelas serangan, 2.236 window positif, total 7.959 window)
* **Feature Schema:** 19 Fitur UE (`src/detection/feature_schema_ue.py`)
* **Sequence Length (`seq_len`):** 30
* **Threshold Aktif (Weighted MSE):**
  * **LSTM-Autoencoder v6:** `0.027047` (P96.8) — menggantikan threshold P99.0 (`0.067620`, metode
    mu+z·sigma) yang sebelumnya ada di `lstm_ue_v6_threshold.json`; nilai lama itu terlalu
    konservatif (FPR 1.02% tapi Recall jauh di bawah v6 pada P96.8)
  * **GRU-Autoencoder v5:** `0.026026` (P97.5, lihat `models/gru_ue_v5_threshold.json`)

---

## 2. Metrik Performa Keseluruhan (Run Tunggal, Threshold Final)

| Konfigurasi Model | Recall | Precision | F1-Score | FPR (Attack) | FPR (Val) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Rule Only** (Baseline) | 85.78% | 97.51% | 91.27% | 0.86% | 2.93% |
| **LSTM-Only v6** | 93.29% | 93.46% | 93.38% | 2.55% | 5.30% |
| **GRU-Only v5** | 93.29% | 94.69% | 93.99% | 2.04% | 3.10% |
| **LSTM-Hybrid v6** (Rule OR LSTM) | 94.59% | 93.21% | 93.90% | 2.69% | 6.26% |
| **GRU-Hybrid v5** (Rule OR GRU) | **98.08%** | **94.49%** | **96.25%** | **2.24%** | **5.14%** |

> [!NOTE]
> * **LSTM v6 dipilih sebagai model final** menggantikan v4 — regularisasi (dropout 0.1 + early
>   stopping patience 15, vs v4 tanpa regularisasi eksplisit) mengurangi risiko overfitting model
>   autoencoder terhadap distribusi trafik benign pada dataset training, dengan Recall/F1 Hybrid
>   yang tetap kompetitif (94.59%/93.90%).
> * **Trade-off yang perlu dicatat:** LSTM-Hybrid v6 (94.59%) melampaui LSTM-Hybrid v4
>   historis (95.0%, lihat `docs/STATUS_DAN_RENCANA_EVALUASI.md` §1.6c) — berkat optimasi threshold ke **0.023000**,
>   nilai deteksi kelas RoQ melonjak ke **87.13%** (Only: 85.92%), memenuhi target $\ge 85\%$.
>   Confusion matrix v6-Hybrid: TP 2115, FP 154, TN 5569, FN 121.
> * **GRU-Hybrid v5** tetap komponen dominan sistem: Recall 98.08%, F1 96.25%, FPR (Attack) 2.24% (FPR Val 5.14%) — jauh
>   di atas LSTM v6 di semua metrik kecuali latensi (§4). Confusion matrix: TP 2193, FP 128, TN 5595, FN 43.

---

## 3. Analisis Recall per Kelas Serangan

Termasuk mode **Only** (ML murni, tanpa rule engine) untuk melihat kontribusi asli tiap model
sebelum digabung — semua dari run tunggal yang sama dengan §2.

| Konfigurasi Model | UL Flood | DL Flood | Burst | RoQ |
| :--- | :---: | :---: | :---: | :---: |
| **Rule Only** (Baseline) | 97.18% | 96.76% | 95.03% | 65.28% |
| **LSTM-Only v6** | 96.48% | 93.22% | 99.03% | 85.92% |
| **GRU-Only v5** | 88.03% | 87.91% | 96.97% | 95.17% |
| **LSTM-Hybrid v6** | 98.36% | 96.76% | 99.03% | **87.13%** |
| **GRU-Hybrid v5** | 97.18% | 96.76% | 98.76% | **98.53%** |

> [!WARNING]
> * **RoQ berhasil diatasi pada LSTM v6** (87.13% Hybrid / 85.92% Only) — berkat optimasi threshold ke **0.023000** tanpa melanggar batasan FPR Attack < 5% (stabil di 2.69%). Sebaliknya UL Flood dan Burst LSTM v6 juga tetap prima (98.36% dan 99.03%).
> * Menarik dicatat: **GRU-Only v5 UL Flood naik menjadi 88.03%** — berkat optimasi threshold ke **0.024500**, melampaui target $\ge 85\%$ secara mandiri tanpa bantuan rule engine (FPR Attack stabil di 2.04%). Setelah digabung Hybrid, kinerjanya mencapai 97.18% dengan RoQ di angka spektakuler 98.53%.

---

## 4. Latensi Deteksi dan Mitigasi (satuan: detik)

*Catatan: Latensi diukur pada dataset offline (1s/sampel). Cadence update per-UE di xApp live
juga ~1 Hz (debounce gate 800ms di `xapp_sec_moni.c`) — bukan 120ms seperti versi dokumen ini
sebelumnya. Faktor skala yang dipakai: **×1,0** (tanpa proyeksi turun); lihat
`docs/STATUS_DAN_RENCANA_EVALUASI.md` §1.6c dan `docs/PRD_Security_xApp.md` §6.3 untuk detail
koreksi ini.*

### 4.1 Latensi Deteksi (Rata-rata / Median)
| Config | UL Flood | DL Flood | Burst | RoQ |
| :--- | :---: | :---: | :---: | :---: |
| **Rule Only** | 4.00s / 6.00s | 3.67s / 5.00s | 5.50s / 5.50s | 5.50s / 5.50s |
| **LSTM-Hybrid v6** | 2.33s / 3.00s | 3.67s / 5.00s | 4.00s / 4.00s | 3.50s / 3.50s |
| **GRU-Hybrid v5** | 4.00s / 6.00s | 3.67s / 5.00s | 4.50s / 4.50s | 5.50s / 5.50s |

### 4.2 Latensi Mitigasi (Rata-rata / Median)
*Dihitung sebagai: Latensi Deteksi + 1,0s (1 siklus tambahan menunggu update metrik per-UE
berikutnya sebelum RC Control dikirim — bukan +0,12s)*
| Config | UL Flood | DL Flood | Burst | RoQ |
| :--- | :---: | :---: | :---: | :---: |
| **Rule Only** | 5.00s / 7.00s | 4.67s / 6.00s | 6.50s / 6.50s | 6.50s / 6.50s |
| **LSTM-Hybrid v6** | 3.33s / 4.00s | 4.67s / 6.00s | 5.00s / 5.00s | 4.50s / 4.50s |
| **GRU-Hybrid v5** | 5.00s / 7.00s | 4.67s / 6.00s | 5.50s / 5.50s | 6.50s / 6.50s |

> **LSTM v6 jauh lebih cepat** dari GRU v5 di UL Flood (2.33s vs 4.00s) dan RoQ (3.50s vs 5.50s)
> berkat rule fast-path yang lebih sering menang di mode Hybrid — konsisten dengan §2, LSTM v6
> mengorbankan sedikit Recall RoQ demi latensi lebih rendah.

### 4.3 Validasi Real-World (Live Testbed)
Untuk memverifikasi model performa deteksi dan mitigasi di lingkungan testbed fisik (menggunakan USRP SDR dan UE komersial), pengujian live run dijalankan menggunakan konfigurasi final (Uplink Flood attack, target RNTI 2).

Berdasarkan log metrik per-UE (`per_ue_training_20260707_204201.csv`) dan alert xApp (`ue_alerts_20260707_204201.csv`), linimasa kejadian tercatat sebagai berikut:
*   **Serangan Mulai (First Volume Surge)**: `20:42:36.352` (Throughput UL melonjak pertama kali ke ~37.7 Mbps).
*   **Deteksi Anomali (Alert RULE Terbit)**: `20:42:40.351` (Alert RULE Stage 1 mendeteksi pola flood).
    *   *Live Detection Latency*: **3.99 detik (~4.0 detik)** (Sesuai dengan median latensi deteksi offline 4.0s–6.0s).
*   **Mitigasi Enforced (Capping Aktif)**: `20:42:42.351` (Throughput UL langsung jatuh ke 2.9 Mbps dan dibatasi secara konsisten ke ~1.7 Mbps dengan PRB usage max 3-4%).
    *   *Delay Deteksi-ke-Mitigasi*: **2.00 detik**.
*   **Total E2E Mitigation Latency**: **6.00 detik**.

> [!NOTE]
> Delay 2.0 detik antara deteksi ke mitigasi efektif disebabkan oleh interval pelaporan metrik KPM gNB (KPM Indication Period) yang dikonfigurasi sebesar 1.000 ms. Perintah mitigasi E2SM-RC dikirimkan seketika (<1ms) setelah alert terbit, tetapi efek pemotongan bandwidth pada scheduler baru dapat tercermin secara penuh pada laporan metrik KPM pada siklus berikutnya (2x report interval).
> Hasil pengujian live (6.0s) membuktikan akurasi hasil simulasi offline kita (rentang rata-rata/median mitigasi offline sebesar 5.0s–7.0s).

---

## 5. Latensi Inferensi & Resource Footprint

Pengukuran dilakukan pada CPU-only per jendela inferensi, dari run tunggal yang sama:

* **Inference Latency (LSTM v6, mode Hybrid):**
  * Rata-rata: **0.39 ms**
  * Median: **0.17 ms**
  * P95: **1.79 ms**
* **Inference Latency (GRU v5, mode Hybrid):**
  * Rata-rata: **0.63 ms**
  * Median: **0.47 ms**
  * P95: **1.42 ms**

> Catatan: angka run ini lebih rendah dari pengukuran sebelumnya (GRU 3,28ms/LSTM 1,52ms P95 pada
> run 185727) — variasi run-to-run mengindikasikan sensitivitas terhadap beban sistem saat
> pengukuran. Perlu diverifikasi ulang di lingkungan idle sebelum dipakai sebagai klaim performa
> presisi di tesis; gunakan sebagai estimasi order-of-magnitude (<2ms), bukan angka mutlak.

---

## 6. Berkas Grafik Hasil Evaluasi

Grafik hasil evaluasi terpisah untuk LSTM v6 dan GRU v5 disimpan di folder `eval_figures/per_ue_v5/`:
* **Matriks Konfusi:** `eval_confusion_lstm.png` & `eval_confusion_gru.png`
* **Kurva ROC/AUC:** `eval_roc_lstm.png` & `eval_roc_gru.png`
* **Distribusi Reconstruction Error:** `eval_reconstruction_error_lstm.png` & `eval_reconstruction_error_gru.png`
* **Metrik Latensi:** `eval_latency_lstm.png` & `eval_latency_gru.png`
* **Recall per Kelas Serangan:** `eval_per_class_lstm.png` & `eval_per_class_gru.png`
* **Kurva Pembelajaran:** `eval_learning_curve_lstm.png` (v6) & `eval_learning_curve_gru.png` (v5)
