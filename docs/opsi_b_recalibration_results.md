# Opsi B — threshold recalibration on the benign validation set

The decision threshold `Th` is now selected on `csv/dataset_validation_ue_juni.csv`
(entirely benign, 1772 windows) as the **lowest** `Th` keeping the Hybrid decision
(`rule OR score > Th`) at or below a target FPR(Val). The attack file is never
consulted during threshold selection, so **FPR(Attack) is a measurement, not a
setting**. Only the source of the negative windows changed; the algorithm is the
same lowest-threshold search as before, and no attack label enters it — the
pipeline stays one-class.

The 5% ceiling comes from Bab II (Axelsson 2000), not from this run.

**Configuration:** uniform-MSE training (all loss weights = 1) + benign-calibrated
scoring (`w_j = 1/(median(e_j) + MAD(e_j) + ε)`, capped at `10 × median(w)`,
derived from benign validation residuals only). Seed 42. No attack-informed
(Scheme A) weighting anywhere. Reference rows: `LSTM | uniform` and `GRU | uniform`
in [loss_ablation_results.md](loss_ablation_results.md).

**Uncertainty note:** deterministic single-seed (42) run. Differences between
architectures are descriptive; no significance claim is made without repeated seeds.

Reproduce with:

```bash
./venv/bin/python3 eval_opsi_b.py         # → results/opsi_b/opsi_b.json
./venv/bin/python3 plot_opsi_b.py         # → eval_figures/final_hybrid/
./venv/bin/python3 verify_scoring_math.py # asserts the scoring formulas
```

## Pipeline sanity check — passed

Rule-Only does not depend on `Th`, so it must reproduce the independently verified
baseline exactly. `eval_opsi_b.py` asserts this and aborts on any mismatch.

| Rule-Only metric | Required | Measured |
|---|---:|---:|
| Global recall | 85.78% | **85.78%** |
| FPR(Attack) | 0.86% | **0.86%** |
| FPR(Val) | 2.93% | **2.93%** |
| Recall UL Flood | 97.18% | **97.18%** |
| Recall DL Flood | 96.76% | **96.76%** |
| Recall Burst | 95.03% | **95.03%** |
| Recall RoQ | 65.28% | **65.28%** |
| Precision | 97.51% | **97.51%** |

Window semantics also match: `seq_len = 30`, windows built per-RNTI, window label =
label of its last sample. Validation 1772 windows (1 UE, all benign); attack file
7959 windows = 5723 benign + 2236 attack; confusion-matrix rows sum to 5723 and 2236.

## Window label purity

Counted per-RNTI, exactly as the evaluation windows are built:

| Window label | n | Mixed | % mixed |
|---|---:|---:|---:|
| benign | 5723 | 203 | 3.5% |
| UL Flood | 426 | 58 | 13.6% |
| DL Flood | 339 | 58 | 17.1% |
| Burst | 725 | 58 | 8.0% |
| RoQ | 746 | 58 | 7.8% |
| **Total** | **7959** | **435** | **5.47%** |

**94.53% of windows carry a single label.** The mixed ones are exactly the
transition-boundary windows: 58 = 2 episodes x 29 windows for every attack class, a
consistent and predictable consequence of `seq_len = 30` rather than labelling
noise.

An earlier evaluator run recorded `mixed_pct = 60.12%`. That figure counted windows
on the globally concatenated label array; because the 8 UEs are interleaved in time,
a global window mixes labels across UEs that never share a window in the real
pipeline. Fixed in `count_mixed_windows_by_rnti()`.

## Thresholds — B-utama (target FPR(Val) ≤ 5%)

| Model | Th | Percentile (val benign) | Percentile (attack benign) |
|---|---:|---:|---:|
| LSTM | 0.015817 | P97.29 | P97.73 |
| GRU | 0.014309 | P97.01 | P98.08 |

Both land near P97 of the validation benign score distribution, matching the ~1
percentile-point increase predicted in `OPSI-B-REKALIBRASI.md` §4.

## Global metrics — B-utama

| Model | Config | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | AUC |
|---|---|---:|---:|---:|---:|---:|---:|
| LSTM | Rule Only | 85.78% | 97.51% | 91.27% | 0.86% | 2.93% | N/A |
| LSTM | ML-Only | 97.09% | 94.35% | 95.70% | 2.27% | 2.71% | 0.9902 |
| LSTM | **Hybrid** | **98.43%** | **94.10%** | **96.22%** | **2.41%** | **4.97%** | N/A |
| GRU | Rule Only | 85.78% | 97.51% | 91.27% | 0.86% | 2.93% | N/A |
| GRU | ML-Only | 95.66% | 95.11% | 95.38% | 1.92% | 2.99% | 0.9895 |
| GRU | **Hybrid** | **98.08%** | **94.89%** | **96.46%** | **2.06%** | **4.97%** | N/A |

