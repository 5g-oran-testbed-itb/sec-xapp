# LSTM v24 FPR Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce LSTM anomaly detector false positive rate from 7.55% to <5% by fixing the CV feature formula and reducing its loss weight, then recalibrating threshold to P97.

**Architecture:** Two targeted code changes (CV formula + weight), same training data as v16 (idle + reconnect normal, no active-normal), then retrain and export to ONNX. No structural changes to model or pipeline.

**Tech Stack:** Python 3.12, PyTorch, scikit-learn MinMaxScaler, ONNX Runtime, train_lstm.py + export_onnx.py

---

## File Map

| File | Change |
|------|--------|
| `src/detection/feature_schema.py` | Reduce `prb_dl_roll_cv` and `prb_ul_roll_cv` weights: 6.0 → 3.0 |
| `train_lstm.py` | Fix CV formula lines 81-82: `+ EPS` → `.clip(lower=0.05)` |

No other files need modification. `export_onnx.py` and `evaluate_detection.py` are used as-is.

---

### Task 1: Fix CV Feature Formula (train_lstm.py)

**Root cause of high FPR:** When PRB usage ≈ 0 (idle state), `prb_ul_roll_cv = std / (mean + 1e-6)` produces CV ≈ 1.0 even from tiny fluctuations (e.g., std=0.001, mean=0.001). Combined with weight=6.0 in the loss, idle normal rows produce anomalously high reconstruction error → false positives.

**Fix:** Use `clip(lower=0.05)` so CV is bounded when the network is idle (PRB < 5%). This correctly signals "no meaningful CV" rather than amplifying noise.

**Files:**
- Modify: `train_lstm.py:81-82`

- [ ] **Step 1: Read current lines to confirm context**

```bash
sed -n '79,84p' train_lstm.py
```

Expected output:
```
    # CV features
    _fill('prb_dl_roll_cv',     lambda: df['prb_dl_roll_std'] / (df['prb_dl_roll_mean'] + EPS))
    _fill('prb_ul_roll_cv',     lambda: df['prb_ul_roll_std'] / (df['prb_usage_ul_ratio'].rolling(W10, min_periods=1).mean() + EPS))
```

- [ ] **Step 2: Apply the fix**

Replace lines 80-82 in `train_lstm.py`:

```python
    # CV features — use clip(lower=0.05) to prevent noise amplification when PRB≈0
    _CV_FLOOR = 0.05
    _fill('prb_dl_roll_cv',     lambda: df['prb_dl_roll_std'] / df['prb_dl_roll_mean'].clip(lower=_CV_FLOOR))
    _fill('prb_ul_roll_cv',     lambda: df['prb_ul_roll_std'] / df['prb_usage_ul_ratio'].rolling(W10, min_periods=1).mean().clip(lower=_CV_FLOOR))
```

Note: `_CV_FLOOR` is defined inside the lambda's enclosing scope (the `compute_features` function), so define it just before the two `_fill` lines.

- [ ] **Step 3: Verify no syntax errors**

```bash
python3 -c "import train_lstm; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Spot-check CV values on real data**

```python
# run from repo root
import sys; sys.path.insert(0, '.')
import pandas as pd
from train_lstm import load_csv, compute_features

df = load_csv('csv/dataset_training_mei.csv')
df = df[df['label'] == 0].copy()
df = compute_features(df)
print("prb_ul_roll_cv max:", df['prb_ul_roll_cv'].max())
print("prb_ul_roll_cv P99:", df['prb_ul_roll_cv'].quantile(0.99))
print("prb_dl_roll_cv max:", df['prb_dl_roll_cv'].max())
```

Expected: max values ≤ 20.0 (bounded), not hundreds or thousands as before. P99 should be < 5.0 for idle-dominated normal data.

- [ ] **Step 5: Commit**

```bash
git add train_lstm.py
git commit -m "fix: bound CV feature denominator to 0.05 to prevent noise amplification at idle PRB"
```

---

### Task 2: Reduce CV Feature Weights (feature_schema.py)

**Rationale:** Even with the CV formula fixed, weights of 6.0 for CV features dominate the weighted MSE loss and could still cause idle rows to contribute outsized loss. Reduce to 3.0 — same level as `prb_usage_ul_ratio` and `prb_ul_roll_std`. CV features remain discriminative (UL/DL Flood detection) but no longer dominate.

**Files:**
- Modify: `src/detection/feature_schema.py:68-69`

- [ ] **Step 1: Read current lines**

```bash
sed -n '67,70p' src/detection/feature_schema.py
```

Expected:
```python
    # CV features — membedakan UDP flood (CV≈0) dari TCP transfer (CV>0)
    "prb_dl_roll_cv":         6.0,  # key DL Flood vs download discriminator
    "prb_ul_roll_cv":         6.0,  # key UL Flood vs upload discriminator
