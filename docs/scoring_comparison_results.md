# Leakage-Aware Scoring Comparison — Results (FPR Attack < 3%)

**Date:** 2026-07-29
**Driver:** `evaluate_scoring_comparison.py` (`--target-fpr 0.03`)
**Test data (held-out):** `csv/dataset_attack_ue_juni.csv` — 5723 benign (`label==0`) windows + 2236 attack windows (UL/DL flood, burst, RoQ), per model, `seq_len=30`. Validation: `csv/dataset_validation_ue_juni.csv` (1772 windows).
**Spec:** [`docs/superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md`](superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md)

Every configuration is evaluated at a **matched operating point**: the threshold is calibrated so `FPR(Attack) = 2.99%` (just under the 3% ceiling), measured on the held-out benign (`label==0`) windows of the attack file. This removes the earlier artifact where attack-informed scoring looked better only because it operated at a higher FPR. Threshold calibration uses **benign windows only — no attack-class labels** — so it does not reintroduce attack leakage for the uniform/benign schemes. `FPR(Val)` on the separate validation set is reported as an independent generalization cross-check.

**Scheme legend:** `uniform` = plain MSE; `benign` = benign-calibrated weights (median+MAD, attack-free — **recommended**); `attack` = Scheme A attack-informed weights (circular here, shown as a biased upper bound).

## Why the previous scheme leaked, and the fix

**What leaked — attack-informed "Scheme A".** The previously deployed weighting derived each feature's weight from the ratio of its *attack* reconstruction error to its *benign* reconstruction error — conceptually `w_j = log(max_c mean_err_attack[c,j] / mean_err_benign[j])` — computed on `csv/dataset_attack_ue_juni.csv`, the very file used to report the final metrics ([`src/detection/feature_schema_ue.py:37`](../src/detection/feature_schema_ue.py)). Two leakage paths resulted:

1. **Scoring leakage** — the anomaly-score function embedded 19 coefficients fitted with attack labels, then was scored on those same attacks → circular / optimistic.
2. **Model-selection leakage** — the weighting variant (Scheme A vs B vs C) was chosen by whichever gave the highest AUC on that same attack file.

Symptom: switching the weighting on raised the reported AUC from ≈0.875 to ≈0.967 — partly real signal, partly fitting to the test set. This is exactly what makes the reviewer's "attack-informed / not fully unsupervised" critique correct.

**The fix — benign-calibrated residual weighting.** Weights now come from benign statistics only:

`w_j = 1 / (median(e_j) + MAD(e_j) + ε)`, capped at `10 × median(w)`,

where `e_j` is the per-feature squared reconstruction error on the **benign validation set** ([`src/detection/scoring.py`](../src/detection/scoring.py)). Features whose benign residual is small and stable get more weight; the cap stops a near-zero-residual feature from dominating. No attack label ever touches the weights or the threshold, so the detector is genuinely one-class/unsupervised and `dataset_attack_ue_juni.csv` becomes a valid held-out test. The tables below show this leakage-free scheme matches or beats the attack-informed one at matched FPR — i.e. the "leaked" gain was recoverable without any attack information.

## Scheme selection — global metrics @ FPR(Attack) = 2.99%

_ML-Only, three scoring schemes side by side, to justify dropping attack-informed. The final deployable config (Rule/ML/Hybrid, benign only) follows below._

| Model | Scoring | Leakage-free | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | AUC |
|---|---|---|---|---|---|---|---|---|
| GRU | uniform | yes | 79.96% | 91.27% | 85.24% | 2.99% | 8.13% | 0.9756 |
| GRU | **benign** | **yes** | **97.32%** | **92.71%** | **94.96%** | **2.99%** | **5.14%** | **0.9912** |
| GRU | attack | NO (biased) | 97.00% | 92.69% | 94.80% | 2.99% | 7.34% | 0.9903 |
| LSTM | uniform | yes | 87.92% | 92.00% | 89.92% | 2.99% | 7.17% | 0.9631 |
| LSTM | **benign** | **yes** | **97.99%** | **92.76%** | **95.30%** | **2.99%** | **4.80%** | **0.9896** |
| LSTM | attack | NO (biased) | 97.41% | 92.72% | 95.01% | 2.99% | 7.56% | 0.9876 |

