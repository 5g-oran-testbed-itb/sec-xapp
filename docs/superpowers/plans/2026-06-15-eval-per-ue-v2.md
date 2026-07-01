# Per-UE IDS Evaluation (`evaluate_per_ue_v2.py`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write `evaluate_per_ue_v2.py` — a single-file evaluation script that runs rule-based R1–R5, LSTM-UE v1, GRU-UE v1, and two hybrid modes against the Juni attack dataset and produces thesis-grade metrics (recall, F1, FPR_val, detection latency, inference latency, ROC-AUC) plus 4 PNG figures.

**Architecture:** Single Python file, 7 logical sections (data pipeline → rule engine → ML scoring → metrics → JSON output → plots → CLI/main). All data is processed per-RNTI independently; windows of seq_len=10 are labeled by the last row's label. FPR comes exclusively from the pure-benign validation dataset.

**Tech Stack:** Python 3.10+, PyTorch (LSTM/GRU autoencoder inference), NumPy, scikit-learn (roc_curve/auc), Matplotlib, pickle (RobustScaler).

---

## Context for Implementer

### Codebase orientation
- Working directory: `/home/telmat/sec-xapp/`
- Per-UE feature schema (15 features): `src/detection/feature_schema_ue.py` — `FEATURE_NAMES`, `NUM_FEATURES=15`
- GRU model class: `src/detection/gru_autoencoder.py` — `GRUAutoencoder`
  - Load: `GRUAutoencoder.load(path, config)` where config has keys `gru_model` and `detection`
  - Inference: `model.compute_reconstruction_error(x: Tensor) -> Tensor` — x shape (batch, seq_len, 15), returns (batch,) MSE
  - `model.seq_len` attribute (= 10)
- LSTM model class: `src/detection/lstm_autoencoder.py` — `LSTMAutoencoder`
  - Load: `LSTMAutoencoder.load(path, config)` where config has keys `lstm_model` and `detection`
  - Same `compute_reconstruction_error` interface
- Both models were trained on **RobustScaler-normalized** data. The scaler must be applied to raw features BEFORE building windows.
- Existing tests directory: `tests/` — add `tests/test_eval_per_ue_v2.py`
- Run tests: `./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v`

### Key datasets
- `csv/dataset_validation_ue_juni.csv` — pure benign, 15 features + label column (all 0)
- `csv/dataset_attack_ue_juni.csv` — 8133 rows, labels 0–4, 8 RNTIs (3/4/5 transient with <20 rows each)

### Model configs (hardcoded — matches training)
```python
GRU_CFG = {
    "gru_model": {"input_features": 15, "encoder_hidden": [64,32],
                  "decoder_hidden": [32,64], "latent_dim": 32, "bidirectional": True},
    "detection": {"sequence_length": 10},
}
LSTM_CFG = {
    "lstm_model": {"input_features": 15, "encoder_hidden": [64,32],
                   "decoder_hidden": [32,64], "latent_dim": 32, "bidirectional": False},
    "detection": {"sequence_length": 10},
}
```

### Rule engine thresholds (from `sec_ids_ue.c`)
```
R1: feat[3]>15000 OR  feat[1]>0.70   consec=5   # UL Flood
R2: feat[2]>15000 OR  feat[0]>0.85   consec=5   # DL Flood
R3: feat[9]>0.12  AND feat[8]>0.05   consec=5   # Burst
R4: feat[10]>=0.90 AND feat[8]>0.50  consec=10  # RoQ/Persistence
R5: feat[1]>0.30  AND feat[7]<5000   consec=3   # Efficiency/LDoS
```
Feature indices follow `FEATURE_NAMES` order (see feature_schema_ue.py):
[0]=prb_usage_dl_ratio [1]=prb_usage_ul_ratio [2]=thp_dl_kbps [3]=thp_ul_kbps
[7]=ul_efficiency [8]=prb_ul_roll_mean [9]=prb_ul_roll_std [10]=ul_persistence

---

## Task 1: Data Pipeline (load, preprocess, RNTI split, windowing)

**Files:**
- Create: `evaluate_per_ue_v2.py` (skeleton + Section 1 functions)
- Create: `tests/test_eval_per_ue_v2.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_per_ue_v2.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
from evaluate_per_ue_v2 import (
    preprocess_rows, split_by_rnti, extract_features,
    get_labels, get_timestamps_ms, build_windows, count_mixed_windows,
)

def _make_rows(n, rnti=1, label=0, prb_dl=0.5, prb_ul=0.5, prb_total=0.5):
    return [
        {"rnti": rnti, "label": label, "timestamp_ms": float(i * 1000),
         "prb_usage_dl_ratio": prb_dl, "prb_usage_ul_ratio": prb_ul,
         "prb_total": prb_total, "thp_dl_kbps": 0.0, "thp_ul_kbps": 0.0,
         "prb_direction": 0.0, "prb_ul_delta": 0.0, "ul_efficiency": 0.0,
         "prb_ul_roll_mean": 0.0, "prb_ul_roll_std": 0.0, "ul_persistence": 0.0,
         "thp_total_kbps": 0.0, "thp_ul_delta": 0.0, "thp_dl_delta": 0.0,
         "traffic_direction": 0.0}
        for i in range(n)
    ]


def test_preprocess_clips_dl_above_one():
    rows = _make_rows(3, prb_dl=1.05, prb_ul=0.94, prb_total=1.11)
    preprocess_rows(rows)
    assert rows[0]["prb_usage_dl_ratio"] == pytest.approx(1.0)
    assert rows[0]["prb_usage_ul_ratio"] == pytest.approx(0.94)   # unchanged
    assert rows[0]["prb_total"] == pytest.approx(1.0)


def test_split_by_rnti_groups_correctly():
    rows = _make_rows(3, rnti=1) + _make_rows(2, rnti=7)
    groups = split_by_rnti(rows)
    assert set(groups.keys()) == {1, 7}
    assert len(groups[1]) == 3
    assert len(groups[7]) == 2


def test_extract_features_shape():
    rows = _make_rows(5)
    X = extract_features(rows)
    assert X.shape == (5, 15)
    assert X.dtype == np.float32


def test_build_windows_shape():
    X = np.zeros((20, 15), dtype=np.float32)
    wins = build_windows(X, seq_len=10)
    assert wins.shape == (11, 10, 15)   # N-seq+1 = 20-10+1 = 11


def test_build_windows_too_short_returns_empty():
    X = np.zeros((5, 15), dtype=np.float32)
    wins = build_windows(X, seq_len=10)
    assert wins.shape[0] == 0


def test_count_mixed_windows():
    # 10 rows: first 5 label=0, next 5 label=1. seq_len=10.
    # Only 1 window (the full array): attack_ratio=0.5 → mixed=1
    labels = np.array([0]*5 + [1]*5, dtype=np.int32)
    assert count_mixed_windows(labels, seq_len=10) == 1
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'evaluate_per_ue_v2'`

- [ ] **Step 3: Create `evaluate_per_ue_v2.py` with Section 1 only**

