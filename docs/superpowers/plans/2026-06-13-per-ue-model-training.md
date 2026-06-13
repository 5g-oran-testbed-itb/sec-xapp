# Per-UE GRU & LSTM Autoencoder Training Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create separate per-UE feature schema and training scripts for GRU and LSTM autoencoders, then train both models on `dataset_training_ue_juni.csv` / `dataset_validation_ue_juni.csv`.

**Architecture:** New `feature_schema_ue.py` (15 per-UE features) + two new training scripts (`train_gru_ue.py`, `train_lstm_ue.py`) that are stripped-down copies of the originals. RobustScaler replaces MinMaxScaler for zero-heavy per-UE distributions. No existing files are modified.

**Tech Stack:** Python 3, PyTorch, scikit-learn (RobustScaler), pandas, numpy. All scripts run inside `/home/telmat/sec-xapp/venv`.

---

## File Map

| File | Action |
|------|--------|
| `src/detection/feature_schema_ue.py` | Create — 15 per-UE feature names |
| `train_gru_ue.py` | Create — GRU training script |
| `train_lstm_ue.py` | Create — LSTM training script |
| `models/gru_ue_v1.pt` | Output from Task 4 |
| `models/gru_ue_v1_scaler.pkl` | Output from Task 4 |
| `models/gru_ue_v1_threshold.json` | Output from Task 4 |
| `models/gru_ue_v1_losses.json` | Output from Task 4 |
| `models/lstm_ue_v1.pt` | Output from Task 5 |
| `models/lstm_ue_v1_scaler.pkl` | Output from Task 5 |
| `models/lstm_ue_v1_threshold.json` | Output from Task 5 |
| `models/lstm_ue_v1_losses.json` | Output from Task 5 |

---

## Task 1: Create `feature_schema_ue.py`

**Files:**
- Create: `src/detection/feature_schema_ue.py`

- [ ] **Step 1: Write the file**

```python
# src/detection/feature_schema_ue.py
# 15 per-UE features from KPM FORMAT_3 + MAC PRB fallback.
# Names match exactly the CSV columns written by csv_per_ue_write() in xapp_sec_moni.c.
# Cell-level feature_schema.py is untouched — these two schemas are independent.

FEATURE_NAMES = [
    "prb_usage_dl_ratio",   # RRU.PrbUsedDl / 100 (from KPM or MAC fallback), clipped [0,1]
    "prb_usage_ul_ratio",   # RRU.PrbUsedUl / 100 (from KPM or MAC fallback), clipped [0,1]
    "thp_dl_kbps",          # DRB.UEThpDl (kbps)
    "thp_ul_kbps",          # DRB.UEThpUl (kbps)
    "prb_direction",        # (prb_ul - prb_dl) / (prb_total + eps), bounded [-1, +1]
    "prb_total",            # prb_dl + prb_ul, clipped [0, 1]
    "prb_ul_delta",         # prb_ul[t] - prb_ul[t-1]
    "ul_efficiency",        # thp_ul / prb_ul, clipped [0, 50000]
    "prb_ul_roll_mean",     # rolling mean prb_ul_ratio over 10 timesteps
    "prb_ul_roll_std",      # rolling std  prb_ul_ratio over 10 timesteps
    "ul_persistence",       # fraction of last 10 ts with prb_ul > 0, in [0, 1]
    "thp_total_kbps",       # thp_dl + thp_ul (kbps)
    "thp_ul_delta",         # thp_ul[t] - thp_ul[t-1] (kbps)
    "thp_dl_delta",         # thp_dl[t] - thp_dl[t-1] (kbps)
    "traffic_direction",    # (thp_ul - thp_dl) / (thp_total + eps), bounded [-1, +1]
]

NUM_FEATURES = len(FEATURE_NAMES)   # 15

# Uniform weighting — no per-feature weighting for per-UE models yet.
FEATURE_WEIGHTS: dict = {}
```

- [ ] **Step 2: Verify NUM_FEATURES**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 -c "
from src.detection.feature_schema_ue import FEATURE_NAMES, NUM_FEATURES
print('NUM_FEATURES =', NUM_FEATURES)
assert NUM_FEATURES == 15, f'Expected 15, got {NUM_FEATURES}'
print('OK')
"
```

Expected output:
```
NUM_FEATURES = 15
OK
```

- [ ] **Step 3: Verify all features exist in training CSV**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 -c "
import pandas as pd
from src.detection.feature_schema_ue import FEATURE_NAMES
df = pd.read_csv('csv/dataset_training_ue_juni.csv', nrows=1)
missing = [f for f in FEATURE_NAMES if f not in df.columns]
if missing:
    print('MISSING:', missing)
else:
    print('All 15 features present in CSV — OK')
"
```

