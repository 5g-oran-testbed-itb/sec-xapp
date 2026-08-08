#!/usr/bin/env python3
"""Figures for the grouped feature ablation.

Part 1 figures show the deployment checkpoint (seed 42) only. There are no
error bars: a single seed was run, so any interval would imply an uncertainty
estimate that was not measured. Part 2 has its own figure showing the spread
of full_19 across the completed seeds, plotted as individual points rather
than a mean.

Style follows eval_figures/final_hybrid/: Okabe-Ito colorblind-safe palette,
redundant hatching for grayscale print, bars anchored at zero, vector PDF
beside every PNG.

Usage:
  ./venv/bin/python3 plot_grouped_ablation.py
"""
import argparse
import csv as csv_mod
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

MODELS = ("lstm", "gru")
ARCH_LABEL = {"lstm": "LSTM-AE", "gru": "GRU-AE"}
CONFIG_ORDER = ("full_19", "no_burst", "no_temporal_family",
                "no_throughput_family", "no_prb_family", "base_only_4")
CONFIG_LABEL = {
    "full_19": "full_19\n(19)", "no_burst": "no_burst\n(15)",
    "no_temporal_family": "no_temporal\n(9)",
    "no_throughput_family": "no_thp\n(10)",
    "no_prb_family": "no_prb\n(8)", "base_only_4": "base_only_4\n(4)",
}
CLASSES = ("UL Flood", "DL Flood", "Burst", "RoQ")

# Okabe-Ito, validated: worst adjacent CVD separation dE 11.0 (deutan).
CLASS_COLORS = ("#0072B2", "#E69F00", "#009E73", "#D55E00")
MODEL_COLORS = {"lstm": "#0072B2", "gru": "#E69F00"}
HATCHES = ("///", "\\\\\\", "...", "xxx")
INK, MUTED = "#1A1A1A", "#5A5A5A"


def style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#B0B0B0")
    ax.spines["bottom"].set_color("#B0B0B0")
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9.5)


def save(fig, out_dir, stem):
    for ext in ("png", "pdf"):
        path = Path(out_dir) / f"{stem}.{ext}"
        fig.savefig(path, dpi=200, bbox_inches="tight")
        print(f"[FIG] {path}")
    plt.close(fig)


def read(path):
    with open(path, newline="") as handle:
        return list(csv_mod.DictReader(handle))


def num(v):
    return float(v) if v not in ("", None) else None


def present(rows, model, mode="ml_only"):
    """Configs available for this model/mode, in canonical order."""
    have = {r["feature_config"] for r in rows
            if r["model"] == model and r["mode"] == mode}
    return [c for c in CONFIG_ORDER if c in have]


# ── 1-2. Global metrics per architecture ──────────────────────────────────────

def plot_global(summary, model, out_dir):
    configs = present(summary, model)
    if len(configs) < 2:
        print(f"[skip] {model}: hanya {len(configs)} konfigurasi")
        return
    idx = {(r["feature_config"], r["mode"]): r for r in summary
           if r["model"] == model}
    metrics = [("recall", "Recall"), ("f1", "F1-Score"), ("roc_auc", "ROC-AUC")]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5.0))
    x = np.arange(len(configs))
    for ax, (key, label) in zip(axes, metrics):
        vals = [num(idx[(c, "ml_only")][key]) for c in configs]
        scale = 100.0 if key == "roc_auc" else 1.0
        vals = [v * scale if v is not None else 0.0 for v in vals]
        bars = ax.bar(x, vals, 0.62, color=MODEL_COLORS[model], hatch="///",
                      edgecolor="white", linewidth=1.1)
        for bar, v, c in zip(bars, vals, configs):
            d = num(idx[(c, "ml_only")][f"delta_{key}"])
            txt = f"{v:.2f}" if key != "roc_auc" else f"{v/100:.4f}"
            if c != "full_19" and d is not None:
                txt += f"\n({d*scale:+.2f})" if key != "roc_auc" else f"\n({d:+.4f})"
            ax.text(bar.get_x() + bar.get_width() / 2, v + 1.2, txt,
                    ha="center", va="bottom", fontsize=8.2, color=INK)
        ax.set_xticks(x, [CONFIG_LABEL[c] for c in configs], fontsize=8.5)
        ax.set_ylabel(f"{label} (%)" if key != "roc_auc" else label,
                      fontsize=10.5, color=INK)
        ax.set_ylim(0, 118)
        ax.set_title(label, fontsize=11.5, fontweight="bold", color=INK, pad=9)
        style_axes(ax)

    fig.suptitle(
        f"Grouped Feature Ablation — {ARCH_LABEL[model]}, ML-Only (seed 42)",
        fontsize=12.5, fontweight="bold", color=INK, y=1.02)
    fig.tight_layout()
    save(fig, out_dir, f"{model}_ablation_global")