```python
#!/usr/bin/env python3
"""
evaluate_per_ue_v2.py — Per-UE IDS evaluation (rule + LSTM + GRU + hybrid).

Usage:
  ./venv/bin/python3 evaluate_per_ue_v2.py \\
      --val    csv/dataset_validation_ue_juni.csv \\
      --attack csv/dataset_attack_ue_juni.csv \\
      --output results/ \\
      [--save-figures]
"""
import argparse
import csv as csv_mod
import json
import os
import pickle
import sys
import time
from collections import defaultdict
from datetime import datetime

import numpy as np
import torch
from sklearn.metrics import roc_curve, auc as sklearn_auc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.lstm_autoencoder import LSTMAutoencoder
from src.detection.feature_schema_ue import FEATURE_NAMES, NUM_FEATURES

SEQ_LEN = 10
LABEL_NAMES = {1: "ul_flood", 2: "dl_flood", 3: "burst", 4: "roq"}

GRU_CFG = {
    "gru_model": {
        "input_features": NUM_FEATURES,
        "encoder_hidden": [64, 32],
        "decoder_hidden": [32, 64],
        "latent_dim": 32,
        "bidirectional": True,
    },
    "detection": {"sequence_length": SEQ_LEN},
}
LSTM_CFG = {
    "lstm_model": {
        "input_features": NUM_FEATURES,
        "encoder_hidden": [64, 32],
        "decoder_hidden": [32, 64],
        "latent_dim": 32,
        "bidirectional": False,
    },
    "detection": {"sequence_length": SEQ_LEN},
}

# ── Section 1: Data pipeline ──────────────────────────────────────────────────

def load_csv(path: str) -> list[dict]:
    STR_COLS = {"datetime"}
    INT_COLS = {"rnti", "label"}
    rows = []
    with open(path, newline="") as f:
        for r in csv_mod.DictReader(f):
            row = {}
            for k, v in r.items():
                if k in STR_COLS:
                    row[k] = v
                elif k in INT_COLS:
                    row[k] = int(float(v))
                else:
                    row[k] = float(v)
            rows.append(row)
    return rows


def preprocess_rows(rows: list[dict]) -> list[dict]:
    """Clip PRB features to [0, 1] in-place."""
    for r in rows:
        for col in ("prb_usage_ul_ratio", "prb_usage_dl_ratio", "prb_total"):
            if col in r:
                r[col] = min(1.0, max(0.0, r[col]))
    return rows


def split_by_rnti(rows: list[dict]) -> dict[int, list[dict]]:
    """Group rows by RNTI, preserving chronological order within each group."""
    d: dict[int, list] = defaultdict(list)
    for r in rows:
        d[int(r["rnti"])].append(r)
    return dict(d)


def extract_features(rows: list[dict]) -> np.ndarray:
    """Returns float32 array of shape (N, 15)."""
    X = np.zeros((len(rows), NUM_FEATURES), dtype=np.float32)
    for i, r in enumerate(rows):
        for j, name in enumerate(FEATURE_NAMES):
            X[i, j] = float(r.get(name, 0.0))
    return X


def get_labels(rows: list[dict]) -> np.ndarray:
    return np.array([int(r["label"]) for r in rows], dtype=np.int32)


def get_timestamps_ms(rows: list[dict]) -> np.ndarray:
    return np.array([float(r["timestamp_ms"]) for r in rows], dtype=np.float64)


def build_windows(X: np.ndarray, seq_len: int = SEQ_LEN) -> np.ndarray:
    """Sliding windows from X (already scaled). Returns (N-seq+1, seq, feat)."""
    N = X.shape[0]
    if N < seq_len:
        return np.empty((0, seq_len, X.shape[1]), dtype=np.float32)
    return np.stack([X[i:i + seq_len] for i in range(N - seq_len + 1)], axis=0)


def count_mixed_windows(labels: np.ndarray, seq_len: int = SEQ_LEN) -> int:
    """Count windows where 0 < fraction_attack < 1."""
    N = len(labels)
    mixed = 0
    for i in range(N - seq_len + 1):
        attack_ratio = float(np.mean(labels[i:i + seq_len] != 0))
        if 0 < attack_ratio < 1:
            mixed += 1
    return mixed
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add evaluate_per_ue_v2.py tests/test_eval_per_ue_v2.py
git commit -m "feat: add evaluate_per_ue_v2 skeleton and data pipeline (Task 1)"
```

---

## Task 2: Rule Engine (R1–R5, Stateful)

**Files:**
- Modify: `evaluate_per_ue_v2.py` (add Section 2)
- Modify: `tests/test_eval_per_ue_v2.py` (add rule engine tests)

- [ ] **Step 1: Add failing tests**

Append to `tests/test_eval_per_ue_v2.py`:

```python
from evaluate_per_ue_v2 import run_rule_engine

def _feat_row(**kwargs) -> np.ndarray:
    """Build a (15,) feature vector with all zeros except specified indices."""
    f = np.zeros(15, dtype=np.float32)
    for k, v in kwargs.items():
        f[int(k)] = v
    return f


def _feat_matrix(rows_list):
    return np.stack(rows_list, axis=0)


def test_r1_fires_after_5_consecutive_ul_flood():
    # feat[3]=20000 (thp_ul > 15000) for 5 rows → fires at t=4
    row = _feat_row(**{"3": 20000.0})
    X = _feat_matrix([row] * 10)
    fires = run_rule_engine(X)
    assert fires[3] == False   # t=3: only 4 consecutive, not yet
    assert fires[4] == True    # t=4: 5 consecutive → fires


def test_r1_resets_counter_on_break():
    # 4 trigger rows, then 1 non-trigger, then 5 trigger rows
    trigger = _feat_row(**{"3": 20000.0})
    no_trig = _feat_row()
    X = _feat_matrix([trigger]*4 + [no_trig] + [trigger]*5)
    fires = run_rule_engine(X)
    assert fires[3] == False    # 4 consec, not enough
    assert fires[4] == False    # reset
    assert fires[9] == True     # 5 consec after reset (indices 5-9)


def test_r3_requires_and_both_conditions():
    # R3: feat[9]>0.12 AND feat[8]>0.05, consec=5
    only_std  = _feat_row(**{"9": 0.20})           # mean condition NOT met
    both_ok   = _feat_row(**{"9": 0.20, "8": 0.10})  # both met

    # 5 rows where only std is high → should NOT fire
    X = _feat_matrix([only_std] * 5)
    fires = run_rule_engine(X)
    assert fires[4] == False

    # 5 rows where both met → should fire at t=4
    X2 = _feat_matrix([both_ok] * 5)
    fires2 = run_rule_engine(X2)
    assert fires2[4] == True


def test_r4_requires_10_consecutive():
    # R4: feat[10]>=0.90 AND feat[8]>0.50, consec=10
    row = _feat_row(**{"10": 0.95, "8": 0.60})
    X = _feat_matrix([row] * 15)
    fires = run_rule_engine(X)
    assert fires[8] == False    # only 9 consecutive
    assert fires[9] == True     # 10 consecutive → fires


def test_r5_requires_low_efficiency():
    # R5: feat[1]>0.30 AND feat[7]<5000, consec=3
    row = _feat_row(**{"1": 0.50, "7": 1000.0})
    X = _feat_matrix([row] * 5)
    fires = run_rule_engine(X)
    assert fires[1] == False
    assert fires[2] == True


def test_no_fire_when_below_all_thresholds():
    row = _feat_row(**{"3": 5000.0, "1": 0.10})  # below R1 thresholds
    X = _feat_matrix([row] * 20)
    fires = run_rule_engine(X)
    assert not fires.any()
```

- [ ] **Step 2: Run — verify 6 new tests fail**

