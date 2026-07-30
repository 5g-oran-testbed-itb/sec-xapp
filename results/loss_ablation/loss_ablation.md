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
