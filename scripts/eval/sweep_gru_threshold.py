#!/usr/bin/env python3
"""
Threshold sweep untuk GRU-A dan GRU-B.
Kumpulkan skor per-row, lalu sweep threshold untuk cari recall/FPR optimal.

Usage:
  ./venv/bin/python3 sweep_gru_threshold.py
"""

import csv, pickle, sys, os
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.detection.gru_autoencoder import GRUAutoencoder, GRUEnsemble
from src.detection.feature_schema import FEATURE_NAMES

SCALER_PATH  = "models/scaler_gru.pkl"
MODEL_A_PATH = "models/gru_autoencoder_A_v1.pt"
MODEL_B_PATH = "models/gru_autoencoder_B_v1.pt"
CSV_PATH     = "csv/dataset_attack_mei.csv"

LABEL_NAMES = {0: "Normal", 1: "UL Flood", 2: "DL Flood", 3: "Burst", 4: "RRC Storm"}

SEQ_LEN_B = 30  # max buffer


def load_csv(path):
    STR_COLS = {"datetime", "alert_type"}
    rows = []
    with open(path, newline='') as f:
        for r in csv.DictReader(f):
            rows.append({k: float(v) if k not in STR_COLS else v for k, v in r.items()})
    return rows


def collect_scores(rows, model_a, model_b, scaler):
    """Returns arrays: labels, scores_a, scores_b (one per valid window)."""
    buf = np.zeros((SEQ_LEN_B, len(FEATURE_NAMES)), dtype=np.float32)
    filled = 0
    labels, sa, sb = [], [], []

    for row in rows:
        feat = np.array([row.get(f, 0.0) for f in FEATURE_NAMES], dtype=np.float32)
        feat = scaler.transform(feat.reshape(1, -1))[0]
        feat = np.clip(feat, 0.0, 1.0)

        buf = np.roll(buf, -1, axis=0)
        buf[-1] = feat
        if filled < SEQ_LEN_B:
            filled += 1
            continue

        w = torch.FloatTensor(buf).unsqueeze(0)

        # Score dari model A (seq_len=10 → gunakan 10 frame terakhir)
        with torch.no_grad():
            wa = torch.FloatTensor(buf[-model_a.seq_len:]).unsqueeze(0)
            recon_a = model_a(wa)
            err_a = float(((wa - recon_a) ** 2).mean().item())

            recon_b = model_b(w)
            err_b = float(((w - recon_b) ** 2).mean().item())

        labels.append(int(row.get("label", 0)))
        sa.append(err_a)
        sb.append(err_b)

    return np.array(labels), np.array(sa), np.array(sb)


def sweep(labels, scores, label_names, model_name, current_thresh):
    normal_mask = (labels == 0)
    normal_scores = scores[normal_mask]

    print(f"\n{'='*60}")
    print(f"{model_name}  current_thresh={current_thresh:.6f}")
    print(f"{'='*60}")

    # Distribution per label
    for lbl, lname in label_names.items():
        mask = (labels == lbl)
        if mask.sum() == 0:
            continue
        s = scores[mask]
        print(f"  {lname:12s}  n={mask.sum():5d}  "
              f"mean={s.mean():.6f}  P50={np.percentile(s,50):.6f}  "
              f"P75={np.percentile(s,75):.6f}  P95={np.percentile(s,95):.6f}  "
              f"P99={np.percentile(s,99):.6f}")

    print()
    print(f"  {'Threshold':>10}  {'FPR':>6}  {'UL':>6}  {'DL':>6}  {'Burst':>6}  {'RRC':>6}  {'Overall':>8}")

    # Sweep range: desde P50 normal hasta P99 normal
    lo = np.percentile(normal_scores, 30)
    hi = np.percentile(normal_scores, 99.9)
    steps = np.unique(np.concatenate([
        np.linspace(lo, hi, 60),
        np.percentile(normal_scores, [80, 85, 90, 95, 97, 99]),
        [current_thresh],
    ]))
    steps.sort()

    best = None
    for thr in steps:
        pred = (scores >= thr).astype(int)
        fpr = pred[normal_mask].mean() * 100

        recalls = {}
        for lbl in [1, 2, 3, 4]:
            mask = (labels == lbl)
            if mask.sum() == 0:
                recalls[lbl] = float('nan')
            else:
                recalls[lbl] = pred[mask].mean() * 100

        attack_mask = (labels > 0)
        overall = pred[attack_mask].mean() * 100 if attack_mask.sum() > 0 else 0

        marker = " <-- current" if abs(thr - current_thresh) < 1e-7 else ""
        print(f"  {thr:10.6f}  {fpr:6.1f}  "
              f"{recalls[1]:6.1f}  {recalls[2]:6.1f}  {recalls[3]:6.1f}  {recalls[4]:6.1f}  "
              f"{overall:8.1f}{marker}")

        # Track best: maximise min recall across attacks, with FPR<=5%
        min_r = min(v for v in recalls.values() if not np.isnan(v))
        if fpr <= 5.0:
            if best is None or min_r > best['min_r']:
                best = dict(thresh=thr, fpr=fpr, recalls=recalls, min_r=min_r, overall=overall)

    if best:
        print(f"\n  ** Best (FPR<=5%): thresh={best['thresh']:.6f}  FPR={best['fpr']:.1f}%  "
              f"min_recall={best['min_r']:.1f}%  overall={best['overall']:.1f}%")
        print(f"     UL={best['recalls'][1]:.1f}%  DL={best['recalls'][2]:.1f}%  "
              f"Burst={best['recalls'][3]:.1f}%  RRC={best['recalls'][4]:.1f}%")