```bash
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v -k "rule"
```

Expected: `ImportError` or `NameError` for `run_rule_engine`.

- [ ] **Step 3: Add Section 2 to `evaluate_per_ue_v2.py`**

After the Section 1 code, add:

```python
# ── Section 2: Rule engine ────────────────────────────────────────────────────

_RULE_DEFS = [
    # (condition_fn, consec_needed)
    (lambda f: (f[3] > 15000.0) or  (f[1] > 0.70),  5),   # R1 UL Flood
    (lambda f: (f[2] > 15000.0) or  (f[0] > 0.85),  5),   # R2 DL Flood
    (lambda f: (f[9] > 0.12)    and (f[8] > 0.05),  5),   # R3 Burst
    (lambda f: (f[10] >= 0.90)  and (f[8] > 0.50),  10),  # R4 RoQ
    (lambda f: (f[1] > 0.30)    and (f[7] < 5000.0), 3),  # R5 Efficiency
]


def run_rule_engine(X: np.ndarray) -> np.ndarray:
    """
    Stateful R1–R5 evaluation. Counters run continuously (not reset per window).
    Input:  X (N, 15) float32 — raw (unscaled) per-UE features
    Output: rule_fires (N,) bool — True if any rule fires at timestep t
    """
    N = X.shape[0]
    counters = [0] * len(_RULE_DEFS)
    rule_fires = np.zeros(N, dtype=bool)

    for t in range(N):
        f = X[t]
        mask = 0
        for i, (cond, needed) in enumerate(_RULE_DEFS):
            if cond(f):
                counters[i] += 1
            else:
                counters[i] = 0
            if counters[i] >= needed:
                mask |= (1 << i)
        rule_fires[t] = (mask > 0)

    return rule_fires
```

- [ ] **Step 4: Run tests — all pass**

```bash
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v
```

Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add evaluate_per_ue_v2.py tests/test_eval_per_ue_v2.py
git commit -m "feat: add stateful R1-R5 rule engine (Task 2)"
```

---

## Task 3: ML Scoring + Inference Latency

**Files:**
- Modify: `evaluate_per_ue_v2.py` (add Section 3)
- Modify: `tests/test_eval_per_ue_v2.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_eval_per_ue_v2.py`:

```python
import pickle
from evaluate_per_ue_v2 import load_models, score_ml, build_windows

def test_load_models_returns_dict():
    models = load_models()
    assert "lstm" in models and "gru" in models
    lstm_model, lstm_scaler, lstm_thresh = models["lstm"]
    gru_model,  gru_scaler,  gru_thresh  = models["gru"]
    assert lstm_thresh > 0
    assert gru_thresh  > 0
    assert lstm_model.seq_len == 10
    assert gru_model.seq_len  == 10


def test_score_ml_output_shape():
    models = load_models()
    gru, scaler, thresh = models["gru"]
    # 20 rows of zeros → 11 windows
    X = np.zeros((20, 15), dtype=np.float32)
    mse, latencies = score_ml(gru, scaler, X)
    assert mse.shape == (11,)            # N - seq_len + 1 = 20 - 10 + 1
    assert len(latencies) == 11          # one latency measurement per window
    assert all(lat >= 0 for lat in latencies)


def test_score_ml_too_short_returns_empty():
    models = load_models()
    gru, scaler, _ = models["gru"]
    X = np.zeros((5, 15), dtype=np.float32)
    mse, latencies = score_ml(gru, scaler, X)
    assert len(mse) == 0
    assert len(latencies) == 0


def test_score_ml_mse_nonnegative():
    models = load_models()
    lstm, scaler, _ = models["lstm"]
    X = np.random.rand(15, 15).astype(np.float32)
    mse, _ = score_ml(lstm, scaler, X)
    assert (mse >= 0).all()
```

- [ ] **Step 2: Run — verify they fail**

```bash
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v -k "model or score_ml"
```

Expected: `ImportError` for `load_models`, `score_ml`.

- [ ] **Step 3: Add Section 3 to `evaluate_per_ue_v2.py`**

```python
# ── Section 3: ML scoring + inference latency ─────────────────────────────────

def load_models(
    lstm_pt:    str = "models/lstm_ue_v1.pt",
    lstm_pkl:   str = "models/lstm_ue_v1_scaler.pkl",
    lstm_json:  str = "models/lstm_ue_v1_threshold.json",
    gru_pt:     str = "models/gru_ue_v1.pt",
    gru_pkl:    str = "models/gru_ue_v1_scaler.pkl",
    gru_json:   str = "models/gru_ue_v1_threshold.json",
) -> dict:
    """Returns dict with 'lstm' and 'gru' keys, each a (model, scaler, threshold) tuple."""
    print("[*] Loading GRU-UE v1...")
    gru = GRUAutoencoder.load(gru_pt, GRU_CFG)
    gru.eval()
    with open(gru_pkl, "rb") as f:
        gru_scaler = pickle.load(f)
    gru_thresh = json.load(open(gru_json))["threshold"]
    print(f"    threshold={gru_thresh:.0f}")

    print("[*] Loading LSTM-UE v1...")
    lstm = LSTMAutoencoder.load(lstm_pt, LSTM_CFG)
    lstm.eval()
    with open(lstm_pkl, "rb") as f:
        lstm_scaler = pickle.load(f)
    lstm_thresh = json.load(open(lstm_json))["threshold"]
    print(f"    threshold={lstm_thresh:.0f}")

    return {
        "lstm": (lstm, lstm_scaler, lstm_thresh),
        "gru":  (gru,  gru_scaler,  gru_thresh),
    }


def score_ml(
    model, scaler, X_raw: np.ndarray, batch: int = 256
) -> tuple[np.ndarray, list[float]]:
    """
    Score all windows from X_raw.
    X_raw: (N, 15) unscaled features.
    Returns:
      mse (N-9,) float32 — MSE[i] aligns to timestep i+9 (last row of window)
      latency_ms (list of float) — per-window inference time in milliseconds
    """
    X_scaled = scaler.transform(X_raw).astype(np.float32)
    wins = build_windows(X_scaled, SEQ_LEN)
    if len(wins) == 0:
        return np.array([], dtype=np.float32), []

    mse_parts: list[np.ndarray] = []
    latencies: list[float] = []
    model.eval()

    for i in range(0, len(wins), batch):
        chunk = torch.tensor(wins[i:i + batch])
        t0 = time.perf_counter()
        err = model.compute_reconstruction_error(chunk)
        t1 = time.perf_counter()
        mse_parts.append(err.detach().numpy())
        n = len(chunk)
        per_win_ms = (t1 - t0) * 1000.0 / n
        latencies.extend([per_win_ms] * n)

    return np.concatenate(mse_parts).astype(np.float32), latencies
```

- [ ] **Step 4: Run tests — all pass**

```bash
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v
```

Expected: all 16 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add evaluate_per_ue_v2.py tests/test_eval_per_ue_v2.py
git commit -m "feat: add ML scoring + inference latency measurement (Task 3)"
```

---

## Task 4: Metrics Helpers

**Files:**
- Modify: `evaluate_per_ue_v2.py` (add Section 4)
- Modify: `tests/test_eval_per_ue_v2.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_eval_per_ue_v2.py`:

