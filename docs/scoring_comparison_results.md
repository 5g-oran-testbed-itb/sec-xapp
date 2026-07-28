# Leakage-Aware Scoring Comparison — Results (FPR Attack < 3%)

**Date:** 2026-07-29
**Driver:** `evaluate_scoring_comparison.py` (`--target-fpr 0.03`)
**Test data (held-out):** `csv/dataset_attack_ue_juni.csv` — 5723 benign (`label==0`) windows + 2236 attack windows (UL/DL flood, burst, RoQ), per model, `seq_len=30`. Validation: `csv/dataset_validation_ue_juni.csv` (1772 windows).
**Spec:** [`docs/superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md`](superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md)

Every configuration is evaluated at a **matched operating point**: the threshold is calibrated so `FPR(Attack) = 2.99%` (just under the 3% ceiling), measured on the held-out benign (`label==0`) windows of the attack file. This removes the earlier artifact where attack-informed scoring looked better only because it operated at a higher FPR. Threshold calibration uses **benign windows only — no attack-class labels** — so it does not reintroduce attack leakage for the uniform/benign schemes. `FPR(Val)` on the separate validation set is reported as an independent generalization cross-check.

**Scheme legend:** `uniform` = plain MSE; `benign` = benign-calibrated weights (median+MAD, attack-free — **recommended**); `attack` = Scheme A attack-informed weights (circular here, shown as a biased upper bound).

## Global metrics @ FPR(Attack) = 2.99%

| Model | Scoring | Leakage-free | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | AUC |
|---|---|---|---|---|---|---|---|---|
| GRU | uniform | yes | 79.96% | 91.27% | 85.24% | 2.99% | 8.13% | 0.9756 |
| GRU | **benign** | **yes** | **97.32%** | **92.71%** | **94.96%** | **2.99%** | **5.14%** | **0.9912** |
| GRU | attack | NO (biased) | 97.00% | 92.69% | 94.80% | 2.99% | 7.34% | 0.9903 |
| LSTM | uniform | yes | 87.92% | 92.00% | 89.92% | 2.99% | 7.17% | 0.9631 |
| LSTM | **benign** | **yes** | **97.99%** | **92.76%** | **95.30%** | **2.99%** | **4.80%** | **0.9896** |
| LSTM | attack | NO (biased) | 97.41% | 92.72% | 95.01% | 2.99% | 7.56% | 0.9876 |

## Per-class recall @ FPR(Attack) = 2.99%

| Model | Scoring | UL Flood | DL Flood | Burst | RoQ |
|---|---|---|---|---|---|
| GRU | uniform | 28.40% | 92.63% | 97.79% | 86.33% |
| GRU | benign | 98.83% | 88.20% | 99.03% | 98.93% |
| GRU | attack | 96.71% | 92.92% | 98.34% | 97.72% |
| LSTM | uniform | 90.38% | 95.28% | 99.17% | 72.25% |
| LSTM | benign | 98.83% | 90.86% | 99.31% | 99.46% |
| LSTM | attack | 98.59% | 95.87% | 99.31% | 95.58% |

## Interpretation

1. **Benign-calibrated equals or beats attack-informed at matched FPR — without leakage.** At a fixed FPR(Attack) of 2.99%, the leakage-free benign scheme wins on Recall, F1, and AUC for both models (GRU 97.32%/94.96%/0.9912 vs attack 97.00%/94.80%/0.9903; LSTM 97.99%/95.30%/0.9896 vs attack 97.41%/95.01%/0.9876). This is the decisive result: the attack-informed weighting provided **no genuine benefit** once the FPR is matched — its earlier apparent advantage was purely the leakage/FPR artifact. Per the spec §8 decision rule, **adopt benign-calibrated scoring**.

2. **Benign-calibrated also generalizes better.** Its FPR on the independent validation set is markedly lower (GRU 5.14%, LSTM 4.80%) than the attack-informed scheme (7.34% / 7.56%), i.e. the attack-tuned weights overfit the attack-capture benign distribution.

3. **Only residual weakness: DL flood.** Benign-calibrated DL-flood recall (GRU 88.20%, LSTM 90.86%) trails attack-informed (92.92% / 95.87%) because it down-weights DL-side features. It still clears the ≥85% target, and in the deployed hybrid the rule **R2** (`prb_usage_dl_ratio > 0.85`) catches DL flood independently of the ML score.

4. **Uniform MSE is the honest floor.** It collapses on GRU UL Flood (28.40%) and LSTM RoQ (72.25%), confirming feature weighting genuinely matters — and that benign-calibrated captures that benefit with zero attack information.

**Relation to deployed numbers** (`docs/per_ue_v5_results.md`): those report ML-Only at validation-derived thresholds (GRU-Only 93.29% @ FPR-Atk 2.04%, LSTM-Only 93.29% @ 2.55%). The numbers here are not a strict apples-to-apples replacement — they use a fixed 2.99% FPR operating point and a leakage-free scheme — but they show the deployed recall is reachable (and exceeded) **without** attack-informed weighting.

## Limitations (state in the thesis)

- **Model-selection leakage remains.** AE architecture, `seq_len=30`, and versions (GRU v5, LSTM v6) were previously chosen using this same attack file. This experiment removes scoring-level leakage only; a freshly collected attack test set (spec §3, Track B) is still required to close model-selection leakage.
- The 2.99% operating threshold is calibrated on the attack file's `label==0` (benign) windows; this uses benign traffic only (no attack-class labels), but is a mild use of test-set benign data. `FPR(Val)` is the fully-independent generalization check.
- AUC is threshold-independent; Recall/Precision/F1/FPR are all at the matched 2.99% FPR(Attack) operating point.
