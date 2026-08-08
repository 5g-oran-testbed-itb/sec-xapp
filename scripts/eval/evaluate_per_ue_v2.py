#!/usr/bin/env python3
"""
evaluate_per_ue_v2.py — Per-UE IDS evaluation (rule + LSTM + GRU + hybrid).

Usage:
  ./venv/bin/python3 evaluate_per_ue_v2.py \\
      --val    csv/dataset_validation_ue_juni.csv \\
      --attack csv/dataset_attack_ue_juni.csv \\
      --output results/ \\
      [--save-figures]
"""
import argparse
import csv as csv_mod
import json
import os
import pickle
import sys
import time
from collections import defaultdict
from datetime import datetime

import numpy as np
import torch
from sklearn.metrics import roc_curve, auc as sklearn_auc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.lstm_autoencoder import LSTMAutoencoder
from src.detection.feature_schema_ue import FEATURE_NAMES, NUM_FEATURES, FEATURE_WEIGHTS, add_burst_features_rows

SEQ_LEN = 30
# Weighted MSE scoring — Scheme A weights derived from per-feature error analysis.
_WEIGHT_VEC = torch.tensor(
    [FEATURE_WEIGHTS.get(n, 1.0) for n in FEATURE_NAMES], dtype=torch.float32
)
# xApp KPM cycle period — mitigation is triggered on the next cycle after detection.
_XAPP_CYCLE_S = 0.12
LABEL_NAMES = {1: "ul_flood", 2: "dl_flood", 3: "burst", 4: "roq"}

GRU_CFG = {
    "gru_model": {
        "input_features": NUM_FEATURES,
        "encoder_hidden": [64, 32],
        "decoder_hidden": [32, 64],
        "latent_dim": 32,
        "bidirectional": True,
    },
    "detection": {"sequence_length": SEQ_LEN},
}
LSTM_CFG = {
    "lstm_model": {
        "input_features": NUM_FEATURES,
        "encoder_hidden": [64, 32],
        "decoder_hidden": [32, 64],
        "latent_dim": 32,
        "bidirectional": False,
    },
    "detection": {"sequence_length": SEQ_LEN},
}

# ── Section 1: Data pipeline ──────────────────────────────────────────────────

def load_csv(path: str) -> list[dict]:
    STR_COLS = {"datetime"}
    INT_COLS = {"rnti", "label"}
    rows = []
    with open(path, newline="") as f:
        for r in csv_mod.DictReader(f):
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


def preprocess_rows(rows: list[dict]) -> None:
    """Clip PRB features to [0, 1] in-place."""
    for r in rows:
        for col in ("prb_usage_ul_ratio", "prb_usage_dl_ratio", "prb_total"):
            if col in r:
                r[col] = min(1.0, max(0.0, r[col]))


def split_by_rnti(rows: list[dict]) -> dict[int, list[dict]]:
    """Group rows by RNTI, preserving chronological order within each group."""
    d: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        d[int(r["rnti"])].append(r)
    return dict(d)


def extract_features(rows: list[dict]) -> np.ndarray:
    """Returns float32 array of shape (N, 15)."""
    X = np.zeros((len(rows), NUM_FEATURES), dtype=np.float32)
    for i, r in enumerate(rows):
        for j, name in enumerate(FEATURE_NAMES):
            X[i, j] = float(r.get(name, 0.0))
    return X


def get_labels(rows: list[dict]) -> np.ndarray:
    return np.array([int(r["label"]) for r in rows], dtype=np.int32)


def get_timestamps_ms(rows: list[dict]) -> np.ndarray:
    return np.array([float(r["timestamp_ms"]) for r in rows], dtype=np.float64)


def build_windows(X: np.ndarray, seq_len: int = SEQ_LEN) -> np.ndarray:
    """Sliding windows from X (already scaled). Returns (N-seq+1, seq, feat)."""
    N = X.shape[0]
    if N < seq_len:
        return np.empty((0, seq_len, X.shape[1]), dtype=np.float32)
    return np.stack([X[i:i + seq_len] for i in range(N - seq_len + 1)], axis=0)


def count_mixed_windows(labels: np.ndarray, seq_len: int = SEQ_LEN) -> int:
    """Count windows where 0 < fraction_attack < 1."""
    N = len(labels)
    mixed = 0
    for i in range(N - seq_len + 1):
        attack_ratio = float(np.mean(labels[i:i + seq_len] != 0))
        if 0 < attack_ratio < 1:
            mixed += 1
    return mixed


# ── Section 2: Rule engine ────────────────────────────────────────────────────

_RULE_DEFS = [
    # (condition_fn, consec_needed)
    (lambda f: (f[3] > 15000.0) or  (f[1] > 0.70),  5),   # R1 UL Flood
    (lambda f: (f[2] > 15000.0) or  (f[0] > 0.85),  5),   # R2 DL Flood
    (lambda f: (f[9] > 0.12)    and (f[8] > 0.05),  5),   # R3 Burst
    (lambda f: (f[10] >= 0.90)  and (f[8] > 0.50),  10),  # R4 RoQ
    (lambda f: (f[1] > 0.30)    and (f[7] < 5000.0), 3),  # R5 Efficiency
]


def run_rule_engine(X: np.ndarray) -> np.ndarray:
    """
    Stateful R1–R5 evaluation. Counters run continuously (not reset per window).
    Input:  X (N, 15) float32 — raw (unscaled) per-UE features
    Output: rule_fires (N,) bool — True if any rule fires at timestep t
    """
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