```python
from evaluate_per_ue_v2 import (
    compute_cm, compute_fpr_val, compute_per_class_recall,
    find_attack_segments, compute_detection_latency,
    compute_inference_latency, compute_roc_auc,
)

def test_compute_cm_basic():
    preds = np.array([True, True, False, False], dtype=bool)
    labels = np.array([1, 0, 1, 0], dtype=np.int32)
    cm = compute_cm(preds, labels)
    assert cm["tp"] == 1
    assert cm["fp"] == 1
    assert cm["tn"] == 1
    assert cm["fn"] == 1
    assert cm["recall"]    == pytest.approx(0.5)
    assert cm["precision"] == pytest.approx(0.5)
    assert cm["f1"]        == pytest.approx(0.5)


def test_compute_fpr_val():
    # 10 val windows, 2 false alerts → FPR = 0.2
    val_fires = np.array([True, False, True, False, False,
                          False, False, False, False, False])
    fpr = compute_fpr_val(val_fires, n_val_windows=10)
    assert fpr == pytest.approx(0.2)


def test_compute_per_class_recall():
    # 4 UL Flood windows (label=1), 2 detected
    preds  = np.array([True, True, False, False], dtype=bool)
    labels = np.array([1, 1, 1, 1], dtype=np.int32)
    pcr = compute_per_class_recall(preds, labels)
    assert pcr["ul_flood"] == pytest.approx(0.5)
    assert pcr["dl_flood"] is None   # no dl_flood windows


def test_find_attack_segments_splits_on_rnti_change():
    # UL Flood on RNTI 1 then UL Flood on RNTI 7 → two segments
    labels  = np.array([1, 1, 1, 1, 1], dtype=np.int32)
    ts_ms   = np.array([0, 1000, 2000, 3000, 4000], dtype=np.float64)
    rntis   = np.array([1, 1, 7, 7, 7], dtype=np.int32)
    segs = find_attack_segments(labels, ts_ms, rntis)
    assert len(segs) == 2
    assert segs[0]["rnti"] == 1 and segs[0]["end_idx"] == 1
    assert segs[1]["rnti"] == 7 and segs[1]["start_idx"] == 2


def test_compute_detection_latency_basic():
    # Segment: label=1, RNTI=1, timestamps 0-5s (6 rows).
    # Fires at index 3 → latency = (3000 - 0) / 1000 = 3.0 s
    labels  = np.array([1]*6, dtype=np.int32)
    ts_ms   = np.array([0, 1000, 2000, 3000, 4000, 5000], dtype=np.float64)
    rntis   = np.array([1]*6, dtype=np.int32)
    fires   = np.array([False, False, False, True, True, True], dtype=bool)
    result  = compute_detection_latency(fires, labels, ts_ms, rntis)
    assert result["ul_flood"]["mean_s"]   == pytest.approx(3.0)
    assert result["ul_flood"]["median_s"] == pytest.approx(3.0)


def test_compute_detection_latency_no_alert_excluded():
    # Segment fires nowhere → n_segments=0
    labels = np.array([1]*5, dtype=np.int32)
    ts_ms  = np.arange(5, dtype=np.float64) * 1000
    rntis  = np.array([1]*5, dtype=np.int32)
    fires  = np.zeros(5, dtype=bool)
    result = compute_detection_latency(fires, labels, ts_ms, rntis)
    assert result["ul_flood"]["n_segments"] == 0
    assert result["ul_flood"]["mean_s"] is None


def test_compute_inference_latency():
    lats = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    result = compute_inference_latency(lats)
    assert result["mean_ms"]   == pytest.approx(5.5)
    assert result["median_ms"] == pytest.approx(5.5)
    assert result["p95_ms"]    == pytest.approx(9.55, rel=1e-2)


def test_compute_roc_auc_perfect():
    # Perfect separation: val MSE all low, attack MSE all high
    mse_val    = np.array([1.0, 2.0, 1.5], dtype=np.float32)
    mse_attack = np.array([100.0, 200.0, 150.0], dtype=np.float32)
    fpr, tpr, auc_val = compute_roc_auc(mse_val, mse_attack)
    assert auc_val == pytest.approx(1.0)
```

- [ ] **Step 2: Run — verify new tests fail**

```bash
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v -k "cm or fpr or recall or segment or latency or roc"
```

Expected: `ImportError` for new functions.

- [ ] **Step 3: Add Section 4 to `evaluate_per_ue_v2.py`**

```python
# ── Section 4: Metrics ────────────────────────────────────────────────────────

def compute_cm(preds_binary: np.ndarray, labels_attack: np.ndarray) -> dict:
    """
    preds_binary: (N,) bool — True = anomaly predicted
    labels_attack: (N,) int — 0=benign, >0=attack
    Returns: {tp, fp, tn, fn, recall, precision, f1}
    """
    is_attack = labels_attack > 0
    TP = int(( preds_binary &  is_attack).sum())
    FP = int(( preds_binary & ~is_attack).sum())
    TN = int((~preds_binary & ~is_attack).sum())
    FN = int((~preds_binary &  is_attack).sum())
    precision = TP / (TP + FP) if (TP + FP) > 0 else 0.0
    recall    = TP / (TP + FN) if (TP + FN) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)
    return {"tp": TP, "fp": FP, "tn": TN, "fn": FN,
            "recall": round(recall, 4), "precision": round(precision, 4),
            "f1": round(f1, 4)}


def compute_fpr_val(val_fires: np.ndarray, n_val_windows: int) -> float:
    """False positive rate on pure-benign validation windows."""
    if n_val_windows == 0:
        return 0.0
    return round(float(val_fires.sum()) / n_val_windows, 4)


def compute_per_class_recall(
    preds_binary: np.ndarray, labels_attack: np.ndarray
) -> dict[str, float | None]:
    """TPR for each attack class. None if no windows of that class."""
    result: dict[str, float | None] = {}
    for lbl, name in LABEL_NAMES.items():
        mask = labels_attack == lbl
        if mask.sum() == 0:
            result[name] = None
        else:
            result[name] = round(float(preds_binary[mask].sum()) / mask.sum(), 4)
    return result


def find_attack_segments(
    labels: np.ndarray, timestamps_ms: np.ndarray, rntis: np.ndarray
) -> list[dict]:
    """
    Returns list of attack segments where (label, rnti) is constant and label > 0.
    Each segment: {label, rnti, start_ts, start_idx, end_idx (inclusive)}.
    """
    segments = []
    N = len(labels)
    i = 0
    while i < N:
        if labels[i] == 0:
            i += 1
            continue
        j = i + 1
        while j < N and labels[j] == labels[i] and rntis[j] == rntis[i]:
            j += 1
        segments.append({
            "label":     int(labels[i]),
            "rnti":      int(rntis[i]),
            "start_ts":  float(timestamps_ms[i]),
            "start_idx": i,
            "end_idx":   j - 1,
        })
        i = j
    return segments


def compute_detection_latency(
    preds_binary: np.ndarray,
    labels: np.ndarray,
    timestamps_ms: np.ndarray,
    rntis: np.ndarray,
) -> dict[str, dict]:
    """
    Per-class detection latency. Segments with no alert are excluded (FN).
    Returns {class_name: {mean_s, median_s, n_segments}}.
    """
    segments = find_attack_segments(labels, timestamps_ms, rntis)
    lats: dict[str, list[float]] = defaultdict(list)

    for seg in segments:
        name = LABEL_NAMES.get(seg["label"])
        if name is None:
            continue
        s, e = seg["start_idx"], seg["end_idx"]
        alert_idxs = np.where(preds_binary[s:e + 1])[0]
        if len(alert_idxs) == 0:
            continue
        first_alert_ts = timestamps_ms[s + alert_idxs[0]]
        lats[name].append((first_alert_ts - seg["start_ts"]) / 1000.0)

    result: dict[str, dict] = {}
    for name in LABEL_NAMES.values():
        vals = lats[name]
        if vals:
            result[name] = {
                "mean_s":     round(float(np.mean(vals)), 3),
                "median_s":   round(float(np.median(vals)), 3),
                "n_segments": len(vals),
            }
        else:
            result[name] = {"mean_s": None, "median_s": None, "n_segments": 0}
    return result


def compute_inference_latency(latency_ms: list[float]) -> dict:
    """Summarise per-window inference times (ms). Returns mean/median/p95."""
    if not latency_ms:
        return {"mean_ms": None, "median_ms": None, "p95_ms": None}
    a = np.array(latency_ms)
    return {
        "mean_ms":   round(float(np.mean(a)), 4),
        "median_ms": round(float(np.median(a)), 4),
        "p95_ms":    round(float(np.percentile(a, 95)), 4),
    }


def compute_roc_auc(
    mse_val: np.ndarray, mse_attack_pos: np.ndarray
) -> tuple[np.ndarray, np.ndarray, float]:
    """
    mse_val: MSE of validation (pure-benign) windows → y_true=0
    mse_attack_pos: MSE of attack-dataset windows with label>0 → y_true=1
    Returns (fpr_arr, tpr_arr, auc_val).
    """
    y_true  = np.concatenate([np.zeros(len(mse_val)), np.ones(len(mse_attack_pos))])
    y_score = np.concatenate([mse_val, mse_attack_pos])
    fpr, tpr, _ = roc_curve(y_true, y_score)
    auc_val = float(sklearn_auc(fpr, tpr))
    return fpr, tpr, round(auc_val, 4)
```

