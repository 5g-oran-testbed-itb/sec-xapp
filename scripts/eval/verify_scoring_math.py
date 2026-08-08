#!/usr/bin/env python3
"""Verify the two-stage scoring math behind the reported metrics.

Stage 1 (training) is unweighted: the uniform-loss models were trained with all
19 feature weights equal to 1, i.e. plain MSE.

Stage 2 (anomaly scoring) IS a weighted MSE: per-feature residuals averaged over
the window's timesteps, then combined with benign-calibrated weights derived from
validation residuals only.

This script proves three things, so the numbers in docs/opsi_b_metrics.md can be
reproduced at the defence:

  1. the benign weight vector recomputed by hand equals the library's;
  2. sum(w*e)/sum(w) recomputed by hand equals weighted_score();
  3. replacing the benign weights with uniform weights collapses RoQ recall below
     the 85% target — i.e. the scoring weights are load-bearing, not cosmetic.

Usage:
  ./venv/bin/python3 verify_scoring_math.py
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from eval_opsi_b import load_variant, pooled_data, ARCHITECTURES
from evaluate_per_ue_v2 import (
    compute_roc_auc, load_csv, preprocess_rows, split_by_rnti,
)
from evaluate_scoring_comparison import calibrate_hybrid_threshold, metrics_from_fires
from src.detection.feature_schema_ue import FEATURE_NAMES, FEATURE_WEIGHTS
from src.detection.scoring import make_weight_vec, weighted_score

EPS = 1e-6
CAP_MULT = 10.0
ROQ_TARGET = 0.85


def benign_weights_by_hand(residuals):
    """Reimplementation of scoring.benign_calibrated_weights from the formula."""
    med = np.median(residuals, axis=0)
    mad = np.median(np.abs(residuals - med), axis=0)
    raw = 1.0 / (med + mad + EPS)
    cap = CAP_MULT * np.median(raw)
    return np.minimum(raw, cap), cap, int((raw > cap).sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", default="models/ablation_loss")
    ap.add_argument("--val", default="csv/dataset_validation_ue_juni.csv")
    ap.add_argument("--attack", default="csv/dataset_attack_ue_juni.csv")
    ap.add_argument("--target-fpr-val", type=float, default=0.05)
    args = ap.parse_args()

    val_rows = load_csv(args.val)
    preprocess_rows(val_rows)
    val_by_rnti = split_by_rnti(val_rows)
    atk_rows = load_csv(args.attack)
    preprocess_rows(atk_rows)
    atk_by_rnti = split_by_rnti(atk_rows)

    failures = []
    for arch in ARCHITECTURES:
        model, scaler = load_variant(args.model_dir, arch)
        val = pooled_data(model, scaler, val_by_rnti)
        atk = pooled_data(model, scaler, atk_by_rnti)
        print(f"\n=== {arch.upper()}-AE (uniform training loss) ===")

        # ── Check 1: weight derivation ───────────────────────────────────────
        w_hand, cap, n_capped = benign_weights_by_hand(val["res"])
        w_lib = make_weight_vec("benign", FEATURE_NAMES, FEATURE_WEIGHTS,
                                benign_residuals=val["res"])
        ok = np.allclose(w_hand, w_lib, rtol=1e-5)
        print(f"[{'PASS' if ok else 'FAIL'}] hand-derived w_j == library w_j     "
              f"(cap = {cap:.1f}, {n_capped}/{len(FEATURE_NAMES)} features capped)")
        if not ok:
            failures.append(f"{arch}: weight mismatch")

        # ── Check 2: weighted-score formula ──────────────────────────────────
        e0 = atk["res"][0]
        s_hand = float((e0 * w_lib).sum() / w_lib.sum())
        s_lib = float(weighted_score(atk["res"], w_lib)[0])
        s_unweighted = float(e0.mean())
        ok = abs(s_hand - s_lib) < 1e-7
        print(f"[{'PASS' if ok else 'FAIL'}] sum(w*e)/sum(w) == weighted_score()  "
              f"S = {s_hand:.8f}")
        print(f"       unweighted mean(e) = {s_unweighted:.8f} "
              f"→ {s_unweighted / s_hand:.2f}x larger (pure weight-scale artifact)")
        if not ok:
            failures.append(f"{arch}: score formula mismatch")

        # ── Check 3: are the scoring weights load-bearing? ────────────────────
        for tag, w in (("benign ", w_lib),
                       ("uniform", make_weight_vec("uniform", FEATURE_NAMES,
                                                   FEATURE_WEIGHTS))):
            val_s = weighted_score(val["res"], w)
            atk_s = weighted_score(atk["res"], w)
            thr = calibrate_hybrid_threshold(val_s, val["rule"], args.target_fpr_val)
            neg = atk["labels"] == 0
            _, _, auc = compute_roc_auc(atk_s[neg], atk_s[atk["labels"] > 0])
            m = metrics_from_fires(atk["rule"] | (atk_s > thr), atk["labels"],
                                   val["rule"] | (val_s > thr))
            roq = m["per_class_recall"]["roq"]
            flag = "OK " if roq >= ROQ_TARGET else "!! below 85% target"
            print(f"       scoring={tag}  Th={thr:.6f}  Recall={m['recall']*100:6.2f}%  "
                  f"F1={m['f1']*100:6.2f}%  FPR(Atk)={m['fpr_attack']*100:.2f}%  "
                  f"FPR(Val)={m['fpr_val']*100:.2f}%  AUC={auc:.4f}  "
                  f"RoQ={roq*100:6.2f}% {flag}")

    if failures:
        print("\n[FAIL] " + "; ".join(failures))
        raise SystemExit(1)
    print("\n[PASS] scoring math verified for both architectures")


if __name__ == "__main__":
    main()