FPR(Attack) is now an out-of-sample measurement. It landed at 2.41% (LSTM) and
2.06% (GRU) — inside the 2.0–2.5% band predicted in `OPSI-B-REKALIBRASI.md` §4, and
below the old *calibrated* 2.99% without any circularity.

AUC is threshold-independent, but it does depend on which windows are the
negatives — and the two are not the same number. The values above use the
**attack-file benign windows** as negatives (n = 5723), i.e. the curve whose x-axis
is FPR(Attack), consistent with the reported operating point and with the ROC
figures. Substituting the validation set as negatives (n = 1772) gives **0.9931**
(LSTM) and **0.9909** (GRU) — a cross-session curve whose x-axis is FPR(Val), a
different quantity rather than a correction. Both are recorded under
`metadata.auc` in the evaluator JSON. For reference, the old Scheme A scoring gave
0.979.

## Recall per attack class — B-utama

| Model | Config | UL Flood | DL Flood | Burst | RoQ |
|---|---|---:|---:|---:|---:|
| — | Rule Only | 97.18% | 96.76% | 95.03% | 65.28% |
| LSTM | ML-Only | 97.65% | 89.68% | 99.03% | 98.26% |
| LSTM | **Hybrid** | **98.12%** | **96.76%** | **99.03%** | **98.79%** |
| GRU | ML-Only | 96.24% | 87.61% | 98.21% | 96.51% |
| GRU | **Hybrid** | **97.65%** | **96.76%** | **98.76%** | **98.26%** |

Every class clears 85% by at least 11.8 points. DL Flood Hybrid is exactly 96.76% —
identical to the Rule-Only floor, as predicted: ML detection on that class is a
subset of rule R2, so DL Flood recall is insensitive to `Th`.

## Sensitivity frontier — FPR(Val) targets 5.0% / 4.5% / 4.0%

| Model | Target FPR(Val) | Th | P(val) | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | RoQ recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LSTM | 5.0% | 0.015817 | P97.29 | 98.43% | 94.10% | 96.22% | 2.41% | 4.97% | 98.79% |
| LSTM | 4.5% | 0.020268 | P97.86 | 97.85% | 95.17% | 96.49% | 1.94% | 4.46% | 97.45% |
| LSTM | 4.0% | 0.025787 | P98.48 | 96.02% | 95.98% | 96.00% | 1.57% | 3.95% | 92.49% |
| GRU | 5.0% | 0.014309 | P97.01 | 98.08% | 94.89% | 96.46% | 2.06% | 4.97% | 98.26% |
| GRU | 4.5% | 0.018915 | P97.74 | 97.00% | 95.51% | 96.25% | 1.78% | 4.46% | 95.44% |
| GRU | 4.0% | 0.022145 | P98.59 | 95.93% | 95.76% | 95.84% | 1.66% | 3.95% | 92.36% |

Values are Hybrid. Tightening the budget from 5.0% to 4.0% costs 2.41 points of
recall on LSTM and 2.15 on GRU, and RoQ — the only class with a low rule floor —
still holds 92.4% at the tightest setting. The cost is graceful, so 5.0% is a
budget choice rather than a cliff edge. F1 is nearly flat across the whole frontier
(96.00–96.49% LSTM, 95.84–96.46% GRU) because recall lost is repaid in precision.

## FPR uncertainty — B-utama

Every FPR is reported with a 95% Wilson score interval, the number of **separate
false-alarm episodes** (runs of consecutive fired windows, counted per UE), and the
alerts surviving the xApp's 30 s `ALERT_COOLDOWN_MS`. Windows overlap 29/30, so a
window count massively overstates the independent information available.

**Monitored-time definition (one definition, both sets).** The per-UE window
cadence is exactly 1 Hz, so exposure = **windows / 3600**, applied to the exact
window set each FPR is computed on: all 1772 validation windows, and the 5723
`label==0` windows of the attack file. A false alarm can only occur while traffic
is benign, so benign window-time is the correct denominator — attack windows are
not false-alarm exposure. Validation = 1772/3600 = **0.4922 UE-hours**; attack
benign = 5723/3600 = **1.5897 UE-hours**.