- [ ] **Step 4: Run tests — all pass**

```bash
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v
```

Expected: all 24 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add evaluate_per_ue_v2.py tests/test_eval_per_ue_v2.py
git commit -m "feat: add metrics helpers (CM, FPR, per-class recall, latency, ROC-AUC) (Task 4)"
```

---

## Task 5: JSON Output + ASCII Summary Table

**Files:**
- Modify: `evaluate_per_ue_v2.py` (add Section 5)
- Modify: `tests/test_eval_per_ue_v2.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_eval_per_ue_v2.py`:

```python
import tempfile
from evaluate_per_ue_v2 import build_result_entry, save_results_json

def _dummy_cm():
    return {"tp": 10, "fp": 2, "tn": 50, "fn": 3,
            "recall": 0.77, "precision": 0.83, "f1": 0.80}

def _dummy_det_lat():
    return {name: {"mean_s": 5.0, "median_s": 4.5, "n_segments": 2}
            for name in ["ul_flood", "dl_flood", "burst", "roq"]}

def _dummy_pcr():
    return {"ul_flood": 0.9, "dl_flood": 0.85, "burst": 0.7, "roq": 0.65}


def test_build_result_entry_rule_only_keys():
    entry = build_result_entry(
        cm=_dummy_cm(), fpr_val=0.02,
        det_latency=_dummy_det_lat(), per_class_recall=_dummy_pcr(),
    )
    assert "recall" in entry
    assert "f1" in entry
    assert "fpr_val" in entry
    assert "confusion_matrix" in entry
    assert "detection_latency" in entry
    assert "per_class_recall" in entry
    assert "inference_latency" not in entry   # rule-only has no inference lat
    assert "auc" not in entry


def test_build_result_entry_lstm_has_auc_and_inf_lat():
    inf_lat = {"mean_ms": 1.2, "median_ms": 1.1, "p95_ms": 2.0}
    entry = build_result_entry(
        cm=_dummy_cm(), fpr_val=0.02,
        det_latency=_dummy_det_lat(), per_class_recall=_dummy_pcr(),
        inf_latency=inf_lat, auc_val=0.97,
    )
    assert "inference_latency" in entry
    assert "auc" in entry


def test_save_results_json_creates_file():
    metadata = {"val_csv": "v.csv", "attack_csv": "a.csv", "seq_len": 10,
                "thresholds": {"lstm": 1.0, "gru": 1.0, "source": "test"},
                "window_counts": {}}
    results = {"rule_only": build_result_entry(
        _dummy_cm(), 0.02, _dummy_det_lat(), _dummy_pcr())}
    with tempfile.TemporaryDirectory() as tmp:
        path = save_results_json(metadata, results, tmp)
        assert os.path.exists(path)
        data = json.load(open(path))
        assert "metadata" in data and "results" in data
        assert "rule_only" in data["results"]
```

- [ ] **Step 2: Run — verify they fail**

```bash
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v -k "entry or json"
```

Expected: `ImportError`.

- [ ] **Step 3: Add Section 5 to `evaluate_per_ue_v2.py`**

```python
# ── Section 5: JSON output + stdout summary ───────────────────────────────────

def build_result_entry(
    cm: dict,
    fpr_val: float,
    det_latency: dict,
    per_class_recall: dict,
    inf_latency: dict | None = None,
    auc_val: float | None = None,
) -> dict:
    """Build the JSON sub-dict for one detection configuration."""
    entry: dict = {
        "recall":    cm["recall"],
        "precision": cm["precision"],
        "f1":        cm["f1"],
        "fpr_val":   fpr_val,
        "confusion_matrix": {
            "tn": cm["tn"], "fp": cm["fp"],
            "fn": cm["fn"], "tp": cm["tp"],
        },
        "detection_latency": det_latency,
        "per_class_recall":  per_class_recall,
    }
    if inf_latency is not None:
        entry["inference_latency"] = inf_latency
    if auc_val is not None:
        entry["auc"] = auc_val
    return entry


def save_results_json(metadata: dict, results: dict, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"eval_per_ue_v2_{ts}.json")
    with open(path, "w") as f:
        json.dump({"metadata": metadata, "results": results}, f, indent=2)
    print(f"\n[JSON] Saved → {path}")
    return path


def print_summary_table(results: dict) -> None:
    configs = ["rule_only", "lstm_only", "gru_only", "lstm_hybrid", "gru_hybrid"]
    header = (f"{'Config':<16} {'Recall':>7} {'F1':>7} "
              f"{'FPR_val':>8} {'Det.Lat(s)':>12} {'Inf.Lat(ms)':>12}")
    print("\n" + header)
    print("─" * len(header))
    for cfg in configs:
        r = results.get(cfg)
        if r is None:
            continue
        recall  = f"{r['recall']*100:.1f}%"
        f1      = f"{r['f1']*100:.1f}%"
        fpr     = f"{r['fpr_val']*100:.2f}%"
        det_vals = [
            v["mean_s"] for v in r.get("detection_latency", {}).values()
            if isinstance(v, dict) and v.get("mean_s") is not None
        ]
        det_str = f"{float(np.mean(det_vals)):.1f}" if det_vals else "—"
        inf_ms  = r.get("inference_latency", {}).get("mean_ms")
        inf_str = f"{inf_ms:.2f}" if inf_ms is not None else "—"
        print(f"{cfg:<16} {recall:>7} {f1:>7} {fpr:>8} {det_str:>12} {inf_str:>12}")
