#!/usr/bin/env python3
"""Publication figures for the Opsi B validation-calibrated threshold run.

Style follows eval_figures/loss_ablation/: Okabe-Ito colorblind-safe palette,
redundant hatch patterns for grayscale printing, bars anchored at zero, no error
bars (single deterministic seed), vector PDF beside every PNG.
"""
import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ARCHITECTURES = ("lstm", "gru")
CONFIGS = (("rule_only", "Rule-Only"), ("ml_only", "ML-Only"), ("hybrid", "Hybrid"))
CLASSES = ("ul_flood", "dl_flood", "burst", "roq")
CLASS_LABELS = ("UL Flood", "DL Flood", "Burst", "RoQ")

# Okabe-Ito, validated: worst adjacent CVD separation dE 11.0 (deutan).
CLASS_COLORS = ("#0072B2", "#E69F00", "#009E73", "#D55E00")
CONFIG_COLORS = ("#0072B2", "#E69F00", "#009E73")
HATCHES = ("///", "\\\\\\", "...", "xxx")
MAIN_TARGET = "0.050"

INK = "#1A1A1A"
MUTED = "#5A5A5A"


def style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#B0B0B0")
    ax.spines["bottom"].set_color("#B0B0B0")
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9.5)


def save(fig, out_dir, stem):
    """Write PNG and vector PDF under the same stem."""
    for ext in ("png", "pdf"):
        path = Path(out_dir) / f"{stem}.{ext}"
        fig.savefig(path, dpi=200, bbox_inches="tight")
        print(f"[FIG] {path}")
    plt.close(fig)


# ── 1. Confusion matrices ─────────────────────────────────────────────────────

def plot_confusion(result, arch, out_dir):
    """Three panels — Rule-Only, ML-Only, Hybrid — on the attack file."""
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.9))
    for ax, (key, label) in zip(axes, CONFIGS):
        entry = result[key]
        c = entry["confusion"]
        cm = np.array([[c["tn"], c["fp"]], [c["fn"], c["tp"]]])

        ax.imshow(cm, cmap="Blues", interpolation="nearest",
                  vmin=0, vmax=cm.max())
        ax.set_title(label, fontsize=12, fontweight="bold", color=INK, pad=9)
        ax.set_xticks([0, 1], ["Normal", "Anomaly"], fontsize=10)
        ax.set_yticks([0, 1], ["Normal", "Anomaly"], fontsize=10, rotation=90, va="center")
        ax.set_xlabel("Predicted", fontsize=10, color=MUTED)
        ax.set_ylabel("Actual", fontsize=10, color=MUTED)
        for i in range(2):
            for j in range(2):
                ax.text(j, i, f"{cm[i, j]:,}", ha="center", va="center",
                        fontsize=15, fontweight="bold",
                        color="white" if cm[i, j] > cm.max() * 0.55 else INK)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(length=0, colors=MUTED)

        note = (f"Recall {entry['recall']*100:.2f}%   F1 {entry['f1']*100:.2f}%\n"
                f"FPR(Attack) {entry['fpr_attack']*100:.2f}%   "
                f"FPR(Val) {entry['fpr_val']*100:.2f}%")
        ax.text(0.5, -0.20, note, transform=ax.transAxes, ha="center", va="top",
                fontsize=10, color=INK, linespacing=1.5)

    fig.suptitle(
        f"Confusion Matrix — {arch.upper()}-AE "
        f"(Th = {result['threshold']:.6f}, FPR(Val) ≤ 5%)",
        fontsize=12.5, fontweight="bold", color=INK, y=1.03)
    fig.tight_layout()
    save(fig, out_dir, f"eval_confusion_{arch}")


# ── 2. ROC curves ─────────────────────────────────────────────────────────────

