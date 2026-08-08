#!/usr/bin/env python3
"""
calibrate_threshold_remote.py — Calibrate LSTM-UE v6 threshold percentiles.
Sweeps percentiles to find the sweet spot for LSTM-Hybrid (Recall RoQ >= 85%, FPR_val <= 5%).
"""

import json
import os
import pickle
import sys
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.detection.lstm_autoencoder import LSTMAutoencoder
from src.detection.feature_schema_ue import FEATURE_NAMES, NUM_FEATURES, FEATURE_WEIGHTS, add_burst_features_rows

SEQ_LEN = 30
_WEIGHT_VEC = torch.tensor(
    [FEATURE_WEIGHTS.get(n, 1.0) for n in FEATURE_NAMES], dtype=torch.float32
)

# Rule definitions (same as evaluate_per_ue_v2.py)
_RULE_DEFS = [
    (lambda f: (f[3] > 15000.0) or  (f[1] > 0.70),  5),   # R1 UL Flood
    (lambda f: (f[2] > 15000.0) or  (f[0] > 0.85),  5),   # R2 DL Flood
    (lambda f: (f[9] > 0.12)    and (f[8] > 0.05),  5),   # R3 Burst
    (lambda f: (f[10] >= 0.90)  and (f[8] > 0.50),  8),  # R4 RoQ
    (lambda f: (f[1] > 0.30)    and (f[7] < 5000.0), 3),  # R5 Efficiency
]

def load_csv(path):
    import csv
    STR_COLS = {"datetime"}
    INT_COLS = {"rnti", "label"}
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
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

def preprocess_rows(rows):
    for r in rows:
        for col in ("prb_usage_dl_ratio", "prb_usage_ul_ratio", "prb_total"):
            if col in r:
                r[col] = min(1.0, max(0.0, r[col]))

def split_by_rnti(rows):
    from collections import defaultdict
    d = defaultdict(list)
    for r in rows:
        d[int(r["rnti"])].append(r)
    return dict(d)

def extract_features(rows):
    X = np.zeros((len(rows), NUM_FEATURES), dtype=np.float32)
    for i, r in enumerate(rows):
        for j, name in enumerate(FEATURE_NAMES):
            X[i, j] = float(r.get(name, 0.0))
    return X

def get_labels(rows):
    return np.array([int(r["label"]) for r in rows], dtype=np.int32)

def run_rule_engine(X):
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

def score_ml(model, scaler, X_raw, batch=256):
    X_scaled = scaler.transform(X_raw).astype(np.float32)
    N = X_scaled.shape[0]
    if N < SEQ_LEN:
        return np.empty((0,), dtype=np.float32)
    
    # build windows
    wins = np.stack([X_scaled[i:i + SEQ_LEN] for i in range(N - SEQ_LEN + 1)], axis=0)
    
    score_parts = []
    model.eval()
    w = _WEIGHT_VEC
    
    for i in range(0, len(wins), batch):
        chunk = torch.tensor(wins[i:i + batch])
        with torch.no_grad():
            recon = model(chunk)
            fe = ((recon - chunk) ** 2).mean(dim=1)
            score = (fe * w).sum(dim=1) / w.sum()
        score_parts.append(score.numpy())
    return np.concatenate(score_parts).astype(np.float32)

