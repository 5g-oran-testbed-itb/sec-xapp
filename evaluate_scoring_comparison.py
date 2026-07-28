#!/usr/bin/env python3
"""Leakage-aware scoring comparison: Uniform vs Benign-calibrated vs Attack-informed.

For each deployed model (GRU v5, LSTM v6) and each scoring scheme, computes
held-out metrics on csv/dataset_attack_ue_juni.csv. Uniform and Benign schemes
never touch attack data during calibration, so the attack file is a valid
held-out test for them. Attack-informed is shown as a labeled (biased) comparison.

See docs/superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md
"""
import argparse
import json
import os

import numpy as np

from evaluate_per_ue_v2 import (
    SEQ_LEN, load_csv, preprocess_rows, split_by_rnti, extract_features,
    get_labels, build_windows, compute_roc_auc, load_models,
)
from src.detection.feature_schema_ue import (
    FEATURE_NAMES, FEATURE_WEIGHTS, add_burst_features_rows,
)
from src.detection.scoring import (
    make_weight_vec, weighted_score, per_feature_residuals_from_windows,
)

LABEL_NAMES = {1: "ul_flood", 2: "dl_flood", 3: "burst", 4: "roq"}
SCORINGS = ["uniform", "benign", "attack"]
FPR_POINTS = [0.05, 0.03]
P_THRESHOLD = 97.0  # percentile on benign validation scores


def pooled_residuals(model, scaler, rows_by_rnti):
    """Return (residuals (M,F), labels (M,)) pooled across RNTIs, window-aligned."""
    res_parts, lbl_parts = [], []
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
        lbls = get_labels(rows)[SEQ_LEN - 1:]          # align: window i -> last row label
        res_parts.append(res)
        lbl_parts.append(lbls[:len(res)])
    if not res_parts:
        raise ValueError("no RNTI had >= SEQ_LEN rows")
    return np.concatenate(res_parts), np.concatenate(lbl_parts)


def recall_at_fpr(neg, pos, target_fpr):
    """TPR at a target FPR, read off the ROC curve."""
    fpr, tpr, _ = compute_roc_auc(neg, pos)
    return float(np.interp(target_fpr, fpr, tpr))


def evaluate_one(model, scaler, val_res, atk_res, atk_lbls, scoring):
    """Return a metrics dict for one (model, scoring) combination."""
    w = make_weight_vec(scoring, FEATURE_NAMES, FEATURE_WEIGHTS,
                        benign_residuals=val_res)
    val_scores = weighted_score(val_res, w)
    thr = float(np.percentile(val_scores, P_THRESHOLD))

    atk_scores = weighted_score(atk_res, w)
    neg = atk_scores[atk_lbls == 0]                    # held-out benign windows
    pos = atk_scores[atk_lbls > 0]

    held_out_fpr = float((neg > thr).mean()) if len(neg) else 0.0
    recall = float((pos > thr).mean()) if len(pos) else 0.0
    _, _, auc = compute_roc_auc(neg, pos)

    per_class = {}
    for lbl, name in LABEL_NAMES.items():
        m = atk_lbls == lbl
        per_class[name] = round(float((atk_scores[m] > thr).mean()), 4) if m.sum() else None

    return {
        "scoring": scoring,
        "leakage_free": scoring in ("uniform", "benign"),
        "threshold_p97": round(thr, 6),
        "held_out_fpr": round(held_out_fpr, 4),
        "recall_at_p97": round(recall, 4),
        "auc": auc,
        "recall_at_fpr": {f"{int(p*100)}pct": round(recall_at_fpr(neg, pos, p), 4)
                          for p in FPR_POINTS},
        "per_class_recall": per_class,
        "n_neg": int(len(neg)),
        "n_pos": int(len(pos)),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--val",    default="csv/dataset_validation_ue_juni.csv")
    ap.add_argument("--attack", default="csv/dataset_attack_ue_juni.csv")
    ap.add_argument("--output", default="results/scoring_comparison/")
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
        print(f"\n=== {mtype.upper()} — computing residuals ===")
        val_res, _ = pooled_residuals(model, scaler, val_by_rnti)
        atk_res, atk_lbls = pooled_residuals(model, scaler, atk_by_rnti)
        print(f"    val windows={len(val_res)}  attack windows={len(atk_res)} "
              f"(benign={int((atk_lbls==0).sum())}, attack={int((atk_lbls>0).sum())})")
        all_results[mtype] = [
            evaluate_one(model, scaler, val_res, atk_res, atk_lbls, s)
            for s in SCORINGS
        ]

    # ── Save + print ──────────────────────────────────────────────────────────
    json_path = os.path.join(args.output, "scoring_comparison.json")
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n[JSON] {json_path}")

    lines = ["# Scoring Comparison (held-out on dataset_attack_ue_juni.csv)\n",
             "| Model | Scoring | Leakage-free | Recall@P97 | Held-out FPR | AUC | Recall@5% | Recall@3% |",
             "|---|---|---|---|---|---|---|---|"]
    for mtype in ["gru", "lstm"]:
        for r in all_results[mtype]:
            lines.append(
                f"| {mtype.upper()} | {r['scoring']} | "
                f"{'yes' if r['leakage_free'] else 'NO (biased)'} | "
                f"{r['recall_at_p97']:.4f} | {r['held_out_fpr']:.4f} | {r['auc']:.4f} | "
                f"{r['recall_at_fpr']['5pct']:.4f} | {r['recall_at_fpr']['3pct']:.4f} |")
    md_path = os.path.join(args.output, "scoring_comparison.md")
    with open(md_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[MD]   {md_path}\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