# ── Section 3: ML scoring + inference latency ─────────────────────────────────

def load_models(
    lstm_pt:    str = "models/lstm_ue_v4.pt",
    lstm_pkl:   str = "models/lstm_ue_v4_scaler.pkl",
    lstm_json:  str = "models/lstm_ue_v4_threshold.json",
    gru_pt:     str = "models/gru_ue_v4.pt",
    gru_pkl:    str = "models/gru_ue_v4_scaler.pkl",
    gru_json:   str = "models/gru_ue_v4_threshold.json",
) -> dict:
    """Returns dict with 'lstm' and 'gru' keys, each a (model, scaler, threshold) tuple."""
    print(f"[*] Loading GRU-UE from {gru_pt}...")
    gru = GRUAutoencoder.load(gru_pt, GRU_CFG)
    gru.eval()
    with open(gru_pkl, "rb") as f:
        gru_scaler = pickle.load(f)
    with open(gru_json) as f:
        gru_thresh = json.load(f)["threshold"]
    print(f"    threshold={gru_thresh:.6f}")

    print(f"[*] Loading LSTM-UE from {lstm_pt}...")
    lstm = LSTMAutoencoder.load(lstm_pt, LSTM_CFG)
    lstm.eval()
    with open(lstm_pkl, "rb") as f:
        lstm_scaler = pickle.load(f)
    with open(lstm_json) as f:
        lstm_thresh = json.load(f)["threshold"]
    print(f"    threshold={lstm_thresh:.6f}")

    return {
        "lstm": (lstm, lstm_scaler, lstm_thresh),
        "gru":  (gru,  gru_scaler,  gru_thresh),
    }


def score_ml(
    model, scaler, X_raw: np.ndarray, batch: int = 256
) -> tuple[np.ndarray, list[float]]:
    """
    Score all windows from X_raw using weighted MSE (Scheme A weights).
    X_raw: (N, num_features) unscaled features.
    Returns:
      score (N-seq_len+1,) float32 — weighted MSE per window
      latency_ms (list of float) — per-window inference time in milliseconds
    """
    X_scaled = scaler.transform(X_raw).astype(np.float32)
    wins = build_windows(X_scaled, SEQ_LEN)
    if len(wins) == 0:
        return np.array([], dtype=np.float32), []

    score_parts: list[np.ndarray] = []
    latencies: list[float] = []
    model.eval()
    w = _WEIGHT_VEC  # (num_features,)

    for i in range(0, len(wins), batch):
        chunk = torch.tensor(wins[i:i + batch])
        t0 = time.perf_counter()
        with torch.no_grad():
            recon = model(chunk)                          # (B, seq, feat)
            fe = ((recon - chunk) ** 2).mean(dim=1)      # (B, feat) — mean over time
            score = (fe * w).sum(dim=1) / w.sum()        # (B,) — weighted mean over feat
        t1 = time.perf_counter()
        score_parts.append(score.numpy())
        n = len(chunk)
        latencies.extend([(t1 - t0) * 1000.0 / n] * n)

    return np.concatenate(score_parts).astype(np.float32), latencies


# ── Section 4: Metrics ────────────────────────────────────────────────────────

def compute_cm(preds_binary: np.ndarray, labels_attack: np.ndarray) -> dict:
    """
    preds_binary: (N,) bool — True = anomaly predicted
    labels_attack: (N,) int — 0=benign, >0=attack
    Returns: {tp, fp, tn, fn, recall, precision, f1}
    """
    is_attack = labels_attack > 0
    TP = int(( preds_binary &  is_attack).sum())
    FP = int(( preds_binary & ~is_attack).sum())
    TN = int((~preds_binary & ~is_attack).sum())
    FN = int((~preds_binary &  is_attack).sum())
    precision = TP / (TP + FP) if (TP + FP) > 0 else 0.0
    recall    = TP / (TP + FN) if (TP + FN) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)
    return {"tp": TP, "fp": FP, "tn": TN, "fn": FN,
            "recall": round(recall, 4), "precision": round(precision, 4),
            "f1": round(f1, 4)}


def compute_fpr_val(val_fires: np.ndarray, n_val_windows: int) -> float:
    """False positive rate on pure-benign validation windows."""
    if n_val_windows == 0:
        return 0.0
    return round(float(val_fires.sum()) / n_val_windows, 4)


def compute_per_class_recall(
    preds_binary: np.ndarray, labels_attack: np.ndarray
) -> dict[str, float | None]:
    """TPR for each attack class. None if no windows of that class."""
    result: dict[str, float | None] = {}
    for lbl, name in LABEL_NAMES.items():
        mask = labels_attack == lbl
        if mask.sum() == 0:
            result[name] = None
        else:
            result[name] = round(float(preds_binary[mask].sum()) / mask.sum(), 4)
    return result


def find_attack_segments(
    labels: np.ndarray, timestamps_ms: np.ndarray, rntis: np.ndarray
) -> list[dict]:
    """
    Returns list of attack segments where (label, rnti) is constant and label > 0.
    Each segment: {label, rnti, start_ts, start_idx, end_idx (inclusive)}.
    """
    segments = []
    N = len(labels)
    i = 0
    while i < N:
        if labels[i] <= 0:
            i += 1
            continue
        j = i + 1
        while j < N and labels[j] == labels[i] and rntis[j] == rntis[i]:
            j += 1
        segments.append({
            "label":     int(labels[i]),
            "rnti":      int(rntis[i]),
            "start_ts":  float(timestamps_ms[i]),
            "start_idx": i,
            "end_idx":   j - 1,
        })
        i = j
    return segments