```

- [ ] **Step 2: Apply weight reduction**

In `src/detection/feature_schema.py`, change lines 68-69:

```python
    # CV features — membedakan UDP flood (CV≈0) dari TCP transfer (CV>0)
    "prb_dl_roll_cv":         3.0,  # key DL Flood vs download discriminator
    "prb_ul_roll_cv":         3.0,  # key UL Flood vs upload discriminator
```

- [ ] **Step 3: Verify weight sum is correct**

```bash
python3 -c "
from src.detection.feature_schema import FEATURE_WEIGHTS, FEATURE_NAMES
print('CV weights:', FEATURE_WEIGHTS['prb_dl_roll_cv'], FEATURE_WEIGHTS['prb_ul_roll_cv'])
print('Total features:', len(FEATURE_NAMES))
"
```

Expected: `CV weights: 3.0 3.0` and `Total features: 27`

- [ ] **Step 4: Commit**

```bash
git add src/detection/feature_schema.py
git commit -m "fix: reduce CV feature weights 6.0→3.0 to reduce FPR from idle-state noise"
```

---

### Task 3: Train v24

**Training data:** Same as v16 — idle normal + reconnect normal. No active-normal (benign) data. No aggressive PRB filtering.

**Threshold:** P97 (targets ~3% theoretical FPR on validation set, expected real-world FPR < 5%).

**Architecture:** Identical to v16 — encoder [64,32], latent_dim=32, bidirectional=False, seq_len=10, 100 epochs.

**Files:**
- None (run existing train_lstm.py with correct arguments)

- [ ] **Step 1: Run training**

```bash
python3 train_lstm.py \
  --train-csv csv/dataset_training_mei.csv csv/dataset_training_mei_reconnect.csv \
  --val-csv csv/dataset_validation_mei.csv \
  --model-out models/lstm_autoencoder_v24.pt \
  --epochs 100 \
  --batch-size 32 \
  --threshold-percentile 97 \
  2>&1 | tee /tmp/train_v24.log
```

- [ ] **Step 2: Verify training succeeds (no NaN)**

```bash
grep -E "(NaN|nan|Epoch 10|Epoch 50|Epoch 100|Best checkpoint|Threshold)" /tmp/train_v24.log
```

Expected: No NaN lines. Epoch losses should decrease smoothly (train ≈ 0.001-0.003, val ≈ 0.010-0.015 by epoch 100). Best checkpoint epoch shown. Threshold line should show FPR ≈ 3%.

If NaN appears at epoch 10: stop, run `python3 -c "import pandas as pd; df=pd.read_pickle('models/scaler.pkl'); print(df)"` to inspect scaler, then investigate.

- [ ] **Step 3: Check threshold file**

```bash
cat models/lstm_autoencoder_v24_threshold.json
```

Expected: `fpr` field ≈ 3.0 (percent). If fpr > 5.0 on validation set, the CV fix may not be sufficient — reconsider reducing `prb_ul_near_zero_rate` weight as well.

---

### Task 4: Export v24 to ONNX

**Files:**
- None (use existing export_onnx.py)

- [ ] **Step 1: Export**

```bash
python3 export_onnx.py \
  --model-pt models/lstm_autoencoder_v24.pt \
  --model-out security_model_v24.onnx \
  --threshold-json models/lstm_autoencoder_v24_threshold.json \
  2>&1 | tee /tmp/export_v24.log
