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
