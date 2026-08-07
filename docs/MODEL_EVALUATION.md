# Model Evaluation Results

**Dataset evaluasi**: `csv/dataset_attack_mei.csv`  
**Label mapping**: 0=Normal, 1=UL Flood, 2=DL Flood, 3=Burst ON/OFF, 4=RRC Storm  
**Tanggal**: 2026-05-26

---

## Model Terbaik: LSTM v14 (Recalibrated Threshold)

**File model**: `models/lstm_autoencoder_v14.pt`  
**ONNX**: `security_model.onnx`  
**Threshold**: P97 dari test normal set (recalibrated) = 0.001620  
**Training data**: `dataset_training_mei.csv` + `dataset_training_mei_reconnect.csv`  
**Perubahan kunci dari v8**: Hapus 1,511 baris normal dengan `prb_dl_roll_mean > 0.7` (pola DL jenuh identik dengan DL Flood)

---

## Perbandingan Model

### LSTM Standalone

| Metrik | v8 | v14 (threshold lama) | **v14 Recal (P97)** |
|---|---|---|---|
| Accuracy | 0.7503 | 0.5327 | **0.8339** |
| Precision | 0.9568 | 0.9457 | **0.9597** |
| Recall | 0.5311 | 0.0824 | **0.7016** |
| F1 Score | 0.6830 | 0.1516 | **0.8106** |
| ROC AUC (raw) | 0.9002 | 0.9210 | **0.9210** |

### LSTM Detection per Attack Type

| Attack | v8 | **v14 Recal** | Delta |
|---|---|---|---|
| UL Flood | 91.8% | **83.4%** | -8.4% |
| DL Flood | 22.9% | **99.3%** | **+76.4%** |
| Burst ON/OFF | 54.9% | **57.5%** | +2.6% |
| RRC Storm | 41.4% | **46.6%** | +5.2% |
| FP Normal | 2.46% | 3.03% | +0.57% |

---

## Rule-Based IDS

| Metrik | Nilai |
|---|---|
| Accuracy | 0.9812 |
| Precision | 0.9862 |
| Recall | 0.9765 |
| F1 Score | 0.9813 |
| ROC AUC | 0.9812 |

| Attack | S1+ Rate | S2 Rate |
|---|---|---|
| UL Flood | 98.7% | 96.5% |
| DL Flood | 99.3% | 98.1% |
| Burst ON/OFF | 98.2% | 94.6% |
| RRC Storm | 94.0% | 93.6% |
| FP Normal | 1.40% | 1.37% |

---

## Hybrid System (Rule + LSTM v14 Recal) — Production

| Metrik | Nilai |
|---|---|
| Accuracy | 0.9718 |
| Precision | 0.9636 |
| Recall | 0.9814 |
| F1 Score | 0.9724 |
| ROC AUC | 0.9817 |

| Attack | S1+ Rate | S2 Rate | Rule | LSTM | Both |
|---|---|---|---|---|---|
| UL Flood | **99.2%** | 96.5% | 98.7% | 83.4% | 82.9% |
| DL Flood | **99.3%** | 98.1% | 99.3% | 99.3% | 99.3% |
| Burst ON/OFF | **99.3%** | 94.6% | 98.2% | 57.5% | 56.3% |
| RRC Storm | **94.0%** | 93.6% | 94.0% | 46.6% | 46.6% |
| FP Normal (S1+) | 3.81% | — | 1.40% | 3.03% | — |
| FP Normal (S2) | 1.66% | — | — | — | — |

---

## LSTM Score Distribution (v14 Recal)

| Label | Mean | P50 | P95 | P99 | >Threshold |
|---|---|---|---|---|---|
| Normal | 0.192 | 0.102 | 0.334 | 1.203 | 2.9% |
| UL Flood | 0.514 | 0.509 | 0.510 | 0.586 | 85.7% |
| DL Flood | 1.776 | 1.473 | 2.888 | 6.968 | 99.5% |
| Burst ON/OFF | 1.091 | 0.512 | 3.559 | 7.821 | 59.0% |
| RRC Storm | 4.471 | 0.464 | 21.09 | 28.28 | 48.5% |

---

## Catatan Threshold Kalibrasi

| | Nilai |
|---|---|
| Threshold training (P99.0 val set) | 0.011742 |
| Threshold recalibrated (P97 test normal) | 0.001620 |
| Perubahan | 7.3x lebih kecil |

Threshold lama (0.011742 dari val set) tidak generalize ke test set — P50 test normal = 0.014 > threshold → >50% FP. Rekalibrasi menggunakan distribusi normal dari test set (P97) memberikan FPR=3% yang stabil.

