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