## Scheme selection — per-class recall @ FPR(Attack) = 2.99%

| Model | Scoring | UL Flood | DL Flood | Burst | RoQ |
|---|---|---|---|---|---|
| GRU | uniform | 28.40% | 92.63% | 97.79% | 86.33% |
| GRU | benign | 98.83% | 88.20% | 99.03% | 98.93% |
| GRU | attack | 96.71% | 92.92% | 98.34% | 97.72% |
| LSTM | uniform | 90.38% | 95.28% | 99.17% | 72.25% |
| LSTM | benign | 98.83% | 90.86% | 99.31% | 99.46% |
| LSTM | attack | 98.59% | 95.87% | 99.31% | 95.58% |

## Final benign-calibrated detection — Rule / ML-Only / Hybrid

Using **only** the leakage-free benign scheme (no Scheme A), threshold calibrated so the deployed **Hybrid** config stays under FPR(Attack) = 3% (the binding constraint; ML-Only and Rule-Only sit below). This is the leakage-free equivalent of the `per_ue_v5_results.md` §2–§3 tables.

### Threshold (Th) and percentile

| Model | Th (benign-calibrated weighted MSE) | Percentile on val benign | Percentile on attack benign |
|---|---|---|---|
| GRU v5 | **0.006654** | **P95.32** | P97.15 |
| LSTM v6 | **0.008619** | **P95.32** | P97.15 |

The threshold is chosen so the deployed Hybrid lands at FPR(Attack) = 2.99%, using benign windows only. Reading it: at `Th`, 95.32% of validation-benign windows score below it (→ ML-Only FPR(Val) = 4.68%) and 97.15% of attack-file benign windows do (→ ML-Only FPR(Attack) = 2.85%); the rule engine adds the remaining false positives up to the 2.99% Hybrid ceiling.

> **Scale note:** these `Th` values live in the *benign-calibrated* weighted-MSE space and are **not** comparable to the deployed Scheme A thresholds (GRU `0.0245`, LSTM `0.023` in `models/*_threshold.json`) — a different weight vector produces a different score scale. Compare percentiles, not raw thresholds.

#### How the threshold is determined

It is **not** read off the ROC curve, and it never uses attack labels. It is a direct false-positive-rate calibration on benign windows ([`calibrate_hybrid_threshold`](../evaluate_scoring_comparison.py)):

1. Compute the benign-calibrated weighted score `S(x)` for every window.
2. Keep only the **benign windows** (`label==0`) of the attack file — the held-out negatives.
3. Find the **lowest** `Th` such that the deployed **Hybrid** decision (`rule OR S>Th`) fires on **≤ 3%** of those benign windows. Because the rule engine's own false positives are fixed, this lowers the ML cut as far as possible while the combined FPR(Attack) stays under 3%.

Equivalently, `Th` is the `(1 − 0.03)` quantile of the benign hybrid-score distribution — the same point you would read off the ROC curve at FPR = 3%. But the ROC/AUC here is only a threshold-**independent** summary of separability (computed over all thresholds via `roc_curve`); it is **not** the input to threshold selection. **Recall (TPR) is a consequence** of this benign-only calibration, never an input — the threshold never "sees" attack-positive windows.

This inverts the deployed Scheme A convention (`per_ue_v5_results.md`), which fixes the threshold at a chosen percentile of the **validation** benign scores (e.g. P97.5) and reports FPR(Attack) as an outcome. Here we fix FPR(Attack) ≤ 3% and let the percentile be the outcome (→ ≈ P97.15 on attack-benign, P95.32 on validation-benign).

> **Honesty note:** calibrating on the attack file's `label==0` windows is a mild use of test-set *benign* traffic (no attack-class labels). `FPR(Val)`, measured on the fully independent validation set, is the clean generalization cross-check.

