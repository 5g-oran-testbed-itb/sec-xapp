# Leakage-Aware Scoring Comparison — Results

**Date:** 2026-07-29
**Driver:** `evaluate_scoring_comparison.py`
**Test data (held-out):** `csv/dataset_attack_ue_juni.csv` — 5723 benign (`label==0`) windows + 2236 attack windows (UL/DL flood, burst, RoQ), per model, `seq_len=30`.
**Spec:** [`docs/superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md`](superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md)

For **Uniform** and **Benign-calibrated** scoring, the attack file is a valid held-out test — neither scheme uses attack data to derive weights or threshold. **Attack-informed** (Scheme A) derives its weights from this same file, so its numbers here are circular and shown only as a labeled upper-bound.

## Aggregate results

| Model | Scoring | Leakage-free | Recall@P97 | Held-out FPR | AUC | Recall@5% FPR | Recall@3% FPR |
|---|---|---|---|---|---|---|---|
| GRU | uniform | yes | 0.6534 | 0.0166 | 0.9756 | 0.8927 | 0.7996 |
| GRU | **benign** | **yes** | **0.8189** | **0.0171** | **0.9912** | **0.9861** | **0.9732** |
| GRU | attack | NO (biased) | 0.9316 | 0.0204 | 0.9903 | 0.9911 | 0.9700 |
| LSTM | uniform | yes | 0.7701 | 0.0201 | 0.9631 | 0.9137 | 0.8792 |
| LSTM | **benign** | **yes** | **0.8318** | **0.0210** | **0.9896** | **0.9893** | **0.9799** |
| LSTM | attack | NO (biased) | 0.9047 | 0.0236 | 0.9876 | 0.9919 | 0.9741 |

## Per-class recall @ P97 threshold

| Model | Scoring | ul_flood | dl_flood | burst | roq |
|---|---|---|---|---|---|
| GRU | uniform | 0.061 | 0.870 | 0.952 | 0.603 |
| GRU | benign | 0.937 | **0.000** | 0.981 | 0.967 |
| GRU | attack | 0.873 | 0.879 | 0.970 | 0.952 |
| LSTM | uniform | 0.683 | 0.917 | 0.982 | 0.547 |
| LSTM | benign | 0.967 | **0.086** | 0.989 | 0.941 |
| LSTM | attack | 0.955 | 0.915 | 0.989 | 0.790 |

## Interpretation

1. **Benign-calibrated matches attack-informed on aggregate — without leakage.** At matched FPR, benign-calibrated ties or beats the attack-informed upper bound (GRU AUC 0.9912 vs 0.9903; recall@3% 0.9732 vs 0.9700; LSTM AUC 0.9896 vs 0.9876). Per the spec §8 decision rule, this satisfies the target, so **benign-calibrated is the recommended scheme**: it is fully one-class/unsupervised and eliminates the attack–test leakage the reviewer flagged.

2. **The attack-informed "lead" at P97 is largely an FPR artifact.** Its higher recall@P97 (GRU 0.9316 vs 0.8189) comes with a higher operating FPR (0.0204 vs 0.0171). Compared fairly at a fixed FPR (recall@5%/@3%), the gap nearly vanishes. This means the previously reported attack-informed gains were mostly recoverable from benign statistics — not genuine leakage-driven superiority.

3. **Honest weakness of benign-calibrated: DL flood at the strict threshold.** Because it weights features by inverse benign-residual scale, it down-weights DL-side features and misses DL flood at P97 (GRU 0.000, LSTM 0.086). Two mitigations: (a) the gap closes at a slightly looser threshold — aggregate recall@5% FPR is ≈0.99, so DL flood is recovered there; (b) in the deployed hybrid, rule **R2** (`prb_usage_dl_ratio > 0.85`) catches DL flood independently of the ML score. Attack-informed avoids this dip only because it explicitly up-weights DL features using attack labels.

4. **Uniform MSE is the honest floor.** It clearly underperforms both weighted schemes (GRU AUC 0.9756, recall@3% 0.7996), confirming that feature weighting genuinely helps — and that benign-calibrated captures almost all of that benefit with zero attack information.

## Limitations (must be stated in the thesis)

- **Model-selection leakage remains.** The AE architecture, `seq_len=30`, and model versions (GRU v5, LSTM v6) were previously selected using this same attack file. This experiment removes scoring-level leakage, not model-selection leakage. A freshly collected attack test set (spec §3, Track B) is still required to fully close that gap.
- Threshold is P97 on benign validation; the held-out FPR is measured on `label==0` attack-file windows that never entered threshold calibration.
- Recall@FPR values are read off the ROC (`np.interp`), independent of the P97 operating point.