def compute_detection_latency(
    preds_binary: np.ndarray,
    labels: np.ndarray,
    timestamps_ms: np.ndarray,
    rntis: np.ndarray,
) -> dict[str, dict]:
    """
    Per-class detection latency. Segments with no alert are excluded (FN).
    Returns {class_name: {mean_s, median_s, n_segments}}.
    """
    segments = find_attack_segments(labels, timestamps_ms, rntis)
    lats: dict[str, list[float]] = defaultdict(list)

    for seg in segments:
        name = LABEL_NAMES.get(seg["label"])
        if name is None:
            continue
        s, e = seg["start_idx"], seg["end_idx"]
        alert_idxs = np.where(preds_binary[s:e + 1])[0]
        if len(alert_idxs) == 0:
            continue
        first_alert_ts = timestamps_ms[s + alert_idxs[0]]
        lats[name].append((first_alert_ts - seg["start_ts"]) / 1000.0)

    result: dict[str, dict] = {}
    for name in LABEL_NAMES.values():
        vals = lats[name]
        if vals:
            result[name] = {
                "mean_s":     round(float(np.mean(vals)), 3),
                "median_s":   round(float(np.median(vals)), 3),
                "n_segments": len(vals),
            }
        else:
            result[name] = {"mean_s": None, "median_s": None, "n_segments": 0}
    return result


def compute_mitigation_latency(det_latency: dict) -> dict:
    """
    Mitigation latency = detection latency + one xApp KPM cycle (_XAPP_CYCLE_S).
    After detection fires, the xApp triggers E2SM-RC PRB throttle on the next cycle.
    Inherits n_segments from detection latency (same segments, same FN exclusion).
    """
    result = {}
    for cls, v in det_latency.items():
        if v["mean_s"] is None:
            result[cls] = {"mean_s": None, "median_s": None, "n_segments": 0}
        else:
            result[cls] = {
                "mean_s":     round(v["mean_s"]   + _XAPP_CYCLE_S, 3),
                "median_s":   round(v["median_s"] + _XAPP_CYCLE_S, 3),
                "n_segments": v["n_segments"],
            }
    return result


def compute_inference_latency(latency_ms: list[float]) -> dict:
    """Summarise per-window inference times (ms). Returns mean/median/p95."""
    if not latency_ms:
        return {"mean_ms": None, "median_ms": None, "p95_ms": None}
    a = np.array(latency_ms)
    return {
        "mean_ms":   round(float(np.mean(a)), 4),
        "median_ms": round(float(np.median(a)), 4),
        "p95_ms":    round(float(np.percentile(a, 95)), 4),
    }


def compute_roc_auc(
    mse_val: np.ndarray, mse_attack_pos: np.ndarray
) -> tuple[np.ndarray, np.ndarray, float]:
    """
    mse_val: MSE of validation (pure-benign) windows → y_true=0
    mse_attack_pos: MSE of attack-dataset windows with label>0 → y_true=1
    Returns (fpr_arr, tpr_arr, auc_val).
    """
    y_true  = np.concatenate([np.zeros(len(mse_val)), np.ones(len(mse_attack_pos))])
    y_score = np.concatenate([mse_val, mse_attack_pos])
    fpr, tpr, _ = roc_curve(y_true, y_score)
    auc_val = float(sklearn_auc(fpr, tpr))
    return fpr, tpr, round(auc_val, 4)


# ── Section 5: JSON output + stdout summary ───────────────────────────────────

def build_result_entry(
    cm: dict,
    fpr_val: float,
    det_latency: dict,
    per_class_recall: dict,
    inf_latency: dict | None = None,
    auc_val: float | None = None,
) -> dict:
    """Build the JSON sub-dict for one detection configuration."""
    mit_latency = compute_mitigation_latency(det_latency)
    entry: dict = {
        "recall":    cm["recall"],
        "precision": cm["precision"],
        "f1":        cm["f1"],
        "fpr_val":   fpr_val,
        "confusion_matrix": {
            "tn": cm["tn"], "fp": cm["fp"],
            "fn": cm["fn"], "tp": cm["tp"],
        },
        "detection_latency":   det_latency,
        "mitigation_latency":  mit_latency,
        "per_class_recall":    per_class_recall,
    }
    if inf_latency is not None:
        entry["inference_latency"] = inf_latency
    if auc_val is not None:
        entry["auc"] = auc_val
    return entry


def save_results_json(metadata: dict, results: dict, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"eval_per_ue_v2_{ts}.json")
    with open(path, "w") as f:
        json.dump({"metadata": metadata, "results": results}, f, indent=2)
    print(f"\n[JSON] Saved → {path}")
    return path


