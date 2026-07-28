# Scoring Comparison (held-out on dataset_attack_ue_juni.csv)

| Model | Scoring | Leakage-free | Recall@P97 | Held-out FPR | AUC | Recall@5% | Recall@3% |
|---|---|---|---|---|---|---|---|
| GRU | uniform | yes | 0.6534 | 0.0166 | 0.9756 | 0.8927 | 0.7996 |
| GRU | benign | yes | 0.8189 | 0.0171 | 0.9912 | 0.9861 | 0.9732 |
| GRU | attack | NO (biased) | 0.9316 | 0.0204 | 0.9903 | 0.9911 | 0.9700 |
| LSTM | uniform | yes | 0.7701 | 0.0201 | 0.9631 | 0.9137 | 0.8792 |
| LSTM | benign | yes | 0.8318 | 0.0210 | 0.9896 | 0.9893 | 0.9799 |
| LSTM | attack | NO (biased) | 0.9047 | 0.0236 | 0.9876 | 0.9919 | 0.9741 |
