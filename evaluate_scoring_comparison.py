#!/usr/bin/env python3
"""Benign-calibrated scoring evaluation — Rule-Only, ML-Only, Hybrid.

Uses the leakage-free benign-calibrated weights only (no attack-informed Scheme A).
Reports Rule-Only, ML-Only (benign), and Hybrid (Rule OR ML) on the held-out
attack dataset, in the per_ue_v5/v6 metric format. The threshold is calibrated so
the deployed Hybrid config stays under the FPR(Attack) ceiling (default 3%),
using only benign (label==0) windows — no attack-class labels.

See docs/superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md
"""
import argparse
import json
import os

import numpy as np

from evaluate_per_ue_v2 import (
    SEQ_LEN, load_csv, preprocess_rows, split_by_rnti, extract_features,
    get_labels, build_windows, run_rule_engine, compute_roc_auc, load_models,
)
from src.detection.feature_schema_ue import (
    FEATURE_NAMES, FEATURE_WEIGHTS, add_burst_features_rows,
)
from src.detection.scoring import (
    make_weight_vec, weighted_score, per_feature_residuals_from_windows,
)

LABEL_NAMES = {1: "ul_flood", 2: "dl_flood", 3: "burst", 4: "roq"}
TARGET_FPR_ATTACK = 0.03  # FPR(Attack) ceiling for the deployed Hybrid config


def pooled_data(model, scaler, rows_by_rnti):
    """Return (residuals (M,F), labels (M,), rule_fires (M,)) pooled + window-aligned."""
    res_parts, lbl_parts, rule_parts = [], [], []
    for rnti, rows in sorted(rows_by_rnti.items()):
        if len(rows) < SEQ_LEN:
            continue
        add_burst_features_rows(rows)
        X = extract_features(rows)
        X_scaled = scaler.transform(X).astype(np.float32)
        wins = build_windows(X_scaled, SEQ_LEN)
        if len(wins) == 0:
            continue
        res = per_feature_residuals_from_windows(model, wins)
        lbls = get_labels(rows)[SEQ_LEN - 1:]          # align: window i -> last-row label
        rule = run_rule_engine(X)[SEQ_LEN - 1:]        # rule engine on raw features
        res_parts.append(res)
        lbl_parts.append(lbls[:len(res)])
        rule_parts.append(rule[:len(res)])
    if not res_parts:
        raise ValueError("no RNTI had >= SEQ_LEN rows")
    return (np.concatenate(res_parts), np.concatenate(lbl_parts),
            np.concatenate(rule_parts))


def calibrate_hybrid_threshold(neg_ml, neg_rule, target):
    """Lowest ML threshold keeping hybrid (rule OR ml>thr) benign FPR <= target.

    Uses benign (label==0) windows only — no attack-class labels. Returns +inf if
    the rule engine alone already exceeds the target (ML then adds nothing).
    """
    if float(neg_rule.mean()) > target:
        return float("inf")
    best = float("inf")
    for thr in np.unique(neg_ml)[::-1]:                # high -> low: FPR rises as thr falls
        if float((neg_rule | (neg_ml > thr)).mean()) <= target:
            best = float(thr)
        else:
            break
    return best


def metrics_from_fires(fires_atk, atk_lbls, fires_val, auc=None):
    """Full metric suite for a binary decision vector over the attack windows."""
    neg = atk_lbls == 0
    pos = atk_lbls > 0
    tp = int(fires_atk[pos].sum()); fn = int(pos.sum() - tp)
    fp = int(fires_atk[neg].sum()); tn = int(neg.sum() - fp)
    recall    = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) else 0.0)
    per_class = {}
    for lbl, name in LABEL_NAMES.items():
        m = atk_lbls == lbl
        per_class[name] = round(float(fires_atk[m].mean()), 4) if m.sum() else None
    return {
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "f1": round(f1, 4),
        "fpr_attack": round(fp / (fp + tn) if (fp + tn) else 0.0, 4),
        "fpr_val": round(float(fires_val.mean()) if len(fires_val) else 0.0, 4),
        "auc": auc,
        "per_class_recall": per_class,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }


def evaluate_model(model, scaler, val_data, atk_data, target_fpr):
    """Return {rule_only, ml_only, hybrid} metric dicts for one model (benign scoring)."""
    val_res, _, val_rule = val_data
    atk_res, atk_lbls, atk_rule = atk_data

    w = make_weight_vec("benign", FEATURE_NAMES, FEATURE_WEIGHTS, benign_residuals=val_res)
    atk_ml = weighted_score(atk_res, w)
    val_ml = weighted_score(val_res, w)

    neg = atk_lbls == 0
    thr = calibrate_hybrid_threshold(atk_ml[neg], atk_rule[neg], target_fpr)

    atk_ml_fires = atk_ml > thr
    val_ml_fires = val_ml > thr
    _, _, ml_auc = compute_roc_auc(atk_ml[neg], atk_ml[atk_lbls > 0])

    # Threshold provenance: what percentile of each benign score distribution it sits at.
    finite = thr != float("inf")
    pct_val = float((val_ml <= thr).mean() * 100) if (finite and len(val_ml)) else 100.0
    pct_atk_benign = float((atk_ml[neg] <= thr).mean() * 100) if finite else 100.0

    return {
        "threshold": (None if not finite else round(thr, 6)),
        "threshold_pct_val": round(pct_val, 2),
        "threshold_pct_attack_benign": round(pct_atk_benign, 2),
        # ROC-AUC only defined for the pure ML score (N/A for rule/hybrid, per convention).
        "rule_only": metrics_from_fires(atk_rule, atk_lbls, val_rule, auc=None),
        "ml_only":   metrics_from_fires(atk_ml_fires, atk_lbls, val_ml_fires, auc=ml_auc),
        "hybrid":    metrics_from_fires(atk_rule | atk_ml_fires, atk_lbls,
                                        val_rule | val_ml_fires, auc=None),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--val",    default="csv/dataset_validation_ue_juni.csv")
    ap.add_argument("--attack", default="csv/dataset_attack_ue_juni.csv")
    ap.add_argument("--output", default="results/scoring_comparison/")
    ap.add_argument("--target-fpr", type=float, default=TARGET_FPR_ATTACK,
                    help="FPR(Attack) ceiling for the Hybrid config")
    ap.add_argument("--lstm-model",  default="models/lstm_ue_v6.pt")
    ap.add_argument("--lstm-scaler", default="models/lstm_ue_v6_scaler.pkl")
    ap.add_argument("--lstm-threshold", default="models/lstm_ue_v6_threshold.json")
    ap.add_argument("--gru-model",   default="models/gru_ue_v5.pt")
    ap.add_argument("--gru-scaler",  default="models/gru_ue_v5_scaler.pkl")
    ap.add_argument("--gru-threshold", default="models/gru_ue_v5_threshold.json")
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)
    models = load_models(
        lstm_pt=args.lstm_model, lstm_pkl=args.lstm_scaler, lstm_json=args.lstm_threshold,
        gru_pt=args.gru_model, gru_pkl=args.gru_scaler, gru_json=args.gru_threshold,
    )

    print(f"[*] Loading validation: {args.val}")
    val_rows = load_csv(args.val); preprocess_rows(val_rows)
    val_by_rnti = split_by_rnti(val_rows)

    print(f"[*] Loading attack (held-out test): {args.attack}")
    atk_rows = load_csv(args.attack); preprocess_rows(atk_rows)
    atk_by_rnti = split_by_rnti(atk_rows)

    all_results = {}
    for mtype in ["gru", "lstm"]:
        model, scaler, _ = models[mtype]
        print(f"\n=== {mtype.upper()} — computing residuals + rule fires ===")
        val_data = pooled_data(model, scaler, val_by_rnti)
        atk_data = pooled_data(model, scaler, atk_by_rnti)
        atk_lbls = atk_data[1]
        print(f"    val windows={len(val_data[0])}  attack windows={len(atk_data[0])} "
              f"(benign={int((atk_lbls==0).sum())}, attack={int((atk_lbls>0).sum())})")
        all_results[mtype] = evaluate_model(model, scaler, val_data, atk_data, args.target_fpr)

    # ── Save + print ──────────────────────────────────────────────────────────
    json_path = os.path.join(args.output, "benign_hybrid_comparison.json")
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n[JSON] {json_path}")

    tgt = f"{args.target_fpr*100:.0f}%"
    row_names = [("rule_only", "Rule Only"), ("ml_only", "ML-Only (benign)"),
                 ("hybrid", "Hybrid (Rule OR benign)")]
    lines = [f"# Benign-Calibrated Detection @ Hybrid FPR(Attack) <= {tgt}\n",
             "## Threshold (benign-calibrated weighted MSE)",
             "| Model | Th | Percentile (val benign) | Percentile (attack benign) |",
             "|---|---|---|---|"]
    for mtype in ["gru", "lstm"]:
        r = all_results[mtype]
        th = f"{r['threshold']:.6f}" if r["threshold"] is not None else "inf"
        lines.append(f"| {mtype.upper()} | {th} | P{r['threshold_pct_val']:.2f} | "
                     f"P{r['threshold_pct_attack_benign']:.2f} |")
    lines += ["\n## Global metrics",
             "| Model | Config | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | AUC |",
             "|---|---|---|---|---|---|---|---|"]
    for mtype in ["gru", "lstm"]:
        for key, label in row_names:
            r = all_results[mtype][key]
            auc = f"{r['auc']:.4f}" if r["auc"] is not None else "N/A"
            lines.append(
                f"| {mtype.upper()} | {label} | {r['recall']*100:.2f}% | "
                f"{r['precision']*100:.2f}% | {r['f1']*100:.2f}% | "
                f"{r['fpr_attack']*100:.2f}% | {r['fpr_val']*100:.2f}% | {auc} |")
    lines += ["\n## Per-class recall",
              "| Model | Config | UL Flood | DL Flood | Burst | RoQ |",
              "|---|---|---|---|---|---|"]

    def _pc(v):
        return f"{v*100:.2f}%" if v is not None else "-"
    for mtype in ["gru", "lstm"]:
        for key, label in row_names:
            pc = all_results[mtype][key]["per_class_recall"]
            lines.append(
                f"| {mtype.upper()} | {label} | {_pc(pc['ul_flood'])} | "
                f"{_pc(pc['dl_flood'])} | {_pc(pc['burst'])} | {_pc(pc['roq'])} |")
    md_path = os.path.join(args.output, "benign_hybrid_comparison.md")
    with open(md_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[MD]   {md_path}\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