def main():
    print(f"Loading scaler from {SCALER_PATH}")
    with open(SCALER_PATH, 'rb') as f:
        scaler = pickle.load(f)

    print(f"Loading models...")
    cfg = {'gru_model': {}, 'detection': {}}
    model_a = GRUAutoencoder.load(MODEL_A_PATH, cfg)
    model_b = GRUAutoencoder.load(MODEL_B_PATH, cfg)
    model_a.eval()
    model_b.eval()

    thr_a = model_a.anomaly_threshold or 0.002881  # fallback to P99-fitted value
    thr_b = model_b.anomaly_threshold or 0.009865
    print(f"GRU-A thresh={thr_a:.6f}  seq_len={model_a.seq_len}")
    print(f"GRU-B thresh={thr_b:.6f}  seq_len={model_b.seq_len}")

    print(f"\nLoading dataset {CSV_PATH}...")
    rows = load_csv(CSV_PATH)
    print(f"Rows: {len(rows)}")

    print("\nCollecting scores (this may take a minute)...")
    labels, sa, sb = collect_scores(rows, model_a, model_b, scaler)
    print(f"Valid windows: {len(labels)}  "
          f"Normal={( labels==0).sum()}  Attack={(labels>0).sum()}")

    sweep(labels, sa, LABEL_NAMES, "GRU-A (seq_len=10)", thr_a)
    sweep(labels, sb, LABEL_NAMES, "GRU-B (seq_len=30)", thr_b)

    # Also sweep combined OR logic (same as current GRUEnsemble)
    print(f"\n{'='*60}")
    print("Combined (A OR B) sweep — best combo")
    print(f"{'='*60}")
    normal_mask = (labels == 0)

    # Fix A at best or current, sweep B for RRC Storm
    print("\nFix GRU-A@current, sweep GRU-B:")
    print(f"  {'ThrB':>10}  {'FPR':>6}  {'UL':>6}  {'DL':>6}  {'Burst':>6}  {'RRC':>6}  {'Overall':>8}")
    for thrb in np.percentile(sb[normal_mask], [50, 60, 70, 80, 85, 87, 89, 90, 91, 93, 95, 97, 99]):
        pred = ((sa >= thr_a) | (sb >= thrb)).astype(int)
        fpr = pred[normal_mask].mean() * 100
        recalls = {}
        for lbl in [1, 2, 3, 4]:
            mask = (labels == lbl)
            recalls[lbl] = pred[mask].mean() * 100 if mask.sum() > 0 else 0
        overall = pred[(labels > 0)].mean() * 100
        print(f"  {thrb:10.6f}  {fpr:6.1f}  "
              f"{recalls[1]:6.1f}  {recalls[2]:6.1f}  {recalls[3]:6.1f}  {recalls[4]:6.1f}  "
              f"{overall:8.1f}")


if __name__ == "__main__":
    main()
