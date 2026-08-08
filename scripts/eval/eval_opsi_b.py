#!/usr/bin/env python3
"""Opsi B — recalibrate the decision threshold on the benign validation set.

The threshold is chosen as the lowest Th keeping the Hybrid decision
(rule OR score > Th) below a target false-positive rate measured on
csv/dataset_validation_ue_juni.csv, which is entirely benign. The attack file
is never touched during threshold selection, so FPR(Attack) becomes a measured
out-of-sample quantity instead of a calibration target.

Configuration under evaluation: uniform-MSE trained autoencoders (all loss
weights = 1) with benign-calibrated scoring weights derived from validation
residuals only. No attack-informed (Scheme A) weighting anywhere.

See docs/OPSI-B-REKALIBRASI.md and docs/PROMPT-RIC-REKALIBRASI.md.
"""
import argparse
import json
import os
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from evaluate_per_ue_v2 import (
    GRU_CFG, LSTM_CFG, SEQ_LEN, build_windows, compute_roc_auc, extract_features,
    get_labels, get_timestamps_ms, load_csv, preprocess_rows, run_rule_engine,
    split_by_rnti,
)
from evaluate_scoring_comparison import calibrate_hybrid_threshold, metrics_from_fires
from src.detection.feature_schema_ue import (
    FEATURE_NAMES, FEATURE_WEIGHTS, add_burst_features_rows,
)
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.lstm_autoencoder import LSTMAutoencoder
from src.detection.scoring import (
    make_weight_vec, per_feature_residuals_from_windows, weighted_score,
)

ARCHITECTURES = ("lstm", "gru")          # LSTM first: primary author's model
CLASSES = ("ul_flood", "dl_flood", "burst", "roq")
CLASS_LABELS = ("UL Flood", "DL Flood", "Burst", "RoQ")
LABEL_NAMES = {1: "ul_flood", 2: "dl_flood", 3: "burst", 4: "roq"}
CONFIGS = (("rule_only", "Rule Only"), ("ml_only", "ML-Only"), ("hybrid", "Hybrid"))
TARGETS = (0.05, 0.045, 0.04)            # FPR(Val) calibration frontier
ALERT_COOLDOWN_MS = 30_000               # mirrors sec_ids_ue.h


# ── Data ──────────────────────────────────────────────────────────────────────

def load_variant(model_dir, arch):
    """Load one uniform-loss ablation model and its matching scaler."""
    base = Path(model_dir) / f"{arch}_ue_lossuniform"
    config = GRU_CFG if arch == "gru" else LSTM_CFG
    model_class = GRUAutoencoder if arch == "gru" else LSTMAutoencoder
    model = model_class.load(str(base.with_suffix(".pt")), config)
    with open(f"{base}_scaler.pkl", "rb") as handle:
        scaler = pickle.load(handle)
    return model, scaler


def pooled_data(model, scaler, rows_by_rnti):
    """Per-RNTI windowing pooled into flat arrays, keeping time and UE identity.

    Returns dict of residuals (M,F), labels (M,), rule fires (M,),
    timestamps (M,), rntis (M,). Window i is labelled by its last sample.
    """
    parts = {k: [] for k in ("res", "labels", "rule", "ts", "rnti")}
    for rnti, rows in sorted(rows_by_rnti.items()):
        if len(rows) < SEQ_LEN:
            continue
        add_burst_features_rows(rows)
        X = extract_features(rows)
        wins = build_windows(scaler.transform(X).astype(np.float32), SEQ_LEN)
        if len(wins) == 0:
            continue
        n = len(wins)
        parts["res"].append(per_feature_residuals_from_windows(model, wins))
        parts["labels"].append(get_labels(rows)[SEQ_LEN - 1:][:n])
        parts["rule"].append(run_rule_engine(X)[SEQ_LEN - 1:][:n])
        parts["ts"].append(get_timestamps_ms(rows)[SEQ_LEN - 1:][:n])
        parts["rnti"].append(np.full(n, rnti, dtype=np.int32))
    if not parts["res"]:
        raise ValueError("no RNTI had >= SEQ_LEN rows")
    return {k: np.concatenate(v) for k, v in parts.items()}