def print_summary_table(results: dict) -> None:
    configs = ["rule_only", "lstm_only", "gru_only", "lstm_hybrid", "gru_hybrid"]
    header = (f"{'Config':<16} {'Recall':>7} {'F1':>7} {'FPR_val':>8} "
              f"{'Det.Lat(s)':>11} {'Mit.Lat(s)':>11} {'Inf.Lat(ms)':>12}")
    print("\n" + header)
    print("─" * len(header))
    for cfg in configs:
        r = results.get(cfg)
        if r is None:
            continue
        recall  = f"{r['recall']*100:.1f}%"
        f1      = f"{r['f1']*100:.1f}%"
        fpr     = f"{r['fpr_val']*100:.2f}%"
        det_vals = [
            v["mean_s"] for v in r.get("detection_latency", {}).values()
            if isinstance(v, dict) and v.get("mean_s") is not None
        ]
        mit_vals = [
            v["mean_s"] for v in r.get("mitigation_latency", {}).values()
            if isinstance(v, dict) and v.get("mean_s") is not None
        ]
        det_str = f"{float(np.mean(det_vals)):.2f}" if det_vals else "—"
        mit_str = f"{float(np.mean(mit_vals)):.2f}" if mit_vals else "—"
        inf_ms  = r.get("inference_latency", {}).get("mean_ms")
        inf_str = f"{inf_ms:.2f}" if inf_ms is not None else "—"
        print(f"{cfg:<16} {recall:>7} {f1:>7} {fpr:>8} "
              f"{det_str:>11} {mit_str:>11} {inf_str:>12}")


# ── Section 6: Plots ──────────────────────────────────────────────────────────

def _ensure_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def plot_confusion_matrices(results: dict, output_dir: str) -> None:
    plt = _ensure_matplotlib()
    for model_type in ["lstm", "gru"]:
        configs = ["rule_only", f"{model_type}_only", f"{model_type}_hybrid"]
        fig, axes = plt.subplots(2, 2, figsize=(10, 8))
        axes = axes.flatten()

        for idx, cfg in enumerate(configs):
            ax = axes[idx]
            r  = results[cfg]
            cm_d = r["confusion_matrix"]
            cm   = np.array([[cm_d["tn"], cm_d["fp"]],
                             [cm_d["fn"], cm_d["tp"]]])

            cmap = "Greens" if model_type == "lstm" else "Oranges"
            im = ax.imshow(cm, interpolation="nearest", cmap=cmap)
            ax.set_title(cfg.upper().replace("_ONLY", "-ONLY").replace("_HYBRID", "-HYBRID"), fontsize=11, fontweight="bold")
            ax.set_xlabel("Predicted"); ax.set_ylabel("Actual")
            ax.set_xticks([0, 1]); ax.set_yticks([0, 1])
            ax.set_xticklabels(["Normal", "Anomaly"])
            ax.set_yticklabels(["Normal", "Anomaly"])
            for i in range(2):
                for j in range(2):
                    ax.text(j, i, f"{cm[i, j]:,}",
                            ha="center", va="center", fontsize=13,
                            color="white" if cm[i, j] > cm.max() / 2 else "black")
            plt.colorbar(im, ax=ax)

        # Summary text in the 4th quadrant
        ax_text = axes[3]
        ax_text.axis("off")
        lines = ["Summary Metrics", "=" * 25, ""]
        for cfg in configs:
            r = results[cfg]
            lines.append(f"{cfg.upper().replace('_', ' ')}:")
            lines.append(f"  Recall: {r['recall']*100:.2f}%")
            lines.append(f"  F1-Score: {r['f1']*100:.2f}%")
            lines.append(f"  FPR (Val): {r['fpr_val']*100:.2f}%")
            lines.append("")
        ax_text.text(0.05, 0.95, "\n".join(lines), transform=ax_text.transAxes,
                     verticalalignment="top", fontfamily="monospace", fontsize=9.5)

        plt.tight_layout()
        path = os.path.join(output_dir, f"eval_confusion_{model_type}.png")
        plt.savefig(path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"[FIG] {path}")


def plot_per_class(results: dict, output_dir: str) -> None:
    plt = _ensure_matplotlib()
    classes = ["ul_flood", "dl_flood", "burst", "roq"]
    labels  = ["UL Flood", "DL Flood", "Burst", "RoQ"]

    for model_type in ["lstm", "gru"]:
        configs = ["rule_only", f"{model_type}_only", f"{model_type}_hybrid"]
        x = np.arange(len(configs))
        width = 0.18
        fig, ax = plt.subplots(figsize=(8, 5))

        colors = ['#1f77b4', '#aec7e8', '#ff7f0e', '#ffbb78'] if model_type == "lstm" else ['#2ca02c', '#98df8a', '#d62728', '#ff9896']
        for i, (cls, lbl) in enumerate(zip(classes, labels)):
            vals = []
            for cfg in configs:
                v = results[cfg].get("per_class_recall", {}).get(cls)
                vals.append((v or 0.0) * 100)
            ax.bar(x + i * width, vals, width, label=lbl, color=colors[i])

        ax.set_xticks(x + width * 1.5)
        ax.set_xticklabels([c.upper().replace("_", " ") for c in configs], rotation=0, ha="center")
        ax.set_ylabel("Recall (%)", fontsize=11)
        ax.set_title(f"Per-Class Recall by Configuration ({model_type.upper()}-UE v4)", fontsize=12, fontweight="bold")
        ax.legend(loc="lower left")
        ax.set_ylim(0, 115)
        ax.axhline(100, color="gray", linestyle="--", linewidth=0.8)
        ax.grid(True, alpha=0.3, axis='y')

        plt.tight_layout()
        path = os.path.join(output_dir, f"eval_per_class_{model_type}.png")
        plt.savefig(path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"[FIG] {path}")


