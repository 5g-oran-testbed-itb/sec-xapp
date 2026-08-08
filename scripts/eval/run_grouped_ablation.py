#!/usr/bin/env python3
"""Grouped feature ablation across both final architectures.

2 models x 6 feature configurations x 5 seeds = 60 trainings.

Protocol (identical for LSTM and BiGRU — results from different evaluation
procedures must never be compared):
  * every cell is retrained from scratch on the benign training set;
  * benign-calibrated scoring weights are re-derived per cell, because the
    weight vector is indexed by the surviving features;
  * the threshold is recalibrated on the benign validation set only, to the
    same Hybrid FPR(Val) <= 5% target used by the deployed configuration;
  * the attack file is opened only after model and threshold are frozen.

The rule branch is NOT ablated: R1-R5 index features positionally and are not
learned, so the Hybrid arm always evaluates rules on the full 19-feature
vector. Ablation applies to the autoencoder input only.

ML-Only is the primary result. Hybrid is secondary — the rule branch can mask
a degraded model, which is exactly what makes Hybrid unsuitable for judging
feature contribution.

Resumable: cells already present in metrics_by_seed.csv are skipped.

Usage:
  ./venv/bin/python3 run_grouped_ablation.py                 # all 60
  ./venv/bin/python3 run_grouped_ablation.py --models gru --seeds 42
"""
import argparse
import csv as csv_mod
import json
import os
import pickle
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from evaluate_per_ue_v2 import (
    GRU_CFG, LSTM_CFG, SEQ_LEN, build_windows, compute_roc_auc, extract_features,
    get_labels, load_csv, preprocess_rows, run_rule_engine, split_by_rnti,
)
from evaluate_scoring_comparison import calibrate_hybrid_threshold, metrics_from_fires
from src.detection.feature_groups import CONFIGS, DROPPED, kept_features, kept_indices
from src.detection.feature_schema_ue import (
    FEATURE_NAMES, FEATURE_WEIGHTS, add_burst_features_rows,
)
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.lstm_autoencoder import LSTMAutoencoder
from src.detection.scoring import (
    make_weight_vec, per_feature_residuals_from_windows, weighted_score,
)

MODELS = ("lstm", "gru")
SEEDS = (42, 43, 44, 45, 46)
TARGET_FPR_VAL = 0.05
CLASSES = ("ul_flood", "dl_flood", "burst", "roq")

FIELDNAMES = [
    "model", "feature_config", "seed", "mode", "threshold", "recall", "precision",
    "f1", "fpr_validation", "fpr_test", "roc_auc", "recall_ul", "recall_dl",
    "recall_burst", "recall_roq", "best_epoch", "parameter_count",
    "training_time_s", "inference_time_ms", "n_features", "status",
]


# ── Training ──────────────────────────────────────────────────────────────────

def train_cell(arch, config, seed, model_dir, args):
    """Retrain one cell from scratch. Returns (model_path, seconds, best_epoch)."""
    model_path = Path(model_dir) / f"{arch}_{config}_seed{seed}.pt"
    script = "train_gru_ue.py" if arch == "gru" else "train_lstm_ue.py"
    cmd = [
        sys.executable, script,
        "--train", args.train, "--val", args.val,
        "--seq-len", str(SEQ_LEN),
        "--epochs", str(args.epochs),
        "--batch-size", str(args.batch_size),
        "--lr", str(args.lr),
        "--loss-weights", "uniform",          # matches the adopted configuration
        "--seed", str(seed),
        "--model-out", str(model_path),
    ]
    if config != "full_19":
        cmd += ["--features", ",".join(kept_features(config))]

    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-6:]
        raise RuntimeError(f"training failed ({arch}/{config}/seed{seed}):\n"
                           + "\n".join(tail))

    losses_path = str(model_path).replace(".pt", "_losses.json")
    with open(losses_path) as handle:
        best_epoch = json.load(handle)["best_epoch"]
    return model_path, elapsed, best_epoch


# ── Evaluation ────────────────────────────────────────────────────────────────

def load_cell(arch, model_path, n_features):
    """Load a trained cell and its scaler, with the ablated input dimension."""
    base_cfg = GRU_CFG if arch == "gru" else LSTM_CFG
    key = "gru_model" if arch == "gru" else "lstm_model"
    cfg = {key: dict(base_cfg[key]), "detection": dict(base_cfg["detection"])}
    cfg[key]["input_features"] = n_features
    model_class = GRUAutoencoder if arch == "gru" else LSTMAutoencoder
    model = model_class.load(str(model_path), cfg)
    with open(str(model_path).replace(".pt", "_scaler.pkl"), "rb") as handle:
        scaler = pickle.load(handle)
    return model, scaler