# ── Uncertainty reporting ─────────────────────────────────────────────────────

def wilson_ci(k, n, z=1.96):
    """95% Wilson score interval for a binomial proportion, as (low, high)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def episode_durations_s(fires, ts, rntis):
    """Duration of each false-alarm episode, in seconds.

    Needed to explain rows where alerts exceed episodes: an episode longer than
    the 30 s cooldown legitimately emits more than one alert.
    """
    durations = []
    start = None
    for i, (fire, rnti) in enumerate(zip(fires, rntis)):
        new_run = fire and (start is None or rnti != rntis[i - 1])
        if new_run:
            if start is not None:
                durations.append((ts[i - 1] - ts[start]) / 1000.0)
            start = i
        elif not fire and start is not None:
            durations.append((ts[i - 1] - ts[start]) / 1000.0)
            start = None
    if start is not None:
        durations.append((ts[len(fires) - 1] - ts[start]) / 1000.0)
    return [round(d, 1) for d in durations]


def mixed_window_stats(rows_by_rnti):
    """Per-class mixed-window counts using the same per-RNTI windowing as scoring."""
    per_class = {}
    total = mixed_total = 0
    for _rnti, rows in sorted(rows_by_rnti.items()):
        if len(rows) < SEQ_LEN:
            continue
        labels = get_labels(rows)
        for i in range(len(labels) - SEQ_LEN + 1):
            window = labels[i:i + SEQ_LEN]
            name = LABEL_NAMES.get(int(window[-1]), "benign")
            entry = per_class.setdefault(name, {"windows": 0, "mixed": 0})
            entry["windows"] += 1
            total += 1
            if len(np.unique(window)) > 1:
                entry["mixed"] += 1
                mixed_total += 1
    return {
        "windowing": "per_rnti",
        "windows_total": total,
        "mixed_total": mixed_total,
        "mixed_pct": round(mixed_total / max(1, total) * 100, 2),
        "pure_pct": round((total - mixed_total) / max(1, total) * 100, 2),
        "per_class": per_class,
    }


def count_episodes(fires, rntis):
    """Number of separate false-alarm runs (consecutive fired windows, per UE).

    Overlapping windows share 29/30 samples, so a single event produces a long
    run of fired windows. Episodes count events; window counts do not.
    """
    episodes = 0
    prev_fire = False
    prev_rnti = None
    for fire, rnti in zip(fires, rntis):
        if fire and not (prev_fire and rnti == prev_rnti):
            episodes += 1
        prev_fire, prev_rnti = bool(fire), rnti
    return episodes


def cooldown_alerts(fires, ts, rntis, cooldown_ms=ALERT_COOLDOWN_MS):
    """Alerts surviving the xApp's per-UE 30 s alert cooldown."""
    alerts = 0
    last = {}
    for fire, t, rnti in zip(fires, ts, rntis):
        if not fire:
            continue
        if rnti not in last or (t - last[rnti]) >= cooldown_ms:
            alerts += 1
            last[rnti] = t
    return alerts


WINDOWS_PER_HOUR = 3600.0   # per-UE window cadence is exactly 1 Hz


def monitored_ue_hours(n_windows):
    """Benign exposure time, in UE-hours, for a false-alarm rate.

    Definition: one window per UE per second, so exposure = n_windows / 3600.
    It is applied to the exact window set the FPR is computed on — all 1772
    validation windows, and the 5723 label==0 windows of the attack file. A
    false alarm can only occur while traffic is benign, so benign window-time
    is the correct denominator; attack windows are not exposure.

    Using timestamp span per UE instead is wrong here: on the attack file the
    span silently includes the attack periods that were excluded from the
    numerator.
    """
    return n_windows / WINDOWS_PER_HOUR


