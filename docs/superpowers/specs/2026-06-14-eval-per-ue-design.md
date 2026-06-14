# Per-UE IDS Evaluation Framework — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Evaluate per-UE IDS — LSTM-UE v1, GRU-UE v1, dan Rule-based R1–R5 — menggunakan dataset attack pertama (`dataset_attack_ue_juni.csv`) dan menghasilkan metrik thesis-grade + 4 figure siap pakai.

**Output utama:** `evaluate_per_ue_v2.py` — satu file, menggantikan `evaluate_ue_models.py` yang lama.

**Tech Stack:** Python 3, PyTorch, NumPy, scikit-learn (ROC/AUC), Matplotlib, pickle (RobustScaler).

---

## Section 1 — Data Pipeline & Window Labeling

### Preprocessing

Terapkan sebelum windowing, pada kedua dataset:

```python
for col in ["prb_usage_ul_ratio", "prb_usage_dl_ratio", "prb_total"]:
    df[col] = df[col].clip(0.0, 1.0)
```

Alasan: `prb_usage_dl_ratio` max=1.05, `prb_total` max=1.11 di dataset. UL max=0.94 (tidak overshoot) tetapi clip defensif tidak ada downside.

### Datasets

| Dataset | Path | Digunakan untuk |
|---------|------|----------------|
| Validation | `csv/dataset_validation_ue_juni.csv` | **FPR saja** — pure benign |
| Attack | `csv/dataset_attack_ue_juni.csv` | Confusion matrix, TPR, per-class recall, detection latency |

### Windowing

- **Per RNTI**, diproses secara independen dalam urutan kronologis
- RNTI transien (3, 4, 5 — masing-masing ≤18 rows) tidak menghasilkan window lengkap dan otomatis dilewati
- `seq_len = 10`, `stride = 1`
- Window label = label baris terakhir dalam window (konsisten dengan keputusan runtime C xApp)
- **Log mixed-window statistics:** hitung dan simpan jumlah window dengan `0 < attack_ratio < 1.0` ke metadata JSON

### Catatan RNTI

Dataset mengandung 8 RNTI. RNTI 3/4/5 (10, 18, 1 baris — semua label=1) adalah RNTI reassignment transien selama UL Flood, bukan UE terpisah. Tidak ada baris benign untuk RNTI ini. Konsekuensinya: mereka tidak berkontribusi ke window manapun dan tidak mempengaruhi evaluasi.

---

## Section 2 — Detection Engines

### Lima Konfigurasi (Ablation)

| # | Nama | Logic |
|---|------|-------|
| 1 | `rule_only` | R1–R5 saja |
| 2 | `lstm_only` | LSTM-UE v1 MSE > threshold saja |
| 3 | `gru_only` | GRU-UE v1 MSE > threshold saja |
| 4 | `lstm_hybrid` | rule OR LSTM |
| 5 | `gru_hybrid` | rule OR GRU |

Kombinasi `rule AND ML` tidak diimplementasi di tahap ini. Akan ditambahkan sebagai varian jika FPR hybrid terlalu tinggi.

### Rule Engine — R1–R5 (Python, Stateful)

Counter per RNTI berjalan terus secara kronologis — **tidak di-reset per window**. Ini mereplikasi persis perilaku C xApp.

```python
# Thresholds langsung dari sec_ids_ue.c
R1: feat[3] > 15000.0 OR  feat[1] > 0.70   → consec_needed=5   # UL Flood
R2: feat[2] > 15000.0 OR  feat[0] > 0.85   → consec_needed=5   # DL Flood
R3: feat[9] > 0.12    AND feat[8] > 0.05   → consec_needed=5   # Burst
R4: feat[10] >= 0.90  AND feat[8] > 0.50   → consec_needed=10  # Persistence/RoQ
R5: feat[1] > 0.30    AND feat[7] < 5000.0 → consec_needed=3   # Efficiency/LDoS
```

Feature indices mengikuti `feature_schema_ue.py`:
- [0]=prb_usage_dl_ratio, [1]=prb_usage_ul_ratio, [2]=thp_dl_kbps, [3]=thp_ul_kbps
- [7]=ul_efficiency, [8]=prb_ul_roll_mean, [9]=prb_ul_roll_std, [10]=ul_persistence

Output per timestep: `rule_mask` (bitmask uint8, bit 0–4 = R1–R5), `rule_fires` (bool = mask > 0).

**Alignment:** Rule engine dapat fire sejak t=0. Evaluasi hanya dari t≥9 agar align dengan ML/window label.

### ML Engine

