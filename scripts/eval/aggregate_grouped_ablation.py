#!/usr/bin/env python3
"""Aggregate the grouped feature ablation into two strictly separated parts.

PART 1 — Ablation of the deployment checkpoint (seed 42 only).
  Seed 42 was fixed before the experiment, is the pipeline default, and is the
  checkpoint deployed on the xApp. The ablation therefore describes the
  sensitivity of the deployed configuration to each feature group. Values are
  reported per cell; nothing is averaged, because only one seed was run.

PART 2 — Baseline seed sensitivity (appendix).
  full_19 across the seeds that were completed before the scope was reduced.
  Reported as individual values and ranges only. These rows never enter the
  Part 1 contribution figures.

Terminology enforced in generated text:
  used     — "kontribusi empiris pada checkpoint deployment",
             "penurunan performa setelah kelompok fitur dihapus",
             "sensitivitas konfigurasi seed 42 terhadap kelompok fitur"
  avoided  — "fitur paling penting", "konsisten lintas seed",
             "kontribusi kausal", "robust terhadap inisialisasi"

Usage:
  ./venv/bin/python3 aggregate_grouped_ablation.py
"""
import argparse
import csv as csv_mod
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.detection.feature_groups import CONFIGS, DROPPED, kept_features
from src.detection.feature_schema_ue import FEATURE_NAMES

MODELS = ("lstm", "gru")
MODES = ("ml_only", "hybrid")
CLASSES = ("ul", "dl", "burst", "roq")
CLASS_LABELS = {"ul": "UL Flood", "dl": "DL Flood", "burst": "Burst", "roq": "RoQ"}
METRICS = ("recall", "precision", "f1", "roc_auc", "fpr_validation", "fpr_test")
BASELINE = "full_19"
ABLATION_SEED = 42


def read_metrics(path):
    rows, failed = [], []
    if not Path(path).exists():
        return rows, failed
    with open(path, newline="") as handle:
        for r in csv_mod.DictReader(handle):
            if r.get("status", "ok") != "ok":
                failed.append(r)
                continue
            for k in list(r):
                if k in ("model", "feature_config", "mode", "status"):
                    continue
                r[k] = float(r[k]) if r[k] not in ("", None) else None
            rows.append(r)
    return rows, failed


def r4(x):
    return None if x is None else round(x, 4)


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="") as handle:
        writer = csv_mod.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[CSV] {path}  ({len(rows)} baris)")


def index_by(rows, seed=None):
    out = {}
    for r in rows:
        if seed is not None and int(r["seed"]) != seed:
            continue
        out[(r["model"], r["feature_config"], r["mode"])] = r
    return out


# ── Part 1: deployment-checkpoint ablation (seed 42) ──────────────────────────

def summary_by_model(idx, out_dir):
    """Per-cell metrics with the change relative to full_19 seed 42."""
    fields = (["model", "feature_config", "mode", "seed", "n_features", "threshold"]
              + list(METRICS) + [f"delta_{k}" for k in METRICS])
    out = []
    for model in MODELS:
        for config in CONFIGS:
            for mode in MODES:
                r = idx.get((model, config, mode))
                base = idx.get((model, BASELINE, mode))
                if not r:
                    continue
                row = {"model": model, "feature_config": config, "mode": mode,
                       "seed": ABLATION_SEED,
                       "n_features": int(r["n_features"]),
                       "threshold": r["threshold"]}
                for k in METRICS:
                    row[k] = r4(r[k])
                    b = base[k] if base else None
                    row[f"delta_{k}"] = (r4(r[k] - b)
                                         if (r[k] is not None and b is not None)
                                         else None)
                out.append(row)
    write_csv(out_dir / "summary_by_model.csv", fields, out)
    return out


def cross_model_comparison(idx, out_dir):
    """LSTM against BiGRU on the same configuration — seed 42 only."""
    fields = ["feature_config", "mode", "metric", "lstm", "gru", "lstm_minus_gru",
              "lstm_delta_vs_full19", "gru_delta_vs_full19", "same_direction"]
    out = []
    for config in CONFIGS:
        for mode in MODES:
            l, g = idx.get(("lstm", config, mode)), idx.get(("gru", config, mode))
            lb, gb = idx.get(("lstm", BASELINE, mode)), idx.get(("gru", BASELINE, mode))
            if not (l and g):
                continue
            for k in METRICS:
                ld = (l[k] - lb[k]) if (lb and l[k] is not None and lb[k] is not None) else None
                gd = (g[k] - gb[k]) if (gb and g[k] is not None and gb[k] is not None) else None
                same = ""
                if config != BASELINE and ld is not None and gd is not None:
                    same = "yes" if (ld >= 0) == (gd >= 0) else "no"
                out.append({
                    "feature_config": config, "mode": mode, "metric": k,
                    "lstm": r4(l[k]), "gru": r4(g[k]),
                    "lstm_minus_gru": (r4(l[k] - g[k])
                                       if (l[k] is not None and g[k] is not None)
                                       else None),
                    "lstm_delta_vs_full19": r4(ld),
                    "gru_delta_vs_full19": r4(gd),
                    "same_direction": same,
                })
    write_csv(out_dir / "cross_model_comparison.csv", fields, out)
    return out