| Model | Config | Set | FPR | 95% CI | Windows | Episodes | Alerts after cooldown | Alerts / UE-hour |
|---|---|---|---:|---|---:|---:|---:|---:|
| — | Rule Only | Attack | 0.86% | [0.65% ; 1.13%] | 49 / 5723 | 8 | 6 | 3.77 |
| — | Rule Only | Val | 2.93% | [2.24% ; 3.83%] | 52 / 1772 | 6 | 5 | 10.16 |
| LSTM | ML-Only | Attack | 2.27% | [1.92% ; 2.69%] | 130 / 5723 | 10 | 8 | 5.03 |
| LSTM | ML-Only | Val | 2.71% | [2.05% ; 3.57%] | 48 / 1772 | 4 | 3 | 6.09 |
| LSTM | Hybrid | Attack | 2.41% | [2.04% ; 2.84%] | 138 / 5723 | 11 | 9 | 5.66 |
| LSTM | Hybrid | Val | 4.97% | [4.05% ; 6.08%] | 88 / 1772 | 8 | 5 | 10.16 |
| GRU | ML-Only | Attack | 1.92% | [1.60% ; 2.31%] | 110 / 5723 | 8 | 7 | 4.40 |
| GRU | ML-Only | Val | 2.99% | [2.29% ; 3.89%] | 53 / 1772 | 2 | 3 | 6.09 |
| GRU | Hybrid | Attack | 2.06% | [1.72% ; 2.46%] | 118 / 5723 | 9 | 8 | 5.03 |
| GRU | Hybrid | Val | 4.97% | [4.05% ; 6.08%] | 88 / 1772 | 5 | 5 | 10.16 |

**Alerts may exceed episodes.** The 30 s cooldown clock runs per-UE and
continuously, not per-episode, so an episode longer than 30 s emits more than one
alert, while closely spaced episodes can collapse into one. Verified example: the
`GRU · ML-Only · Val` row has two episodes lasting **31.0 s** and **20.0 s**; the
31.0 s one crosses the cooldown and yields 2 alerts, giving 3 alerts from 2
episodes. The two columns are therefore not monotonically related, and that is
correct behaviour. Per-episode durations are recorded in `results/opsi_b/opsi_b.json`
under `episode_durations_s`.

The interval on FPR(Val) spans about 2 percentage points — wider than any
adjustment made in this run. **No FPR in the thesis may be quoted to two decimals
without its interval**; the whole estimate rests on one 30-minute benign session
with one UE, i.e. 6 independent Rule-Only false-alarm episodes and roughly 60
non-overlapping windows.

Wilson score intervals are used throughout. For reference, the normal (Wald)
approximation quoted earlier for the pre-Opsi-B FPR(Val) of 5.76% gives
[4.67% ; 6.84%]; Wilson gives [4.76% ; 6.94%] for the same count. The conclusion is
the same either way — the interval is roughly three times the size of the
adjustment being argued over.

## Acceptance criteria

| Criterion | Result |
|---|---|
| **FPR(Attack) ≤ 5% — the primary compliance claim, out-of-sample** | ✅ **LSTM 2.41% CI [2.04% ; 2.84%] · GRU 2.06% CI [1.72% ; 2.46%] — the entire interval sits below the 5% budget** |
| FPR(Val) ≤ 5.0% | ✅ 4.97% both models — satisfied *by construction* of the calibration; CI upper bound **6.08%** |
| FPR(Attack) reported as a measurement, never forced | ✅ measured, not targeted |
| F1 ≥ 90% | ✅ 96.22% LSTM / 96.46% GRU |
| Recall ≥ 85% for every attack class | ✅ minimum 96.76% (DL Flood), both models |
| Rule-Only sanity table reproduces exactly | ✅ asserted in code, all 8 rows |
| Th reported with val-benign and attack-benign percentiles | ✅ see threshold table |
| Every FPR carries CI, episode count, post-cooldown alert rate | ✅ see uncertainty table |
| Every FPR names its set explicitly | ✅ `FPR(Attack)` / `FPR(Val)` throughout |

## Detection latency — read the dispersion, not the mean

Every per-class detection-latency cell rests on **n = 2 or 3 attack segments**, and
the spread is wide (several classes span 0-6 s). Quoting a mean to two decimals here
repeats exactly the false-precision failure diagnosed for FPR, so latency is reported
as **median + [min ; max] + n** in
[opsi_b_metrics.md](opsi_b_metrics.md) §7a.

What survives the small sample and may be claimed: Hybrid is never slower than its
components, and on DL Flood it cuts ML latency sharply (LSTM median 16.00 s -> 5.00 s;
GRU 21.00 s -> 5.00 s) because rule R2 fires first. Sub-second differences between
classes or between architectures may **not** be claimed.

