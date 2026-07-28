# Benign-Calibrated Detection @ Hybrid FPR(Attack) <= 3%

## Threshold (benign-calibrated weighted MSE)
| Model | Th | Percentile (val benign) | Percentile (attack benign) |
|---|---|---|---|
| GRU | 0.006654 | P95.32 | P97.15 |
| LSTM | 0.008619 | P95.32 | P97.15 |

## Global metrics
| Model | Config | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | AUC |
|---|---|---|---|---|---|---|---|
| GRU | Rule Only | 85.78% | 97.51% | 91.27% | 0.86% | 2.93% | N/A |
| GRU | ML-Only (benign) | 97.14% | 93.02% | 95.03% | 2.85% | 4.68% | 0.9912 |
| GRU | Hybrid (Rule OR benign) | 98.61% | 92.80% | 95.62% | 2.99% | 5.87% | N/A |
| LSTM | Rule Only | 85.78% | 97.51% | 91.27% | 0.86% | 2.93% | N/A |
| LSTM | ML-Only (benign) | 97.99% | 93.08% | 95.47% | 2.85% | 4.68% | 0.9896 |
| LSTM | Hybrid (Rule OR benign) | 98.88% | 92.82% | 95.76% | 2.99% | 5.81% | N/A |

## Per-class recall
| Model | Config | UL Flood | DL Flood | Burst | RoQ |
|---|---|---|---|---|---|
| GRU | Rule Only | 97.18% | 96.76% | 95.03% | 65.28% |
| GRU | ML-Only (benign) | 98.83% | 87.91% | 98.76% | 98.79% |
| GRU | Hybrid (Rule OR benign) | 98.83% | 96.76% | 98.90% | 99.06% |
| LSTM | Rule Only | 97.18% | 96.76% | 95.03% | 65.28% |
| LSTM | ML-Only (benign) | 98.83% | 90.86% | 99.31% | 99.46% |
| LSTM | Hybrid (Rule OR benign) | 98.83% | 96.76% | 99.31% | 99.46% |
