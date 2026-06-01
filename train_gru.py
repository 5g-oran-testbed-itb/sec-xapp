# train_gru.py
"""Train GRU Autoencoder (GRU-A seq_len=10 atau GRU-B seq_len=30).

Usage:
  # GRU-A (short window, flood specialist)
  ./venv/bin/python3 train_gru.py \\
    --train csv/dataset_training_clean.csv \\
    --val   csv/dataset_validation_clean.csv \\
    --seq-len 10 \\
    --model-out models/gru_autoencoder_A_v1.pt

  # GRU-B (long window, RRC storm specialist)
  ./venv/bin/python3 train_gru.py \\
    --train csv/dataset_training_clean.csv \\
    --val   csv/dataset_validation_clean.csv \\
    --seq-len 30 \\
    --model-out models/gru_autoencoder_B_v1.pt
"""

import argparse
import glob
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.feature_schema import FEATURE_NAMES, FEATURE_WEIGHTS as _FW_DICT

_FEATURE_WEIGHTS = torch.tensor(
    [_FW_DICT.get(n, 1.0) for n in FEATURE_NAMES], dtype=torch.float32
)

EPS = 1e-6
W10 = 10


def _add_computed_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def _fill(col, formula):
        if col not in df.columns or df[col].isna().any():
            computed = formula()
            if col not in df.columns:
                df[col] = computed
            else:
                df[col] = df[col].fillna(computed)

    _fill('rach_roll_max_30',      lambda: df['rach_preamble'].rolling(30, min_periods=1).max())
    _fill('empty_ind_roll_sum_30', lambda: df['empty_ind_rate'].rolling(30, min_periods=1).sum())
    _fill('prb_dl_roll_cv',        lambda: df['prb_dl_roll_std'] / (df['prb_dl_roll_mean'] + EPS))
    _fill('prb_ul_roll_cv',        lambda: df['prb_ul_roll_std'] / (df['prb_usage_ul_ratio'].rolling(W10, min_periods=1).mean() + EPS))
    _fill('cqi_roll_std',          lambda: df['cqi'].rolling(W10, min_periods=1).std(ddof=0).fillna(0))
    _fill('rach_roll_mean',        lambda: df['rach_preamble'].rolling(W10, min_periods=1).mean())
    _fill('prb_ul_near_zero_rate', lambda: (df['prb_usage_ul_ratio'] < 6/106).rolling(W10, min_periods=1).mean())
    _fill('prb_peak_drop',         lambda: df['prb_ul_roll_max_100'] - df['prb_usage_ul_ratio'])
    _fill('rach_cqi_joint',        lambda: df['rach_preamble'] * (1.0 - df['cqi'] / 15.0))
    _fill('prb_dl_ul_asym',        lambda: (df['prb_usage_dl_ratio'] - df['prb_usage_ul_ratio']).abs() / (df['prb_usage_dl_ratio'] + df['prb_usage_ul_ratio'] + EPS))
    return df


def load_csv(paths, label_filter=0) -> pd.DataFrame:
    if isinstance(paths, str):
        paths = [paths]
    all_paths = []
    for p in paths:
        matched = glob.glob(p)
        all_paths.extend(matched) if matched else print(f"  [WARN] no match: {p}")
    if not all_paths:
        print(f"Error: no files found from {paths}")
        sys.exit(1)
    dfs = [pd.read_csv(p) for p in sorted(set(all_paths))]
    df = pd.concat(dfs, ignore_index=True)
    if 'label' in df.columns:
        before = len(df)
        df = df[df['label'] == label_filter]
        print(f"  Filter label={label_filter}: {before} → {len(df)} rows")
    df = _add_computed_features(df)
    for f in FEATURE_NAMES:
        if f not in df.columns:
            print(f"Error: column '{f}' missing. Use latest xapp_sec_moni CSV.")
            sys.exit(1)
    return df


def prepare_sequences(data: np.ndarray, seq_len: int) -> np.ndarray:
    return np.array([data[i:i+seq_len] for i in range(len(data) - seq_len + 1)],
                    dtype=np.float32)


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
            batch = torch.FloatTensor(val_seqs[i:i+batch_size])
            out   = model(batch)
            total += nn.functional.mse_loss(out, batch).item() * len(batch)
            count += len(batch)
    return total / count if count else 0.0


