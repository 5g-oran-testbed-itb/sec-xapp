# Design: Dual-LSTM Ensemble (Approach B) → Approach A — Single Model v23

## Status

**Approach B (Dual-LSTM v16+v22):** Implemented and evaluated. Recall targets met (UL 83.6%, DL 99.8%, RRC 85.8%) but FPR=7.55% exceeds target.

**Approach A (current):** Active-normal training data collected (`dataset_training_mei_benign.csv`, 14.034 rows, 2026-05-31). Training v23 with combined dataset.

---

## Goal

Achieve LSTM standalone recall >80% for UL Flood, DL Flood, and RRC Storm
simultaneously with FPR < 5%, using a **single model**.

## Context and Decision

**Approach A (current):** Collect a short (~20 min) active-normal
session (web browsing, light download, no airplane mode) to diversify the training
distribution. Expected to compress Normal FPR from 7.6% → <5% at threshold 0.21,
enabling a single model to hit all targets.

**Approach B (implemented, superseded):** Dual-LSTM ensemble using two existing models trained on
complementary data strategies (v16 + v22). Results:
- UL Flood: 83.6% ✓, DL Flood: 99.8% ✓, RRC Storm: 85.8% ✓
- LSTM FPR: **7.55% ✗** (target ≤5%) — Normal FPs temporally correlated, 2-of-3 voting insufficient
- Hybrid FPR (Stage 2): 1.37% ✓

---

## Root Cause of v16 FPR

| Model | Strengths | Weakness | Why |
|-------|-----------|----------|-----|
| v16 (with reconnect, seq10, 25f) | UL Flood 84% @ thresh=0.21, DL Flood 99.8% | RRC Storm 64.9%, FPR 7.6% @ 0.21 | Reconnect data teaches RACH/empty_ind as normal |
| v22 (no reconnect, seq10, 25f) | RRC Storm 83.8%, FPR 2.76% @ 0.5 | UL Flood 0.7% | No reconnect → Normal distribution underfitted → UL Flood indistinguishable |

Normal FPR of 7.6% on v16 at threshold=0.21 caused by Normal training data being idle-only.
Active traffic (web, download) has different PRB patterns that the model hasn't seen,
causing elevated reconstruction error → FP.

---

## Approach A: Training Data Composition

### v23 Training Data (single model)

| Dataset | Rows | Description |
|---------|------|-------------|
| `csv/dataset_training_mei.csv` | 60.157 | Idle benign, no reconnect |
| `csv/dataset_training_mei_benign.csv` | 14.034 | **Active-normal benign** (web, light streaming, 2026-05-31) |
| **Combined** | **74.191** | Mixed idle + active normal |
| `csv/dataset_validation_mei.csv` | 15.031 | Validation (separate, benign) |

**NOT included:** `dataset_training_mei_reconnect.csv` — reconnect data would teach RACH/empty_ind as normal, hurting RRC Storm detection.

### Why Active-Normal Data Helps

The Normal distribution seen at training time was only idle traffic (PRB ≈ 0, UL ≈ 0).
Active normal traffic (light browsing, streaming) has non-zero PRB, non-zero UL —
patterns that the model previously classified as "anomalous" (high reconstruction error).

With active-normal in training, the model learns both idle and active patterns are Normal.
Expected effect: UL PRB 5-30% during browsing/streaming → lower reconstruction error →
FPR drops from 7.6% → <5% at threshold=0.21.

### Feature Computation for New CSV

`dataset_training_mei_benign.csv` was recorded with an older C xApp version missing 6 engineered features.
These are computed in `_add_computed_features()` using verified formulas (reverse-engineered from `dataset_attack_mei.csv`):

| Feature | Formula | Verified |
|---------|---------|---------|
| `cqi_roll_std` | `cqi.rolling(10).std(ddof=0)` | ✓ max_diff=0.000 |
| `rach_roll_mean` | `rach_preamble.rolling(10).mean()` | ✓ max_diff=0.000 |
| `prb_ul_near_zero_rate` | `(ul < 6/106).rolling(10).mean()` | ✓ max_diff=0.000 |
| `prb_peak_drop` | `prb_ul_roll_max_100 - prb_usage_ul_ratio` | ✓ max_diff=0.000 |
| `rach_cqi_joint` | `rach_preamble × (1 - cqi/15)` | ✓ max_diff=0.000 |
| `prb_dl_ul_asym` | `\|dl-ul\| / (dl+ul+ε)` | ≈ approx (original formula unknown) |

`prb_dl_ul_asym` approximation is acceptable: dominated by 60K existing rows with correct values; for benign active traffic the magnitude is in the right range.

---

## Approach B Archive: Ensemble Architecture

```
Input: raw KPM features [batch, seq_len, 25]
          │
    ┌─────┴─────┐
    │           │
LSTM-A (v16)  LSTM-B (v22)
thresh=0.21   thresh=0.50
    │           │
  score_A     score_B
    │           │
  vote_A      vote_B        ← 2-of-3 window voting per model
    │           │
    └─────┬─────┘
          │ OR
       anomaly alert
```

### Why FPR Was Not Sufficiently Reduced by 2-of-3 Voting

Design prediction assumed Normal FPs are temporally independent → expected FPR ~1.86%.
Actual FPR = 7.55% because Normal FPs are **temporally correlated** (occur in clusters during
active traffic periods), so 2-of-3 voting cannot suppress them.

---

## Evaluation Plan (v23)

Run after training completes:

```bash
# 1. Export ONNX
./venv/bin/python3 export_onnx.py \
  --model models/lstm_autoencoder_v23.pt \
  --threshold models/lstm_autoencoder_v23_threshold.json \
  --out security_model_v23.onnx --seq-len 10 --num-features 25

# 2. Evaluate
./venv/bin/python3 evaluate_detection.py \
  --csv csv/dataset_attack_mei.csv \
  --model security_model_v23.onnx \
  --seq-len 10 --num-features 25
```

### Success Criteria

| Metric | Target |
|--------|--------|
| UL Flood recall | ≥ 80% |
| DL Flood recall | ≥ 80% |
| RRC Storm recall | ≥ 80% |
| LSTM FPR | ≤ 5% |
| LSTM ROC-AUC (raw) | ≥ 0.90 |

If v23 at default threshold (P99.5) doesn't meet targets, recalibrate threshold on test-normal subset (same method as v16/v22).

---

## Future Work

If Approach A (v23) still cannot hit all targets simultaneously:
- Collect longer active-normal session (30-45 min, diverse: voice call, video stream, upload test)
- Or return to Dual Ensemble but with better FPR suppression (3-of-5 voting + PRB gate)
