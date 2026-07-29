#!/usr/bin/env python3
"""Revised evaluation figures for the benign-calibrated scoring scheme.

Regenerates the figures that changed when moving from attack-informed Scheme A to
leakage-free benign-calibrated weighting. Outputs to a NEW folder so the old
eval_figures/ stay intact. See docs/scoring_comparison_results.md.

Palette: Okabe-Ito (colorblind-safe; validated via dataviz skill).
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from evaluate_per_ue_v2 import (
    load_csv, preprocess_rows, split_by_rnti, load_models, compute_roc_auc,
)
from evaluate_scoring_comparison import (
    pooled_data, calibrate_hybrid_threshold, evaluate_model,
    LABEL_NAMES, TARGET_FPR_ATTACK,
)
from src.detection.feature_schema_ue import FEATURE_NAMES, FEATURE_WEIGHTS
from src.detection.scoring import make_weight_vec, weighted_score

OUT = "eval_figures/per_ue_benign_calibrated"
os.makedirs(OUT, exist_ok=True)

# Okabe-Ito colorblind-safe palette
C_GRU, C_LSTM = "#E69F00", "#0072B2"
C_RULE, C_ML, C_HYB = "#E69F00", "#56B4E9", "#009E73"
C_SCHEMEA, C_BENIGN, C_UNIFORM = "#999999", "#009E73", "#D55E00"
C_ATTACKHIST, C_BENIGNHIST = "#D55E00", "#4CAF50"
INK = "#222222"

plt.rcParams.update({
    "axes.edgecolor": "#888888", "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": INK, "ytick.color": INK, "font.size": 10.5,
    "axes.titlesize": 12, "figure.dpi": 150,
})


def threshold_for_target_fpr(neg_scores, target_fpr):
    """Order-statistic threshold with FPR(neg) <= target (benign scores only)."""
    s = np.sort(np.asarray(neg_scores, dtype=np.float64))
    n = len(s)
    if n == 0:
        return 0.0
    k = min(max(int(np.ceil((1.0 - target_fpr) * n)), 1), n)
    return float(s[k - 1])


def _save(fig, name):
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[FIG] {path}")


def load_all():
    val_rows = load_csv("csv/dataset_validation_ue_juni.csv"); preprocess_rows(val_rows)
    atk_rows = load_csv("csv/dataset_attack_ue_juni.csv"); preprocess_rows(atk_rows)
    val_by, atk_by = split_by_rnti(val_rows), split_by_rnti(atk_rows)
    models = load_models(
        lstm_pt="models/lstm_ue_v6.pt", lstm_pkl="models/lstm_ue_v6_scaler.pkl",
        lstm_json="models/lstm_ue_v6_threshold.json",
        gru_pt="models/gru_ue_v5.pt", gru_pkl="models/gru_ue_v5_scaler.pkl",
        gru_json="models/gru_ue_v5_threshold.json",
    )
    data = {}
    for mt in ["gru", "lstm"]:
        model, scaler, _ = models[mt]
        vres, vlbl, vrule = pooled_data(model, scaler, val_by)
        ares, albl, arule = pooled_data(model, scaler, atk_by)
        wb = make_weight_vec("benign", FEATURE_NAMES, FEATURE_WEIGHTS, benign_residuals=vres)
        a_ml, v_ml = weighted_score(ares, wb), weighted_score(vres, wb)
        neg, pos = a_ml[albl == 0], a_ml[albl > 0]
        thr = calibrate_hybrid_threshold(neg, arule[albl == 0], TARGET_FPR_ATTACK)
        fpr, tpr, auc = compute_roc_auc(neg, pos)
        ev = evaluate_model(model, scaler, (vres, vlbl, vrule), (ares, albl, arule),
                            TARGET_FPR_ATTACK)
        data[mt] = dict(wb=wb, a_ml=a_ml, v_ml=v_ml, ares=ares, albl=albl, vres=vres,
                        neg=neg, pos=pos, thr=thr, fpr=fpr, tpr=tpr, auc=auc, ev=ev)
    return data


def fig_feature_weights(data):
    norm = lambda w: w / w.sum() * 100.0
    wa = norm(make_weight_vec("attack", FEATURE_NAMES, FEATURE_WEIGHTS))
    wg, wl = norm(data["gru"]["wb"]), norm(data["lstm"]["wb"])
    order = np.argsort(wg)[::-1]
    names = [FEATURE_NAMES[i] for i in order]
    y = np.arange(len(names)); h = 0.26
    fig, ax = plt.subplots(figsize=(9, 8.5))
    ax.barh(y + h, wa[order], h, color=C_SCHEMEA, label="Scheme A (attack-informed, biased)")
    ax.barh(y,     wg[order], h, color=C_GRU,     label="Benign-calibrated (GRU v5)")
    ax.barh(y - h, wl[order], h, color=C_LSTM,    label="Benign-calibrated (LSTM v6)")
    ax.set_yticks(y); ax.set_yticklabels(names, fontsize=8.5); ax.invert_yaxis()
    ax.set_xlabel("Relative weight contribution (%)")
    ax.set_title("Feature weighting — attack-informed vs benign-calibrated", fontweight="bold")
    ax.legend(fontsize=9); ax.grid(axis="x", alpha=0.3)
    _save(fig, "feature_weights_comparison.png")


def fig_roc(data):
    fig, ax = plt.subplots(figsize=(7, 6))
    for mt, c in [("gru", C_GRU), ("lstm", C_LSTM)]:
        d = data[mt]
        ax.plot(d["fpr"], d["tpr"], color=c, lw=2.2,
                label=f"{mt.upper()} benign-cal (AUC={d['auc']:.4f})")
    ax.axvline(0.03, color="#D55E00", ls=":", lw=1.5, label="FPR(Attack)=3%")
    ax.plot([0, 1], [0, 1], "k--", lw=0.8, alpha=0.6, label="Random")
    ax.set_xlabel("False Positive Rate"); ax.set_ylabel("True Positive Rate (Recall)")
    ax.set_title("ROC — Benign-Calibrated ML (held-out attack)", fontweight="bold")
    ax.legend(loc="lower right"); ax.grid(alpha=0.3)
    ax.set_xlim(-0.02, 1.02); ax.set_ylim(-0.02, 1.02)
    _save(fig, "roc_benign_calibrated.png")


def fig_per_class(data):
    classes = list(LABEL_NAMES.values())
    labels = [c.replace("_", " ").title() for c in classes]
    for mt in ["gru", "lstm"]:
        ev = data[mt]["ev"]
        def row(cfg):
            pc = ev[cfg]["per_class_recall"]
            return [(pc[c] or 0.0) * 100 for c in classes]
        x = np.arange(len(classes)); w = 0.26
        fig, ax = plt.subplots(figsize=(8, 5))
        for off, cfg, col, lab in [(-w, "rule_only", C_RULE, "Rule Only"),
                                   (0, "ml_only", C_ML, "ML-Only (benign)"),
                                   (w, "hybrid", C_HYB, "Hybrid")]:
            b = ax.bar(x + off, row(cfg), w, color=col, label=lab)
            ax.bar_label(b, fmt="%.0f", fontsize=8, padding=2)
        ax.axhline(85, color="#D55E00", ls="--", lw=1, alpha=0.7, label="Target ≥85%")
        ax.set_xticks(x); ax.set_xticklabels(labels)
        ax.set_ylabel("Recall (%)"); ax.set_ylim(0, 112)
        ax.set_title(f"Per-class recall — {mt.upper()} benign-cal @ FPR(Attack) 2.99%",
                     fontweight="bold")
        ax.legend(ncol=2, fontsize=9, loc="lower left"); ax.grid(axis="y", alpha=0.3)
        _save(fig, f"per_class_recall_{mt}.png")


def fig_score_dist(data):
    for mt in ["gru", "lstm"]:
        d = data[mt]
        benign = np.concatenate([d["v_ml"], d["neg"]])
        attack = d["pos"]
        xmax = float(np.percentile(np.concatenate([benign, attack]), 99.5)) * 1.1
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.hist(benign, bins=80, range=(0, xmax), density=True, color=C_BENIGNHIST,
                alpha=0.55, label=f"Benign (n={len(benign):,})")
        ax.hist(attack, bins=80, range=(0, xmax), density=True, color=C_ATTACKHIST,
                alpha=0.5, label=f"Attack (n={len(attack):,})")
        ax.axvline(d["thr"], color="#111111", ls="--", lw=2,
                   label=f"Threshold = {d['thr']:.6f}")
        ax.set_xlabel("Benign-calibrated weighted MSE"); ax.set_ylabel("Density")
        ax.set_title(f"Score distribution — {mt.upper()} benign-calibrated", fontweight="bold")
        ax.legend(); ax.grid(alpha=0.3); ax.set_xlim(0, xmax)
        _save(fig, f"score_dist_{mt}.png")


def fig_confusion(data):
    for mt in ["gru", "lstm"]:
        cm = data[mt]["ev"]["hybrid"]["confusion"]
        M = np.array([[cm["tn"], cm["fp"]], [cm["fn"], cm["tp"]]], dtype=float)
        fig, ax = plt.subplots(figsize=(5.6, 5))
        im = ax.imshow(M, cmap="Blues")
        ax.set_xticks([0, 1]); ax.set_xticklabels(["Pred Normal", "Pred Anomaly"])
        ax.set_yticks([0, 1]); ax.set_yticklabels(["Actual Benign", "Actual Attack"])
        vmax = M.max()
        for i in range(2):
            for j in range(2):
                ax.text(j, i, f"{int(M[i, j]):,}", ha="center", va="center",
                        fontsize=15, fontweight="bold",
                        color="white" if M[i, j] > 0.5 * vmax else INK)
        ax.set_title(f"Confusion Matrix — {mt.upper()} Hybrid (benign-cal)\n"
                     f"@ FPR(Attack) 2.99%", fontweight="bold")
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        _save(fig, f"confusion_hybrid_{mt}.png")


def fig_scheme_comparison(data):
    schemes = [("uniform", C_UNIFORM), ("benign", C_BENIGN), ("attack", C_SCHEMEA)]
    labels_scheme = {"uniform": "Uniform", "benign": "Benign-cal (clean)",
                     "attack": "Attack-informed (biased)"}
    x = np.arange(2); w = 0.26
    fig, ax = plt.subplots(figsize=(7.5, 5))
    for i, (sc, col) in enumerate(schemes):
        vals = []
        for mt in ["gru", "lstm"]:
            d = data[mt]
            wv = make_weight_vec(sc, FEATURE_NAMES, FEATURE_WEIGHTS, benign_residuals=d["vres"])
            s = weighted_score(d["ares"], wv)
            neg, pos = s[d["albl"] == 0], s[d["albl"] > 0]
            thr = threshold_for_target_fpr(neg, TARGET_FPR_ATTACK)
            vals.append(float((pos > thr).mean()) * 100)
        b = ax.bar(x + (i - 1) * w, vals, w, color=col, label=labels_scheme[sc])
        ax.bar_label(b, fmt="%.1f", fontsize=8, padding=2)
    ax.set_xticks(x); ax.set_xticklabels(["GRU v5", "LSTM v6"])
    ax.set_ylabel("ML-Only Recall (%)"); ax.set_ylim(0, 108)
    ax.set_title("Scheme comparison — ML-Only recall @ FPR(Attack) 3%", fontweight="bold")
    ax.legend(fontsize=9, loc="lower right"); ax.grid(axis="y", alpha=0.3)
    _save(fig, "scheme_comparison_recall.png")


def main():
    print("[*] Computing residuals / scores for both models ...")
    data = load_all()
    fig_feature_weights(data)
    fig_roc(data)
    fig_per_class(data)
    fig_score_dist(data)
    fig_confusion(data)
    fig_scheme_comparison(data)
    print(f"\nDone. {len(os.listdir(OUT))} figures in {OUT}/")


if __name__ == "__main__":
    main()