Expected output:
```
All 15 features present in CSV — OK
```

- [ ] **Step 4: Commit**

```bash
cd /home/telmat/sec-xapp
git add src/detection/feature_schema_ue.py
git commit -m "feat: add per-UE feature schema (15 features)"
```

---

## Task 2: Create `train_gru_ue.py`

**Files:**
- Create: `train_gru_ue.py`

- [ ] **Step 1: Write the file**

```python
# train_gru_ue.py
"""Train GRU Autoencoder on per-UE KPM dataset.

Uses feature_schema_ue (15 features) and RobustScaler.
Does NOT modify feature_schema.py or models/scaler.pkl.

Usage:
  ./venv/bin/python3 train_gru_ue.py \\
    --train csv/dataset_training_ue_juni.csv \\
    --val   csv/dataset_validation_ue_juni.csv \\
    --seq-len 10 --epochs 150 \\
    --model-out models/gru_ue_v1.pt
"""

import argparse
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.preprocessing import RobustScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.feature_schema_ue import FEATURE_NAMES, FEATURE_WEIGHTS as _FW_DICT

_FEATURE_WEIGHTS = torch.tensor(
    [_FW_DICT.get(n, 1.0) for n in FEATURE_NAMES], dtype=torch.float32
)

EPS = 1e-6


def load_csv(path: str, label_filter: int = 0) -> pd.DataFrame:
    df = pd.read_csv(path)
    print(f"  Loaded: {path}  ({len(df)} rows)")
    if 'label' in df.columns:
        before = len(df)
        df = df[df['label'] == label_filter]
        print(f"  Filter label={label_filter}: {before} → {len(df)} rows")
    for f in FEATURE_NAMES:
        if f not in df.columns:
            print(f"Error: column '{f}' missing in {path}")
            sys.exit(1)
    return df


def prepare_sequences(data: np.ndarray, seq_len: int) -> np.ndarray:
    return np.array(
        [data[i:i + seq_len] for i in range(len(data) - seq_len + 1)],
        dtype=np.float32,
    )


def weighted_mse(output: torch.Tensor, target: torch.Tensor,
                 weights: torch.Tensor) -> torch.Tensor:
    err = (output - target) ** 2
    return (err * weights).mean()


def compute_val_loss(model: GRUAutoencoder, val_seqs: np.ndarray,
                     batch_size: int = 256) -> float:
    model.eval()
    total, count = 0.0, 0
    with torch.no_grad():
        for i in range(0, len(val_seqs), batch_size):
            batch = torch.FloatTensor(val_seqs[i:i + batch_size])
            out = model(batch)
            total += nn.functional.mse_loss(out, batch).item() * len(batch)
            count += len(batch)
    model.train()
    return total / count if count > 0 else 0.0


def train(model: GRUAutoencoder, train_norm: np.ndarray, val_seqs: np.ndarray,
          epochs: int, batch_size: int, lr: float):
    seq_len = model.seq_len
    train_seqs = prepare_sequences(train_norm, seq_len)
    dataset = torch.utils.data.TensorDataset(torch.FloatTensor(train_seqs))
    loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    fw = _FEATURE_WEIGHTS

    best_val, best_state, best_epoch = float('inf'), None, 0
    train_losses, val_losses = [], []

    print(f"[GRU] Training {epochs} epochs  seq_len={seq_len}  lr={lr}  "
          f"train_seqs={len(train_seqs)}  val_seqs={len(val_seqs)}")

    for epoch in range(1, epochs + 1):
        model.train()
        t_total = 0.0
        for (batch,) in loader:
            optimizer.zero_grad()
            out = model(batch)
            loss = weighted_mse(out, batch, fw)
            loss.backward()
            optimizer.step()
            t_total += loss.item()
        t_avg = t_total / len(loader)
        v_avg = compute_val_loss(model, val_seqs, batch_size)
        train_losses.append(t_avg)
        val_losses.append(v_avg)
        if v_avg < best_val:
            best_val = v_avg
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            best_epoch = epoch
        if epoch % 10 == 0:
            print(f"  Epoch {epoch:3d}/{epochs}  train={t_avg:.6f}  val={v_avg:.6f}"
                  f"{'  ← best' if epoch == best_epoch else ''}")

    if best_state:
        model.load_state_dict(best_state)
        print(f"[GRU] Best checkpoint: epoch {best_epoch} (val={best_val:.6f})")

    return train_losses, val_losses, best_epoch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train",     type=str, required=True)
    parser.add_argument("--val",       type=str, required=True)
    parser.add_argument("--seq-len",   type=int,   default=10)
    parser.add_argument("--epochs",    type=int,   default=150)
    parser.add_argument("--batch-size",type=int,   default=32)
    parser.add_argument("--lr",        type=float, default=0.001)
    parser.add_argument("--model-out", type=str,   default="models/gru_ue_v1.pt")
    parser.add_argument("--threshold-percentile", type=float, default=99.0)
    args = parser.parse_args()

    os.makedirs('models', exist_ok=True)

    print(f"[*] Loading training CSV: {args.train}")
    df_train = load_csv(args.train)

    print(f"[*] Loading validation CSV: {args.val}")
    df_val = load_csv(args.val)

    scaler = RobustScaler()
    train_raw = df_train[FEATURE_NAMES].values.astype(np.float32)
    val_raw   = df_val[FEATURE_NAMES].values.astype(np.float32)
    scaler.fit(train_raw)

    train_norm = scaler.transform(train_raw)
    val_norm   = scaler.transform(val_raw)

    # Save scaler alongside model (does NOT overwrite models/scaler.pkl)
    scaler_path = args.model_out.replace('.pt', '_scaler.pkl')
    with open(scaler_path, 'wb') as f:
        pickle.dump(scaler, f)
    print(f"[*] Scaler saved: {scaler_path}")

    val_seqs = prepare_sequences(val_norm, args.seq_len)

    config = {
        'gru_model': {
            'input_features': len(FEATURE_NAMES),
            'encoder_hidden': [64, 32],
            'decoder_hidden': [32, 64],
            'latent_dim':     32,
            'bidirectional':  True,
        },
        'detection': {'sequence_length': args.seq_len},
    }
    model = GRUAutoencoder(config)
    param_count = sum(p.numel() for p in model.parameters())
    print(f"[*] GRU-AE params: {param_count:,}  features={len(FEATURE_NAMES)}")

    train_losses, val_losses, best_epoch = train(
        model, train_norm, val_seqs,
        epochs=args.epochs, batch_size=args.batch_size, lr=args.lr,
    )

    losses_path = args.model_out.replace('.pt', '_losses.json')
    with open(losses_path, 'w') as f:
        json.dump({'train': train_losses, 'val': val_losses, 'best_epoch': best_epoch}, f)
    print(f"[*] Loss history: {losses_path}")

    model.eval()
    with torch.no_grad():
        val_tensor = torch.FloatTensor(val_seqs)
        val_errors = model.compute_reconstruction_error(val_tensor).numpy()

    mu_val     = float(np.mean(val_errors))
    std_val    = float(np.std(val_errors))
    percentile = args.threshold_percentile
    thresh_val = float(np.percentile(val_errors, percentile))
    fpr_val    = float(np.mean(val_errors > thresh_val) * 100)

    model.fit_threshold(val_errors, percentile)
    model.save(args.model_out)

    threshold_path = args.model_out.replace('.pt', '_threshold.json')
    with open(threshold_path, 'w') as f:
        json.dump({
            'mu':         mu_val,
            'sigma':      std_val,
            'threshold':  thresh_val,
            'percentile': percentile,
            'fpr_pct':    fpr_val,
            'source':     'validation_set',
        }, f, indent=2)

    print(f"\n[*] Done. Model: {args.model_out}")
    print(f"    seq_len={args.seq_len}  best_epoch={best_epoch}/{args.epochs}")
    print(f"    Threshold (P{percentile}): {thresh_val:.6f}  FPR={fpr_val:.2f}%")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test — 2 epochs to verify no crash**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 train_gru_ue.py \
  --train csv/dataset_training_ue_juni.csv \
  --val   csv/dataset_validation_ue_juni.csv \
  --seq-len 10 --epochs 2 \
  --model-out models/gru_ue_smoketest.pt
```

