# Per-UE GRU & LSTM Autoencoder Training — Design Spec

**Date:** 2026-06-13
**Status:** Approved

---

## Goal

Train GRU and LSTM autoencoder models for per-UE anomaly detection using the new
per-UE KPM dataset (`dataset_training_ue_juni.csv` / `dataset_validation_ue_juni.csv`).
These models are separate from the existing cell-level models and do not replace them.

---

## Background

The existing training pipeline uses `feature_schema.py` (16 cell-level features: PRB,
RACH, CQI, empty_ind_rate). The per-UE dataset has 15 different features (PRB + throughput
+ ul_efficiency + temporal rolling stats). Both pipelines must coexist without interference.

**Dataset summary:**
- Training: `csv/dataset_training_ue_juni.csv` — 4200 rows, 70 min, label=0, 1s intervals
- Validation: `csv/dataset_validation_ue_juni.csv` — 1800 rows, 30 min, label=0, 1s intervals
- PRB non-zero: ~38% (MAC fallback active), throughput non-zero: ~60%
- Both datasets clipped: `prb_usage_dl_ratio`, `prb_total` ≤ 1.0

---

## Architecture Decision

**Option chosen: C — Separate scripts + new feature schema file**

- `src/detection/feature_schema_ue.py` — new file, 15 per-UE features
- `train_gru_ue.py` — new script, imports from `feature_schema_ue`
- `train_lstm_ue.py` — new script, imports from `feature_schema_ue`
- `feature_schema.py` — untouched
- `train_gru.py` / `train_lstm.py` — untouched

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `src/detection/feature_schema_ue.py` | Create | 15 per-UE feature names |
| `train_gru_ue.py` | Create | GRU training for per-UE data |
| `train_lstm_ue.py` | Create | LSTM training for per-UE data |
| `models/gru_ue_v1.pt` | Output | Trained GRU model |
| `models/gru_ue_v1_scaler.pkl` | Output | RobustScaler for GRU |
| `models/gru_ue_v1_threshold.json` | Output | P99 threshold + FPR |
| `models/lstm_ue_v1.pt` | Output | Trained LSTM model |
| `models/lstm_ue_v1_scaler.pkl` | Output | RobustScaler for LSTM |
| `models/lstm_ue_v1_threshold.json` | Output | P99 threshold + FPR |

---

## Feature Schema (`feature_schema_ue.py`)

15 features, all present in the per-UE CSV columns:

```python
FEATURE_NAMES = [
    "prb_usage_dl_ratio",   # RRU.PrbUsedDl from KPM/MAC, clipped [0,1]
    "prb_usage_ul_ratio",   # RRU.PrbUsedUl from KPM/MAC, clipped [0,1]
    "thp_dl_kbps",          # DRB.UEThpDl (kbps)
    "thp_ul_kbps",          # DRB.UEThpUl (kbps)
    "prb_direction",        # (prb_ul - prb_dl) / (prb_total + eps), [-1, +1]
    "prb_total",            # prb_dl + prb_ul, clipped [0,1]
    "prb_ul_delta",         # prb_ul[t] - prb_ul[t-1]
    "ul_efficiency",        # thp_ul / prb_ul, clipped [0, 50000]
    "prb_ul_roll_mean",     # rolling mean prb_ul over 10 timesteps
    "prb_ul_roll_std",      # rolling std prb_ul over 10 timesteps
    "ul_persistence",       # fraction of last 10 ts with prb_ul > 0
    "thp_total_kbps",       # thp_dl + thp_ul
    "thp_ul_delta",         # thp_ul[t] - thp_ul[t-1]
    "thp_dl_delta",         # thp_dl[t] - thp_dl[t-1]
    "traffic_direction",    # (thp_ul - thp_dl) / (thp_total + eps), [-1, +1]
]
NUM_FEATURES = len(FEATURE_NAMES)   # 15
FEATURE_WEIGHTS: dict = {}          # uniform — no per-feature weighting yet
```

---

## Script Changes vs Originals

### Removed from original scripts (not applicable to per-UE):
- `_add_computed_features()` — all features already in CSV
- RACH domain override (`rach_preamble`, `rach_roll_mean`, etc.) — no RACH in per-UE
- `--clean-dl-thresh` / `--clean-ul-thresh` filtering — `prb_dl_roll_mean` /
  `prb_ul_roll_max` columns don't exist in per-UE schema

### Changed:
- **Scaler:** `MinMaxScaler` → `RobustScaler` — better for zero-heavy distributions
  (61% of prb_ul rows are 0; MinMaxScaler compresses this poorly)
- **Scaler output path:** `models/scaler.pkl` → `models/<model-out-stem>_scaler.pkl`
  — avoids overwriting cell-level scaler
- **Default model-out:** `models/gru_ue_v1.pt` / `models/lstm_ue_v1.pt`

### Kept identical:
- Model architecture: encoder `[64, 32]`, latent `32`, decoder `[32, 64]`, bidirectional GRU
- `seq_len=10` default
- `epochs=150`, `batch_size=32`, `lr=0.001`
- Threshold fitting at P99 from validation set
- Loss JSON + threshold JSON output format

---

## Training Commands

```bash
cd /home/telmat/sec-xapp

# GRU
./venv/bin/python3 train_gru_ue.py \
  --train csv/dataset_training_ue_juni.csv \
  --val   csv/dataset_validation_ue_juni.csv \
  --seq-len 10 --epochs 150 \
  --model-out models/gru_ue_v1.pt

# LSTM
./venv/bin/python3 train_lstm_ue.py \
  --train csv/dataset_training_ue_juni.csv \
  --val   csv/dataset_validation_ue_juni.csv \
  --seq-len 10 --epochs 150 \
  --model-out models/lstm_ue_v1.pt
```

---

## Success Criteria

- Both models train without error
- Validation loss converges (no NaN, no divergence)
- FPR ≤ 2% at P99 threshold on validation set
- `scaler_ue.pkl` saved separately (cell-level `scaler.pkl` untouched)
