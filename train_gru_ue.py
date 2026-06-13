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