# ── 3-4. Per-class recall per architecture ────────────────────────────────────

def plot_per_class(per_class, model, out_dir):
    rows = [r for r in per_class if r["model"] == model and r["mode"] == "ml_only"]
    configs = [c for c in CONFIG_ORDER
               if c in {r["feature_config"] for r in rows}]
    if len(configs) < 2:
        print(f"[skip] per-class {model}")
        return
    idx = {(r["feature_config"], r["attack_class"]): r for r in rows}

    fig, ax = plt.subplots(figsize=(11, 5.6))
    x = np.arange(len(configs))
    width = 0.20
    for i, cls in enumerate(CLASSES):
        vals = [num(idx[(c, cls)]["recall"]) or 0.0 for c in configs]
        bars = ax.bar(x + (i - 1.5) * width, vals, width * 0.92, label=cls,
                      color=CLASS_COLORS[i], hatch=HATCHES[i],
                      edgecolor="white", linewidth=1.0)
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, v + 1.2, f"{v:.0f}",
                    ha="center", va="bottom", fontsize=7.6, color=INK)
    ax.axhline(85, color="#8A8A8A", lw=1.1, ls="--")
    ax.set_xlim(-0.55, len(configs) - 0.30)
    ax.text(len(configs) - 0.52, 86.5, "target 85%", fontsize=9, color=MUTED)
    ax.set_xticks(x, [CONFIG_LABEL[c] for c in configs], fontsize=9)
    ax.set_ylabel("Recall (%)", fontsize=10.5, color=INK)
    ax.set_ylim(0, 112)
    ax.set_title(f"Per-Class Recall by Feature Configuration — "
                 f"{ARCH_LABEL[model]}, ML-Only (seed 42)",
                 fontsize=12.5, fontweight="bold", color=INK, pad=34)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.005), ncol=4,
              fontsize=9.5, frameon=False)
    style_axes(ax)
    fig.tight_layout()
    save(fig, out_dir, f"{model}_ablation_per_class")


# ── 5. Cross-architecture delta heatmap ───────────────────────────────────────

def plot_delta_heatmap(summary, out_dir):
    idx = {(r["model"], r["feature_config"]): r for r in summary
           if r["mode"] == "ml_only"}
    configs = [c for c in CONFIG_ORDER if c != "full_19"
               and all((m, c) in idx for m in MODELS)]
    if not configs:
        print("[skip] heatmap: butuh kedua arsitektur")
        return
    metrics = [("recall", "Recall"), ("f1", "F1"), ("recall_roq_x", "RoQ"),
               ("recall_burst_x", "Burst")]
    # RoQ/Burst deltas live in per_class; recompute from summary where possible.
    rows_lbl, data = [], []
    for model in MODELS:
        for key, lbl in (("recall", "Recall"), ("f1", "F1")):
            rows_lbl.append(f"{ARCH_LABEL[model]} {lbl}")
            data.append([num(idx[(model, c)][f"delta_{key}"]) or 0.0
                         for c in configs])
    arr = np.array(data)

    fig, ax = plt.subplots(figsize=(1.6 * len(configs) + 3.4, 0.62 * len(rows_lbl) + 2.6))
    lim = max(1.0, float(np.abs(arr).max()))
    im = ax.imshow(arr, cmap="RdBu", vmin=-lim, vmax=lim, aspect="auto")
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            ax.text(j, i, f"{arr[i, j]:+.2f}", ha="center", va="center",
                    fontsize=9.5, fontweight="bold",
                    color="white" if abs(arr[i, j]) > lim * 0.55 else INK)
    ax.set_xticks(range(len(configs)),
                  [CONFIG_LABEL[c].replace("\n", " ") for c in configs],
                  fontsize=9, rotation=15, ha="right")
    ax.set_yticks(range(len(rows_lbl)), rows_lbl, fontsize=9.5)
    ax.set_title("Perubahan terhadap full_19 — ML-Only, seed 42\n"
                 "(negatif = performa turun setelah kelompok fitur dihapus)",
                 fontsize=11.5, fontweight="bold", color=INK, pad=12)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0, colors=MUTED)
    fig.colorbar(im, ax=ax, shrink=0.8, label="poin persentase")
    fig.tight_layout()
    save(fig, out_dir, "cross_model_delta_heatmap")