Expected: runs to completion, prints loss for epochs 1 and 2, creates
`models/gru_ue_smoketest.pt`, `models/gru_ue_smoketest_scaler.pkl`,
`models/gru_ue_smoketest_threshold.json`. No NaN in losses.

- [ ] **Step 3: Clean up smoke test files**

```bash
rm -f models/gru_ue_smoketest.pt models/gru_ue_smoketest_scaler.pkl \
      models/gru_ue_smoketest_threshold.json models/gru_ue_smoketest_losses.json
```

- [ ] **Step 4: Commit**

```bash
cd /home/telmat/sec-xapp
git add train_gru_ue.py
git commit -m "feat: add train_gru_ue.py for per-UE GRU autoencoder"
```

---

## Task 3: Create `train_lstm_ue.py`

**Files:**
- Create: `train_lstm_ue.py`

- [ ] **Step 1: Write the file**

```python
# train_lstm_ue.py
"""Train LSTM Autoencoder on per-UE KPM dataset.

Uses feature_schema_ue (15 features) and RobustScaler.
Does NOT modify feature_schema.py or models/scaler.pkl.

Usage:
  ./venv/bin/python3 train_lstm_ue.py \\
    --train csv/dataset_training_ue_juni.csv \\
    --val   csv/dataset_validation_ue_juni.csv \\
    --seq-len 10 --epochs 150 \\
    --model-out models/lstm_ue_v1.pt
"""

import argparse
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import RobustScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.detection.lstm_autoencoder import LSTMAutoencoder, ModelTrainer
from src.detection.feature_schema_ue import FEATURE_NAMES, FEATURE_WEIGHTS as _FW_DICT

_FEATURE_WEIGHTS = torch.tensor(
    [_FW_DICT.get(n, 1.0) for n in FEATURE_NAMES], dtype=torch.float32
)


def load_csv(path: str, label_filter: int = 0) -> pd.DataFrame:
    df = pd.read_csv(path)
    print(f"  Loaded: {path}  ({len(df)} rows)")
    if 'label' in df.columns:
        before = len(df)
        df = df[df['label'] == label_filter]
        print(f"  Filter label={label_filter}: {before} → {len(df)} rows")
    for f in FEATURE_NAMES:
        if f not in df.columns:
            print(f"Error: column '{f}' missing in {path}")
            sys.exit(1)
    return df


def prepare_sequences(data: np.ndarray, seq_len: int = 10) -> np.ndarray:
    return np.array(
        [data[i:i + seq_len] for i in range(len(data) - seq_len + 1)],
        dtype=np.float32,
    )


def weighted_mse(output: torch.Tensor, target: torch.Tensor,
                 weights: torch.Tensor) -> torch.Tensor:
    err = (output - target) ** 2
    return (err * weights).mean()


def train_with_val(model: LSTMAutoencoder, trainer: ModelTrainer,
                   train_data: np.ndarray, val_seqs: np.ndarray,
                   epochs: int, batch_size: int):
    seq_len = model.seq_len
    train_seqs = prepare_sequences(train_data, seq_len)
    fw = _FEATURE_WEIGHTS

    best_val, best_state, best_epoch = float('inf'), None, 0
    train_losses, val_losses = [], []

    print(f"[LSTM] Training {epochs} epochs  seq_len={seq_len}  "
          f"train_seqs={len(train_seqs)}  val_seqs={len(val_seqs)}")

    for epoch in range(1, epochs + 1):
        model.train()
        epoch_loss, n_batches = 0.0, 0
        indices = np.random.permutation(len(train_seqs))
        for start in range(0, len(train_seqs), batch_size):
            batch_idx = indices[start:start + batch_size]
            batch = torch.FloatTensor(train_seqs[batch_idx])
            trainer.optimizer.zero_grad()
            out = model(batch)
            loss = weighted_mse(out, batch, fw)
            loss.backward()
            trainer.optimizer.step()
            epoch_loss += loss.item()
            n_batches += 1
        t_loss = epoch_loss / n_batches

        model.eval()
        with torch.no_grad():
            val_tensor = torch.FloatTensor(val_seqs)
            val_out = model(val_tensor)
            v_loss = weighted_mse(val_out, val_tensor, fw).item()
        model.train()

        train_losses.append(t_loss)
        val_losses.append(v_loss)

        if v_loss < best_val:
            best_val = v_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            best_epoch = epoch

        if epoch % 10 == 0:
            print(f"  Epoch {epoch:3d}/{epochs}  train={t_loss:.6f}  val={v_loss:.6f}"
                  f"{'  ← best' if epoch == best_epoch else ''}")

    if best_state:
        model.load_state_dict(best_state)
        print(f"[LSTM] Best checkpoint: epoch {best_epoch} (val={best_val:.6f})")

    return train_losses, val_losses, best_epoch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train",     type=str, required=True)
    parser.add_argument("--val",       type=str, required=True)
    parser.add_argument("--seq-len",   type=int,   default=10)
    parser.add_argument("--epochs",    type=int,   default=150)
    parser.add_argument("--batch-size",type=int,   default=32)
    parser.add_argument("--lr",        type=float, default=0.001)
    parser.add_argument("--model-out", type=str,   default="models/lstm_ue_v1.pt")
    parser.add_argument("--threshold-percentile", type=float, default=99.0)
    args = parser.parse_args()

    os.makedirs('models', exist_ok=True)

    print(f"[*] Loading training CSV: {args.train}")
    df_train = load_csv(args.train)

    print(f"[*] Loading validation CSV: {args.val}")
    df_val = load_csv(args.val)

    scaler = RobustScaler()
    train_raw = df_train[FEATURE_NAMES].values.astype(np.float32)
    val_raw   = df_val[FEATURE_NAMES].values.astype(np.float32)
    scaler.fit(train_raw)

    train_norm = scaler.transform(train_raw)
    val_norm   = scaler.transform(val_raw)

    # Save scaler alongside model (does NOT overwrite models/scaler.pkl)
    scaler_path = args.model_out.replace('.pt', '_scaler.pkl')
    with open(scaler_path, 'wb') as f:
        pickle.dump(scaler, f)
    print(f"[*] Scaler saved: {scaler_path}")

    val_seqs = prepare_sequences(val_norm, args.seq_len)

    config = {
        'lstm_model': {
            'input_features': len(FEATURE_NAMES),
            'encoder_hidden': [64, 32],
            'decoder_hidden': [32, 64],
            'latent_dim':     32,
            'bidirectional':  False,
            'learning_rate':  args.lr,
            'epochs':         args.epochs,
            'batch_size':     args.batch_size,
        },
        'detection': {
            'sequence_length':              args.seq_len,
            'anomaly_threshold_percentile': args.threshold_percentile,
        },
    }

    model   = LSTMAutoencoder(config)
    trainer = ModelTrainer(model, config)
    param_count = sum(p.numel() for p in model.parameters())
    print(f"[*] LSTM-AE params: {param_count:,}  features={len(FEATURE_NAMES)}")

    train_losses, val_losses, best_epoch = train_with_val(
        model, trainer, train_norm, val_seqs,
        epochs=args.epochs, batch_size=args.batch_size,
    )

    losses_path = args.model_out.replace('.pt', '_losses.json')
    with open(losses_path, 'w') as f:
        json.dump({'train': train_losses, 'val': val_losses, 'best_epoch': best_epoch}, f)
    print(f"[*] Loss history: {losses_path}")

    model.save(args.model_out)

    model.eval()
    with torch.no_grad():
        val_tensor = torch.FloatTensor(val_seqs)
        val_errors = model.compute_reconstruction_error(val_tensor).numpy()

    mu_val     = float(np.mean(val_errors))
    std_val    = float(np.std(val_errors))
    percentile = args.threshold_percentile
    thresh_val = float(np.percentile(val_errors, percentile))
    fpr_val    = float(np.mean(val_errors > thresh_val) * 100)

    model.fit_threshold(val_errors, percentile)

    threshold_path = args.model_out.replace('.pt', '_threshold.json')
    with open(threshold_path, 'w') as f:
        json.dump({
            'mu':         mu_val,
            'sigma':      std_val,
            'threshold':  thresh_val,
            'percentile': percentile,
            'fpr_pct':    fpr_val,
            'source':     'validation_set',
        }, f, indent=2)

    print(f"\n[*] Done. Model: {args.model_out}")
    print(f"    seq_len={args.seq_len}  best_epoch={best_epoch}/{args.epochs}")
    print(f"    Threshold (P{percentile}): {thresh_val:.6f}  FPR={fpr_val:.2f}%")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test — 2 epochs**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 train_lstm_ue.py \
  --train csv/dataset_training_ue_juni.csv \
  --val   csv/dataset_validation_ue_juni.csv \
  --seq-len 10 --epochs 2 \
  --model-out models/lstm_ue_smoketest.pt
```