```python
# Per RNTI:
X_scaled = scaler.transform(X_rnti)           # RobustScaler dari .pkl
windows   = build_windows(X_scaled, seq_len=10)  # shape (N-9, 10, 15)
mse       = model.compute_reconstruction_error(windows)  # shape (N-9,)
anomaly   = mse > threshold
```

MSE di-align ke timestep terakhir setiap window: `anomaly[i]` ↔ `timestep[i + seq_len - 1]`.

Models dan thresholds:
- LSTM-UE v1: `models/lstm_ue_v1.pt`, scaler `models/lstm_ue_v1_scaler.pkl`, threshold `2793713.03` (P99, `validation_set`)
- GRU-UE v1: `models/gru_ue_v1.pt`, scaler `models/gru_ue_v1_scaler.pkl`, threshold `2793671.0` (P99, `validation_set`)

**Inference latency** diukur per batch: `time.perf_counter()` sebelum dan sesudah `model.compute_reconstruction_error()`, dikumpulkan lalu dihitung mean/median/P95.

### Timeline per RNTI (t ≥ 9)

```
rule_fires[t]   ← dari rule engine (stateful sejak t=0)
ml_anomaly[t]   ← dari mse[t-9] (tersedia mulai t=9)
hybrid[t]       ← rule_fires[t] OR ml_anomaly[t]
window_label[t] ← labels[t]
```

---

## Section 3 — Metrics

### Sumber per Metrik

| Metrik | Sumber |
|--------|--------|
| FPR_val, TNR | `dataset_validation_ue_juni.csv` (pure benign) |
| Recall, F1, Precision | `dataset_attack_ue_juni.csv` |
| Confusion matrix (TP/FP/TN/FN) | `dataset_attack_ue_juni.csv` — label=0 → negatives, label>0 → positives |
| Detection latency | `dataset_attack_ue_juni.csv` — per attack segment |
| Inference latency | Diukur saat inferensi berlangsung |
| ROC-AUC | `dataset_attack_ue_juni.csv`, menggunakan raw MSE sebagai skor |

### Metrik Utama (Main Table)

Dihitung untuk semua 5 konfigurasi:

```
recall   = TP / (TP + FN)
precision = TP / (TP + FP)
f1       = 2 * precision * recall / (precision + recall)
fpr_val  = FP_val / N_val_windows   ← dari validation set
```

Akurasi tidak dilaporkan (distribusi label 5810:2323 membuat accuracy tidak informatif untuk IDS).

### Detection Latency

Definisi: untuk setiap **attack segment** (blok rows berurutan dengan label>0 per RNTI), hitung:

```
latency = timestamp_first_alert - timestamp_attack_start
```

Jika dalam satu segment tidak ada alert sama sekali → tidak masuk perhitungan latency (terhitung FN). Laporkan: `mean`, `median` per konfigurasi per attack class.

### Inference Latency

Hanya untuk LSTM dan GRU (rule-based tidak ada model inference). Laporkan: `mean_ms`, `median_ms`, `p95_ms`.

### Per-Class Recall

Untuk label k ∈ {1=UL Flood, 2=DL Flood, 3=Burst, 4=RoQ}:

```
recall_k = (windows berlabel k yang terdeteksi anomali) / (semua windows berlabel k)
```

### ROC-AUC

Hanya `lstm_only` dan `gru_only`. Input ke `sklearn.metrics.roc_curve`: `y_true` = binary (label>0), `y_score` = raw MSE. Rule-based ditampilkan sebagai titik operasi (★) di plot yang sama.

### Pooling

Semua windows dari semua RNTI di-pool — tidak per-RNTI lalu di-average. RNTI 7 dan 8 memiliki lebih banyak sampel secara wajar; per-RNTI averaging akan memberikan bobot berlebih ke RNTI transien.

---

## Section 4 — Output

### JSON Structure

File: `results/eval_per_ue_v2_<YYYYMMDD_HHMMSS>.json`