def plot_latency(results: dict, output_dir: str) -> None:
    plt = _ensure_matplotlib()
    classes = ["ul_flood", "dl_flood", "burst", "roq"]
    class_labels = ["UL Flood", "DL Flood", "Burst", "RoQ"]

    for model_type in ["lstm", "gru"]:
        configs = ["rule_only", f"{model_type}_only", f"{model_type}_hybrid"]
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))

        # Subplot 1: Detection Latency
        x = np.arange(len(configs))
        width = 0.18
        colors = ['#1f77b4', '#aec7e8', '#ff7f0e', '#ffbb78'] if model_type == "lstm" else ['#2ca02c', '#98df8a', '#d62728', '#ff9896']
        for i, (cls, clbl) in enumerate(zip(classes, class_labels)):
            vals = []
            for cfg in configs:
                lat = results[cfg].get("detection_latency", {}).get(cls, {})
                vals.append(lat.get("mean_s") or 0.0)
            ax1.bar(x + i * width, vals, width, label=clbl, color=colors[i])
        ax1.set_xticks(x + width * 1.5)
        ax1.set_xticklabels([c.upper().replace("_", " ") for c in configs])
        ax1.set_ylabel("Mean Detection Latency (s)", fontsize=11)
        ax1.set_title("Detection Latency by Configuration", fontsize=11.5, fontweight="bold")
        ax1.legend()
        ax1.grid(True, alpha=0.3, axis='y')

        # Subplot 2: Inference Latency
        ml_cfgs = [f"{model_type}_only", f"{model_type}_hybrid"]
        means = []; p95s = []; xlbls = []
        for cfg in ml_cfgs:
            il = results.get(cfg, {}).get("inference_latency")
            if il and il.get("mean_ms") is not None:
                means.append(il["mean_ms"])
                p95s.append(il["p95_ms"])
                xlbls.append(cfg.upper().replace("_", " "))

        if means:
            xp = np.arange(len(xlbls))
            color_mean = '#2196F3' if model_type == "lstm" else '#4CAF50'
            color_p95  = '#FF9800' if model_type == "lstm" else '#FF5722'
            ax2.bar(xp - 0.2, means, 0.35, label="Mean", color=color_mean)
            ax2.bar(xp + 0.2, p95s,  0.35, label="P95", color=color_p95, alpha=0.7)
            ax2.set_xticks(xp)
            ax2.set_xticklabels(xlbls)
            ax2.set_ylabel("Inference Latency (ms / window)", fontsize=11)
            ax2.set_title("Inference Latency", fontsize=11.5, fontweight="bold")
            ax2.legend()
            ax2.grid(True, alpha=0.3, axis='y')
        else:
            ax2.text(0.5, 0.5, "No ML inference data", ha="center", va="center",
                     transform=ax2.transAxes)

        plt.tight_layout()
        path = os.path.join(output_dir, f"eval_latency_{model_type}.png")
        plt.savefig(path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"[FIG] {path}")


def plot_roc(roc_data: dict, results: dict, output_dir: str) -> None:
    plt = _ensure_matplotlib()
    colors = {"lstm": "steelblue", "gru": "darkorange"}
    for model_type in ["lstm", "gru"]:
        fig, ax = plt.subplots(figsize=(7, 6))

        fpr, tpr, auc_v = roc_data[model_type]
        label = f"{model_type.upper()}-UE v4 (AUC={auc_v:.3f})"
        ax.plot(fpr, tpr, color=colors.get(model_type, "gray"), lw=2.2, label=label)

        rule = results.get("rule_only", {})
        if rule:
            cm   = rule["confusion_matrix"]
            fpr_r = cm["fp"] / (cm["fp"] + cm["tn"]) if (cm["fp"] + cm["tn"]) > 0 else 0
            tpr_r = rule["recall"]
            ax.plot(fpr_r, tpr_r, marker="*", markersize=14, color="red",
                    label=f"Rule-only (FPR={fpr_r*100:.1f}%, TPR={tpr_r*100:.1f}%)",
                    linestyle="None")

        ax.plot([0, 1], [0, 1], "k--", lw=0.8, label="Random")
        ax.set_xlabel("False Positive Rate", fontsize=11)
        ax.set_ylabel("True Positive Rate (Recall)", fontsize=11)
        ax.set_title(f"ROC Curve — {model_type.upper()}-UE v4", fontsize=12, fontweight="bold")
        ax.legend(loc="lower right")
        ax.set_xlim([-0.02, 1.02]); ax.set_ylim([-0.02, 1.02])
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        path = os.path.join(output_dir, f"eval_roc_{model_type}.png")
        plt.savefig(path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"[FIG] {path}")