Expected: runs to completion, prints loss for epochs 1 and 2, creates
`models/lstm_ue_smoketest.pt`, `models/lstm_ue_smoketest_scaler.pkl`,
`models/lstm_ue_smoketest_threshold.json`. No NaN in losses.

- [ ] **Step 3: Clean up smoke test files**

```bash
rm -f models/lstm_ue_smoketest.pt models/lstm_ue_smoketest_scaler.pkl \
      models/lstm_ue_smoketest_threshold.json models/lstm_ue_smoketest_losses.json
```

- [ ] **Step 4: Commit**

```bash
cd /home/telmat/sec-xapp
git add train_lstm_ue.py
git commit -m "feat: add train_lstm_ue.py for per-UE LSTM autoencoder"
```

---

## Task 4: Train GRU model (full run)

**Files:**
- Output: `models/gru_ue_v1.pt`, `models/gru_ue_v1_scaler.pkl`,
  `models/gru_ue_v1_threshold.json`, `models/gru_ue_v1_losses.json`

- [ ] **Step 1: Run full GRU training**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 train_gru_ue.py \
  --train csv/dataset_training_ue_juni.csv \
  --val   csv/dataset_validation_ue_juni.csv \
  --seq-len 10 --epochs 150 --batch-size 32 --lr 0.001 \
  --model-out models/gru_ue_v1.pt \
  2>&1 | tee models/gru_ue_v1_train.log