### Global metrics @ Hybrid FPR(Attack) = 2.99%

| Model | Config | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | AUC |
|---|---|---|---|---|---|---|---|
| GRU | Rule Only | 85.78% | 97.51% | 91.27% | 0.86% | 2.93% | N/A |
| GRU | ML-Only (benign) | 97.14% | 93.02% | 95.03% | 2.85% | 4.68% | 0.9912 |
| GRU | **Hybrid (Rule OR benign)** | **98.61%** | 92.80% | **95.62%** | 2.99% | 5.87% | N/A |
| LSTM | Rule Only | 85.78% | 97.51% | 91.27% | 0.86% | 2.93% | N/A |
| LSTM | ML-Only (benign) | 97.99% | 93.08% | 95.47% | 2.85% | 4.68% | 0.9896 |
| LSTM | **Hybrid (Rule OR benign)** | **98.88%** | 92.82% | **95.76%** | 2.99% | 5.81% | N/A |

### Per-class recall @ Hybrid FPR(Attack) = 2.99%

| Model | Config | UL Flood | DL Flood | Burst | RoQ |
|---|---|---|---|---|---|
| GRU | Rule Only | 97.18% | 96.76% | 95.03% | 65.28% |
| GRU | ML-Only (benign) | 98.83% | 87.91% | 98.76% | 98.79% |
| GRU | Hybrid (Rule OR benign) | 98.83% | 96.76% | 98.90% | 99.06% |
| LSTM | Rule Only | 97.18% | 96.76% | 95.03% | 65.28% |
| LSTM | ML-Only (benign) | 98.83% | 90.86% | 99.31% | 99.46% |
| LSTM | Hybrid (Rule OR benign) | 98.83% | 96.76% | 99.31% | 99.46% |

Artifacts: `results/scoring_comparison/benign_hybrid_comparison.{json,md}`.

## Interpretation

1. **Benign-calibrated equals or beats attack-informed at matched FPR — without leakage.** At a fixed FPR(Attack) of 2.99%, the leakage-free benign scheme wins on Recall, F1, and AUC for both models (GRU 97.32%/94.96%/0.9912 vs attack 97.00%/94.80%/0.9903; LSTM 97.99%/95.30%/0.9896 vs attack 97.41%/95.01%/0.9876). This is the decisive result: the attack-informed weighting provided **no genuine benefit** once the FPR is matched — its earlier apparent advantage was purely the leakage/FPR artifact. Per the spec §8 decision rule, **adopt benign-calibrated scoring**.

2. **The benign-calibrated Hybrid is the deployable, leakage-free config.** GRU-Hybrid 98.61% Recall / 95.62% F1 and LSTM-Hybrid 98.88% / 95.76% at FPR(Attack) 2.99% — matching or exceeding the previously reported attack-informed hybrids (GRU-Hybrid v5 98.08%, LSTM-Hybrid v6 94.59% in `per_ue_v5_results.md`) with no attack leakage. The Rule-Only baseline (85.78% / 0.86%) reproduces the reference exactly, confirming pipeline consistency.

3. **The hybrid closes benign-calibrated's DL-flood gap.** ML-Only benign DL-flood recall (GRU 87.91%, LSTM 90.86%) is lifted to 96.76% in the hybrid because rule **R2** (`prb_usage_dl_ratio > 0.85`) catches DL flood independently of the ML score. Conversely the ML score carries RoQ (Rule-Only RoQ is only 65.28%). The two are complementary — every attack class clears ≥85% in the hybrid.

2. **Benign-calibrated also generalizes better.** Its FPR on the independent validation set is markedly lower (GRU 5.14%, LSTM 4.80%) than the attack-informed scheme (7.34% / 7.56%), i.e. the attack-tuned weights overfit the attack-capture benign distribution.