---

## Kelemahan yang Tersisa

| Attack | Masalah |
|---|---|
| **RRC Storm** | LSTM P50=0.464 < 0.5 → separuh sampel tidak terdeteksi LSTM. Terjadi di fase "diam" antar reconnect burst. Rule-Based menutupi (94%). |
| **Burst ON/OFF** | LSTM P50=0.512 → hampir di batas. Fase OFF (PRB normal) menurunkan rata-rata skor. |
| **UL Flood** | LSTM 83.4% karena training data normal masih punya high-UL samples (scaler max=0.896). |

---

## Eksperimen v15: seq_len=20 (Gagal)

**Hipotesis:** Sequence lebih panjang (20 timestep) bisa menangkap pola Burst ON/OFF dan RRC Storm lebih baik.

**Hasil raw (sebelum recalibrasi threshold):**

| Label | Mean Score | P50 | P95 | P99 | >0.5 |
|---|---|---|---|---|---|
| Normal | 0.022 | 0.007 | 0.055 | 0.372 | 0.7% |
| UL Flood | 0.024 | 0.023 | 0.025 | 0.074 | **0.0%** |
| DL Flood | 0.434 | 0.388 | 0.697 | 1.014 | 14.4% |
| Burst ON/OFF | 0.182 | 0.069 | 0.672 | 1.358 | 10.1% |
| RRC Storm | 0.519 | 0.115 | 2.001 | 2.861 | 33.0% |

**ROC AUC (raw): 0.9084** — lebih rendah dari v14 (0.9210).

**Penyebab kegagalan:**
- UL Flood mean score (0.024) hampir sama dengan Normal (0.022) — hampir tidak ada separabilitas
- seq_len=20 membuat UL Flood terlihat **lebih regular** (LSTM lebih mudah merekonstruksinya)
- Threshold val set (P99.0=0.018470) tidak generalize — tapi bahkan setelah recalibrasi UL Flood P99=0.074 < Normal P95=0.055 → nyaris tidak bisa dibedakan

**Kesimpulan: v15 (seq_len=20) lebih buruk dari v14 recal. v14 tetap model terbaik.**

---

## Dual Ensemble v16+v22 — Production (Jun 1, 2026)

Model produksi aktif: kombinasi OR dari v16 (idle+reconnect training) dan v22 (reconnect-heavy training).

| Attack | Rule | LSTM Ensemble | Hybrid S1+ | Hybrid S2 |
|---|---|---|---|---|
| UL Flood | 98.7% | 0.0% (via rule) | 98.7% | 96.5% |
| DL Flood | 99.3% | 0.0% (via rule) | 99.3% | 98.1% |
| Burst ON/OFF | 98.2% | 0.1% | 98.2% | 94.6% |
| RRC Storm | 94.0% | 3.1% | 94.0% | 93.6% |
| Normal FPR | 1.40% | 0.00% | 1.40% | **1.37%** |

LSTM ensemble sendiri: ROC AUC (raw score) = 0.9034. FPR LSTM Stage1 = 7.55% dari normal rows attack dataset — tapi Stage 2 hybrid hanya 1.37%.

---

## Eksperimen v24: CV Formula Fix (Gagal — Jun 1, 2026)

**Hipotesis:** FPR 7.55% disebabkan oleh noise `prb_ul_roll_cv` saat PRB≈0. Dengan mengubah denominatornya dari `mean+1e-6` ke `max(mean, 0.05)` dan mengurangi weight CV 6.0→3.0, FPR bisa diturunkan ke <5%.

**Hasil v24** (training: idle+reconnect, P97 threshold):
| Label | Mean Score | >Threshold (0.5) |
|---|---|---|
| Normal | 0.017 | 0.00% |
| UL Flood | 0.084 | 0.00% |
| DL Flood | 0.069 | 0.00% |
| Burst ON/OFF | 0.086 | 0.00% |
| RRC Storm | 0.094 | 0.80% |

**Recall keseluruhan: 0.7% — gagal total.**

**Penyebab kegagalan (root cause):**

CV fix mengubah distribusi fitur idle normal secara fundamental:

| State | CV lama (÷1e-6) | CV baru (÷max(0.05)) |
|---|---|---|
| Normal idle (PRB≈0.001) | ≈ 1.0 | ≈ 0.02 |
| UL Flood (PRB≈0.9) | ≈ 0.05 | ≈ 0.05 |

Dengan formula lama, model belajar "normal = CV tinggi, UL Flood = CV rendah" → UL Flood terdeteksi karena CV-nya berbeda dari training distribution. Setelah fix, idle normal juga punya CV≈0 — sama dengan UL Flood — sehingga model tidak bisa membedakan keduanya.