def fpr_report(fires, ts, rntis, set_name):
    """FPR plus the uncertainty context every FPR in the thesis must carry."""
    n = len(fires)
    k = int(fires.sum())
    low, high = wilson_ci(k, n)
    alerts = cooldown_alerts(fires, ts, rntis)
    hours = monitored_ue_hours(n)
    return {
        "set": set_name,
        "windows_fired": k,
        "windows_total": n,
        "fpr": round(k / n, 6) if n else 0.0,
        "ci95_low": round(low, 6),
        "ci95_high": round(high, 6),
        "episodes": count_episodes(fires, rntis),
        "episode_durations_s": episode_durations_s(fires, ts, rntis),
        "alerts_after_cooldown": alerts,
        "monitored_ue_hours": round(hours, 4),
        "monitored_time_basis": "benign_windows / 3600",
        # An episode longer than the 30 s cooldown yields more than one alert,
        # so alerts may exceed the episode count.
        "alerts_per_ue_hour": round(alerts / hours, 2) if hours > 0 else None,
    }


# ── Evaluation ────────────────────────────────────────────────────────────────

def evaluate_at_target(val, atk, val_ml, atk_ml, target):
    """Calibrate Th on the benign validation set, then measure on the attack file."""
    thr = calibrate_hybrid_threshold(val_ml, val["rule"], target)
    finite = np.isfinite(thr)

    atk_ml_fires = atk_ml > thr
    val_ml_fires = val_ml > thr
    neg = atk["labels"] == 0
    _, _, ml_auc = compute_roc_auc(atk_ml[neg], atk_ml[atk["labels"] > 0])

    fires = {
        "rule_only": (atk["rule"], val["rule"]),
        "ml_only": (atk_ml_fires, val_ml_fires),
        "hybrid": (atk["rule"] | atk_ml_fires, val["rule"] | val_ml_fires),
    }

    result = {
        "target_fpr_val": target,
        "threshold": None if not finite else round(float(thr), 6),
        "threshold_pct_val": round(float((val_ml <= thr).mean() * 100), 2) if finite else 100.0,
        "threshold_pct_attack_benign": (
            round(float((atk_ml[neg] <= thr).mean() * 100), 2) if finite else 100.0
        ),
    }
    for key, (f_atk, f_val) in fires.items():
        entry = metrics_from_fires(f_atk, atk["labels"], f_val,
                                   auc=(ml_auc if key == "ml_only" else None))
        entry["fpr_attack_detail"] = fpr_report(f_atk[neg], atk["ts"][neg],
                                                atk["rnti"][neg], "attack")
        entry["fpr_val_detail"] = fpr_report(f_val, val["ts"], val["rnti"], "validation")
        result[key] = entry
    return result


