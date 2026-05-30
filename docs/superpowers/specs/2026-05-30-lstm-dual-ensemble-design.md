# Design: Dual-LSTM Ensemble (Approach B) — v16 + v22 for >80% Recall

## Goal

Achieve LSTM standalone recall >80% for UL Flood, DL Flood, and RRC Storm
simultaneously with FPR < 5%, without collecting new training data.

## Context and Decision

**Approach A (recommended long-term):** Collect a short (~20 min) active-normal
session (web browsing, light download, no airplane mode) to diversify the training
distribution. Expected to compress Normal FPR from 7.6% → <5% at threshold 0.21,
enabling a single model to hit all targets. **Deferred — testbed not available.**

**Approach B (current):** Dual-LSTM ensemble using two existing models trained on
complementary data strategies.

## Root Cause Analysis

| Model | Strengths | Weakness | Why |
|-------|-----------|----------|-----|
| v16 (with reconnect, seq10, 25f) | UL Flood 84% @ thresh=0.21, DL Flood 99.8% | RRC Storm 64.9%, FPR 7.6% @ 0.21 | Reconnect data teaches RACH/empty_ind as normal |
| v22 (no reconnect, seq10, 25f) | RRC Storm 83.8%, FPR 2.76% @ 0.5 | UL Flood 0.7% | No reconnect → Normal distribution underfitted → UL Flood indistinguishable |

Single-model trade-off is irresolvable: including reconnect data helps UL Flood
(by compressing Normal distribution) but hurts RRC Storm (teaches RACH patterns as normal).

## Ensemble Architecture

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

### Models

| | Model | Threshold | Targets |
|---|---|---|---|
| LSTM-A | `security_model_v16.onnx` | 0.21 | UL Flood, DL Flood |
| LSTM-B | `security_model_v22.onnx` | 0.50 | RRC Storm |
| seq_len | both 10 | — | same inference window |
| features | both 25 | — | same feature vector |

### Score Combination

Alert = vote_A OR vote_B, where each vote uses **2-of-3 sliding window majority**:
- Keep a circular buffer of the last 3 window scores per model
- vote = 1 if ≥ 2 of the last 3 scores exceed the model's threshold

### Why 2-of-3 Voting Reduces FPR

Flood/storm patterns are sustained (consecutive windows score high).
Normal FPs are transient spikes (rarely appear in ≥ 2 of 3 consecutive windows).

| | Per-window | 2-of-3 vote |
|---|---|---|
| UL Flood recall (v16 @ 0.21) | 83.9% | ~93% |
| RRC Storm recall (v22 @ 0.5) | 83.8% | ~93% |
| DL Flood recall (v16 @ 0.21) | 99.8% | ~99.9% |
| Normal FPR — v16 | 7.6% | ~1.6% |
| Normal FPR — v22 | 2.76% | ~0.22% |
| **Combined FPR** | **~10%** | **~1.8%** |

Note: FPR estimates assume Normal FPs are temporally independent. If FPs cluster
(e.g., during session transitions), effective FPR will be higher — measure on real data.

## Threshold Justification

**v16 threshold = 0.21** — corresponds to the median UL Flood score. At this threshold:
- UL Flood: 83.9% recall
- RRC Storm: 79.3% (v16 alone — supplemented by v22)
- DL Flood: 99.8%
- Normal FPR: 7.6% (mitigated by 2-of-3 voting → ~1.6%)

Score cliff between 0.21–0.23: UL Flood drops from 84% to 1.3%. Threshold must be ≤ 0.21.

**v22 threshold = 0.50** — P97 recalibrated on test-normal data (3.01% FPR per-window).

## Implementation

### Python (evaluate_detection.py)

Add `DualLSTMDetector` that:
1. Instantiates two `LSTMDetector` instances (v16 @ thresh=0.21, v22 @ thresh=0.5)
2. Maintains a 3-element score buffer per model
3. Returns alert if either model's 2-of-3 vote triggers

### C xApp (xapp_sec_moni.c / future)

Load both ONNX files at startup. On each KPM report:
1. Build feature vector (same 25 features for both)
2. Run inference on both models
3. Apply 2-of-3 circular buffer voting per model
4. OR the two votes → anomaly flag

The two models share the same seq_len=10 sliding window and feature vector, so
no extra preprocessing is needed beyond what is already implemented.

## Evaluation Plan

1. Implement `DualLSTMDetector` in `evaluate_detection.py`
2. Evaluate on `csv/dataset_attack_mei.csv` — verify:
   - UL Flood recall ≥ 80%
   - DL Flood recall ≥ 80%
   - RRC Storm recall ≥ 80%
   - FPR ≤ 5%
3. Record ROC-AUC, Precision, F1 for the combined system
4. Compare against thesis targets: TPR>90%, FPR<3%, ROC-AUC>0.90, F1>0.90

## Future Work (Approach A — when testbed is available)

Collect a ~20-min session of light active-normal traffic (web browsing, YouTube stream,
voice call) without airplane mode or iperf. Add to training alongside idle CSV (no
reconnect). Expected to allow a **single model** to hit all targets:
- Normal distribution compressed by diverse training → FPR < 5% at threshold 0.21
- RRC Storm maintained (no reconnect → no RACH training signal)
- UL Flood maintained (no high-UL training signal)

A single model is cleaner for the thesis and simpler for C deployment.