```

- [ ] **Step 4: Run tests — all pass**

```bash
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v
```

Expected: all 27 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add evaluate_per_ue_v2.py tests/test_eval_per_ue_v2.py
git commit -m "feat: add JSON output and ASCII summary table (Task 5)"
```

---

## Task 6: Plots + CLI + `main()` Integration

**Files:**
- Modify: `evaluate_per_ue_v2.py` (add Sections 6 + 7 + main)
- Modify: `tests/test_eval_per_ue_v2.py` (smoke test)

- [ ] **Step 1: Add smoke test**

Append to `tests/test_eval_per_ue_v2.py`:

```python
import subprocess

@pytest.mark.integration
def test_end_to_end_smoke(tmp_path):
    """Full pipeline run — requires actual dataset files."""
    val_path    = "csv/dataset_validation_ue_juni.csv"
    attack_path = "csv/dataset_attack_ue_juni.csv"
    if not (os.path.exists(val_path) and os.path.exists(attack_path)):
        pytest.skip("Dataset files not present")

    result = subprocess.run(
        ["./venv/bin/python3", "evaluate_per_ue_v2.py",
         "--val", val_path, "--attack", attack_path,
         "--output", str(tmp_path), "--save-figures"],
        capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stderr[-2000:]

    # JSON exists
    jsons = list(tmp_path.glob("eval_per_ue_v2_*.json"))
    assert len(jsons) == 1
    data = json.load(open(jsons[0]))
    assert set(data["results"].keys()) == {
        "rule_only", "lstm_only", "gru_only", "lstm_hybrid", "gru_hybrid"
    }
    for cfg in data["results"].values():
        assert "recall" in cfg
        assert "f1" in cfg
        assert "confusion_matrix" in cfg

    # PNGs exist
    for fname in ["eval_confusion.png", "eval_per_class.png",
                  "eval_latency.png", "eval_roc.png"]:
        assert (tmp_path / fname).exists(), f"Missing {fname}"
```

- [ ] **Step 2: Run smoke test — verify it fails (ImportError)**

```bash
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v -m integration
```

Expected: script runs but hits `SystemExit` or error (no `main()` yet).

- [ ] **Step 3: Add Section 6 (plots) to `evaluate_per_ue_v2.py`**

```python
# ── Section 6: Plots ──────────────────────────────────────────────────────────

def _ensure_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def plot_confusion_matrices(results: dict, output_dir: str) -> None:
    plt = _ensure_matplotlib()
    configs = ["rule_only", "lstm_only", "gru_only", "lstm_hybrid", "gru_hybrid"]
    fig, axes = plt.subplots(2, 3, figsize=(14, 9))
    axes = axes.flatten()

    for idx, cfg in enumerate(configs):
        ax = axes[idx]
        r  = results[cfg]
        cm_d = r["confusion_matrix"]
        cm   = np.array([[cm_d["tn"], cm_d["fp"]],
                         [cm_d["fn"], cm_d["tp"]]])
        im = ax.imshow(cm, interpolation="nearest", cmap="Blues")
        ax.set_title(cfg, fontsize=11, fontweight="bold")
        ax.set_xlabel("Predicted"); ax.set_ylabel("Actual")
        ax.set_xticks([0, 1]); ax.set_yticks([0, 1])
        ax.set_xticklabels(["Normal", "Anomaly"])
        ax.set_yticklabels(["Normal", "Anomaly"])
        for i in range(2):
            for j in range(2):
                ax.text(j, i, str(cm[i, j]),
                        ha="center", va="center", fontsize=13,
                        color="white" if cm[i, j] > cm.max() / 2 else "black")
        plt.colorbar(im, ax=ax)

    # subplot 6: summary text
    ax6 = axes[5]
    ax6.axis("off")
    lines = ["Summary", ""]
    for cfg in configs:
        r = results[cfg]
        lines.append(f"{cfg}: R={r['recall']*100:.1f}% F1={r['f1']*100:.1f}%")
    ax6.text(0.05, 0.95, "\n".join(lines), transform=ax6.transAxes,
             verticalalignment="top", fontfamily="monospace", fontsize=9)

    plt.tight_layout()
    path = os.path.join(output_dir, "eval_confusion.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[FIG] {path}")


def plot_per_class(results: dict, output_dir: str) -> None:
    plt = _ensure_matplotlib()
    configs = ["rule_only", "lstm_only", "gru_only", "lstm_hybrid", "gru_hybrid"]
    classes = ["ul_flood", "dl_flood", "burst", "roq"]
    labels  = ["UL Flood", "DL Flood", "Burst", "RoQ"]

    x = np.arange(len(configs))
    width = 0.18
    fig, ax = plt.subplots(figsize=(12, 5))

    for i, (cls, lbl) in enumerate(zip(classes, labels)):
        vals = []
        for cfg in configs:
            v = results[cfg].get("per_class_recall", {}).get(cls)
            vals.append((v or 0.0) * 100)
        ax.bar(x + i * width, vals, width, label=lbl)

    ax.set_xticks(x + width * 1.5)
    ax.set_xticklabels(configs, rotation=15, ha="right")
    ax.set_ylabel("Recall (%)")
    ax.set_title("Per-Class Recall by Configuration")
    ax.legend()
    ax.set_ylim(0, 110)
    ax.axhline(100, color="gray", linestyle="--", linewidth=0.8)

    plt.tight_layout()
    path = os.path.join(output_dir, "eval_per_class.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[FIG] {path}")


def plot_latency(results: dict, output_dir: str) -> None:
    plt = _ensure_matplotlib()
    configs = ["rule_only", "lstm_only", "gru_only", "lstm_hybrid", "gru_hybrid"]
    classes = ["ul_flood", "dl_flood", "burst", "roq"]
    class_labels = ["UL Flood", "DL Flood", "Burst", "RoQ"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    # Left: detection latency per class per config
    x = np.arange(len(configs))
    width = 0.18
    for i, (cls, clbl) in enumerate(zip(classes, class_labels)):
        vals = []
        for cfg in configs:
            lat = results[cfg].get("detection_latency", {}).get(cls, {})
            vals.append(lat.get("mean_s") or 0.0)
        ax1.bar(x + i * width, vals, width, label=clbl)
    ax1.set_xticks(x + width * 1.5)
    ax1.set_xticklabels(configs, rotation=15, ha="right")
    ax1.set_ylabel("Mean Detection Latency (s)")
    ax1.set_title("Detection Latency by Configuration")
    ax1.legend()

    # Right: inference latency for LSTM and GRU
    ml_cfgs = ["lstm_only", "gru_only", "lstm_hybrid", "gru_hybrid"]
    means = []; p95s = []; xlbls = []
    for cfg in ml_cfgs:
        il = results.get(cfg, {}).get("inference_latency")
        if il and il.get("mean_ms") is not None:
            means.append(il["mean_ms"])
            p95s.append(il["p95_ms"])
            xlbls.append(cfg)
    if means:
        xp = np.arange(len(xlbls))
        ax2.bar(xp - 0.2, means, 0.35, label="Mean")
        ax2.bar(xp + 0.2, p95s,  0.35, label="P95", alpha=0.7)
        ax2.set_xticks(xp)
        ax2.set_xticklabels(xlbls, rotation=15, ha="right")
        ax2.set_ylabel("Inference Latency (ms / window)")
        ax2.set_title("Inference Latency (ML Configurations)")
        ax2.legend()
    else:
        ax2.text(0.5, 0.5, "No ML inference data", ha="center", va="center",
                 transform=ax2.transAxes)

    plt.tight_layout()
    path = os.path.join(output_dir, "eval_latency.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[FIG] {path}")


def plot_roc(roc_data: dict, results: dict, output_dir: str) -> None:
    """
    roc_data: {'lstm': (fpr_arr, tpr_arr, auc), 'gru': (...)}
    rule operating point taken from results['rule_only']
    """
    plt = _ensure_matplotlib()
    fig, ax = plt.subplots(figsize=(7, 6))

    colors = {"lstm": "steelblue", "gru": "darkorange"}
    for key, (fpr, tpr, auc_v) in roc_data.items():
        label = f"{key.upper()}-UE v1 (AUC={auc_v:.3f})"
        ax.plot(fpr, tpr, color=colors.get(key, "gray"), lw=2, label=label)

    # Rule operating point
    rule = results.get("rule_only", {})
    if rule:
        cm   = rule["confusion_matrix"]
        fpr_r = cm["fp"] / (cm["fp"] + cm["tn"]) if (cm["fp"] + cm["tn"]) > 0 else 0
        tpr_r = rule["recall"]
        ax.plot(fpr_r, tpr_r, marker="*", markersize=14, color="red",
                label=f"Rule-only (FPR={fpr_r*100:.1f}%, TPR={tpr_r*100:.1f}%)",
                linestyle="None")

    ax.plot([0, 1], [0, 1], "k--", lw=0.8, label="Random")
    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate (Recall)")
    ax.set_title("ROC Curve — Per-UE IDS")
    ax.legend(loc="lower right")
    ax.set_xlim([0, 1]); ax.set_ylim([0, 1.02])
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    path = os.path.join(output_dir, "eval_roc.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[FIG] {path}")
```