def run(args):
    print(f"[*] Validation : {args.val}")
    val_rows = load_csv(args.val)
    preprocess_rows(val_rows)
    val_by_rnti = split_by_rnti(val_rows)

    print(f"[*] Attack     : {args.attack}")
    atk_rows = load_csv(args.attack)
    preprocess_rows(atk_rows)
    atk_by_rnti = split_by_rnti(atk_rows)

    mixed = mixed_window_stats(atk_by_rnti)
    print(f"[*] Mixed windows (per-RNTI): {mixed['mixed_total']}/{mixed['windows_total']} "
          f"= {mixed['mixed_pct']}%  ({mixed['pure_pct']}% pure single-label)")

    results = {}
    roc = {}
    for arch in ARCHITECTURES:
        print(f"\n[*] {arch.upper()} — uniform-loss model from {args.model_dir}")
        model, scaler = load_variant(args.model_dir, arch)
        val = pooled_data(model, scaler, val_by_rnti)
        atk = pooled_data(model, scaler, atk_by_rnti)

        # Benign-calibrated weights: validation residuals only, no attack labels.
        w = make_weight_vec("benign", FEATURE_NAMES, FEATURE_WEIGHTS,
                            benign_residuals=val["res"])
        val_ml = weighted_score(val["res"], w)
        atk_ml = weighted_score(atk["res"], w)

        neg = atk["labels"] == 0
        fpr_arr, tpr_arr, auc_v = compute_roc_auc(atk_ml[neg], atk_ml[atk["labels"] > 0])
        roc[arch] = {"fpr": fpr_arr.tolist(), "tpr": tpr_arr.tolist(), "auc": auc_v}

        results[arch] = {
            "windows": {
                "validation": int(len(val["labels"])),
                "attack_total": int(len(atk["labels"])),
                "attack_benign": int(neg.sum()),
                "attack_positive": int((atk["labels"] > 0).sum()),
            },
            "weights": {n: round(float(x), 4) for n, x in zip(FEATURE_NAMES, w)},
            "targets": {},
        }
        for target in TARGETS:
            r = evaluate_at_target(val, atk, val_ml, atk_ml, target)
            results[arch]["targets"][f"{target:.3f}"] = r
            h = r["hybrid"]
            print(f"    target FPR(Val) {target*100:.1f}%  Th={r['threshold']}  "
                  f"P{r['threshold_pct_val']:.2f}(val)  "
                  f"Hybrid recall={h['recall']*100:.2f}%  F1={h['f1']*100:.2f}%  "
                  f"FPR(Attack)={h['fpr_attack']*100:.2f}%  FPR(Val)={h['fpr_val']*100:.2f}%")

    return results, roc, mixed


# ── Sanity check ──────────────────────────────────────────────────────────────

SANITY = {
    "recall": 0.8578, "precision": 0.9751, "fpr_attack": 0.0086, "fpr_val": 0.0293,
    "per_class": {"ul_flood": 0.9718, "dl_flood": 0.9676, "burst": 0.9503, "roq": 0.6528},
}


def check_sanity(results):
    """Rule-Only is threshold-independent, so it must reproduce exactly."""
    failures = []
    for arch in ARCHITECTURES:
        for target_key, target_result in results[arch]["targets"].items():
            r = target_result["rule_only"]
            for metric, expected in SANITY.items():
                if metric == "per_class":
                    for cls, exp in expected.items():
                        got = r["per_class_recall"][cls]
                        if abs(got - exp) > 5e-5:
                            failures.append(f"{arch}@{target_key} {cls}: {got} != {exp}")
                elif abs(r[metric] - expected) > 5e-5:
                    failures.append(f"{arch}@{target_key} {metric}: {r[metric]} != {expected}")
    return failures


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", default="models/ablation_loss")
    ap.add_argument("--val", default="csv/dataset_validation_ue_juni.csv")
    ap.add_argument("--attack", default="csv/dataset_attack_ue_juni.csv")
    ap.add_argument("--output", type=Path, default=Path("results/opsi_b"))
    args = ap.parse_args()

    results, roc, mixed = run(args)

    print("\n[*] Rule-Only sanity check...")
    failures = check_sanity(results)
    if failures:
        print("[FAIL] Rule-Only did not reproduce the verified baseline:")
        for f in failures:
            print(f"       {f}")
        raise SystemExit(1)
    print("    PASS — 85.78% recall / 0.86% FPR(Attack) / 2.93% FPR(Val) reproduced exactly")

    args.output.mkdir(parents=True, exist_ok=True)
    payload = {
        "experiment": "opsi_b_validation_calibrated_threshold",
        "training_loss": "uniform",
        "scoring": "benign_calibrated",
        "seed": 42,
        "calibration_set": args.val,
        "evaluation_set": args.attack,
        "targets_fpr_val": list(TARGETS),
        "alert_cooldown_ms": ALERT_COOLDOWN_MS,
        "mixed_windows": mixed,
        "results": results,
        "roc": roc,
    }
    path = args.output / "opsi_b.json"
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2)
    print(f"\n[JSON] {path}")


if __name__ == "__main__":
    main()