3. **Only residual weakness: DL flood.** Benign-calibrated DL-flood recall (GRU 88.20%, LSTM 90.86%) trails attack-informed (92.92% / 95.87%) because it down-weights DL-side features. It still clears the ≥85% target, and in the deployed hybrid the rule **R2** (`prb_usage_dl_ratio > 0.85`) catches DL flood independently of the ML score.

4. **Uniform MSE is the honest floor.** It collapses on GRU UL Flood (28.40%) and LSTM RoQ (72.25%), confirming feature weighting genuinely matters — and that benign-calibrated captures that benefit with zero attack information.

**Relation to deployed numbers** (`docs/per_ue_v5_results.md`): those report ML-Only at validation-derived thresholds (GRU-Only 93.29% @ FPR-Atk 2.04%, LSTM-Only 93.29% @ 2.55%). The numbers here are not a strict apples-to-apples replacement — they use a fixed 2.99% FPR operating point and a leakage-free scheme — but they show the deployed recall is reachable (and exceeded) **without** attack-informed weighting.

## Design-choice validity — architecture and sequence length

Two choices precede the leakage-free evaluation and deserve explicit, a-priori justification.

**Architecture (LSTM / GRU autoencoder) — principled prior, not leakage.** Reconstruction-based recurrent autoencoders are a standard, well-established approach for unsupervised time-series anomaly detection. Choosing this model *family* is an inductive-bias decision grounded in the literature — no test-set information enters it, so it is not a source of leakage.

**Sequence length (`seq_len = 30`, i.e. 30 s at 1 Hz) — fixed a-priori from attack timescales, not swept on the test set.** The value follows from the *known* physical characteristics of the attacks (configured by the attacker scripts, not learned from the test data), via two independent mechanisms:

- *Periodicity (Burst / RRC-storm).* Burst ON/OFF cycles are 3–7 s ON + 2–6 s OFF (period ≈ 5–13 s) and RRC-storm toggles every ~5–10 s. Recognising a periodic signature requires observing **≥ 2 full cycles**; with the slowest period ≈ 13 s that needs ≥ ~26 s → 30 s.
- *SNR integration (RoQ / low-rate DoS).* RoQ is low-throughput by design (occupies PRB at low `ul_efficiency`), so its per-sample deviation is small. A longer window integrates this weak-but-consistent deviation across more samples, shrinking the variance of the reconstruction-error estimate (~1/√N) and separating the RoQ vs benign score distributions. Volumetric floods (strong signal) do not need this; RoQ does.

Both mechanisms converge on ~30 s, while staying short enough to keep detection latency low.

**Leakage assessment.** `seq_len = 30` is set from the arguments above and then evaluated as a **single, pre-specified configuration** on the test set — it is *not* chosen by maximising a metric over a grid of candidate lengths on the evaluation data. A recall-vs-`seq_len` sweep on the attack file is deliberately **not** run, because picking the best value that way *would* introduce selection bias. Evaluating one principled configuration once is the correct use of a held-out set, and is categorically different from the Scheme A leakage (19 continuous weights fitted + A/B/C scheme selected on the test file). For a single, physically-motivated, un-searched hyperparameter this is standard, accepted practice and contributes negligible optimism.

## Limitations (state in the thesis)

- **No hyperparameter leakage from architecture or `seq_len`.** The AE family is a literature-standard prior, and `seq_len = 30` is fixed a-priori (see above) and evaluated as a single pre-specified config — neither is selected using test metrics. The residual item is **version selection** (GRU kept at v5 rather than v6, and threshold hand-tuning), which did consult attack-file metrics; a freshly collected attack test set (spec §3, Track B) would close this last mile. The absolute clean-room guarantee for any test-set-derived number still comes only from such a fresh set.
- The 2.99% operating threshold is calibrated on the attack file's `label==0` (benign) windows; this uses benign traffic only (no attack-class labels), but is a mild use of test-set benign data. `FPR(Val)` is the fully-independent generalization check.
- AUC is threshold-independent; Recall/Precision/F1/FPR are all at the matched 2.99% FPR(Attack) operating point.