def _draw_roc(ax, roc, result, arch, legend):
    fpr = np.asarray(roc["fpr"])
    tpr = np.asarray(roc["tpr"])
    rule = result["rule_only"]
    ml = result["ml_only"]

    ax.plot([0, 1], [0, 1], color="#9A9A9A", lw=1.0, ls=":",
            label="Random" if legend else None)
    ax.plot(fpr, tpr, color=CONFIG_COLORS[0], lw=2.0,
            label=(f"{arch.upper()}-AE (AUC = {roc['auc']:.4f})"
                   if legend else None))
    ax.plot(rule["fpr_attack"], rule["recall"], marker="*", markersize=17,
            color=CONFIG_COLORS[2], linestyle="None", markeredgecolor="white",
            markeredgewidth=1.2,
            label=(f"Rule-Only op. point "
                   f"({rule['fpr_attack']*100:.2f}%, {rule['recall']*100:.2f}%)"
                   if legend else None))
    ax.plot(ml["fpr_attack"], ml["recall"], marker="o", markersize=11,
            color=CONFIG_COLORS[1], linestyle="None", markeredgecolor="white",
            markeredgewidth=1.2,
            label=(f"ML op. point at Th "
                   f"({ml['fpr_attack']*100:.2f}%, {ml['recall']*100:.2f}%)"
                   if legend else None))


def plot_roc(roc, result, arch, out_dir):
    """Full ROC plus a zoom on the operating region, where every point sits."""
    fig, (ax, ax_zoom) = plt.subplots(1, 2, figsize=(12.6, 5.8))

    _draw_roc(ax, roc, result, arch, legend=True)
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.set_title("Full range", fontsize=11.5, fontweight="bold", color=INK, pad=9)
    ax.legend(loc="lower right", fontsize=9, frameon=True, framealpha=0.95,
              edgecolor="#DDDDDD")

    _draw_roc(ax_zoom, roc, result, arch, legend=False)
    ax_zoom.set_xlim(-0.002, 0.06)
    ax_zoom.set_ylim(0.80, 1.005)
    ax_zoom.set_title("Zoom on the operating region", fontsize=11.5,
                      fontweight="bold", color=INK, pad=9)

    for axis in (ax, ax_zoom):
        axis.set_xlabel("FPR(Attack) — benign windows of the attack file",
                        fontsize=10.5, color=INK)
        axis.set_ylabel("True Positive Rate (Recall)", fontsize=10.5, color=INK)
        style_axes(axis)
        axis.grid(axis="x", color="#D8D8D8", linewidth=0.6, alpha=0.8)

    fig.suptitle(f"ROC Curve — {arch.upper()}-AE",
                 fontsize=12.5, fontweight="bold", color=INK, y=1.01)
    fig.tight_layout()
    save(fig, out_dir, f"eval_roc_{arch}")


# ── 3. Per-class recall ───────────────────────────────────────────────────────

def plot_per_class(result, arch, out_dir):
    fig, ax = plt.subplots(figsize=(9, 5.4))
    x = np.arange(len(CONFIGS))
    width = 0.20

    for i, (cls, label) in enumerate(zip(CLASSES, CLASS_LABELS)):
        values = [result[key]["per_class_recall"][cls] * 100 for key, _ in CONFIGS]
        offset = (i - 1.5) * width
        bars = ax.bar(x + offset, values, width * 0.92, label=label,
                      color=CLASS_COLORS[i], hatch=HATCHES[i],
                      edgecolor="white", linewidth=1.1)
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + 1.2, f"{value:.1f}",
                    ha="center", va="bottom", fontsize=8.5, color=INK)

    ax.axhline(85, color="#8A8A8A", lw=1.1, ls="--")
    ax.set_xlim(-0.55, len(CONFIGS) - 0.10)
    ax.text(len(CONFIGS) - 0.47, 86.4, "target 85%", fontsize=9, color=MUTED,
            ha="left", va="bottom")
    ax.set_xticks(x, [label for _, label in CONFIGS], fontsize=10.5)
    ax.set_ylabel("Recall (%)", fontsize=10.5, color=INK)
    ax.set_ylim(0, 108)
    ax.set_title(
        f"Per-Class Recall by Configuration — {arch.upper()}-AE "
        f"(Th = {result['threshold']:.6f})",
        fontsize=12.5, fontweight="bold", color=INK, pad=34)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.005), fontsize=9.5, ncol=4,
              frameon=False)
    style_axes(ax)
    fig.tight_layout()
    save(fig, out_dir, f"eval_per_class_{arch}")


