#!/usr/bin/env python3
"""Evaluate uniform- and benign-loss AEs with benign-calibrated scoring."""

import argparse
import json
import pickle
from pathlib import Path

from evaluate_per_ue_v2 import (
    GRU_CFG, LSTM_CFG, load_csv, preprocess_rows, split_by_rnti,
)
from evaluate_scoring_comparison import (
    TARGET_FPR_ATTACK, evaluate_model, pooled_data,
)
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.lstm_autoencoder import LSTMAutoencoder

ARCHITECTURES = ("gru", "lstm")
VARIANTS = ("uniform", "benign")
CONFIGS = (
    ("rule_only", "Rule Only"),
    ("ml_only", "ML-Only"),
    ("hybrid", "Hybrid"),
)


def load_variant(model_dir, arch, variant):
    """Load one ablation model and its matching scaler."""
    base = Path(model_dir) / f"{arch}_ue_loss{variant}"
    config = GRU_CFG if arch == "gru" else LSTM_CFG
    model_class = GRUAutoencoder if arch == "gru" else LSTMAutoencoder
    model = model_class.load(str(base.with_suffix(".pt")), config)
    with open(f"{base}_scaler.pkl", "rb") as handle:
        scaler = pickle.load(handle)
    return model, scaler


def build_markdown(results, target_fpr):
    """Build the complete matched-pair ablation report."""
    lines = [
        "# AE loss-weighting ablation — benign-calibrated scoring",
        "",
        (
            "Matched-pair comparison of **uniform MSE** and **benign-scale "
            "weighted MSE** training. Both variants are free of attack-derived "
            "training weights and use identical benign-calibrated scoring at "
            f"Hybrid FPR(Attack) < {target_fpr * 100:.0f}%."
        ),
        "",
        (
            "**Uncertainty note:** this is a deterministic single-seed (42) "
            "ablation. Differences are descriptive; no confidence interval or "
            "statistical-significance claim is made without repeated seeds."
        ),
        "",
        "## Thresholds",
        "",
        "| Model | Training loss | Th | Percentile (val benign) | Percentile (attack benign) |",
        "|---|---|---:|---:|---:|",
    ]
    for arch in ARCHITECTURES:
        for variant in VARIANTS:
            result = results[arch][variant]
            threshold = result["threshold"]
            threshold_text = "inf" if threshold is None else f"{threshold:.6f}"
            lines.append(
                f"| {arch.upper()} | {variant} | {threshold_text} | "
                f"P{result['threshold_pct_val']:.2f} | "
                f"P{result['threshold_pct_attack_benign']:.2f} |"
            )
    lines += [
        "",
        "## Global metrics",
        "",
        "| Model | Training loss | Config | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | AUC |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arch in ARCHITECTURES:
        for variant in VARIANTS:
            for config_key, config_label in CONFIGS:
                metric = results[arch][variant][config_key]
                auc_text = "N/A" if metric["auc"] is None else f"{metric['auc']:.4f}"
                lines.append(
                    f"| {arch.upper()} | {variant} | {config_label} | "
                    f"{metric['recall'] * 100:.2f}% | "
                    f"{metric['precision'] * 100:.2f}% | "
                    f"{metric['f1'] * 100:.2f}% | "
                    f"{metric['fpr_attack'] * 100:.2f}% | "
                    f"{metric['fpr_val'] * 100:.2f}% | {auc_text} |"
                )
    lines += [
        "",
        "## Hybrid recall per class",
        "",
        "| Model | Training loss | UL Flood | DL Flood | Burst | RoQ |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for arch in ARCHITECTURES:
        for variant in VARIANTS:
            per_class = results[arch][variant]["hybrid"]["per_class_recall"]
            lines.append(
                f"| {arch.upper()} | {variant} | "
                f"{per_class['ul_flood'] * 100:.2f}% | "
                f"{per_class['dl_flood'] * 100:.2f}% | "
                f"{per_class['burst'] * 100:.2f}% | "
                f"{per_class['roq'] * 100:.2f}% |"
            )
    return "\n".join(lines) + "\n"


def validate_results(results, target_fpr):
    """Reject outputs that violate the operating point or have invalid AUC."""
    for arch in ARCHITECTURES:
        for variant in VARIANTS:
            result = results[arch][variant]
            hybrid_fpr = result["hybrid"]["fpr_attack"]
            if hybrid_fpr > target_fpr + 5e-4:
                raise RuntimeError(
                    f"{arch}:{variant} exceeds target FPR: "
                    f"{hybrid_fpr:.4f} > {target_fpr:.4f}"
                )
            auc_value = result["ml_only"]["auc"]
            if auc_value is None or not 0.5 < auc_value <= 1.0:
                raise RuntimeError(f"{arch}:{variant} has invalid AUC: {auc_value}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path("models/ablation_loss"))
    parser.add_argument("--val", default="csv/dataset_validation_ue_juni.csv")
    parser.add_argument("--attack", default="csv/dataset_attack_ue_juni.csv")
    parser.add_argument("--output", type=Path, default=Path("results/loss_ablation"))
    parser.add_argument("--doc", type=Path, default=Path("docs/loss_ablation_results.md"))
    parser.add_argument("--target-fpr", type=float, default=TARGET_FPR_ATTACK)
    args = parser.parse_args()

    validation_rows = load_csv(args.val)
    preprocess_rows(validation_rows)
    attack_rows = load_csv(args.attack)
    preprocess_rows(attack_rows)
    validation_by_rnti = split_by_rnti(validation_rows)
    attack_by_rnti = split_by_rnti(attack_rows)

    results = {}
    for arch in ARCHITECTURES:
        results[arch] = {}
        for variant in VARIANTS:
            print(f"\n=== {arch.upper()} / {variant} loss ===")
            model, scaler = load_variant(args.model_dir, arch, variant)
            validation_data = pooled_data(model, scaler, validation_by_rnti)
            attack_data = pooled_data(model, scaler, attack_by_rnti)
            result = evaluate_model(
                model, scaler, validation_data, attack_data, args.target_fpr
            )
            results[arch][variant] = result
            print(
                f"Th={result['threshold']}  "
                f"Hybrid recall={result['hybrid']['recall']:.4f}  "
                f"F1={result['hybrid']['f1']:.4f}  "
                f"FPR(Attack)={result['hybrid']['fpr_attack']:.4f}  "
                f"ML AUC={result['ml_only']['auc']:.4f}"
            )

    validate_results(results, args.target_fpr)
    markdown = build_markdown(results, args.target_fpr)
    args.output.mkdir(parents=True, exist_ok=True)
    args.doc.parent.mkdir(parents=True, exist_ok=True)
    json_path = args.output / "loss_ablation.json"
    json_path.write_text(json.dumps(results, indent=2) + "\n")
    args.doc.write_text(markdown)
    (args.output / "loss_ablation.md").write_text(markdown)
    print(f"\n[JSON] {json_path}")
    print(f"[MD]   {args.doc}\n")
    print(markdown)


if __name__ == "__main__":
    main()
