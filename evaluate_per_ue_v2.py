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


def preprocess_rows(rows: list[dict]) -> None:
    """Clip PRB features to [0, 1] in-place."""
    for r in rows:
        for col in ("prb_usage_ul_ratio", "prb_usage_dl_ratio", "prb_total"):
            if col in r:
                r[col] = min(1.0, max(0.0, r[col]))


def split_by_rnti(rows: list[dict]) -> dict[int, list[dict]]:
    """Group rows by RNTI, preserving chronological order within each group."""
    d: dict[int, list[dict]] = defaultdict(list)
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