- [ ] **Step 4: Add Section 7 (`main()` + CLI) to `evaluate_per_ue_v2.py`**

```python
# ── Section 7: CLI + main ─────────────────────────────────────────────────────

def _pool_per_rnti(by_rnti: dict, models: dict, thresh_lstm: float, thresh_gru: float):
    """
    Process each RNTI independently. Returns arrays pooled across all RNTIs
    sufficient for metric computation.

    Returns dict with:
      rule_fires, lstm_fires, gru_fires (bool arrays, length = sum of (N_rnti - 9) per RNTI)
      labels_aligned (int array)
      timestamps_aligned (float64 array)
      rntis_aligned (int array)
      lstm_mse (float32), gru_mse (float32)   — aligned to same indices
      lstm_mse_attack_pos (float32 arrays for ROC — only label>0 windows)
      lstm_latencies, gru_latencies (list of float)
    """
    lstm_model, lstm_scaler, _ = models["lstm"]
    gru_model,  gru_scaler,  _ = models["gru"]

    all_rule   = []
    all_lstm   = []
    all_gru    = []
    all_labels = []
    all_ts     = []
    all_rntis  = []
    all_lstm_mse = []
    all_gru_mse  = []
    lstm_lats_all = []
    gru_lats_all  = []

    for rnti, rows in sorted(by_rnti.items()):
        N = len(rows)
        if N < SEQ_LEN:
            print(f"  [SKIP] RNTI {rnti}: only {N} rows (< {SEQ_LEN})")
            continue

        X    = extract_features(rows)
        lbls = get_labels(rows)
        ts   = get_timestamps_ms(rows)

        rule_full = run_rule_engine(X)

        lstm_mse, llats = score_ml(lstm_model, lstm_scaler, X)
        gru_mse,  glats = score_ml(gru_model,  gru_scaler,  X)

        # Align everything to timestep indices t >= SEQ_LEN-1
        aligned_rule  = rule_full[SEQ_LEN - 1:]
        aligned_lbls  = lbls[SEQ_LEN - 1:]
        aligned_ts    = ts[SEQ_LEN - 1:]
        aligned_rntis = np.full(len(aligned_lbls), rnti, dtype=np.int32)

        lstm_fires = lstm_mse > thresh_lstm
        gru_fires  = gru_mse  > thresh_gru

        all_rule.append(aligned_rule)
        all_lstm.append(lstm_fires)
        all_gru.append(gru_fires)
        all_labels.append(aligned_lbls)
        all_ts.append(aligned_ts)
        all_rntis.append(aligned_rntis)
        all_lstm_mse.append(lstm_mse)
        all_gru_mse.append(gru_mse)
        lstm_lats_all.extend(llats)
        gru_lats_all.extend(glats)

    return {
        "rule_fires":   np.concatenate(all_rule),
        "lstm_fires":   np.concatenate(all_lstm),
        "gru_fires":    np.concatenate(all_gru),
        "labels":       np.concatenate(all_labels),
        "timestamps":   np.concatenate(all_ts),
        "rntis":        np.concatenate(all_rntis),
        "lstm_mse":     np.concatenate(all_lstm_mse),
        "gru_mse":      np.concatenate(all_gru_mse),
        "lstm_latencies": lstm_lats_all,
        "gru_latencies":  gru_lats_all,
    }


def main():
    ap = argparse.ArgumentParser(description="Per-UE IDS evaluation — 5 configs")
    ap.add_argument("--val",    default="csv/dataset_validation_ue_juni.csv")
    ap.add_argument("--attack", default="csv/dataset_attack_ue_juni.csv")
    ap.add_argument("--output", default="results/")
    ap.add_argument("--save-figures", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # ── Load models ───────────────────────────────────────────────────────────
    models = load_models()
    _, _, thresh_lstm = models["lstm"]
    _, _, thresh_gru  = models["gru"]

    # ── Validation dataset (FPR only) ─────────────────────────────────────────
    print(f"\n[1/4] Validation dataset: {args.val}")
    val_rows = load_csv(args.val)
    preprocess_rows(val_rows)
    val_by_rnti = split_by_rnti(val_rows)

    val_rule_fires_all = []
    val_lstm_mse_all   = []
    val_gru_mse_all    = []

    for rnti, rows in sorted(val_by_rnti.items()):
        if len(rows) < SEQ_LEN:
            continue
        X = extract_features(rows)
        rule_f = run_rule_engine(X)[SEQ_LEN - 1:]
        lstm_mse, _ = score_ml(models["lstm"][0], models["lstm"][1], X)
        gru_mse,  _ = score_ml(models["gru"][0],  models["gru"][1],  X)
        val_rule_fires_all.append(rule_f)
        val_lstm_mse_all.append(lstm_mse)
        val_gru_mse_all.append(gru_mse)

    val_rule_fires = np.concatenate(val_rule_fires_all) if val_rule_fires_all else np.array([], dtype=bool)
    val_lstm_mse   = np.concatenate(val_lstm_mse_all)   if val_lstm_mse_all   else np.array([], dtype=np.float32)
    val_gru_mse    = np.concatenate(val_gru_mse_all)    if val_gru_mse_all    else np.array([], dtype=np.float32)

    n_val = len(val_rule_fires)
    fpr_rule_val = compute_fpr_val(val_rule_fires, n_val)
    fpr_lstm_val = compute_fpr_val(val_lstm_mse > thresh_lstm, n_val)
    fpr_gru_val  = compute_fpr_val(val_gru_mse  > thresh_gru,  n_val)
    fpr_lstm_hyb = compute_fpr_val(val_rule_fires | (val_lstm_mse > thresh_lstm), n_val)
    fpr_gru_hyb  = compute_fpr_val(val_rule_fires | (val_gru_mse  > thresh_gru),  n_val)

    print(f"    Validation windows: {n_val}")
    print(f"    FPR — rule:{fpr_rule_val*100:.2f}%  lstm:{fpr_lstm_val*100:.2f}%  gru:{fpr_gru_val*100:.2f}%")

    # ── Attack dataset ─────────────────────────────────────────────────────────
    print(f"\n[2/4] Attack dataset: {args.attack}")
    atk_rows = load_csv(args.attack)
    preprocess_rows(atk_rows)

    # Count mixed windows across full attack dataset
    all_atk_labels_raw = get_labels(atk_rows)
    mixed_count = count_mixed_windows(all_atk_labels_raw)
    mixed_pct   = round(mixed_count / max(1, len(all_atk_labels_raw) - SEQ_LEN + 1) * 100, 2)
    print(f"    Mixed windows: {mixed_count} ({mixed_pct}%)")

    atk_by_rnti = split_by_rnti(atk_rows)
    p = _pool_per_rnti(atk_by_rnti, models, thresh_lstm, thresh_gru)

    lbls    = p["labels"]
    ts      = p["timestamps"]
    rntis   = p["rntis"]
    rf      = p["rule_fires"]
    lf      = p["lstm_fires"]
    gf      = p["gru_fires"]
    lhf     = rf | lf
    ghf     = rf | gf
    lmse    = p["lstm_mse"]
    gmse    = p["gru_mse"]

    # MSE of attack-positive windows (for ROC)
    pos_mask = lbls > 0
    lmse_pos = lmse[pos_mask]
    gmse_pos = gmse[pos_mask]

    # ── Compute metrics ───────────────────────────────────────────────────────
    print("\n[3/4] Computing metrics...")
    inf_lstm = compute_inference_latency(p["lstm_latencies"])
    inf_gru  = compute_inference_latency(p["gru_latencies"])

    lstm_fpr, lstm_tpr, lstm_auc = compute_roc_auc(val_lstm_mse, lmse_pos)
    gru_fpr,  gru_tpr,  gru_auc  = compute_roc_auc(val_gru_mse,  gmse_pos)

    def _entry(fires, fpr_val, inf_lat=None, auc_val=None):
        return build_result_entry(
            cm               = compute_cm(fires, lbls),
            fpr_val          = fpr_val,
            det_latency      = compute_detection_latency(fires, lbls, ts, rntis),
            per_class_recall = compute_per_class_recall(fires, lbls),
            inf_latency      = inf_lat,
            auc_val          = auc_val,
        )

    results = {
        "rule_only":   _entry(rf,  fpr_rule_val),
        "lstm_only":   _entry(lf,  fpr_lstm_val, inf_lstm, lstm_auc),
        "gru_only":    _entry(gf,  fpr_gru_val,  inf_gru,  gru_auc),
        "lstm_hybrid": _entry(lhf, fpr_lstm_hyb, inf_lstm),
        "gru_hybrid":  _entry(ghf, fpr_gru_hyb,  inf_gru),
    }

    # ── Count attack windows ──────────────────────────────────────────────────
    n_atk_total  = int(len(lbls))
    n_atk_label0 = int((lbls == 0).sum())
    n_atk_pos    = int(pos_mask.sum())

    metadata = {
        "val_csv":    args.val,
        "attack_csv": args.attack,
        "seq_len":    SEQ_LEN,
        "thresholds": {
            "lstm":   thresh_lstm,
            "gru":    thresh_gru,
            "source": "validation_p99",
        },
        "window_counts": {
            "validation":      n_val,
            "attack_total":    n_atk_total,
            "attack_label0":   n_atk_label0,
            "attack_label_gt0": n_atk_pos,
            "mixed":           mixed_count,
            "mixed_pct":       mixed_pct,
        },
    }

    # ── Output ────────────────────────────────────────────────────────────────
    print("\n[4/4] Output...")
    save_results_json(metadata, results, args.output)
    print_summary_table(results)

    if args.save_figures:
        roc_data = {
            "lstm": (lstm_fpr, lstm_tpr, lstm_auc),
            "gru":  (gru_fpr,  gru_tpr,  gru_auc),
        }
        plot_confusion_matrices(results, args.output)
        plot_per_class(results, args.output)
        plot_latency(results, args.output)
        plot_roc(roc_data, results, args.output)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run full test suite**

```bash
./venv/bin/python -m pytest tests/test_eval_per_ue_v2.py -v
```

Expected: all 27 unit tests PASS (integration test skipped if datasets absent).

- [ ] **Step 6: Run integration smoke test**

```bash
./venv/bin/python3 evaluate_per_ue_v2.py \
    --val    csv/dataset_validation_ue_juni.csv \
    --attack csv/dataset_attack_ue_juni.csv \
    --output results/ \
    --save-figures
