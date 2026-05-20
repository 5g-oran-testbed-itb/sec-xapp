import argparse
import glob
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import MinMaxScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.detection.lstm_autoencoder import LSTMAutoencoder, ModelTrainer
from src.detection.feature_schema import FEATURE_NAMES


def load_csv(pattern_or_path: str, label_filter: int = 0) -> pd.DataFrame:
    paths = glob.glob(pattern_or_path)
    if not paths:
        print(f"Error: tidak ada file yang cocok: {pattern_or_path}")
        sys.exit(1)
    dfs = []
    for p in sorted(paths):
        try:
            dfs.append(pd.read_csv(p))
        except Exception as e:
            print(f"  Gagal baca {p}: {e}")
    df = pd.concat(dfs, ignore_index=True)
    if 'label' in df.columns:
        before = len(df)
        df = df[df['label'] == label_filter]
        print(f"  Filter label={label_filter}: {before} → {len(df)} baris")
    for f in FEATURE_NAMES:
        if f not in df.columns:
            print(f"Error: kolom '{f}' tidak ada. Pastikan CSV dari xapp_sec_moni terbaru.")
            sys.exit(1)
    return df


def prepare_sequences(data: np.ndarray, seq_len: int = 10) -> np.ndarray:
    seqs = [data[i:i+seq_len] for i in range(len(data) - seq_len + 1)]
    return np.array(seqs, dtype=np.float32)


def compute_val_loss(model: LSTMAutoencoder, val_seqs: np.ndarray,
                     batch_size: int = 256) -> float:
    criterion = torch.nn.MSELoss()
    model.eval()
    total, count = 0.0, 0
    with torch.no_grad():
        for i in range(0, len(val_seqs), batch_size):
            batch = torch.FloatTensor(val_seqs[i:i+batch_size])
            out  = model(batch)
            total += criterion(out, batch).item() * len(batch)
            count += len(batch)
    return total / count if count else 0.0


def train_with_val(model: LSTMAutoencoder, trainer: ModelTrainer,
                   train_data: np.ndarray, val_seqs: np.ndarray,
                   epochs: int, batch_size: int):
    """Train dan hitung val_loss setiap epoch."""
    seq_len = model.seq_len
    train_seqs = prepare_sequences(train_data, seq_len)
    dataset    = torch.utils.data.TensorDataset(torch.FloatTensor(train_seqs))
    loader     = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)
    criterion  = torch.nn.MSELoss()

    train_losses, val_losses = [], []
    best_val_loss   = float('inf')
    best_state      = None
    best_epoch      = 0

    print(f"[Trainer] Mulai training {epochs} epochs...")
    print(f"[Trainer] Train seqs: {len(train_seqs):,}  |  Val seqs: {len(val_seqs):,}")

    for epoch in range(1, epochs + 1):
        model.train()
        epoch_loss, n_batches = 0.0, 0
        for (batch,) in loader:
            trainer.optimizer.zero_grad()
            out  = model(batch)
            loss = criterion(out, batch)
            loss.backward()
            trainer.optimizer.step()
            epoch_loss += loss.item()
            n_batches  += 1
        t_loss = epoch_loss / n_batches
        v_loss = compute_val_loss(model, val_seqs, batch_size)
        train_losses.append(t_loss)
        val_losses.append(v_loss)

        if v_loss < best_val_loss:
            best_val_loss = v_loss
            best_state    = {k: v.clone() for k, v in model.state_dict().items()}
            best_epoch    = epoch

        if epoch % 10 == 0:
            print(f"  Epoch {epoch:3d}/{epochs}  train={t_loss:.6f}  val={v_loss:.6f}"
                  f"{'  ← best' if epoch == best_epoch else ''}")

    # Restore best checkpoint
    if best_state:
        model.load_state_dict(best_state)
        print(f"[Trainer] Best checkpoint: epoch {best_epoch} (val={best_val_loss:.6f})")

    # Fit threshold dari train reconstruction errors (percentile-based, distribution-free)
    model.eval()
    with torch.no_grad():
        all_train = torch.FloatTensor(train_seqs)
        train_errors = model.compute_reconstruction_error(all_train).numpy()
    model.fit_threshold(train_errors, percentile=99.5)

    return train_losses, val_losses, best_epoch


