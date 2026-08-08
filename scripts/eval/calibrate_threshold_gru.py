#!/usr/bin/env python3
"""
calibrate_threshold_gru.py — Sweep percentile thresholds for GRU-UE model
to find the best Recall while keeping FPR (val) <= 5%, for both
GRU-only and GRU-Hybrid (Rule OR ML) modes.

Usage: ./venv/bin/python3 calibrate_threshold_gru.py [--version v5|v6]
"""

import argparse
import json
import os
import pickle
import sys
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.feature_schema_ue import FEATURE_NAMES, NUM_FEATURES, FEATURE_WEIGHTS, add_burst_features_rows

SEQ_LEN = 30
_WEIGHT_VEC = torch.tensor(
    [FEATURE_WEIGHTS.get(n, 1.0) for n in FEATURE_NAMES], dtype=torch.float32
)

_RULE_DEFS = [
    (lambda f: (f[3] > 15000.0) or  (f[1] > 0.70),  5),   # R1 UL Flood
    (lambda f: (f[2] > 15000.0) or  (f[0] > 0.85),  5),   # R2 DL Flood
    (lambda f: (f[9] > 0.12)    and (f[8] > 0.05),  5),   # R3 Burst
    (lambda f: (f[10] >= 0.90)  and (f[8] > 0.50),  10),  # R4 Persistence
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v5", choices=["v5", "v6"])
    args = ap.parse_args()

    ver = args.version
    gru_pt = f"models/gru_ue_{ver}.pt"
    gru_pkl = f"models/gru_ue_{ver}_scaler.pkl"
    val_csv = "csv/dataset_validation_ue_juni.csv"
    attack_csv = "csv/dataset_attack_ue_juni.csv"

    print(f"[*] Loading GRU-UE {ver} model from {gru_pt}...")
    gru_cfg = {
        "gru_model": {
            "input_features": NUM_FEATURES,
            "encoder_hidden": [64, 32],
            "decoder_hidden": [32, 64],
            "latent_dim": 32,
            "bidirectional": True,
        },
        "detection": {"sequence_length": SEQ_LEN},
    }
    gru = GRUAutoencoder.load(gru_pt, gru_cfg)
    gru.eval()

    with open(gru_pkl, "rb") as f:
        gru_scaler = pickle.load(f)

    # 1. Validation set
    print(f"[*] Loading validation set: {val_csv}")
    val_rows = load_csv(val_csv)
    preprocess_rows(val_rows)
    val_by_rnti = split_by_rnti(val_rows)

    val_rule_fires_all = []
    val_gru_mse_all = []
    X_val_all = []

    for rnti, rows in val_by_rnti.items():
        if len(rows) < SEQ_LEN:
            continue
        add_burst_features_rows(rows)
        X = extract_features(rows)
        rule_f = run_rule_engine(X)[SEQ_LEN - 1:]
        gru_mse = score_ml(gru, gru_scaler, X)
        val_rule_fires_all.append(rule_f)
        val_gru_mse_all.append(gru_mse)
        X_val_all.append(X[SEQ_LEN - 1:])

    val_rule_fires = np.concatenate(val_rule_fires_all)
    val_gru_mse = np.concatenate(val_gru_mse_all)
    X_val = np.concatenate(X_val_all)

    print("\n[*] Rule Engine False Positives on Validation Data:")
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

    # 2. Attack set
    print(f"[*] Loading attack set: {attack_csv}")
    atk_rows = load_csv(attack_csv)
    preprocess_rows(atk_rows)
    atk_by_rnti = split_by_rnti(atk_rows)

    atk_rule_fires_all = []
    atk_gru_mse_all = []
    atk_labels_all = []

    for rnti, rows in sorted(atk_by_rnti.items()):
        if len(rows) < SEQ_LEN:
            continue
        add_burst_features_rows(rows)
        X = extract_features(rows)
        lbls = get_labels(rows)[SEQ_LEN - 1:]
        rule_f = run_rule_engine(X)[SEQ_LEN - 1:]
        gru_mse = score_ml(gru, gru_scaler, X)

        atk_rule_fires_all.append(rule_f)
        atk_gru_mse_all.append(gru_mse)
        atk_labels_all.append(lbls)

    atk_rule_fires = np.concatenate(atk_rule_fires_all)
    atk_gru_mse = np.concatenate(atk_gru_mse_all)
    atk_labels = np.concatenate(atk_labels_all)

    # 3. Sweep percentiles
    percentiles = sorted(list(set(
        [99.9, 99.7, 99.5, 99.2, 99.0, 98.7, 98.5, 98.2, 98.0, 97.8, 97.5,
         97.2, 97.0, 96.8, 96.5, 96.0, 95.5, 95.0] +
        list(np.arange(94.0, 96.0, 0.2))
    )), reverse=True)

    print("\n" + "=" * 120)
    print(f"{'Pctl':<8} {'Thresh':<12} {'FPR GRU (%)':<13} {'FPR Hyb (%)':<13} "
          f"{'Rec GRU (%)':<13} {'Rec Hyb (%)':<13} {'RoQ GRU (%)':<13} {'RoQ Hyb (%)':<13}")
    print("=" * 120)

    rows_out = []
    attack_mask = atk_labels > 0
    roq_mask = atk_labels == 4

    for p in percentiles:
        thresh = float(np.percentile(val_gru_mse, p))

        fpr_val_gru = (val_gru_mse > thresh).mean() * 100
        fpr_val_hyb = (val_rule_fires | (val_gru_mse > thresh)).mean() * 100

        pred_gru = (atk_gru_mse > thresh)
        pred_hyb = (atk_rule_fires | pred_gru)

        rec_gru = pred_gru[attack_mask].mean() * 100
        rec_hyb = pred_hyb[attack_mask].mean() * 100
        roq_gru = pred_gru[roq_mask].mean() * 100
        roq_hyb = pred_hyb[roq_mask].mean() * 100

        rows_out.append(dict(p=p, thresh=thresh, fpr_gru=fpr_val_gru, fpr_hyb=fpr_val_hyb,
                              rec_gru=rec_gru, rec_hyb=rec_hyb, roq_gru=roq_gru, roq_hyb=roq_hyb))

        print(f"{p:<8.1f} {thresh:<12.6f} {fpr_val_gru:<13.2f} {fpr_val_hyb:<13.2f} "
              f"{rec_gru:<13.2f} {rec_hyb:<13.2f} {roq_gru:<13.2f} {roq_hyb:<13.2f}")

    print("=" * 120)

    # 4. Pick best: maximize recall (GRU-only mode preferred for tighter FPR control)
    # among all rows where FPR <= 5.0%, both modes.
    candidates = []
    for r in rows_out:
        if r["fpr_gru"] <= 5.0:
            candidates.append(("GRU-only", r["p"], r["thresh"], r["fpr_gru"], r["rec_gru"], r["roq_gru"]))
        if r["fpr_hyb"] <= 5.0:
            candidates.append(("Hybrid", r["p"], r["thresh"], r["fpr_hyb"], r["rec_hyb"], r["roq_hyb"]))

    if not candidates:
        print("\n[!] No percentile satisfies FPR <= 5% in either mode.")
        return

    # among candidates with recall in [90, 100), pick highest recall; else pick global best recall under 5% FPR
    in_90s = [c for c in candidates if c[4] >= 90.0]
    pool = in_90s if in_90s else candidates
    best = max(pool, key=lambda c: c[4])

    mode, p, thresh, fpr, rec, roq = best
    print(f"\n[*] Best candidate ({'Recall>=90% & FPR<=5%' if in_90s else 'closest under FPR<=5%'}):")
    print(f"    Mode: {mode}")
    print(f"    Percentile: P{p:.1f}")
    print(f"    Threshold: {thresh:.6f}")
    print(f"    FPR (val): {fpr:.2f}%")
    print(f"    Overall Recall: {rec:.2f}%")
    print(f"    RoQ Recall: {roq:.2f}%")

    output_path = f"models/gru_ue_{ver}_threshold_recal.json"
    with open(output_path, "w") as f:
        json.dump({
            "mode": mode,
            "percentile": p,
            "threshold": thresh,
            "fpr_pct": fpr,
            "overall_recall_pct": rec,
            "roq_recall_pct": roq,
            "source": f"recalibrated_gru_{ver}_p{p:.1f}"
        }, f, indent=2)
    print(f"\n[OK] Saved recalibrated threshold to {output_path}")


if __name__ == "__main__":
    main()
