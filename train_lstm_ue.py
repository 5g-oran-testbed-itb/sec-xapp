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