```

Expected:
- Prints validation FPR for all 5 configurations
- Prints ASCII summary table
- Creates `results/eval_per_ue_v2_<timestamp>.json`
- Creates 4 PNG files: `eval_confusion.png`, `eval_per_class.png`, `eval_latency.png`, `eval_roc.png`

Verify:
```bash
ls results/eval_per_ue_v2_*.json results/eval_*.png
python3 -c "import json; d=json.load(open(sorted(__import__('glob').glob('results/eval_per_ue_v2_*.json'))[-1])); print(list(d['results'].keys()))"
```

Expected output: `['rule_only', 'lstm_only', 'gru_only', 'lstm_hybrid', 'gru_hybrid']`

- [ ] **Step 7: Commit**

```bash
git add evaluate_per_ue_v2.py tests/test_eval_per_ue_v2.py
git commit -m "feat: add plots, CLI, main() — evaluate_per_ue_v2.py complete (Task 6)"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ Section 1 (clip, per-RNTI, stride=1, seq_len=10, mixed stats) → Task 1
- ✅ Section 2 (R1–R5 stateful, 5 configs, aligned t≥9) → Task 2
- ✅ Section 3 (ML scoring, threshold from JSON, inference latency) → Task 3
- ✅ Section 4 (recall, F1, FPR_val, detection latency per-segment, per-class recall, ROC) → Task 4
- ✅ Section 4 (ROC uses val + attack-pos) → Task 4 `compute_roc_auc`
- ✅ Section 4 (detection latency segment = (label, rnti) constant) → `find_attack_segments`
- ✅ Section 4 (inference latency mean/median/p95) → `compute_inference_latency`
- ✅ Section 4 (pooled across RNTIs, not per-RNTI average) → `_pool_per_rnti`
- ✅ Section 4 (no accuracy metric) → confirmed absent
- ✅ Section 4 (hybrid no AUC field) → `build_result_entry` only adds auc when passed
- ✅ Section 5 JSON (thresholds with source, window_counts, confusion matrix per config) → Task 5 + main()
- ✅ Section 5 (4 figures) → Task 6 Section 6
- ✅ Section 5 CLI (--val, --attack, --output, --save-figures) → Task 6 `main()`

**Type consistency:** All function names used in Task 6 (`_pool_per_rnti`, `_entry`) reference functions defined in earlier tasks. `build_result_entry` signature matches `Task 5` definition exactly.
