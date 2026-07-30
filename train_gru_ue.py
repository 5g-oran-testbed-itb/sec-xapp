# train_gru_ue.py
"""Train GRU Autoencoder on per-UE KPM dataset.

Uses feature_schema_ue (15 features) and MinMaxScaler.
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
from sklearn.preprocessing import MinMaxScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.feature_schema_ue import (
    FEATURE_NAMES, CSV_FEATURE_NAMES, FEATURE_WEIGHTS as _FW_DICT,
    add_burst_features_rows,
)
from src.detection.scoring import load_loss_weights
from src.detection.training_utils import set_reproducible_seed

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
    for f in CSV_FEATURE_NAMES:
        if f not in df.columns:
            print(f"Error: column '{f}' missing in {path}")
            sys.exit(1)
    return df


def df_to_raw(df: pd.DataFrame) -> np.ndarray:
    """Convert DataFrame to feature array, computing burst index features on-the-fly."""
    rows = df.to_dict('records')
    add_burst_features_rows(rows)
    return np.array([[float(r.get(n, 0.0)) for n in FEATURE_NAMES] for r in rows],
                    dtype=np.float32)


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


class EarlyStopping:
    def __init__(self, patience=15, min_delta=0.0001, checkpoint_path='checkpoint.pt'):
        self.patience = patience
        self.min_delta = min_delta
        self.checkpoint_path = checkpoint_path
        self.counter = 0
        self.best_loss = None
        self.early_stop = False
    def __call__(self, val_loss, model, optimizer, epoch):
        if self.best_loss is None:
            self.best_loss = val_loss
            self.save_checkpoint(model, optimizer, val_loss, epoch)
        elif val_loss > self.best_loss - self.min_delta:
            self.counter += 1
            print(f"EarlyStopping counter: {self.counter} out of {self.patience}")
            if self.counter >= self.patience:
                self.early_stop = True
        else:
            self.best_loss = val_loss
            self.save_checkpoint(model, optimizer, val_loss, epoch)
            self.counter = 0
    def save_checkpoint(self, model, optimizer, val_loss, epoch):
        torch.save({
            'epoch': epoch,
            'model_state_dict': model.state_dict(),
            'optimizer_state_dict': optimizer.state_dict(),
            'loss': val_loss,
        }, self.checkpoint_path)
        print(f"Validation loss decreased. Saving best model checkpoint to {self.checkpoint_path}...")


def train(model: GRUAutoencoder, train_norm: np.ndarray, val_seqs: np.ndarray,
          epochs: int, batch_size: int, lr: float, checkpoint_path: str,
          loss_weights=None):
    seq_len = model.seq_len
    train_seqs = prepare_sequences(train_norm, seq_len)
    dataset = torch.utils.data.TensorDataset(torch.FloatTensor(train_seqs))
    loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    fw = _FEATURE_WEIGHTS if loss_weights is None else loss_weights

    train_losses, val_losses = [], []
    early_stopping = EarlyStopping(patience=15, min_delta=0.0001, checkpoint_path=checkpoint_path)

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

        if epoch % 10 == 0 or epoch == 1:
            print(f"  Epoch {epoch:3d}/{epochs}  train={t_avg:.6f}  val={v_avg:.6f}")

        early_stopping(v_avg, model, optimizer, epoch)
        if early_stopping.early_stop:
            print(f"Early stopping triggered at epoch {epoch}. Training stopped.")
            break

    # Load best state from checkpoint
    if os.path.exists(checkpoint_path):
        checkpoint = torch.load(checkpoint_path, map_location='cpu', weights_only=False)
        model.load_state_dict(checkpoint['model_state_dict'])
        best_epoch = checkpoint['epoch']
        best_val = checkpoint['loss']
        print(f"[GRU] Loaded best model checkpoint from epoch {best_epoch} (val_loss={best_val:.6f})")
    else:
        best_epoch = epoch
        print(f"[GRU] Warning: checkpoint file {checkpoint_path} not found!")

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
    parser.add_argument("--loss-weights", choices=["schemea", "uniform", "benign"],
                        default="schemea")
    parser.add_argument("--loss-weights-json", type=str, default=None)
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducible training (default: 42)")
    args = parser.parse_args()

    set_reproducible_seed(args.seed)
    os.makedirs('models', exist_ok=True)

    print(f"[*] Loading training CSV: {args.train}")
    df_train = load_csv(args.train)

    print(f"[*] Loading validation CSV: {args.val}")
    df_val = load_csv(args.val)

    scaler = MinMaxScaler()
    train_raw = df_to_raw(df_train)
    val_raw   = df_to_raw(df_val)
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

    loss_w = torch.tensor(
        load_loss_weights(args.loss_weights, FEATURE_NAMES, _FW_DICT, args.loss_weights_json),
        dtype=torch.float32)
    print(f"[*] Loss weighting: {args.loss_weights}")

    checkpoint_path = args.model_out.replace('.pt', '_checkpoint.pt')
    train_losses, val_losses, best_epoch = train(
        model, train_norm, val_seqs,
        epochs=args.epochs, batch_size=args.batch_size, lr=args.lr,
        checkpoint_path=checkpoint_path, loss_weights=loss_w,
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
