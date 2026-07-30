#!/usr/bin/env python3
"""Publication-ready figures for the two-variant AE loss ablation."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ARCHITECTURES = ("gru", "lstm")
VARIANTS = ("uniform", "benign")
CLASSES = ("ul_flood", "dl_flood", "burst", "roq")
COLORS = ("#0072B2", "#E69F00")
HATCHES = ("///", "\\\\")


def metric_matrix(results, metric):
    """Return architecture × variant Hybrid metric values in percent."""
    return np.array(
        [
            [results[arch][variant]["hybrid"][metric] * 100 for variant in VARIANTS]
            for arch in ARCHITECTURES
        ],
        dtype=float,
    )


def per_class_matrix(results, arch):
    """Return class × variant Hybrid recall values in percent."""
    return np.array(
        [
            [
                results[arch][variant]["hybrid"]["per_class_recall"][label] * 100
                for variant in VARIANTS
            ]
            for label in CLASSES
        ],
        dtype=float,
    )


def style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#D0D0D0", linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)


def add_grouped_bars(ax, values, group_labels, ylabel):
    x = np.arange(len(group_labels))
    width = 0.34
    for index, variant in enumerate(VARIANTS):
        offset = (index - 0.5) * width
        bars = ax.bar(
            x + offset,
            values[:, index],
            width,
            label=variant.capitalize(),
            color=COLORS[index],
            edgecolor="#222222",
            linewidth=0.6,
            hatch=HATCHES[index],
        )
        ax.bar_label(bars, fmt="%.1f", fontsize=7, padding=2)
    ax.set_xticks(x, group_labels)
    ax.set_ylabel(ylabel)
    ax.set_ylim(0, 106)
    style_axes(ax)


def save_figure(fig, output_dir, stem):
    output_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("png", "pdf"):
        path = output_dir / f"{stem}.{suffix}"
        fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
        print(f"[FIG] {path}")
    plt.close(fig)


def plot_global_metrics(results, output_dir):
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2), sharey=True)
    for panel, (ax, metric, label) in enumerate(
        zip(axes, ("recall", "f1"), ("Hybrid recall (%)", "Hybrid F1 (%)"))
    ):
        add_grouped_bars(
            ax,
            metric_matrix(results, metric),
            ("GRU", "LSTM"),
            label,
        )
        ax.set_title(("A  Recall", "B  F1 score")[panel], loc="left", weight="bold")
    axes[1].legend(frameon=False, loc="lower right")
    fig.suptitle(
        "Training-loss ablation at Hybrid FPR(Attack) < 3%",
        fontsize=11,
        weight="bold",
    )
    fig.tight_layout()
    save_figure(fig, output_dir, "loss_ablation_global")


def plot_per_class(results, arch, output_dir):
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    add_grouped_bars(
        ax,
        per_class_matrix(results, arch),
        ("UL flood", "DL flood", "Burst", "RoQ"),
        "Hybrid recall (%)",
    )
    ax.set_title(
        f"{arch.upper()} Hybrid recall by attack class",
        fontsize=11,
        weight="bold",
    )
    ax.legend(frameon=False, ncol=2, loc="lower right")
    fig.tight_layout()
    save_figure(fig, output_dir, f"loss_ablation_per_class_{arch}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("results/loss_ablation/loss_ablation.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval_figures/loss_ablation"),
    )
    args = parser.parse_args()

    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.size": 8,
            "axes.labelsize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    with args.results.open() as handle:
        results = json.load(handle)
    plot_global_metrics(results, args.output)
    for arch in ARCHITECTURES:
        plot_per_class(results, arch, args.output)


if __name__ == "__main__":
    main()