```

- [ ] **Step 2: Verify export success**

```bash
grep -E "(Exported|Error|error)" /tmp/export_v24.log
ls -lh security_model_v24.onnx
```

Expected: Export success message, file size > 1 MB.

- [ ] **Step 3: Smoke-test ONNX inference**

```bash
python3 -c "
import onnxruntime as rt, numpy as np
sess = rt.InferenceSession('security_model_v24.onnx')
dummy = np.zeros((1, 10, 27), dtype=np.float32)
out = sess.run(None, {'input': dummy})
print('Output shape:', out[0].shape, '  Score:', out[0][0][0])
"
```

Expected: `Output shape: (1, 1)` with a score near 0 (clean input → low anomaly score).

---

### Task 5: Evaluate v24 vs v16+v22 Ensemble

**Goal:** Confirm FPR < 5% on attack dataset normal rows AND recall ≥ 80% for UL Flood, DL Flood, RRC Storm.

**Files:**
- None (use existing evaluate_detection.py)

- [ ] **Step 1: Run v24 evaluation on attack dataset**

```bash
python3 evaluate_detection.py \
  --csv csv/dataset_attack_mei.csv \
  --model security_model_v24.onnx \
  --threshold-json models/lstm_autoencoder_v24_threshold.json \
  2>&1 | tee /tmp/eval_v24_attack.log

cat /tmp/eval_v24_attack.log
```

- [ ] **Step 2: Record per-attack-type metrics**

From the output, fill in this table:

| Attack Type | v16+v22 Recall | v24 Recall | Target |
|-------------|---------------|------------|--------|
| UL Flood    | ~83.6%        | ?          | ≥80%   |
| DL Flood    | ~98%+         | ?          | ≥80%   |
| RRC Storm   | ~97%+         | ?          | ≥80%   |
| Burst ON/OFF| ~64.8%        | ? (sacrifice OK) | N/A |
| Normal FPR  | 7.55%         | ?          | <5%    |

- [ ] **Step 3: Evaluate on benign-only dataset to confirm FPR**

```bash
python3 evaluate_detection.py \
  --csv csv/dataset_training_mei_benign.csv \
  --model security_model_v24.onnx \
  --threshold-json models/lstm_autoencoder_v24_threshold.json \
  2>&1 | tee /tmp/eval_v24_benign.log

cat /tmp/eval_v24_benign.log
```

This confirms FPR on active-normal (browsing/download) traffic — the hardest benign case.

- [ ] **Step 4: Decision gate**

If FPR < 5% AND UL/DL Flood recall ≥ 80% AND RRC Storm recall ≥ 80%:
→ **v24 is the new production LSTM model.** Proceed to Task 6.

If FPR still ≥ 5%:
→ Try `--threshold-percentile 95` (more aggressive):
```bash
python3 train_lstm.py ... --threshold-percentile 95 --model-out models/lstm_autoencoder_v24b.pt
```
Then re-export and re-evaluate.

If UL Flood recall < 70%:
→ Increase `prb_ul_near_zero_rate` weight back to 5.0 in feature_schema.py, retrain.

---

### Task 6: Update References and Commit Final Model

- [ ] **Step 1: Log results to docs**

Append evaluation summary to `docs/MODEL_EVALUATION.md`:

```markdown
## LSTM v24 (2026-06-01)
**Changes from v16:** CV denominator floor=0.05, CV weights 6.0→3.0, threshold P97
**Training data:** dataset_training_mei.csv + dataset_training_mei_reconnect.csv (same as v16)
**Results:**
- UL Flood: [fill from eval]%
- DL Flood: [fill from eval]%
- RRC Storm: [fill from eval]%
- Normal FPR: [fill from eval]%
```

- [ ] **Step 2: Commit model artifacts**

```bash
git add models/lstm_autoencoder_v24_threshold.json models/lstm_autoencoder_v24_losses.json models/scaler.pkl
git add security_model_v24.onnx docs/MODEL_EVALUATION.md
git commit -m "feat: add LSTM v24 — CV formula fix + P97 threshold, target FPR<5%"
```

---

## Expected Outcome

| Metric | v16+v22 Current | v24 Target |
|--------|----------------|------------|
| FPR (attack dataset normal rows) | 7.55% | <5% |
| UL Flood recall | 83.6% | ≥80% |
| DL Flood recall | 98%+ | ≥80% |
| RRC Storm recall | 97%+ | ≥80% |
| Burst ON/OFF recall | 64.8% | N/A (rule-based handles) |

## If v24 Still Fails FPR Target

Next steps in priority order:
1. Try P95 threshold (sacrifice ~2% more recall for FPR headroom)
2. Reduce `prb_ul_roll_std` weight 3.0 → 2.0 (also noisy at idle)
3. Consider separate benign validation set from active-normal traffic only