def per_class_recall(idx, out_dir):
    fields = ["model", "feature_config", "mode", "seed", "attack_class",
              "recall", "delta_vs_full19"]
    out = []
    for model in MODELS:
        for config in CONFIGS:
            for mode in MODES:
                r = idx.get((model, config, mode))
                base = idx.get((model, BASELINE, mode))
                if not r:
                    continue
                for cls in CLASSES:
                    key = f"recall_{cls}"
                    b = base[key] if base else None
                    out.append({
                        "model": model, "feature_config": config, "mode": mode,
                        "seed": ABLATION_SEED, "attack_class": CLASS_LABELS[cls],
                        "recall": r4(r[key]),
                        "delta_vs_full19": (r4(r[key] - b)
                                            if (r[key] is not None and b is not None)
                                            else None),
                    })
    write_csv(out_dir / "per_class_recall.csv", fields, out)
    return out


# ── Part 2: baseline seed-sensitivity appendix ────────────────────────────────

def seed_sensitivity(rows, out_dir):
    """full_19 across every completed seed. Individual values and ranges only."""
    fields = (["model", "feature_config", "mode", "seed", "threshold", "recall",
               "precision", "f1", "roc_auc", "fpr_validation", "fpr_test"]
              + [f"recall_{c}" for c in CLASSES])
    out = []
    for r in sorted(rows, key=lambda r: (r["model"], r["feature_config"],
                                         int(r["seed"]), r["mode"])):
        if r["feature_config"] != BASELINE:
            continue
        out.append({
            "model": r["model"], "feature_config": r["feature_config"],
            "mode": r["mode"], "seed": int(r["seed"]), "threshold": r["threshold"],
            **{k: r4(r[k]) for k in ("recall", "precision", "f1", "roc_auc",
                                     "fpr_validation", "fpr_test")},
            **{f"recall_{c}": r4(r[f"recall_{c}"]) for c in CLASSES},
        })
    write_csv(out_dir / "seed_sensitivity_appendix.csv", fields, out)

    # Ranges — deliberately not means, since these are not ablation results.
    rfields = ["model", "mode", "metric", "n_seeds", "seeds", "min", "max", "range",
               "seed42_value"]
    ranges = []
    for model in MODELS:
        for mode in MODES:
            cells = [r for r in out if r["model"] == model and r["mode"] == mode]
            if len(cells) < 2:
                continue
            seeds = sorted(c["seed"] for c in cells)
            for k in ("recall", "f1", "roc_auc") + tuple(f"recall_{c}" for c in CLASSES):
                vals = [c[k] for c in cells if c[k] is not None]
                if not vals:
                    continue
                s42 = next((c[k] for c in cells if c["seed"] == ABLATION_SEED), None)
                ranges.append({
                    "model": model, "mode": mode, "metric": k,
                    "n_seeds": len(vals), "seeds": " ".join(map(str, seeds)),
                    "min": r4(min(vals)), "max": r4(max(vals)),
                    "range": r4(max(vals) - min(vals)), "seed42_value": s42,
                })
    write_csv(out_dir / "seed_sensitivity_ranges.csv", rfields, ranges)
    return out, ranges


# ── Shared tables ─────────────────────────────────────────────────────────────

def thresholds_by_seed(rows, out_dir):
    fields = ["model", "feature_config", "seed", "threshold", "n_features", "part"]
    seen, out = set(), []
    for r in rows:
        key = (r["model"], r["feature_config"], int(r["seed"]))
        if key in seen:
            continue
        seen.add(key)
        out.append({"model": r["model"], "feature_config": r["feature_config"],
                    "seed": int(r["seed"]), "threshold": r["threshold"],
                    "n_features": int(r["n_features"]),
                    "part": "1_ablation" if int(r["seed"]) == ABLATION_SEED
                            else "2_seed_sensitivity"})
    out.sort(key=lambda r: (r["part"], r["model"], r["feature_config"], r["seed"]))
    write_csv(out_dir / "thresholds_by_seed.csv", fields, out)
    return out


