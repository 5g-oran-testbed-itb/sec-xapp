# Scoring Comparison @ FPR(Attack) <= 3% (held-out on dataset_attack_ue_juni.csv)

## Global metrics
| Model | Scoring | Leakage-free | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | AUC |
|---|---|---|---|---|---|---|---|---|
| GRU | uniform | yes | 79.96% | 91.27% | 85.24% | 2.99% | 8.13% | 0.9756 |
| GRU | benign | yes | 97.32% | 92.71% | 94.96% | 2.99% | 5.14% | 0.9912 |
| GRU | attack | NO (biased) | 97.00% | 92.69% | 94.80% | 2.99% | 7.34% | 0.9903 |
| LSTM | uniform | yes | 87.92% | 92.00% | 89.92% | 2.99% | 7.17% | 0.9631 |
| LSTM | benign | yes | 97.99% | 92.76% | 95.30% | 2.99% | 4.80% | 0.9896 |
| LSTM | attack | NO (biased) | 97.41% | 92.72% | 95.01% | 2.99% | 7.56% | 0.9876 |

## Per-class recall
| Model | Scoring | UL Flood | DL Flood | Burst | RoQ |
|---|---|---|---|---|---|
| GRU | uniform | 28.40% | 92.63% | 97.79% | 86.33% |
| GRU | benign | 98.83% | 88.20% | 99.03% | 98.93% |
| GRU | attack | 96.71% | 92.92% | 98.34% | 97.72% |
| LSTM | uniform | 90.38% | 95.28% | 99.17% | 72.25% |
| LSTM | benign | 98.83% | 90.86% | 99.31% | 99.46% |
| LSTM | attack | 98.59% | 95.87% | 99.31% | 95.58% |