def main():
    lstm_pt = "models/lstm_ue_v6.pt"
    lstm_pkl = "models/lstm_ue_v6_scaler.pkl"
    val_csv = "csv/dataset_validation_ue_juni.csv"
    attack_csv = "csv/dataset_attack_ue_juni.csv"
    
    print(f"[*] Loading LSTM-UE model from {lstm_pt}...")
    lstm_cfg = {
        "lstm_model": {
            "input_features": NUM_FEATURES,
            "encoder_hidden": [64, 32],
            "decoder_hidden": [32, 64],
            "latent_dim": 32,
            "bidirectional": False,
        },
        "detection": {"sequence_length": SEQ_LEN},
    }
    lstm = LSTMAutoencoder.load(lstm_pt, lstm_cfg)
    lstm.eval()
    
    with open(lstm_pkl, "rb") as f:
        lstm_scaler = pickle.load(f)
        
    # 1. Process Validation data
    print(f"[*] Loading validation set: {val_csv}")
    val_rows = load_csv(val_csv)
    preprocess_rows(val_rows)
    val_by_rnti = split_by_rnti(val_rows)
    
    val_rule_fires_all = []
    val_lstm_mse_all = []
    
    for rnti, rows in val_by_rnti.items():
        if len(rows) < SEQ_LEN:
            continue
        add_burst_features_rows(rows)
        X = extract_features(rows)
        rule_f = run_rule_engine(X)[SEQ_LEN - 1:]
        lstm_mse = score_ml(lstm, lstm_scaler, X)
        val_rule_fires_all.append(rule_f)
        val_lstm_mse_all.append(lstm_mse)
        
    val_rule_fires = np.concatenate(val_rule_fires_all)
    val_lstm_mse = np.concatenate(val_lstm_mse_all)
    n_val = len(val_rule_fires)
    
    # Diagnose rule fires on validation data
    print("\n[*] Rule Engine False Positives on Validation Data:")
    X_val_all = []
    for rnti, rows in val_by_rnti.items():
        if len(rows) < SEQ_LEN:
            continue
        X_val_all.append(extract_features(rows)[SEQ_LEN - 1:])
    X_val = np.concatenate(X_val_all)
    
    for i, (cond, needed) in enumerate(_RULE_DEFS):
        rule_fires_single = np.zeros(len(X_val), dtype=bool)
        counter = 0
        for t in range(len(X_val)):
            if cond(X_val[t]):
                counter += 1
            else:
                counter = 0
            if counter >= needed:
                rule_fires_single[t] = True
        print(f"    Rule R{i+1}: fires {rule_fires_single.sum()} times (FPR = {rule_fires_single.mean()*100:.2f}%)")
    
    # 2. Process Attack data
    print(f"[*] Loading attack set: {attack_csv}")
    atk_rows = load_csv(attack_csv)
    preprocess_rows(atk_rows)
    atk_by_rnti = split_by_rnti(atk_rows)
    
    atk_rule_fires_all = []
    atk_lstm_mse_all = []
    atk_labels_all = []
    
    for rnti, rows in sorted(atk_by_rnti.items()):
        if len(rows) < SEQ_LEN:
            continue
        add_burst_features_rows(rows)
        X = extract_features(rows)
        lbls = get_labels(rows)[SEQ_LEN - 1:]
        rule_f = run_rule_engine(X)[SEQ_LEN - 1:]
        lstm_mse = score_ml(lstm, lstm_scaler, X)
        
        atk_rule_fires_all.append(rule_f)
        atk_lstm_mse_all.append(lstm_mse)
        atk_labels_all.append(lbls)
        
    atk_rule_fires = np.concatenate(atk_rule_fires_all)
    atk_lstm_mse = np.concatenate(atk_lstm_mse_all)
    atk_labels = np.concatenate(atk_labels_all)
    
    # 3. Sweep percentiles
    percentiles = sorted(list(set(
        [99.5, 99.0, 98.7, 98.5, 98.2, 98.0, 97.8, 97.5, 97.2, 97.0, 96.8, 96.5, 96.0, 95.5, 95.0] +
        list(np.arange(94.0, 96.0, 0.1))
    )), reverse=True)
    
    print("\n" + "="*110)
    print(f"{'Percentile':<12} {'Threshold':<12} {'FPR LSTM (%)':<14} {'FPR Hyb (%)':<14} {'Rec Lst (%)':<14} {'Rec Hyb (%)':<14} {'RoQ Lst (%)':<14} {'RoQ Hyb (%)':<14}")
    print("="*110)
    
    best_p = None
    best_metrics = None
    
    for p in percentiles:
        thresh = float(np.percentile(val_lstm_mse, p))
        
        # Calculate FPR
        fpr_val_lstm = (val_lstm_mse > thresh).mean() * 100
        fpr_val_hyb = (val_rule_fires | (val_lstm_mse > thresh)).mean() * 100
        
        # Calculate Attack recalls
        pred_lstm = (atk_lstm_mse > thresh)
        pred_hyb = (atk_rule_fires | pred_lstm)
        
        # Overall recalls
        attack_mask = atk_labels > 0
        overall_recall_lstm = pred_lstm[attack_mask].mean() * 100
        overall_recall_hyb = pred_hyb[attack_mask].mean() * 100
        
        # RoQ recalls
        roq_mask = atk_labels == 4  # 4 = roq
        roq_recall_lstm = pred_lstm[roq_mask].mean() * 100
        roq_recall_hyb = pred_hyb[roq_mask].mean() * 100
        
        print(f"{p:<12.1f} {thresh:<12.6f} {fpr_val_lstm:<14.2f} {fpr_val_hyb:<14.2f} {overall_recall_lstm:<14.2f} {overall_recall_hyb:<14.2f} {roq_recall_lstm:<14.2f} {roq_recall_hyb:<14.2f}")
        
        # Check criteria: maximize RoQ Recall under FPR <= 5.0%
        # We check both Hybrid and LSTM-only modes.
        if fpr_val_hyb <= 5.0:
            if best_p is None or (best_metrics["mode"] == "Hybrid" and roq_recall_hyb > best_metrics["roq_recall"]) or best_metrics["mode"] == "LSTM-only":
                best_p = p
                best_metrics = {
                    "mode": "Hybrid",
                    "threshold": thresh,
                    "fpr_lstm": fpr_val_lstm,
                    "fpr_hybrid": fpr_val_hyb,
                    "overall_recall": overall_recall_hyb,
                    "roq_recall": roq_recall_hyb
                }
        elif fpr_val_lstm <= 5.0:
            # If Hybrid FPR > 5.0% but LSTM-only FPR <= 5.0%
            if best_p is None or (best_metrics["mode"] == "LSTM-only" and roq_recall_lstm > best_metrics["roq_recall"]):
                best_p = p
                best_metrics = {
                    "mode": "LSTM-only",
                    "threshold": thresh,
                    "fpr_lstm": fpr_val_lstm,
                    "fpr_hybrid": fpr_val_hyb,
                    "overall_recall": overall_recall_lstm,
                    "roq_recall": roq_recall_lstm
                }
                
    if best_p is not None:
        print("\n" + "="*110)
        print(f"[*] Optimal Percentile Found in {best_metrics['mode']} Mode: P{best_p:.1f}")
        print(f"    Threshold: {best_metrics['threshold']:.6f}")
        print(f"    FPR (LSTM-only / Hybrid): {best_metrics['fpr_lstm']:.2f}% / {best_metrics['fpr_hybrid']:.2f}%")
        print(f"    Overall Recall ({best_metrics['mode']}): {best_metrics['overall_recall']:.2f}%")
        print(f"    RoQ Recall ({best_metrics['mode']}): {best_metrics['roq_recall']:.2f}%")
        print("="*110)
        
        # Save recalibrated threshold JSON
        output_path = "models/lstm_ue_v6_threshold_recal.json"
        with open(output_path, "w") as f:
            json.dump({
                "mode": best_metrics["mode"],
                "percentile": best_p,
                "threshold": best_metrics["threshold"],
                "fpr_lstm_pct": best_metrics["fpr_lstm"],
                "fpr_hybrid_pct": best_metrics["fpr_hybrid"],
                "overall_recall_pct": best_metrics["overall_recall"],
                "roq_recall_pct": best_metrics["roq_recall"],
                "source": f"recalibrated_p{best_p:.1f}_for_best_roq_under_5pct_fpr"
            }, f, indent=2)
        print(f"[OK] Saved recalibrated threshold to {output_path}")
    else:
        print("\n[!] No percentile found that satisfies FPR <= 5% in either mode.")

if __name__ == "__main__":
    main()