def main():
    parser = argparse.ArgumentParser(description="Train LSTM-Autoencoder (train+val mode)")
    # Mode baru: pisah train/val
    parser.add_argument("--train", type=str, default=None, help="CSV training (benign)")
    parser.add_argument("--val",   type=str, default=None, help="CSV validation (benign)")
    # Mode lama (backward compat)
    parser.add_argument("--csv",   type=str, default=None, help="[Lama] CSV tunggal — split 80/20 internal")
    # Hyperparameter
    parser.add_argument("--epochs",      type=int,   default=100)
    parser.add_argument("--batch-size",  type=int,   default=32)
    parser.add_argument("--lr",          type=float, default=0.001)
    parser.add_argument("--model-out",   type=str,   default="models/lstm_autoencoder_v2.pt",
                        help="Path simpan model baru")
    args = parser.parse_args()

    os.makedirs('models', exist_ok=True)
    seq_len = 10

    # ── Muat data ────────────────────────────────────────────────
    if args.train and args.val:
        print(f"[*] Mode: train/val terpisah")
        print(f"[*] Loading training CSV: {args.train}")
        df_train = load_csv(args.train)
        print(f"[*] Loading validation CSV: {args.val}")
        df_val   = load_csv(args.val)

        # Fit scaler HANYA dari training data
        scaler = MinMaxScaler()
        train_raw = df_train[FEATURE_NAMES].values
        val_raw   = df_val[FEATURE_NAMES].values
        scaler.fit(train_raw)

        # Override rach_preamble (idx 3) ke domain-known max=6.
        # Training data hanya melihat RACH 0-1 (benign), sehingga scaler
        # tidak tahu RACH bisa spike ke 6 saat serangan. Tanpa koreksi ini,
        # RACH=6 akan dinormalisasi ke 6.0 lalu di-clip ke 1.0 — sama dengan
        # RACH=1, sehingga fitur ini kehilangan discriminative power.
        rach_idx = FEATURE_NAMES.index('rach_preamble')
        scaler.data_max_[rach_idx]   = 6.0
        scaler.data_range_[rach_idx] = 6.0 - scaler.data_min_[rach_idx]
        scaler.scale_[rach_idx]      = 1.0 / scaler.data_range_[rach_idx]

        train_norm = scaler.transform(train_raw)
        val_norm   = scaler.transform(val_raw)

        print(f"[*] Train: {len(train_norm):,} baris → {len(train_norm)-seq_len+1:,} sequences")
        print(f"[*] Val:   {len(val_norm):,} baris → {len(val_norm)-seq_len+1:,} sequences")

    elif args.csv:
        print(f"[*] Mode: CSV tunggal split 80/20 (backward compat)")
        df = load_csv(args.csv)
        raw = df[FEATURE_NAMES].values
        scaler = MinMaxScaler()
        scaler.fit(raw)
        rach_idx = FEATURE_NAMES.index('rach_preamble')
        scaler.data_max_[rach_idx]   = 6.0
        scaler.data_range_[rach_idx] = 6.0 - scaler.data_min_[rach_idx]
        scaler.scale_[rach_idx]      = 1.0 / scaler.data_range_[rach_idx]
        norm = scaler.transform(raw)
        split = int(len(norm) * 0.8)
        train_norm = norm[:split]
        val_norm   = norm[split:]
        print(f"[*] Train: {len(train_norm):,}  Val: {len(val_norm):,}")
    else:
        print("Error: gunakan --train <file> --val <file>  atau  --csv <file>")
        sys.exit(1)

    # Simpan scaler
    scaler_path = 'models/scaler.pkl'
    with open(scaler_path, 'wb') as f:
        pickle.dump(scaler, f)
    print(f"[*] Scaler tersimpan: {scaler_path}")

    # ── Konfigurasi model ────────────────────────────────────────
    config = {
        'lstm_model': {
            'input_features': len(FEATURE_NAMES),
            'encoder_hidden': [64, 32],
            'decoder_hidden': [32, 64],
            'latent_dim': 32,
            'learning_rate': args.lr,
            'epochs': args.epochs,
            'batch_size': args.batch_size,
        },
        'detection': {
            'sequence_length': seq_len,
            'anomaly_threshold_percentile': 99.5,
        }
    }

    model   = LSTMAutoencoder(config)
    trainer = ModelTrainer(model, config)

    val_seqs = prepare_sequences(val_norm, seq_len)

    # ── Training ─────────────────────────────────────────────────
    train_losses, val_losses, best_epoch = train_with_val(
        model, trainer, train_norm, val_seqs,
        epochs=args.epochs, batch_size=args.batch_size
    )

    # ── Simpan model ─────────────────────────────────────────────
    model.save(args.model_out)

    # Simpan loss history untuk plotting
    losses_path = args.model_out.replace('.pt', '_losses.json')
    with open(losses_path, 'w') as f:
        json.dump({'train': train_losses, 'val': val_losses, 'best_epoch': best_epoch}, f)
    print(f"[*] Loss history: {losses_path}")

    # ── Hitung reconstruction errors & threshold dari val set ────
    model.eval()
    with torch.no_grad():
        val_tensor = torch.FloatTensor(val_seqs)
        val_errors = model.compute_reconstruction_error(val_tensor).numpy()

    mu_val     = float(np.mean(val_errors))
    std_val    = float(np.std(val_errors))
    percentile = 99.0
    thresh_val = float(np.percentile(val_errors, percentile))
    fpr_val    = float(np.mean(val_errors > thresh_val) * 100)

    # Update model threshold dengan nilai dari validation set
    model.fit_threshold(val_errors, percentile)

    # Simpan threshold dari validation (lebih valid, distribution-free)
    threshold_path = args.model_out.replace('.pt', '_threshold.json')
    with open(threshold_path, 'w') as f:
        json.dump({
            'mu':         mu_val,
            'sigma':      std_val,
            'threshold':  thresh_val,
            'percentile': percentile,
            'fpr_pct':    fpr_val,
            'source':     'validation_set'
        }, f, indent=2)
    print(f"[*] Threshold (val): μ={mu_val:.6f}  σ={std_val:.6f}  P{percentile}={thresh_val:.6f}  FPR={fpr_val:.2f}%")
    print(f"[*] Threshold file:  {threshold_path}")

    print(f"\n[*] Training selesai. Model: {args.model_out}")
    print(f"    Best epoch: {best_epoch}/{args.epochs}")
    print(f"    Final train loss: {train_losses[-1]:.6f}")
    print(f"    Final val loss:   {val_losses[-1]:.6f}")

if __name__ == "__main__":
    main()
