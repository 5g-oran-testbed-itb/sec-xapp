# Design: LSTM Autoencoder v21 — Approach 1 (seq_len=30, no reconnect data)

## Goal

Achieve >80% LSTM standalone recall for all four attack classes (UL Flood, DL Flood,
Burst ON/OFF, RRC Storm) without filtering any attack-representative traffic from
training data.

## Root Cause Analysis

| Attack | Root Cause of Weak Detection | Fix |
|--------|------------------------------|-----|
| Burst ON/OFF (~50%) | seq_len=10 captures only ON or OFF phase, not the alternating cycle | Increase seq_len to 30 |
| RRC Storm (~47%) | `dataset_training_mei_reconnect.csv` teaches model that repeated RACH is normal | Remove reconnect CSV from training |
| UL Flood (83.4%) | Already above 80% in v14; maintain without regression | No filter, keep existing features |
| DL Flood (99%+) | Already solved; maintain | No change |

## Training Data Composition

| Dataset | Included | Reason |
|---------|----------|--------|
| `csv/dataset_training_mei.csv` (label=0) | YES — 65,160 rows | Primary normal baseline |
| `csv/dataset_training_mei_reconnect.csv` | **NO** | Teaches RACH-as-normal; undermines RRC Storm detection |
| UL filter (prb_ul_roll_max > 0.5) | NO filter | No data filtering — methodologically clean |
| DL filter (prb_dl_roll_mean > 0.7) | NO filter | No data filtering — methodologically clean |

Validation set: `csv/dataset_validation_mei.csv` (unchanged).

**Methodological justification:** Training on steady-state idle-only data defines "normal"
as the operational baseline. Repeated RACH events (reconnect storms) are excluded from
"normal" by design because the security monitor should flag abnormal signaling activity.

## Sequence Length

- **seq_len = 30** (changed from 10)
- Burst ON/OFF alternates every ~10–20 seconds; seq_len=30 captures at least one full cycle
- 65,160 rows → ~65,131 overlapping sequences (sufficient for training)
- No architecture change — same 2-layer LSTM encoder/decoder

## Feature Set

27 features unchanged from v20 (feature_schema.py). Feature weights unchanged.
CV features (prb_dl_roll_cv, prb_ul_roll_cv) retained but expected to contribute less
given the root causes being addressed are not CV-related.

## Threshold Calibration

Same methodology as previous versions:
- Val-set threshold: P99 of normal val reconstruction errors
- Recalibrated threshold: P97 of test-normal scores (from dataset_attack_mei.csv label=0)
- Deploy recalibrated threshold in ONNX export

## Evaluation Plan

1. **Known attack recall**: UL Flood, DL Flood, Burst ON/OFF, RRC Storm — target >80% each
2. **FPR on val set**: target ≤3% on validation normal data
3. **Benign stress test** (separate session, future work): Google Meet, YouTube+upload,
   OpenSpeedTest — evaluate FPR on unseen high-load normal traffic

## Implementation Steps

1. Modify `train_lstm.py`: change default `--seq-len` from 10 to 30; remove reconnect
   CSV from the training CSV list
2. Train v21 model (100 epochs, same hyperparameters)
3. Export to ONNX (`export_onnx.py --seq-len 30`)
4. Recalibrate threshold on test-normal subset
5. Re-export ONNX with recalibrated threshold
6. Evaluate with `evaluate_detection.py --seq-len 30`
7. Compare against v14 recal (best no-filter baseline)
