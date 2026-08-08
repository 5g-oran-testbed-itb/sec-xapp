# AE loss-weighting ablation — benign-calibrated scoring

Matched-pair comparison of **uniform MSE** and **benign-scale weighted MSE** training. Both variants are free of attack-derived training weights and use identical benign-calibrated scoring at Hybrid FPR(Attack) < 3%.

**Uncertainty note:** this is a deterministic single-seed (42) ablation. Differences are descriptive; no confidence interval or statistical-significance claim is made without repeated seeds.

## Thresholds

| Model | Training loss | Th | Percentile (val benign) | Percentile (attack benign) |
|---|---|---:|---:|---:|
| GRU | uniform | 0.009038 | P95.60 | P97.15 |
| GRU | benign | 0.005493 | P94.98 | P97.15 |
| LSTM | uniform | 0.011033 | P96.05 | P97.15 |
| LSTM | benign | 0.007652 | P94.30 | P97.15 |

## Global metrics

| Model | Training loss | Config | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | AUC |
|---|---|---|---:|---:|---:|---:|---:|---:|
| GRU | uniform | Rule Only | 85.78% | 97.51% | 91.27% | 0.86% | 2.93% | N/A |
| GRU | uniform | ML-Only | 96.47% | 92.97% | 94.69% | 2.85% | 4.40% | 0.9895 |
| GRU | uniform | Hybrid | 98.26% | 92.78% | 95.44% | 2.99% | 5.93% | N/A |
| GRU | benign | Rule Only | 85.78% | 97.51% | 91.27% | 0.86% | 2.93% | N/A |
| GRU | benign | ML-Only | 97.67% | 93.05% | 95.31% | 2.85% | 5.02% | 0.9892 |
| GRU | benign | Hybrid | 98.97% | 92.83% | 95.80% | 2.99% | 5.93% | N/A |
| LSTM | uniform | Rule Only | 85.78% | 97.51% | 91.27% | 0.86% | 2.93% | N/A |
| LSTM | uniform | ML-Only | 97.72% | 93.06% | 95.33% | 2.85% | 3.95% | 0.9902 |
| LSTM | uniform | Hybrid | 98.70% | 92.81% | 95.67% | 2.99% | 5.76% | N/A |
| LSTM | benign | Rule Only | 85.78% | 97.51% | 91.27% | 0.86% | 2.93% | N/A |
| LSTM | benign | ML-Only | 97.58% | 93.05% | 95.26% | 2.85% | 5.70% | 0.9907 |
| LSTM | benign | Hybrid | 98.88% | 92.82% | 95.76% | 2.99% | 6.43% | N/A |

## Hybrid recall per class

| Model | Training loss | UL Flood | DL Flood | Burst | RoQ |
|---|---|---:|---:|---:|---:|
| GRU | uniform | 97.89% | 96.76% | 98.90% | 98.53% |
| GRU | benign | 99.06% | 96.76% | 99.45% | 99.46% |
| LSTM | uniform | 98.59% | 96.76% | 99.17% | 99.20% |
| LSTM | benign | 98.83% | 96.76% | 99.31% | 99.46% |

## Training controls

| Model | Training loss | Seed | Best epoch | Loss-weight scale |
|---|---|---:|---:|---|
| GRU | uniform | 42 | 85 | all weights = 1 |
| GRU | benign | 42 | 88 | relative benign weights, normalized to mean 1 |
| LSTM | uniform | 42 | 89 | all weights = 1 |
| LSTM | benign | 42 | 65 | relative benign weights, normalized to mean 1 |

The shared seed controls Python, NumPy, and PyTorch randomness before model
initialization. A separate smoke test reproduced bit-identical state dictionaries
for two independent same-seed runs of each architecture. Normalizing benign loss
weights to mean 1 preserves their relative feature emphasis without confounding
the comparison through a different aggregate gradient scale.

The P99 thresholds written by the training scripts are training diagnostics and
are **not** used in this comparison. The operative thresholds are the `Th`
values in the first table, recalibrated in the benign-calibrated score space so
the complete Hybrid detector remains below 3% FPR(Attack).

## Interpretation and decision

- For GRU, benign-scale training raises Hybrid recall by **0.71 percentage
  points** and F1 by **0.36 points** relative to uniform training. ML AUC changes
  from 0.9895 to 0.9892 (-0.0003), while Hybrid FPR(Val) is unchanged at 5.93%.
- For LSTM, benign-scale training raises Hybrid recall by **0.18 points** and F1
  by **0.09 points**. ML AUC changes from 0.9902 to 0.9907 (+0.0005), but Hybrid
  FPR(Val) increases by 0.67 points (5.76% to 6.43%).
- Both non-attack losses meet the primary requirement: every Hybrid configuration
  achieves at least 98.26% recall at 2.99% FPR(Attack), with all class recalls
  above 96.7%.

**Decision:** adopt **uniform MSE for AE training** and retain
**benign-calibrated weighting for anomaly scoring**. The small single-seed gains
from benign-scale training do not yet justify the two-pass derivation and its
additional moving part; they are not supported by confidence intervals or a
multi-seed significance analysis. Uniform training is the simpler leakage-free
choice and already meets the operating target with substantial margin.

## Validity boundary

> **Superseded — the threshold leakage path described below is now closed.** The
> thresholds in this document are calibrated on the attack file and are retained
> only as the loss-ablation record. The reported operating point comes from
> [opsi_b_recalibration_results.md](opsi_b_recalibration_results.md), which
> recalibrates on the benign validation set instead. There, FPR(Attack) is no
> longer an operating point but an **out-of-sample generalization estimate**
> (2.41% LSTM / 2.06% GRU, measured), and Precision and F1 no longer inherit the
> optimism of a threshold fitted on the file used to report them.

Neither training variant uses attack data or attack labels. However, the exact
operating threshold is selected from the `label==0` windows in the attack-file
session to enforce the 3% Hybrid FPR ceiling. Thus the training/scoring weights
are attack-free, but FPR(Attack) is an operating-point calibration result rather
than an untouched estimate. FPR(Val), computed on the independent benign
validation set, is retained as the cross-session generalization check. A newly
collected benign+attack test session remains the strongest final confirmation.

## Figures

- [Global Hybrid recall and F1](../eval_figures/loss_ablation/loss_ablation_global.png)
- [GRU Hybrid recall per attack class](../eval_figures/loss_ablation/loss_ablation_per_class_gru.png)
- [LSTM Hybrid recall per attack class](../eval_figures/loss_ablation/loss_ablation_per_class_lstm.png)

Vector PDF versions are stored beside each PNG. Colors use the colorblind-safe
Okabe-Ito blue/orange pair, with redundant hatch patterns for grayscale
readability. Bars begin at zero to avoid visually exaggerating the small
differences. Error bars are intentionally absent because each condition has one
deterministic seed; adding them would imply uncertainty estimates that were not
measured.