# ── 4. Calibration frontier ───────────────────────────────────────────────────

def plot_frontier(results, targets, out_dir):
    """Two panels — one measure each, never two y-scales on one axis."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 5.0))
    x = np.arange(len(targets))
    markers = ("o", "s")
    styles = ("-", "--")
    xticklabels = [f"{float(t)*100:.1f}%" for t in targets]

    panels = (
        (ax1, "recall", "Hybrid recall (%)", "Recall cost of a tighter FPR budget"),
        (ax2, "fpr_attack", "FPR(Attack) (%)", "Measured FPR(Attack) — not calibrated"),
    )
    for ax, metric, ylabel, title in panels:
        series = {arch: [results[arch]["targets"][t]["hybrid"][metric] * 100
                         for t in targets]
                  for arch in ARCHITECTURES}
        for i, arch in enumerate(ARCHITECTURES):
            ax.plot(x, series[arch], marker=markers[i], markersize=9, lw=2.0,
                    ls=styles[i], color=CONFIG_COLORS[i],
                    markeredgecolor="white", markeredgewidth=1.2,
                    label=f"{arch.upper()}-AE")
        # Label above/below by rank at each x, so crossing lines never collide.
        for arch in ARCHITECTURES:
            for xi, value in zip(x, series[arch]):
                others = [series[a][xi] for a in ARCHITECTURES if a != arch]
                above = value >= max(others)
                ax.annotate(f"{value:.2f}", (xi, value), textcoords="offset points",
                            xytext=(0, 11 if above else -17), ha="center",
                            fontsize=8.5, color=INK)
        # Headroom so the offset value labels are never clipped by the axes.
        flat = [v for arch in ARCHITECTURES for v in series[arch]]
        pad = (max(flat) - min(flat)) * 0.28 or 1.0
        ax.set_ylim(min(flat) - pad, max(flat) + pad)
        ax.set_xticks(x, xticklabels, fontsize=10)
        ax.set_xlabel("Calibration target FPR(Val)", fontsize=10.5, color=INK)
        ax.set_ylabel(ylabel, fontsize=10.5, color=INK)
        ax.set_title(title, fontsize=11.5, fontweight="bold", color=INK, pad=9)
        ax.legend(fontsize=9.5, frameon=True, framealpha=0.95, edgecolor="#DDDDDD")
        style_axes(ax)

    ax1.set_ylim(83, 100.5)
    ax1.axhline(85, color="#8A8A8A", lw=1.1, ls=":")
    ax1.text(0, 85.5, "target 85%", fontsize=9, color=MUTED, ha="left")
    fig.suptitle("Calibration Frontier — FPR(Val) Budget vs Hybrid Performance",
                 fontsize=12.5, fontweight="bold", color=INK, y=1.02)
    fig.tight_layout()
    save(fig, out_dir, "eval_calibration_frontier")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=Path("results/opsi_b/opsi_b.json"))
    ap.add_argument("--output", type=Path, default=Path("eval_figures/final_hybrid"))
    args = ap.parse_args()

    with open(args.results) as handle:
        payload = json.load(handle)
    results = payload["results"]
    targets = sorted(results["lstm"]["targets"], key=float, reverse=True)
    args.output.mkdir(parents=True, exist_ok=True)

    for arch in ARCHITECTURES:
        main_result = results[arch]["targets"][MAIN_TARGET]
        plot_confusion(main_result, arch, args.output)
        plot_roc(payload["roc"][arch], main_result, arch, args.output)
        plot_per_class(main_result, arch, args.output)
    plot_frontier(results, targets, args.output)


if __name__ == "__main__":
    main()