```json
{
  "metadata": {
    "val_csv": "csv/dataset_validation_ue_juni.csv",
    "attack_csv": "csv/dataset_attack_ue_juni.csv",
    "seq_len": 10,
    "thresholds": {
      "lstm": 2793713.03,
      "gru": 2793671.0,
      "source": "validation_p99"
    },
    "window_counts": {
      "validation": 0,
      "attack_total": 0,
      "attack_label0": 0,
      "attack_label_gt0": 0,
      "mixed": 0,
      "mixed_pct": 0.0
    }
  },
  "results": {
    "rule_only": {
      "recall": 0.0, "precision": 0.0, "f1": 0.0, "fpr_val": 0.0,
      "confusion_matrix": { "tn": 0, "fp": 0, "fn": 0, "tp": 0 },
      "detection_latency": {
        "ul_flood": { "mean_s": 0.0, "median_s": 0.0 },
        "dl_flood": { "mean_s": 0.0, "median_s": 0.0 },
        "burst":    { "mean_s": 0.0, "median_s": 0.0 },
        "roq":      { "mean_s": 0.0, "median_s": 0.0 }
      },
      "per_class_recall": { "ul_flood": 0.0, "dl_flood": 0.0, "burst": 0.0, "roq": 0.0 }
    },
    "lstm_only": {
      "recall": 0.0, "precision": 0.0, "f1": 0.0, "fpr_val": 0.0,
      "confusion_matrix": { "tn": 0, "fp": 0, "fn": 0, "tp": 0 },
      "inference_latency": { "mean_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0 },
      "auc": 0.0,
      "detection_latency": {
        "ul_flood": { "mean_s": 0.0, "median_s": 0.0 },
        "dl_flood": { "mean_s": 0.0, "median_s": 0.0 },
        "burst":    { "mean_s": 0.0, "median_s": 0.0 },
        "roq":      { "mean_s": 0.0, "median_s": 0.0 }
      },
      "per_class_recall": { "ul_flood": 0.0, "dl_flood": 0.0, "burst": 0.0, "roq": 0.0 }
    },
    "gru_only": {
      "recall": 0.0, "precision": 0.0, "f1": 0.0, "fpr_val": 0.0,
      "confusion_matrix": { "tn": 0, "fp": 0, "fn": 0, "tp": 0 },
      "inference_latency": { "mean_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0 },
      "auc": 0.0,
      "detection_latency": { "ul_flood": {...}, "dl_flood": {...}, "burst": {...}, "roq": {...} },
      "per_class_recall": { "ul_flood": 0.0, "dl_flood": 0.0, "burst": 0.0, "roq": 0.0 }
    },
    "lstm_hybrid": {
      "recall": 0.0, "precision": 0.0, "f1": 0.0, "fpr_val": 0.0,
      "confusion_matrix": { "tn": 0, "fp": 0, "fn": 0, "tp": 0 },
      "inference_latency": { "mean_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0 },
      "detection_latency": { "ul_flood": {...}, "dl_flood": {...}, "burst": {...}, "roq": {...} },
      "per_class_recall": { "ul_flood": 0.0, "dl_flood": 0.0, "burst": 0.0, "roq": 0.0 }
    },
    "gru_hybrid": {
      "recall": 0.0, "precision": 0.0, "f1": 0.0, "fpr_val": 0.0,
      "confusion_matrix": { "tn": 0, "fp": 0, "fn": 0, "tp": 0 },
      "inference_latency": { "mean_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0 },
      "detection_latency": { "ul_flood": {...}, "dl_flood": {...}, "burst": {...}, "roq": {...} },
      "per_class_recall": { "ul_flood": 0.0, "dl_flood": 0.0, "burst": 0.0, "roq": 0.0 }
    }
  }
}
```

### Figures (4 file PNG)

| File | Isi |
|------|-----|
| `eval_confusion.png` | Grid 2×3: confusion matrix binary per konfigurasi (subplot ke-6 = summary text) |
| `eval_per_class.png` | Grouped bar chart: x=konfigurasi, 4 bar per grup (UL/DL/Burst/RoQ) |
| `eval_latency.png` | Dua subplot: (a) detection latency mean per konfigurasi per attack class; (b) inference latency mean/P95 untuk LSTM vs GRU |
| `eval_roc.png` | ROC kurva penuh LSTM-only dan GRU-only + titik operasi (★) rule-only |

### CLI

```bash
python3 evaluate_per_ue_v2.py \
    --val    csv/dataset_validation_ue_juni.csv \
    --attack csv/dataset_attack_ue_juni.csv \
    --output results/ \
    [--save-figures]
```

`--save-figures` opsional. Tanpa flag ini, script tetap mencetak tabel ke stdout dan menyimpan JSON, tetapi tidak membuat PNG.

### Stdout

Ringkasan tabel per konfigurasi dicetak ke stdout setelah selesai, format ASCII table:

```
Config          Recall   F1     FPR_val  Det.Lat(s)  Inf.Lat(ms)
rule_only        0.xxx   0.xxx   0.xxx     x.xx        —
lstm_only        0.xxx   0.xxx   0.xxx     x.xx       x.xx
...
```

---

## File yang Dimodifikasi/Dibuat

| File | Aksi |
|------|------|
| `evaluate_per_ue_v2.py` | **CREATE** — menggantikan `evaluate_ue_models.py` |
| `results/` (direktori) | Auto-created jika belum ada |
| `evaluate_ue_models.py` | Tidak dihapus, dibiarkan untuk referensi |