def train(model: GRUAutoencoder, train_norm: np.ndarray, val_seqs: np.ndarray,
          epochs: int, batch_size: int, lr: float):
    seq_len    = model.seq_len
    train_seqs = prepare_sequences(train_norm, seq_len)
    dataset    = torch.utils.data.TensorDataset(torch.FloatTensor(train_seqs))
    loader     = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)
    optimizer  = torch.optim.Adam(model.parameters(), lr=lr)
    fw         = _FEATURE_WEIGHTS

    train_losses, val_losses = [], []
    best_val, best_state, best_epoch = float('inf'), None, 0

    print(f"[GRU] Training {epochs} epochs  seq_len={seq_len}  lr={lr}")
    print(f"[GRU] Train seqs: {len(train_seqs):,}  |  Val seqs: {len(val_seqs):,}")

    for epoch in range(1, epochs + 1):
        model.train()
        t_loss, n = 0.0, 0
        for (batch,) in loader:
            optimizer.zero_grad()
            out  = model(batch)
            loss = weighted_mse(out, batch, fw)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            t_loss += loss.item()
            n      += 1
        t_avg = t_loss / n
        v_avg = compute_val_loss(model, val_seqs, batch_size)
        train_losses.append(t_avg)
        val_losses.append(v_avg)

        if v_avg < best_val:
            best_val   = v_avg
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
    parser.add_argument("--train",     type=str, nargs='+', required=True)
    parser.add_argument("--val",       type=str, required=True)
    parser.add_argument("--seq-len",   type=int, default=10)
    parser.add_argument("--epochs",    type=int, default=150)
    parser.add_argument("--batch-size",type=int, default=32)
    parser.add_argument("--lr",        type=float, default=0.001)
    parser.add_argument("--model-out", type=str,
                        default="models/gru_autoencoder_A_v1.pt")
    parser.add_argument("--threshold-percentile", type=float, default=99.0)
    parser.add_argument("--scaler",    type=str, default="models/scaler.pkl")
    parser.add_argument("--clean-dl-thresh", type=float, default=0.7)
    parser.add_argument("--clean-ul-thresh", type=float, default=0.5)
    args = parser.parse_args()

    os.makedirs('models', exist_ok=True)

    # Load existing scaler. If feature count mismatches FEATURE_NAMES, fit fresh from training data.
    scaler = None
    if os.path.exists(args.scaler):
        with open(args.scaler, 'rb') as f:
            _loaded = pickle.load(f)
        if len(_loaded.data_min_) == len(FEATURE_NAMES):
            scaler = _loaded
            print(f"[*] Reusing scaler: {args.scaler}  ({len(scaler.data_min_)} features)")
        else:
            print(f"[!] Scaler has {len(_loaded.data_min_)} features but FEATURE_NAMES has {len(FEATURE_NAMES)} — fitting fresh scaler")
    else:
        print(f"[!] {args.scaler} not found — fitting fresh scaler")

    print(f"[*] Loading training CSV: {args.train}")
    df_train = load_csv(args.train)

    if args.clean_dl_thresh < 1.0 and 'prb_dl_roll_mean' in df_train.columns:
        before = len(df_train)
        df_train = df_train[df_train['prb_dl_roll_mean'] <= args.clean_dl_thresh]
        print(f"[*] Clean DL: {before} → {len(df_train)} rows")

    if args.clean_ul_thresh < 1.0 and 'prb_ul_roll_max' in df_train.columns:
        before = len(df_train)
        df_train = df_train[df_train['prb_ul_roll_max'] <= args.clean_ul_thresh]
        print(f"[*] Clean UL: {before} → {len(df_train)} rows")

    print(f"[*] Loading validation CSV: {args.val}")
    df_val = load_csv(args.val)

    if scaler is None:
        from sklearn.preprocessing import MinMaxScaler
        scaler = MinMaxScaler()
        scaler.fit(df_train[FEATURE_NAMES].values.astype(np.float32))
        print(f"[*] Fresh scaler fit on {len(df_train)} training rows  ({len(FEATURE_NAMES)} features)")

    train_norm = scaler.transform(df_train[FEATURE_NAMES].values.astype(np.float32))
    val_norm   = scaler.transform(df_val[FEATURE_NAMES].values.astype(np.float32))
    val_seqs   = prepare_sequences(val_norm, args.seq_len)

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
    print(f"[*] GRU-AE params: {param_count:,}")

    train_losses, val_losses, best_epoch = train(
        model, train_norm, val_seqs,
        epochs=args.epochs, batch_size=args.batch_size, lr=args.lr,
    )

    model.save(args.model_out)

    losses_path = args.model_out.replace('.pt', '_losses.json')
    with open(losses_path, 'w') as f:
        json.dump({'train': train_losses, 'val': val_losses, 'best_epoch': best_epoch}, f)
    print(f"[*] Loss history: {losses_path}")

    # Fit threshold dari val set
    model.eval()
    val_tensor = torch.FloatTensor(val_seqs)
    with torch.no_grad():
        val_errors = model.compute_reconstruction_error(val_tensor).numpy()

    mu_val     = float(np.mean(val_errors))
    std_val    = float(np.std(val_errors))
    thresh_val = float(np.percentile(val_errors, args.threshold_percentile))
    fpr_val    = float(np.mean(val_errors > thresh_val) * 100)
    model.fit_threshold(val_errors, args.threshold_percentile)

    threshold_path = args.model_out.replace('.pt', '_threshold.json')
    with open(threshold_path, 'w') as f:
        json.dump({
            'mu':         mu_val,
            'sigma':      std_val,
            'threshold':  thresh_val,
            'percentile': args.threshold_percentile,
            'fpr_pct':    fpr_val,
            'source':     'validation_set',
        }, f, indent=2)

    print(f"\n[*] Done. Model: {args.model_out}")
    print(f"    seq_len={args.seq_len}  best_epoch={best_epoch}/{args.epochs}")
    print(f"    Threshold (P{args.threshold_percentile}): {thresh_val:.6f}  FPR={fpr_val:.2f}%")


if __name__ == "__main__":
    main()