def plot_reconstruction_error_dist(
    val_lstm_mse: np.ndarray,
    lmse_pos: np.ndarray,
    thresh_lstm: float,
    val_gru_mse: np.ndarray,
    gmse_pos: np.ndarray,
    thresh_gru: float,
    output_dir: str,
    train_lstm_mse: np.ndarray = None,
    train_gru_mse: np.ndarray = None,
) -> None:
    plt = _ensure_matplotlib()
    bins = 100

    # 1. LSTM Plot
    fig, ax1 = plt.subplots(figsize=(8, 5.5))
    xlim_lstm = float(np.percentile(np.concatenate([val_lstm_mse, lmse_pos]), 99.0)) * 1.15
    if train_lstm_mse is not None and len(train_lstm_mse) > 0:
        ax1.hist(train_lstm_mse, bins=bins, range=(0, xlim_lstm), color='#2196F3', alpha=0.4, density=True,
                 label=f'Train Normal (n={len(train_lstm_mse):,})')
    ax1.hist(val_lstm_mse, bins=bins, range=(0, xlim_lstm), color='#4CAF50', alpha=0.55, density=True,
             label=f'Val Normal (n={len(val_lstm_mse):,})')
    ax1.hist(lmse_pos, bins=bins, range=(0, xlim_lstm), color='#F44336', alpha=0.5, density=True,
             label=f'Attack/DoS (n={len(lmse_pos):,})')
    ax1.axvline(thresh_lstm, color='#E91E63', linestyle='--', linewidth=2.0,
                label=f'Threshold = {thresh_lstm:.6f}')
    ax1.axvspan(0, thresh_lstm, alpha=0.05, color='#4CAF50')
    ax1.set_xlabel('Weighted Reconstruction Error (MSE)', fontsize=10.5)
    ax1.set_ylabel('Density', fontsize=10.5)
    ax1.set_title('LSTM-UE v4 Reconstruction Error Distribution', fontsize=12, fontweight='bold')
    ax1.legend(fontsize=9, loc='upper right')
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(0, xlim_lstm)

    plt.tight_layout()
    path_lstm = os.path.join(output_dir, "eval_reconstruction_error_lstm.png")
    plt.savefig(path_lstm, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[FIG] {path_lstm}")

    # 2. GRU Plot
    fig, ax2 = plt.subplots(figsize=(8, 5.5))
    xlim_gru = float(np.percentile(np.concatenate([val_gru_mse, gmse_pos]), 99.0)) * 1.15
    if train_gru_mse is not None and len(train_gru_mse) > 0:
        ax2.hist(train_gru_mse, bins=bins, range=(0, xlim_gru), color='#2196F3', alpha=0.4, density=True,
                 label=f'Train Normal (n={len(train_gru_mse):,})')
    ax2.hist(val_gru_mse, bins=bins, range=(0, xlim_gru), color='#FF9800', alpha=0.55, density=True,
             label=f'Val Normal (n={len(val_gru_mse):,})')
    ax2.hist(gmse_pos, bins=bins, range=(0, xlim_gru), color='#F44336', alpha=0.5, density=True,
             label=f'Attack/DoS (n={len(gmse_pos):,})')
    ax2.axvline(thresh_gru, color='#E91E63', linestyle='--', linewidth=2.0,
                label=f'Threshold = {thresh_gru:.6f}')
    ax2.axvspan(0, thresh_gru, alpha=0.05, color='#2196F3')
    ax2.set_xlabel('Weighted Reconstruction Error (MSE)', fontsize=10.5)
    ax2.set_ylabel('Density', fontsize=10.5)
    ax2.set_title('GRU-UE v4 Reconstruction Error Distribution', fontsize=12, fontweight='bold')
    ax2.legend(fontsize=9, loc='upper right')
    ax2.grid(True, alpha=0.3)
    ax2.set_xlim(0, xlim_gru)

    plt.tight_layout()
    path_gru = os.path.join(output_dir, "eval_reconstruction_error_gru.png")
    plt.savefig(path_gru, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[FIG] {path_gru}")


# ── Section 7: CLI + main ─────────────────────────────────────────────────────

def _pool_per_rnti(by_rnti: dict, models: dict, thresh_lstm: float, thresh_gru: float):
    """
    Process each RNTI independently. Returns arrays pooled across all RNTIs.
    """
    lstm_model, lstm_scaler, _ = models["lstm"]
    gru_model,  gru_scaler,  _ = models["gru"]

    all_rule   = []
    all_lstm   = []
    all_gru    = []
    all_labels = []
    all_ts     = []
    all_rntis  = []
    all_lstm_mse = []
    all_gru_mse  = []
    lstm_lats_all = []
    gru_lats_all  = []

    for rnti, rows in sorted(by_rnti.items()):
        N = len(rows)
        if N < SEQ_LEN:
            print(f"  [SKIP] RNTI {rnti}: only {N} rows (< {SEQ_LEN})")
            continue

        add_burst_features_rows(rows)
        X    = extract_features(rows)
        lbls = get_labels(rows)
        ts   = get_timestamps_ms(rows)

        rule_full = run_rule_engine(X)

        lstm_mse, llats = score_ml(lstm_model, lstm_scaler, X)
        gru_mse,  glats = score_ml(gru_model,  gru_scaler,  X)

        # Align everything to timestep indices t >= SEQ_LEN-1
        aligned_rule  = rule_full[SEQ_LEN - 1:]
        aligned_lbls  = lbls[SEQ_LEN - 1:]
        aligned_ts    = ts[SEQ_LEN - 1:]
        aligned_rntis = np.full(len(aligned_lbls), rnti, dtype=np.int32)

        lstm_fires = lstm_mse > thresh_lstm
        gru_fires  = gru_mse  > thresh_gru

        all_rule.append(aligned_rule)
        all_lstm.append(lstm_fires)
        all_gru.append(gru_fires)
        all_labels.append(aligned_lbls)
        all_ts.append(aligned_ts)
        all_rntis.append(aligned_rntis)
        all_lstm_mse.append(lstm_mse)
        all_gru_mse.append(gru_mse)
        lstm_lats_all.extend(llats)
        gru_lats_all.extend(glats)

    if not all_rule:
        raise ValueError(
            "No valid RNTIs found in attack dataset "
            f"(all have fewer than {SEQ_LEN} rows)"
        )

    return {
        "rule_fires":   np.concatenate(all_rule),
        "lstm_fires":   np.concatenate(all_lstm),
        "gru_fires":    np.concatenate(all_gru),
        "labels":       np.concatenate(all_labels),
        "timestamps":   np.concatenate(all_ts),
        "rntis":        np.concatenate(all_rntis),
        "lstm_mse":     np.concatenate(all_lstm_mse),
        "gru_mse":      np.concatenate(all_gru_mse),
        "lstm_latencies": lstm_lats_all,
        "gru_latencies":  gru_lats_all,
    }


def main():
    ap = argparse.ArgumentParser(description="Per-UE IDS evaluation — 5 configs")
    ap.add_argument("--val",    default="csv/dataset_validation_ue_juni.csv")
    ap.add_argument("--attack", default="csv/dataset_attack_ue_juni.csv")
    ap.add_argument("--train",  default=None, help="Optional training dataset (e.g. csv/dataset_training_ue_juni.csv)")
    ap.add_argument("--output", default="results/")
    ap.add_argument("--save-figures", action="store_true")
    
    # Model overrides
    ap.add_argument("--lstm-model", default="models/lstm_ue_v4.pt")
    ap.add_argument("--lstm-scaler", default="models/lstm_ue_v4_scaler.pkl")
    ap.add_argument("--lstm-threshold", default="models/lstm_ue_v4_threshold.json")
    ap.add_argument("--gru-model", default="models/gru_ue_v4.pt")
    ap.add_argument("--gru-scaler", default="models/gru_ue_v4_scaler.pkl")
    ap.add_argument("--gru-threshold", default="models/gru_ue_v4_threshold.json")
    
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # ── Load models ───────────────────────────────────────────────────────────
    models = load_models(
        lstm_pt=args.lstm_model,
        lstm_pkl=args.lstm_scaler,
        lstm_json=args.lstm_threshold,
        gru_pt=args.gru_model,
        gru_pkl=args.gru_scaler,
        gru_json=args.gru_threshold,
    )
    # Thresholds from training JSON are calibrated on uniform MSE; we recompute
    # P97 on weighted validation scores below after the validation loop.

    # ── Optional training dataset ─────────────────────────────────────────────
    train_lstm_mse = None
    train_gru_mse = None
    if args.train:
        print(f"\n[*] Processing optional training dataset: {args.train}")
        train_rows = load_csv(args.train)
        preprocess_rows(train_rows)
        train_by_rnti = split_by_rnti(train_rows)
        train_lstm_mse_all = []
        train_gru_mse_all = []
        for rnti, rows in sorted(train_by_rnti.items()):
            if len(rows) < SEQ_LEN:
                continue
            add_burst_features_rows(rows)
            X = extract_features(rows)
            lstm_mse, _ = score_ml(models["lstm"][0], models["lstm"][1], X)
            gru_mse,  _ = score_ml(models["gru"][0],  models["gru"][1],  X)
            train_lstm_mse_all.append(lstm_mse)
            train_gru_mse_all.append(gru_mse)
        train_lstm_mse = np.concatenate(train_lstm_mse_all) if train_lstm_mse_all else np.array([], dtype=np.float32)
        train_gru_mse  = np.concatenate(train_gru_mse_all)  if train_gru_mse_all  else np.array([], dtype=np.float32)

    # ── Validation dataset (FPR only) ─────────────────────────────────────────
    print(f"\n[1/4] Validation dataset: {args.val}")
    val_rows = load_csv(args.val)
    preprocess_rows(val_rows)
    val_by_rnti = split_by_rnti(val_rows)

    val_rule_fires_all = []
    val_lstm_mse_all   = []
    val_gru_mse_all    = []

    for rnti, rows in sorted(val_by_rnti.items()):
        if len(rows) < SEQ_LEN:
            continue
        add_burst_features_rows(rows)
        X = extract_features(rows)
        rule_f = run_rule_engine(X)[SEQ_LEN - 1:]
        lstm_mse, _ = score_ml(models["lstm"][0], models["lstm"][1], X)
        gru_mse,  _ = score_ml(models["gru"][0],  models["gru"][1],  X)
        val_rule_fires_all.append(rule_f)
        val_lstm_mse_all.append(lstm_mse)
        val_gru_mse_all.append(gru_mse)

    val_rule_fires = np.concatenate(val_rule_fires_all) if val_rule_fires_all else np.array([], dtype=bool)
    val_lstm_mse   = np.concatenate(val_lstm_mse_all)   if val_lstm_mse_all   else np.array([], dtype=np.float32)
    val_gru_mse    = np.concatenate(val_gru_mse_all)    if val_gru_mse_all    else np.array([], dtype=np.float32)

    # Recalibrate thresholds on weighted validation scores (P97)
    thresh_lstm = float(np.percentile(val_lstm_mse, 97)) if len(val_lstm_mse) > 0 else 0.0
    thresh_gru  = float(np.percentile(val_gru_mse,  97)) if len(val_gru_mse)  > 0 else 0.0
    print(f"    Weighted P97 thresholds — lstm:{thresh_lstm:.6f}  gru:{thresh_gru:.6f}")

    n_val = len(val_rule_fires)
    fpr_rule_val = compute_fpr_val(val_rule_fires, n_val)
    fpr_lstm_val = compute_fpr_val(val_lstm_mse > thresh_lstm, n_val)
    fpr_gru_val  = compute_fpr_val(val_gru_mse  > thresh_gru,  n_val)
    fpr_lstm_hyb = compute_fpr_val(val_rule_fires | (val_lstm_mse > thresh_lstm), n_val)
    fpr_gru_hyb  = compute_fpr_val(val_rule_fires | (val_gru_mse  > thresh_gru),  n_val)

    print(f"    Validation windows: {n_val}")
    print(f"    FPR — rule:{fpr_rule_val*100:.2f}%  lstm:{fpr_lstm_val*100:.2f}%  gru:{fpr_gru_val*100:.2f}%")

    # ── Attack dataset ─────────────────────────────────────────────────────────
    print(f"\n[2/4] Attack dataset: {args.attack}")
    atk_rows = load_csv(args.attack)
    preprocess_rows(atk_rows)

    all_atk_labels_raw = get_labels(atk_rows)
    mixed_count = count_mixed_windows(all_atk_labels_raw)
    mixed_pct   = round(mixed_count / max(1, len(all_atk_labels_raw) - SEQ_LEN + 1) * 100, 2)
    print(f"    Mixed windows: {mixed_count} ({mixed_pct}%)")

    atk_by_rnti = split_by_rnti(atk_rows)
    p = _pool_per_rnti(atk_by_rnti, models, thresh_lstm, thresh_gru)

    lbls    = p["labels"]
    ts      = p["timestamps"]
    rntis   = p["rntis"]
    rf      = p["rule_fires"]
    lf      = p["lstm_fires"]
    gf      = p["gru_fires"]
    lhf     = rf | lf
    ghf     = rf | gf
    lmse    = p["lstm_mse"]
    gmse    = p["gru_mse"]

    pos_mask = lbls > 0
    lmse_pos = lmse[pos_mask]
    gmse_pos = gmse[pos_mask]

    # ── Compute metrics ───────────────────────────────────────────────────────
    print("\n[3/4] Computing metrics...")
    inf_lstm = compute_inference_latency(p["lstm_latencies"])
    inf_gru  = compute_inference_latency(p["gru_latencies"])

    lstm_fpr, lstm_tpr, lstm_auc = compute_roc_auc(val_lstm_mse, lmse_pos)
    gru_fpr,  gru_tpr,  gru_auc  = compute_roc_auc(val_gru_mse,  gmse_pos)

    def _entry(fires, fpr_val, inf_lat=None, auc_val=None):
        return build_result_entry(
            cm               = compute_cm(fires, lbls),
            fpr_val          = fpr_val,
            det_latency      = compute_detection_latency(fires, lbls, ts, rntis),
            per_class_recall = compute_per_class_recall(fires, lbls),
            inf_latency      = inf_lat,
            auc_val          = auc_val,
        )

    results = {
        "rule_only":   _entry(rf,  fpr_rule_val),
        "lstm_only":   _entry(lf,  fpr_lstm_val, inf_lstm, lstm_auc),
        "gru_only":    _entry(gf,  fpr_gru_val,  inf_gru,  gru_auc),
        "lstm_hybrid": _entry(lhf, fpr_lstm_hyb, inf_lstm),
        "gru_hybrid":  _entry(ghf, fpr_gru_hyb,  inf_gru),
    }

    n_atk_total  = int(len(lbls))
    n_atk_label0 = int((lbls == 0).sum())
    n_atk_pos    = int(pos_mask.sum())

    metadata = {
        "val_csv":    args.val,
        "attack_csv": args.attack,
        "seq_len":    SEQ_LEN,
        "thresholds": {
            "lstm":   thresh_lstm,
            "gru":    thresh_gru,
            "source": "validation_p99",
        },
        "window_counts": {
            "validation":       n_val,
            "attack_total":     n_atk_total,
            "attack_label0":    n_atk_label0,
            "attack_label_gt0": n_atk_pos,
            "mixed":            mixed_count,
            "mixed_pct":        mixed_pct,
        },
    }

    # ── Output ────────────────────────────────────────────────────────────────
    print("\n[4/4] Output...")
    save_results_json(metadata, results, args.output)
    print_summary_table(results)

    if args.save_figures:
        roc_data = {
            "lstm": (lstm_fpr, lstm_tpr, lstm_auc),
            "gru":  (gru_fpr,  gru_tpr,  gru_auc),
        }
        plot_confusion_matrices(results, args.output)
        plot_per_class(results, args.output)
        plot_latency(results, args.output)
        plot_roc(roc_data, results, args.output)
        plot_reconstruction_error_dist(
            val_lstm_mse = val_lstm_mse,
            lmse_pos     = lmse_pos,
            thresh_lstm  = thresh_lstm,
            val_gru_mse  = val_gru_mse,
            gmse_pos     = gmse_pos,
            thresh_gru   = thresh_gru,
            output_dir   = args.output,
            train_lstm_mse = train_lstm_mse,
            train_gru_mse  = train_gru_mse,
        )


if __name__ == "__main__":
    main()