# ── 6-7. Single-class group contribution ──────────────────────────────────────

def plot_class_contribution(per_class, cls, stem, out_dir):
    rows = [r for r in per_class if r["mode"] == "ml_only"
            and r["attack_class"] == cls]
    idx = {(r["model"], r["feature_config"]): r for r in rows}
    configs = [c for c in CONFIG_ORDER if any((m, c) in idx for m in MODELS)]
    models = [m for m in MODELS if any((m, c) in idx for c in configs)]
    if not configs:
        print(f"[skip] {stem}")
        return

    fig, ax = plt.subplots(figsize=(10.5, 5.2))
    x = np.arange(len(configs))
    width = 0.36 if len(models) > 1 else 0.55
    for i, model in enumerate(models):
        offset = (i - (len(models) - 1) / 2) * width
        vals = [num(idx[(model, c)]["recall"]) if (model, c) in idx else 0.0
                for c in configs]
        bars = ax.bar(x + offset, vals, width * 0.92, label=ARCH_LABEL[model],
                      color=MODEL_COLORS[model], hatch=HATCHES[i],
                      edgecolor="white", linewidth=1.0)
        for bar, v, c in zip(bars, vals, configs):
            d = (num(idx[(model, c)]["delta_vs_full19"])
                 if (model, c) in idx else None)
            t = f"{v:.1f}" + (f"\n({d:+.1f})" if c != "full_19" and d else "")
            ax.text(bar.get_x() + bar.get_width() / 2, v + 1.2, t,
                    ha="center", va="bottom", fontsize=8, color=INK)
    ax.axhline(85, color="#8A8A8A", lw=1.1, ls="--")
    ax.set_xlim(-0.6, len(configs) - 0.35)
    ax.text(len(configs) - 0.57, 86.5, "target 85%", fontsize=9, color=MUTED)
    ax.set_xticks(x, [CONFIG_LABEL[c] for c in configs], fontsize=9)
    ax.set_ylabel(f"Recall {cls} (%)", fontsize=10.5, color=INK)
    ax.set_ylim(0, 112)
    ax.set_title(f"Penurunan Recall {cls} setelah Kelompok Fitur Dihapus — "
                 f"ML-Only, seed 42", fontsize=12.5, fontweight="bold",
                 color=INK, pad=10)
    ax.legend(fontsize=9.5, frameon=True, framealpha=0.95, edgecolor="#DDDDDD")
    style_axes(ax)
    fig.tight_layout()
    save(fig, out_dir, stem)


# ── 8. FPR / recall trade-off ─────────────────────────────────────────────────

def plot_tradeoff(summary, out_dir):
    fig, ax = plt.subplots(figsize=(8.4, 6.2))
    markers = {"lstm": "o", "gru": "s"}
    plotted = False
    for model in MODELS:
        rows = [r for r in summary if r["model"] == model and r["mode"] == "ml_only"]
        if not rows:
            continue
        plotted = True
        rows.sort(key=lambda r: CONFIG_ORDER.index(r["feature_config"]))
        xs = [num(r["fpr_test"]) for r in rows]
        ys = [num(r["recall"]) for r in rows]
        ax.plot(xs, ys, marker=markers[model], markersize=10, lw=1.4, ls=":",
                color=MODEL_COLORS[model], markeredgecolor="white",
                markeredgewidth=1.2, label=ARCH_LABEL[model])
        for xv, yv, r in zip(xs, ys, rows):
            ax.annotate(r["feature_config"].replace("_family", ""), (xv, yv),
                        textcoords="offset points", xytext=(7, 5),
                        fontsize=8, color=MUTED)
    if not plotted:
        print("[skip] tradeoff")
        plt.close(fig)
        return
    ax.set_xlabel("FPR(Attack) (%)", fontsize=10.5, color=INK)
    ax.set_ylabel("ML-Only Recall (%)", fontsize=10.5, color=INK)
    ax.set_title("Trade-off FPR(Attack) vs Recall antar Konfigurasi Fitur\n"
                 "ML-Only, seed 42", fontsize=12.5, fontweight="bold",
                 color=INK, pad=10)
    ax.legend(fontsize=9.5, frameon=True, framealpha=0.95, edgecolor="#DDDDDD")
    style_axes(ax)
    ax.grid(axis="x", color="#D8D8D8", linewidth=0.6, alpha=0.8)
    fig.tight_layout()
    save(fig, out_dir, "fpr_recall_tradeoff")