def training_runtime(rows, out_dir):
    fields = ["model", "feature_config", "seed", "n_features", "parameter_count",
              "best_epoch", "training_time_s", "training_time_min",
              "inference_time_ms", "part"]
    seen, out = set(), []
    for r in rows:
        key = (r["model"], r["feature_config"], int(r["seed"]))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "model": r["model"], "feature_config": r["feature_config"],
            "seed": int(r["seed"]), "n_features": int(r["n_features"]),
            "parameter_count": int(r["parameter_count"]),
            "best_epoch": int(r["best_epoch"]),
            "training_time_s": r["training_time_s"],
            "training_time_min": r4(r["training_time_s"] / 60.0),
            "inference_time_ms": r["inference_time_ms"],
            "part": "1_ablation" if int(r["seed"]) == ABLATION_SEED
                    else "2_seed_sensitivity",
        })
    out.sort(key=lambda r: (r["part"], r["model"], r["feature_config"], r["seed"]))
    write_csv(out_dir / "training_runtime.csv", fields, out)
    return out


def dataset_manifest(args, rows, failed, out_dir):
    def digest(path):
        h = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    datasets = {}
    for role, path in (("training", args.train), ("validation", args.val),
                       ("attack", args.attack)):
        p = Path(path)
        with open(p) as handle:
            n_rows = sum(1 for _ in handle) - 1
        datasets[role] = {"path": str(p), "rows": n_rows,
                          "bytes": p.stat().st_size, "sha256_16": digest(p)}

    cells = {(r["model"], r["feature_config"], int(r["seed"])) for r in rows}
    manifest = {
        "experiment": "grouped_feature_ablation",
        "scope": {
            "part_1_ablation": {
                "seed": ABLATION_SEED,
                "rationale": "seed 42 ditetapkan sebelum eksperimen, merupakan "
                             "seed default pipeline, dan checkpoint-nya adalah "
                             "model yang diterapkan pada xApp",
                "cells_expected": len(MODELS) * len(CONFIGS),
                "cells_completed": len([c for c in cells if c[2] == ABLATION_SEED]),
                "averaging": "tidak dilakukan — hanya satu seed",
            },
            "part_2_seed_sensitivity": {
                "config": BASELINE,
                "seeds_completed": sorted({c[2] for c in cells
                                           if c[1] == BASELINE}),
                "role": "lampiran; tidak masuk perhitungan kontribusi ablasi",
            },
        },
        "architectures": {"lstm": "LSTM-Autoencoder, unidirectional",
                          "gru": "GRU-Autoencoder, bidirectional (BiGRU)"},
        "feature_configs": {c: {"n_kept": len(kept_features(c)),
                                "kept": kept_features(c),
                                "dropped": DROPPED[c]} for c in CONFIGS},
        "feature_schema": FEATURE_NAMES,
        "cells_failed": len(failed),
        "protocol": {
            "seq_len": 30,
            "loss_weights": "uniform",
            "scoring": "benign_calibrated, diturunkan ulang per konfigurasi",
            "threshold": "dikalibrasi ulang hanya pada validasi benign, "
                         "Hybrid FPR(Val) <= 5%",
            "rule_branch": "TIDAK diablasi — selalu dievaluasi pada 19 fitur penuh",
            "primary_result": "ml_only",
            "secondary_result": "hybrid",
            "test_set_use": "dibuka hanya setelah model dan ambang dibekukan",
        },
        "datasets": datasets,
    }
    path = out_dir / "dataset_manifest.json"
    with open(path, "w") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
    print(f"[JSON] {path}")
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="results/grouped_feature_ablation")
    ap.add_argument("--train", default="csv/dataset_training_ue_juni.csv")
    ap.add_argument("--val", default="csv/dataset_validation_ue_juni.csv")
    ap.add_argument("--attack", default="csv/dataset_attack_ue_juni.csv")
    args = ap.parse_args()

    out_dir = Path(args.input)
    rows, failed = read_metrics(out_dir / "metrics_by_seed.csv")
    if not rows:
        raise SystemExit(f"tidak ada baris valid di {out_dir/'metrics_by_seed.csv'}")

    idx42 = index_by(rows, seed=ABLATION_SEED)
    n42 = len({(k[0], k[1]) for k in idx42})
    print(f"[*] Bagian 1 (seed 42): {n42}/{len(MODELS)*len(CONFIGS)} sel")
    print(f"[*] Bagian 2 (sensitivitas seed): "
          f"{sorted({int(r['seed']) for r in rows if r['feature_config']==BASELINE})}")

    summary_by_model(idx42, out_dir)
    cross_model_comparison(idx42, out_dir)
    per_class_recall(idx42, out_dir)
    seed_sensitivity(rows, out_dir)
    thresholds_by_seed(rows, out_dir)
    training_runtime(rows, out_dir)
    dataset_manifest(args, rows, failed, out_dir)

    if failed:
        print(f"\n[!] {len(failed)} sel gagal — dicatat, tidak dibuang:")
        for r in failed:
            print(f"    {r['model']}/{r['feature_config']}/seed{r['seed']}: "
                  f"{r.get('status','')[:110]}")


if __name__ == "__main__":
    main()
