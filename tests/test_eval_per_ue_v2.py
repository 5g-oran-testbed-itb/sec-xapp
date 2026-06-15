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