```

Expected: ~150 epoch lines, final summary like:
```
[*] Done. Model: models/gru_ue_v1.pt
    seq_len=10  best_epoch=XX/150
    Threshold (P99.0): X.XXXXXX  FPR=X.XX%
```
FPR target: ≤ 2%.

- [ ] **Step 2: Verify output files exist and threshold is sane**

```bash
cd /home/telmat/sec-xapp
python3 -c "
import json
t = json.load(open('models/gru_ue_v1_threshold.json'))
print('GRU threshold:', t)
assert t['fpr_pct'] <= 5.0, f'FPR too high: {t[\"fpr_pct\"]}%'
print('FPR check OK')
"
```

Expected: prints threshold dict, asserts FPR ≤ 5%.

- [ ] **Step 3: Commit trained model**

```bash
cd /home/telmat/sec-xapp
git add models/gru_ue_v1.pt models/gru_ue_v1_scaler.pkl \
        models/gru_ue_v1_threshold.json models/gru_ue_v1_losses.json \
        models/gru_ue_v1_train.log
git commit -m "feat: train GRU per-UE autoencoder v1 (15 features, seq_len=10)"
```

---

## Task 5: Train LSTM model (full run)

**Files:**
- Output: `models/lstm_ue_v1.pt`, `models/lstm_ue_v1_scaler.pkl`,
  `models/lstm_ue_v1_threshold.json`, `models/lstm_ue_v1_losses.json`

- [ ] **Step 1: Run full LSTM training**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 train_lstm_ue.py \
  --train csv/dataset_training_ue_juni.csv \
  --val   csv/dataset_validation_ue_juni.csv \
  --seq-len 10 --epochs 150 --batch-size 32 --lr 0.001 \
  --model-out models/lstm_ue_v1.pt \
  2>&1 | tee models/lstm_ue_v1_train.log
```

Expected: ~150 epoch lines, final summary like:
```
[*] Done. Model: models/lstm_ue_v1.pt
    seq_len=10  best_epoch=XX/150
    Threshold (P99.0): X.XXXXXX  FPR=X.XX%
```
FPR target: ≤ 2%.

- [ ] **Step 2: Verify output files and threshold**

```bash
cd /home/telmat/sec-xapp
python3 -c "
import json
t = json.load(open('models/lstm_ue_v1_threshold.json'))
print('LSTM threshold:', t)
assert t['fpr_pct'] <= 5.0, f'FPR too high: {t[\"fpr_pct\"]}%'
print('FPR check OK')
"
```

- [ ] **Step 3: Commit trained model**

```bash
cd /home/telmat/sec-xapp
git add models/lstm_ue_v1.pt models/lstm_ue_v1_scaler.pkl \
        models/lstm_ue_v1_threshold.json models/lstm_ue_v1_losses.json \
        models/lstm_ue_v1_train.log
git commit -m "feat: train LSTM per-UE autoencoder v1 (15 features, seq_len=10)"
```