def pooled(model, scaler, rows_by_rnti, keep_idx, time_inference=False):
    """Per-RNTI windowing. Rules see all 19 features; the AE sees the subset."""
    parts = {k: [] for k in ("res", "labels", "rule")}
    latencies = []
    for _rnti, rows in sorted(rows_by_rnti.items()):
        if len(rows) < SEQ_LEN:
            continue
        add_burst_features_rows(rows)
        X_full = extract_features(rows)                    # (N, 19) — rule branch
        X_sub = scaler.transform(X_full[:, keep_idx]).astype(np.float32)
        wins = build_windows(X_sub, SEQ_LEN)
        if len(wins) == 0:
            continue
        n = len(wins)
        if time_inference:
            res, lat = residuals_timed(model, wins)
            latencies.extend(lat)
        else:
            res = per_feature_residuals_from_windows(model, wins)
        parts["res"].append(res)
        parts["labels"].append(get_labels(rows)[SEQ_LEN - 1:][:n])
        parts["rule"].append(run_rule_engine(X_full)[SEQ_LEN - 1:][:n])
    if not parts["res"]:
        raise ValueError("no RNTI had >= SEQ_LEN rows")
    out = {k: np.concatenate(v) for k, v in parts.items()}
    out["latencies"] = latencies
    return out


def residuals_timed(model, wins, batch=256):
    """Per-feature residuals plus per-window forward-pass timing in ms."""
    model.eval()
    parts, lat = [], []
    for i in range(0, len(wins), batch):
        chunk = torch.tensor(wins[i:i + batch])
        t0 = time.perf_counter()
        with torch.no_grad():
            recon = model(chunk)
            fe = ((recon - chunk) ** 2).mean(dim=1)
        t1 = time.perf_counter()
        parts.append(fe.numpy())
        lat.extend([(t1 - t0) * 1000.0 / len(chunk)] * len(chunk))
    return np.concatenate(parts).astype(np.float32), lat


def evaluate_cell(arch, model_path, config, val_by_rnti, atk_by_rnti):
    """Return {ml_only, hybrid} metric dicts plus threshold and inference time."""
    keep_idx = kept_indices(config)
    model, scaler = load_cell(arch, model_path, len(keep_idx))
    param_count = sum(p.numel() for p in model.parameters())

    val = pooled(model, scaler, val_by_rnti, keep_idx)
    atk = pooled(model, scaler, atk_by_rnti, keep_idx, time_inference=True)

    # Scoring weights are re-derived per cell: the vector is indexed by the
    # surviving features, so it cannot be carried over from full_19.
    names = kept_features(config)
    w = make_weight_vec("benign", names, FEATURE_WEIGHTS, benign_residuals=val["res"])
    val_ml = weighted_score(val["res"], w)
    atk_ml = weighted_score(atk["res"], w)

    # Threshold from the benign validation set only.
    thr = calibrate_hybrid_threshold(val_ml, val["rule"], TARGET_FPR_VAL)
    finite = np.isfinite(thr)

    neg = atk["labels"] == 0
    _, _, auc = compute_roc_auc(atk_ml[neg], atk_ml[atk["labels"] > 0])
    atk_fires, val_fires = atk_ml > thr, val_ml > thr

    modes = {
        "ml_only": (atk_fires, val_fires),
        "hybrid": (atk["rule"] | atk_fires, val["rule"] | val_fires),
    }
    out = {}
    for mode, (fa, fv) in modes.items():
        m = metrics_from_fires(fa, atk["labels"], fv,
                               auc=(auc if mode == "ml_only" else None))
        out[mode] = m
    return {
        "threshold": (None if not finite else round(float(thr), 6)),
        "parameter_count": param_count,
        "inference_time_ms": (round(float(np.mean(atk["latencies"])), 4)
                              if atk["latencies"] else None),
        "n_features": len(keep_idx),
        **out,
    }


# ── Driver ────────────────────────────────────────────────────────────────────

def done_cells(path):
    """(model, config, seed) already recorded, for resumption."""
    if not Path(path).exists():
        return set()
    with open(path, newline="") as handle:
        return {(r["model"], r["feature_config"], int(r["seed"]))
                for r in csv_mod.DictReader(handle)}