# ── 9. Part 2 appendix: seed spread ───────────────────────────────────────────

def plot_seed_sensitivity(appendix, out_dir):
    rows = [r for r in appendix if r["mode"] == "ml_only"]
    if len(rows) < 2:
        print("[skip] seed sensitivity")
        return
    models = [m for m in MODELS if any(r["model"] == m for r in rows)]
    metrics = [("recall", "Recall"), ("f1", "F1"), ("recall_roq", "Recall RoQ"),
               ("recall_dl", "Recall DL Flood")]

    fig, axes = plt.subplots(1, len(metrics), figsize=(4.0 * len(metrics), 5.0))
    for ax, (key, label) in zip(axes, metrics):
        for i, model in enumerate(models):
            cells = sorted((r for r in rows if r["model"] == model),
                           key=lambda r: int(r["seed"]))
            vals = [num(r[key]) for r in cells]
            seeds = [int(r["seed"]) for r in cells]
            xs = np.full(len(vals), i, dtype=float) + np.linspace(-0.13, 0.13, len(vals))
            ax.plot(xs, vals, "o", markersize=9, color=MODEL_COLORS[model],
                    markeredgecolor="white", markeredgewidth=1.2)
            for xv, yv, s in zip(xs, vals, seeds):
                ax.annotate(str(s), (xv, yv), textcoords="offset points",
                            xytext=(0, 9), ha="center", fontsize=7.5, color=MUTED)
            if len(vals) > 1:
                ax.plot([i - 0.22, i + 0.22], [min(vals)] * 2, "-",
                        color=MODEL_COLORS[model], lw=1.0, alpha=0.5)
                ax.plot([i - 0.22, i + 0.22], [max(vals)] * 2, "-",
                        color=MODEL_COLORS[model], lw=1.0, alpha=0.5)
                ax.annotate(f"rentang\n{max(vals)-min(vals):.1f}",
                            (i + 0.30, (min(vals) + max(vals)) / 2),
                            fontsize=8, color=INK, va="center")
        ax.set_xticks(range(len(models)), [ARCH_LABEL[m] for m in models],
                      fontsize=10)
        ax.set_xlim(-0.5, len(models) - 0.5 + 0.35)
        ax.set_ylabel(label, fontsize=10.5, color=INK)
        ax.set_title(label, fontsize=11.5, fontweight="bold", color=INK, pad=9)
        style_axes(ax)
    fig.suptitle("Lampiran — Sensitivitas Baseline full_19 terhadap Seed\n"
                 "nilai individual per seed; sengaja tidak dirata-ratakan",
                 fontsize=12.5, fontweight="bold", color=INK, y=1.04)
    fig.tight_layout()
    save(fig, out_dir, "seed_sensitivity_appendix")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="results/grouped_feature_ablation")
    ap.add_argument("--output", default="eval_figures/grouped_feature_ablation")
    args = ap.parse_args()

    in_dir, out_dir = Path(args.input), Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = read(in_dir / "summary_by_model.csv")
    per_class = read(in_dir / "per_class_recall.csv")
    appendix = read(in_dir / "seed_sensitivity_appendix.csv")

    for model in MODELS:
        plot_global(summary, model, out_dir)
        plot_per_class(per_class, model, out_dir)
    plot_delta_heatmap(summary, out_dir)
    plot_class_contribution(per_class, "RoQ", "roq_group_contribution", out_dir)
    plot_class_contribution(per_class, "Burst", "burst_group_contribution", out_dir)
    plot_tradeoff(summary, out_dir)
    plot_seed_sensitivity(appendix, out_dir)


if __name__ == "__main__":
    main()