Mitigation latency is detection latency + exactly 1.0 s on every row. It is derived
by construction, not an independent measurement, and adds no evidence of its own.

Hybrid can be faster than **both** of its components (GRU UL Flood median 4.00 s vs
Rule 6.00 s and ML 4.00 s) because the statistic is over per-segment minima, and
E[min(X,Y)] <= min(E[X],E[Y]) always holds. That is mathematically correct, not a bug.

## Validity boundary

Compared with the ablation run this supersedes, the threshold leakage path moves
from **open** to **closed**:

| Quantity | Before (calibrated on the attack file) | After (Opsi B) |
|---|---|---|
| Recall, global and per class | clean — never sees attack labels | clean, unchanged in kind |
| AUC | clean — threshold-independent | clean, unchanged in kind |
| FPR(Attack) | **a setting** (forced to 2.99%) | **an out-of-sample measurement** (2.41% / 2.06%) |
| Precision | inherited the optimism | clean |
| F1 | inherited the optimism via precision | clean |
| FPR(Val) | 5.76%, above the 5% budget | 4.97%, **in-sample** with respect to calibration |

Calibration relocates circularity rather than removing it, and that is the point.
After Opsi B, FPR(Val) is a *set* quantity — that is what a calibration set is for —
and FPR(Attack) is the out-of-sample estimate. **The constraint-compliance claim is
therefore carried by FPR(Attack), not FPR(Val).** Training weights, scoring weights,
and threshold selection are now all free of attack data and attack labels; the only
attack-derived quantity in the pipeline is the reported result itself.

**The ablation-arm choice cannot be a source of optimism, and the direction proves
it.** One selection in this project was made with knowledge of test-set metrics: which
loss-weighting arm to adopt. In `loss_ablation_results.md` the *benign* arm scored
higher on both architectures (LSTM Hybrid recall 98.88% vs uniform 98.70%; GRU 98.97%
vs 98.26%), yet the **uniform** arm was adopted, on grounds of simplicity. The arm
that was selected is therefore the one that scored **lower** on the test file. Whatever
optimism that selection path could contribute is bounded at or below zero: 0.18 points
(LSTM) and 0.71 points (GRU) were given up, not taken. A choice was indeed made with
test-metric knowledge, but it was resolved **against** that metric.

## Limitation — the attack file is a cleaner test

FPR(Attack) is independent, but it is also an easier exam than FPR(Val). The benign
traffic inside the attack session is far more docile than the validation session:
Rule-Only fires on 0.86% of its benign windows versus 2.93% on validation, and the
median `thp_dl` over alarm-triggering benign windows is **32 kbps** on the attack
file versus **76,790 kbps** on validation. The validation session contains
speedtest activity; the benign UEs during the attack session are mostly idle.

So the two numbers should be read side by side and each labelled for what it is:
FPR(Attack) is clean but lenient; FPR(Val) is harsh but in-sample with respect to
the calibration. An honestly cross-session FPR estimate needs a **third benign
session**, collected separately from both the calibration and the attack runs. That
session does not exist yet and belongs in the Bab V future-work list.

## Figures

Regenerated at the new thresholds in `eval_figures/final_hybrid/`:

- [LSTM confusion matrices — Rule-Only / ML-Only / Hybrid](../eval_figures/final_hybrid/eval_confusion_lstm.png)
- [GRU confusion matrices — Rule-Only / ML-Only / Hybrid](../eval_figures/final_hybrid/eval_confusion_gru.png)
- [LSTM ROC with Rule-Only and new-Th operating points](../eval_figures/final_hybrid/eval_roc_lstm.png)
- [GRU ROC with Rule-Only and new-Th operating points](../eval_figures/final_hybrid/eval_roc_gru.png)
- [LSTM per-class recall](../eval_figures/final_hybrid/eval_per_class_lstm.png)
- [GRU per-class recall](../eval_figures/final_hybrid/eval_per_class_gru.png)
- [Calibration frontier — recall cost and measured FPR(Attack)](../eval_figures/final_hybrid/eval_calibration_frontier.png)

Every panel annotates its FPR with the set name. A vector PDF sits beside each PNG.
Colors use the colorblind-safe Okabe-Ito palette (validated: worst adjacent-pair
separation ΔE 11.0 under deuteranopia) with redundant hatch patterns for grayscale
printing. Bars start at zero. There are no error bars on the single-seed comparison
figures — the FPR intervals live in the table above, where they are actually
measured, rather than being implied on bars that never estimated them.

The frontier figure uses two panels rather than a second y-axis: recall and
FPR(Attack) are different measures on different scales and must not share an axis.