def append_rows(path, rows):
    exists = Path(path).exists()
    with open(path, "a", newline="") as handle:
        writer = csv_mod.DictWriter(handle, fieldnames=FIELDNAMES)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def build_rows(arch, config, seed, ev, train_s, best_epoch, status="ok"):
    rows = []
    for mode in ("ml_only", "hybrid"):
        m = ev[mode]
        pc = m["per_class_recall"]
        rows.append({
            "model": arch, "feature_config": config, "seed": seed, "mode": mode,
            "threshold": ev["threshold"],
            "recall": round(m["recall"] * 100, 4),
            "precision": round(m["precision"] * 100, 4),
            "f1": round(m["f1"] * 100, 4),
            "fpr_validation": round(m["fpr_val"] * 100, 4),
            "fpr_test": round(m["fpr_attack"] * 100, 4),
            "roc_auc": m["auc"],
            "recall_ul": round((pc["ul_flood"] or 0) * 100, 4),
            "recall_dl": round((pc["dl_flood"] or 0) * 100, 4),
            "recall_burst": round((pc["burst"] or 0) * 100, 4),
            "recall_roq": round((pc["roq"] or 0) * 100, 4),
            "best_epoch": best_epoch,
            "parameter_count": ev["parameter_count"],
            "training_time_s": round(train_s, 1),
            "inference_time_ms": ev["inference_time_ms"],
            "n_features": ev["n_features"],
            "status": status,
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="+", default=list(MODELS), choices=MODELS)
    ap.add_argument("--configs", nargs="+", default=list(CONFIGS), choices=CONFIGS)
    ap.add_argument("--seeds", nargs="+", type=int, default=list(SEEDS))
    ap.add_argument("--train", default="csv/dataset_training_ue_juni.csv")
    ap.add_argument("--val", default="csv/dataset_validation_ue_juni.csv")
    ap.add_argument("--attack", default="csv/dataset_attack_ue_juni.csv")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=0.001)
    ap.add_argument("--model-dir", default="models/grouped_ablation")
    ap.add_argument("--output", default="results/grouped_feature_ablation")
    args = ap.parse_args()

    Path(args.model_dir).mkdir(parents=True, exist_ok=True)
    Path(args.output).mkdir(parents=True, exist_ok=True)
    metrics_csv = Path(args.output) / "metrics_by_seed.csv"

    print(f"[*] Loading evaluation datasets")
    val_rows = load_csv(args.val); preprocess_rows(val_rows)
    val_by_rnti = split_by_rnti(val_rows)
    atk_rows = load_csv(args.attack); preprocess_rows(atk_rows)
    atk_by_rnti = split_by_rnti(atk_rows)

    already = done_cells(metrics_csv)
    plan = [(a, c, s) for a in args.models for c in args.configs for s in args.seeds]
    todo = [cell for cell in plan if cell not in already]
    print(f"[*] {len(plan)} cells planned, {len(already & set(plan))} already done, "
          f"{len(todo)} to run")

    failures = []
    for i, (arch, config, seed) in enumerate(todo, 1):
        tag = f"{arch}/{config}/seed{seed}"
        print(f"\n[{i}/{len(todo)}] {tag}", flush=True)
        try:
            model_path, train_s, best_epoch = train_cell(
                arch, config, seed, args.model_dir, args)
            ev = evaluate_cell(arch, model_path, config, val_by_rnti, atk_by_rnti)
            append_rows(metrics_csv, build_rows(arch, config, seed, ev,
                                                train_s, best_epoch))
            h, m = ev["hybrid"], ev["ml_only"]
            print(f"      trained {train_s/60:.1f} min (best epoch {best_epoch}) | "
                  f"Th={ev['threshold']} | ML-Only recall={m['recall']*100:.2f}% "
                  f"F1={m['f1']*100:.2f}% AUC={m['auc']} | "
                  f"Hybrid recall={h['recall']*100:.2f}%", flush=True)
        except Exception as exc:                       # noqa: BLE001 — recorded, not hidden
            print(f"      FAILED: {exc}", flush=True)
            failures.append((tag, str(exc)))
            append_rows(metrics_csv, [{
                **{k: "" for k in FIELDNAMES},
                "model": arch, "feature_config": config, "seed": seed,
                "mode": "ml_only", "status": f"FAILED: {exc}"[:200],
            }])

    print(f"\n[*] Finished. {len(todo) - len(failures)} succeeded, "
          f"{len(failures)} failed.")
    for tag, exc in failures:
        print(f"    FAILED {tag}: {exc[:120]}")
    print(f"[CSV] {metrics_csv}")


if __name__ == "__main__":
    main()