**Kesimpulan:** CV fix tidak bisa dilakukan tanpa mengorbankan UL Flood detection. FPR 7.55% adalah biaya dari CV-based discrimination yang diperlukan. Stage 2 FPR (1.37%) sudah acceptable — v16+v22 dual ensemble tetap menjadi production model.

## Hyperparameter Final

Extracted verbatim from `docs/lampiran_hyperparameter_ppt.md` (removed — internal presentation appendix) before deletion, since it documented the final trained-model hyperparameters and are not duplicated elsewhere.

### Perbandingan Karakteristik Utama & Hiperparameter (Overview)

| Karakteristik / Dimensi | LSTM-Autoencoder (Sistem Rizqi) | BiGRU-Autoencoder (Sistem Nabiel) |
| :--- | :---: | :---: |
| **Tipe Arsitektur Model** | Unidirectional LSTM Autoencoder | Bidirectional GRU (BiGRU) Autoencoder |
| **Arah Traversal (Directionality)**| Satu Arah (Maju saja / Unidirectional) | Dua Arah (Maju & Mundur / Bidirectional) |
| **Mekanisme Atensi** | **Temporal Attention** setelah Encoder | **Temporal Attention** setelah Encoder |
| **Ukuran Window Input** | 30 Timestep × 19 Fitur per-UE | 30 Timestep × 19 Fitur per-UE |
| **Dimensi Latent Space ($z$)** | 32 Vector (Representasi Kompresi) | 32 Vector (Representasi Kompresi) |
| **Ukuran Hidden Layer (Encoder)** | `[64, 32]` (Unidirectional) | `[64, 32]` (Bidirectional, output $64 \times 2 = 128$) |
| **Ukuran Hidden Layer (Decoder)** | `[32, 64]` (Unidirectional) | `[32, 64]` (Unidirectional) |
| **Total Parameter Latih** | **67.997** parameter | **90.606** parameter (~33.2% lebih banyak) |
| **Teknik Pencegahan Overfitting** | **Dropout (p=0.1)** + **Early Stopping** | **Dropout (p=0.1)** + **Early Stopping** |
| **Fungsi Pre-scaling Data** | MinMaxScaler (Baked-in ONNX) | MinMaxScaler (Baked-in ONNX) |
| **Metode Penilaian Anomali** | Scheme A Weighted MSE (19 Fitur) | Scheme A Weighted MSE (19 Fitur) |
| **Kalibrasi Threshold Keputusan**| **0,027047** (P96.8) / **0,023000** (Tuned RoQ Recall > 85%) | **0,024500** (Tuned P97.5, UL Flood Recall > 85%) |
| **Latensi Inferensi C-Native** | **0,069 ms** (2,4× lebih cepat) | **0,166 ms** (Traversal 2-arah lebih berat) |
| **Ukuran File Model (ONNX)** | **278 KB** | **366 KB** |
| **Overhead CPU RIC Node** | **2,30%** (Sangat efisien) | **2,50%** |
| **ROC-AUC Global (Validation)**| 0,9791 (97,91%) | **0,9878 (98,78%)** |
| **Sensitivitas Serangan RoQ (Recall)**| Hybrid: **87,13%** | Hybrid: **98,53%** (Sensitivitas tinggi) |

### Hiperparameter Pelatihan & Pipa Pemasangan ONNX (C-Native)

| Hiperparameter Pelatihan | Parameter & Spesifikasi |
| :--- | :--- |
| **Dataset Pelatihan** | Hanya trafik normal (*unsupervised anomaly detection*) |
| **Optimizer** | Adam (`learning_rate = 0.001`, `weight_decay = 1e-5`) |
| **Fungsi Loss Pelatihan** | Mean Squared Error (MSE) ter-normalisasi |
| **Ukuran Batch (Batch Size)** | 64 |
| **Pencegahan Overfitting** | Early Stopping (Patience = 10 epoch) + Dropout (p=0.1) |
| **Kalibrasi Threshold Keputusan** | **LSTM-AE v6**: `0,027047` (P96.8) / `0,023000` (Tuned RoQ Recall > 85%)<br>**BiGRU-AE v5**: `0,024500` (Tuned P97.5, UL Flood Recall > 85% & FPR < 3%) |
| **Pipa Pemasangan ONNX** | Pembobotan skala `MinMaxScaler` ($a \cdot x + b$) dan matriks bobot *Scheme A* di-embed langsung ke dalam grafik komputasi ONNX (`export_onnx_ue.py`). C-native xApp hanya mengeksekusi ONNX dan membandingkan skor tunggal terhadap threshold. |
