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
from src.detection.lstm_autoencoder import LSTMAutoencoder, ModelTrainer, LSTMVariationalAutoencoder
from src.detection.feature_schema import FEATURE_NAMES, FEATURE_WEIGHTS as _FW_DICT

# Build weight tensor dari FEATURE_WEIGHTS dict di feature_schema.py
_FEATURE_WEIGHTS = torch.tensor(
    [_FW_DICT.get(n, 1.0) for n in FEATURE_NAMES], dtype=torch.float32
)


def load_csv(pattern_or_paths, label_filter: int = 0) -> pd.DataFrame:
    if isinstance(pattern_or_paths, str):
        pattern_or_paths = [pattern_or_paths]
    paths = []
    for p in pattern_or_paths:
        matched = glob.glob(p)
        if matched:
            paths.extend(matched)
        else:
            print(f"  [WARN] tidak ada file cocok: {p}")
    if not paths:
        print(f"Error: tidak ada file yang cocok dari: {pattern_or_paths}")
        sys.exit(1)
    paths = sorted(set(paths))
    dfs = []
    for p in paths:
        try:
            dfs.append(pd.read_csv(p))
            print(f"  Loaded: {p}")
        except Exception as e:
            print(f"  Gagal baca {p}: {e}")
    df = pd.concat(dfs, ignore_index=True)
    if 'label' in df.columns:
        before = len(df)
        df = df[df['label'] == label_filter]
        print(f"  Filter label={label_filter}: {before} → {len(df)} baris")
    df = _add_computed_features(df)
    for f in FEATURE_NAMES:
        if f not in df.columns:
            print(f"Error: kolom '{f}' tidak ada. Pastikan CSV dari xapp_sec_moni terbaru.")
            sys.exit(1)
    return df


def _add_computed_features(df: pd.DataFrame) -> pd.DataFrame:
    """Hitung / isi fitur yang hilang atau NaN — aman dipanggil berulang.

    Ketika beberapa CSV digabung (pd.concat), CSV baru mungkin tidak punya kolom
    yang sudah ada di CSV lama, sehingga baris baru menjadi NaN.  Fungsi ini
    mengisi NaN tersebut menggunakan formula yang sudah diverifikasi.
    """
    df = df.copy()
    EPS = 1e-6
    W10 = 10

    def _fill(col, formula):
        """Assign formula ke col; jika col sudah ada, hanya isi NaN-nya."""
        if col not in df.columns or df[col].isna().any():
            computed = formula()
            if col not in df.columns:
                df[col] = computed
            else:
                df[col] = df[col].fillna(computed)

    # long-window rolling features
    _fill('rach_roll_max_30',    lambda: df['rach_preamble'].rolling(30, min_periods=1).max())
    _fill('empty_ind_roll_sum_30', lambda: df['empty_ind_rate'].rolling(30, min_periods=1).sum())
    # CV features — use clip(lower=0.05) to prevent noise amplification when PRB≈0
    _CV_FLOOR = 0.05
    _fill('prb_dl_roll_cv',     lambda: df['prb_dl_roll_std'] / df['prb_dl_roll_mean'].clip(lower=_CV_FLOOR))
    _fill('prb_ul_roll_cv',     lambda: df['prb_ul_roll_std'] / df['prb_usage_ul_ratio'].rolling(W10, min_periods=1).mean().clip(lower=_CV_FLOOR))
    # discriminative features — verified formulas against dataset_attack_mei.csv
    _fill('cqi_roll_std',         lambda: df['cqi'].rolling(W10, min_periods=1).std(ddof=0).fillna(0))
    _fill('rach_roll_mean',       lambda: df['rach_preamble'].rolling(W10, min_periods=1).mean())
    _fill('prb_ul_near_zero_rate', lambda: (df['prb_usage_ul_ratio'] < 6/106).rolling(W10, min_periods=1).mean())
    _fill('prb_peak_drop',        lambda: df['prb_ul_roll_max_100'] - df['prb_usage_ul_ratio'])
    _fill('rach_cqi_joint',       lambda: df['rach_preamble'] * (1.0 - df['cqi'] / 15.0))
    _fill('prb_dl_ul_asym',       lambda: (df['prb_usage_dl_ratio'] - df['prb_usage_ul_ratio']).abs() / (df['prb_usage_dl_ratio'] + df['prb_usage_ul_ratio'] + EPS))
    return df


def prepare_sequences(data: np.ndarray, seq_len: int = 10) -> np.ndarray:
    seqs = [data[i:i+seq_len] for i in range(len(data) - seq_len + 1)]
    return np.array(seqs, dtype=np.float32)


def weighted_mse(output: torch.Tensor, target: torch.Tensor,
                 weights: torch.Tensor) -> torch.Tensor:
    """MSE loss dengan bobot per-fitur (weights shape: [n_features])."""
    err = (output - target) ** 2          # (batch, seq_len, n_features)
    err = (err * weights).mean()          # scalar — broadcast otomatis
    return err


def compute_val_loss(model: LSTMAutoencoder, val_seqs: np.ndarray,
                     batch_size: int = 256) -> float:
    model.eval()
    total, count = 0.0, 0
    with torch.no_grad():
        for i in range(0, len(val_seqs), batch_size):
            batch = torch.FloatTensor(val_seqs[i:i+batch_size])
            out  = model(batch)
            # val loss unweighted agar sebanding dengan threshold
            total += torch.nn.functional.mse_loss(out, batch).item() * len(batch)
            count += len(batch)
    return total / count if count else 0.0


def train_with_val(model: LSTMAutoencoder, trainer: ModelTrainer,
                   train_data: np.ndarray, val_seqs: np.ndarray,
                   epochs: int, batch_size: int):
    """Train dengan weighted MSE; val_loss dihitung unweighted."""
    seq_len = model.seq_len
    train_seqs = prepare_sequences(train_data, seq_len)
    dataset    = torch.utils.data.TensorDataset(torch.FloatTensor(train_seqs))
    loader     = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)
    feat_weights = _FEATURE_WEIGHTS  # (n_features,) broadcast ke (batch, seq_len, n_features)

    train_losses, val_losses = [], []
    best_val_loss   = float('inf')
    best_state      = None
    best_epoch      = 0

    print(f"[Trainer] Mulai training {epochs} epochs  seq_len={seq_len}...")
    print(f"[Trainer] Train seqs: {len(train_seqs):,}  |  Val seqs: {len(val_seqs):,}")

    for epoch in range(1, epochs + 1):
        model.train()
        epoch_loss, n_batches = 0.0, 0
        for (batch,) in loader:
            trainer.optimizer.zero_grad()
            out  = model(batch)
            loss = weighted_mse(out, batch, feat_weights)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
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


def train_vae(model: LSTMVariationalAutoencoder, train_data: np.ndarray,
              val_seqs: np.ndarray, epochs: int, batch_size: int,
              lr: float, beta: float):
    """Training loop untuk VAE dengan β warmup (cegah posterior collapse)."""
    seq_len    = model.seq_len
    train_seqs = prepare_sequences(train_data, seq_len)
    dataset    = torch.utils.data.TensorDataset(torch.FloatTensor(train_seqs))
    loader     = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)
    optimizer  = torch.optim.Adam(model.parameters(), lr=lr)

    train_losses, val_losses = [], []
    best_val, best_state, best_epoch = float('inf'), None, 0

    print(f"[VAE] Mulai training {epochs} epochs  β={beta}  seq_len={seq_len}")
    print(f"[VAE] Train seqs: {len(train_seqs):,}  |  Val seqs: {len(val_seqs):,}")

    for epoch in range(1, epochs + 1):
        # β warmup: 0 → beta selama 30 epoch pertama (cegah posterior collapse)
        beta_now = beta * min(1.0, epoch / 30.0)

        model.train()
        t_loss_sum, n_batches = 0.0, 0
        for (batch,) in loader:
            optimizer.zero_grad()
            recon, mu, logvar = model(batch)
            loss, _, _ = model.elbo_loss(batch, recon, mu, logvar, beta=beta_now)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            t_loss_sum += loss.item()
            n_batches  += 1
        t_loss = t_loss_sum / n_batches

        # Val loss (unweighted recon only untuk konsistensi perbandingan)
        model.eval()
        v_loss_sum, v_count = 0.0, 0
        with torch.no_grad():
            for i in range(0, len(val_seqs), 256):
                b = torch.FloatTensor(val_seqs[i:i+256])
                r, mu, lv = model(b)
                v_loss_sum += torch.nn.functional.mse_loss(r, b).item() * len(b)
                v_count    += len(b)
        v_loss = v_loss_sum / v_count

        train_losses.append(t_loss)
        val_losses.append(v_loss)
        if v_loss < best_val:
            best_val   = v_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            best_epoch = epoch

        if epoch % 10 == 0:
            print(f"  Epoch {epoch:3d}/{epochs}  train={t_loss:.6f}  val={v_loss:.6f}  β={beta_now:.4f}"
                  f"{'  ← best' if epoch == best_epoch else ''}")

    if best_state:
        model.load_state_dict(best_state)
        print(f"[VAE] Best checkpoint: epoch {best_epoch} (val={best_val:.6f})")

    return train_losses, val_losses, best_epoch


def main():
    parser = argparse.ArgumentParser(description="Train LSTM-Autoencoder (train+val mode)")
    # Mode baru: pisah train/val
    parser.add_argument("--train", type=str, nargs='+', default=None, help="CSV training (benign), bisa multiple file/glob")
    parser.add_argument("--val",   type=str, default=None, help="CSV validation (benign)")
    # Mode lama (backward compat)
    parser.add_argument("--csv",   type=str, default=None, help="[Lama] CSV tunggal — split 80/20 internal")
    # Hyperparameter
    parser.add_argument("--epochs",      type=int,   default=100)
    parser.add_argument("--batch-size",  type=int,   default=32)
    parser.add_argument("--lr",          type=float, default=0.001)
    parser.add_argument("--model-out",   type=str,   default="models/lstm_autoencoder_v2.pt",
                        help="Path simpan model baru")
    parser.add_argument("--threshold-percentile", type=float, default=99.0,
                        help="Percentile untuk threshold deteksi dari val set (default: 99.0)")
    parser.add_argument("--seq-len", type=int, default=10,
                        help="Panjang sequence LSTM (default: 10)")
    parser.add_argument("--num-features", type=int, default=None,
                        help="Gunakan N fitur pertama dari FEATURE_NAMES (default: semua). "
                             "Contoh: --num-features 25 untuk skip CV features terakhir.")
    parser.add_argument("--vae", action="store_true",
                        help="Gunakan VAE (Variational Autoencoder) — default: plain AE")
    parser.add_argument("--beta", type=float, default=0.01,
                        help="Bobot KL divergence untuk VAE (default: 0.01)")
    parser.add_argument("--clean-dl-thresh", type=float, default=0.7,
                        help="Hapus baris training dengan prb_dl_roll_mean > nilai ini (default: 0.7). "
                             "Set 1.0 untuk nonaktif.")
    parser.add_argument("--clean-ul-thresh", type=float, default=0.5,
                        help="Hapus baris training dengan prb_ul_roll_max > nilai ini (default: 0.5). "
                             "Mencegah LSTM belajar pola UL jenuh sebagai 'normal'. Set 1.0 untuk nonaktif.")
    args = parser.parse_args()

    os.makedirs('models', exist_ok=True)
    seq_len = args.seq_len

    # Potong feature list jika --num-features diset
    global FEATURE_NAMES, _FEATURE_WEIGHTS
    if args.num_features is not None and args.num_features < len(FEATURE_NAMES):
        FEATURE_NAMES = FEATURE_NAMES[:args.num_features]
        _FEATURE_WEIGHTS = torch.tensor(
            [_FW_DICT.get(n, 1.0) for n in FEATURE_NAMES], dtype=torch.float32
        )
        print(f"[*] num_features={args.num_features}: pakai {FEATURE_NAMES}")

    # ── Muat data ────────────────────────────────────────────────
    if args.train and args.val:
        print(f"[*] Mode: train/val terpisah")
        print(f"[*] Loading training CSV: {args.train}")
        df_train = load_csv(args.train)

        # Hapus baris normal yang punya pola DL jenuh (mirip DL Flood).
        # 1,511 baris (~2.5%) di training normal memiliki prb_dl_roll_mean>0.7
        # dengan distribusi identik ke DL Flood → LSTM belajar pola ini sebagai normal.
        if args.clean_dl_thresh < 1.0 and 'prb_dl_roll_mean' in df_train.columns:
            before = len(df_train)
            df_train = df_train[df_train['prb_dl_roll_mean'] <= args.clean_dl_thresh]
            removed = before - len(df_train)
            print(f"[*] Clean DL: hapus {removed} baris (prb_dl_roll_mean > {args.clean_dl_thresh}) "
                  f"→ {len(df_train)} baris tersisa")

        if args.clean_ul_thresh < 1.0 and 'prb_ul_roll_max' in df_train.columns:
            before = len(df_train)
            df_train = df_train[df_train['prb_ul_roll_max'] <= args.clean_ul_thresh]
            removed = before - len(df_train)
            print(f"[*] Clean UL: hapus {removed} baris (prb_ul_roll_max > {args.clean_ul_thresh}) "
                  f"→ {len(df_train)} baris tersisa")

        print(f"[*] Loading validation CSV: {args.val}")
        df_val   = load_csv(args.val)

        # Fit scaler HANYA dari training data
        scaler = MinMaxScaler()
        train_raw = df_train[FEATURE_NAMES].values
        val_raw   = df_val[FEATURE_NAMES].values
        scaler.fit(train_raw)

        # Override domain-known maxima for RACH-derived features.
        # Benign training data sees RACH≈0 (idle) or RACH≤1 (active-normal reconnects).
        # Without override, scaler range is tiny → scale=1e8 → normalized values 1e7+
        # → exploding gradients → NaN loss.  Domain max=6 for all RACH-derived features
        # because rach_preamble is bounded [0,6] by protocol.
        # rach_cqi_joint = rach * (1 - cqi/15), max when rach=6,cqi=0 → 6.0
        _RACH_DOMAIN_MAX = 6.0
        for _fname, _dmax in [
            ('rach_preamble',      _RACH_DOMAIN_MAX),
            ('rach_roll_mean',     _RACH_DOMAIN_MAX),
            ('rach_roll_max_30',   _RACH_DOMAIN_MAX),
            ('rach_cqi_joint',     _RACH_DOMAIN_MAX),
        ]:
            if _fname in FEATURE_NAMES:
                _fi = FEATURE_NAMES.index(_fname)
                scaler.data_max_[_fi]   = _dmax
                scaler.data_range_[_fi] = _dmax - scaler.data_min_[_fi]
                scaler.scale_[_fi]      = 1.0 / scaler.data_range_[_fi]

        train_norm = scaler.transform(train_raw)
        val_norm   = scaler.transform(val_raw)

        print(f"[*] seq_len={seq_len}  Train: {len(train_norm):,} baris → {len(train_norm)-seq_len+1:,} sequences")
        print(f"[*] seq_len={seq_len}  Val:   {len(val_norm):,} baris → {len(val_norm)-seq_len+1:,} sequences")

    elif args.csv:
        print(f"[*] Mode: CSV tunggal split 80/20 (backward compat)")
        df = load_csv(args.csv)
        raw = df[FEATURE_NAMES].values
        scaler = MinMaxScaler()
        scaler.fit(raw)
        for _fname, _dmax in [
            ('rach_preamble',      6.0),
            ('rach_roll_mean',     6.0),
            ('rach_roll_max_30',   6.0),
            ('rach_cqi_joint',     6.0),
        ]:
            if _fname in FEATURE_NAMES:
                _fi = FEATURE_NAMES.index(_fname)
                scaler.data_max_[_fi]   = _dmax
                scaler.data_range_[_fi] = _dmax - scaler.data_min_[_fi]
                scaler.scale_[_fi]      = 1.0 / scaler.data_range_[_fi]
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
            'bidirectional': False,
            'learning_rate': args.lr,
            'epochs': args.epochs,
            'batch_size': args.batch_size,
        },
        'detection': {
            'sequence_length': seq_len,
            'anomaly_threshold_percentile': 99.5,
        }
    }

    val_seqs = prepare_sequences(val_norm, seq_len)

    if args.vae:
        # ── VAE path ─────────────────────────────────────────────
        model = LSTMVariationalAutoencoder(config)
        print(f"[*] Mode: LSTM-VAE  β={args.beta}")

        train_losses, val_losses, best_epoch = train_vae(
            model, train_norm, val_seqs,
            epochs=args.epochs, batch_size=args.batch_size,
            lr=args.lr, beta=args.beta,
        )

        model.save(args.model_out)

        losses_path = args.model_out.replace('.pt', '_losses.json')
        with open(losses_path, 'w') as f:
            json.dump({'train': train_losses, 'val': val_losses, 'best_epoch': best_epoch}, f)
        print(f"[*] Loss history: {losses_path}")

        # Anomaly scores dari val set (recon + β*KL) untuk threshold
        model.eval()
        val_tensor = torch.FloatTensor(val_seqs)
        val_scores = model.compute_anomaly_scores(val_tensor, beta_inf=args.beta).numpy()

        mu_val     = float(np.mean(val_scores))
        std_val    = float(np.std(val_scores))
        percentile = args.threshold_percentile
        thresh_val = float(np.percentile(val_scores, percentile))
        fpr_val    = float(np.mean(val_scores > thresh_val) * 100)

        model.fit_threshold(val_scores, percentile)

        threshold_path = args.model_out.replace('.pt', '_threshold.json')
        with open(threshold_path, 'w') as f:
            json.dump({
                'mu':         mu_val,
                'sigma':      std_val,
                'threshold':  thresh_val,
                'percentile': percentile,
                'fpr_pct':    fpr_val,
                'source':     'validation_set',
                'vae':        True,
                'beta_inf':   args.beta,
            }, f, indent=2)
        print(f"[*] Threshold (val): μ={mu_val:.6f}  σ={std_val:.6f}  P{percentile}={thresh_val:.6f}  FPR={fpr_val:.2f}%")
        print(f"[*] Threshold file:  {threshold_path}")

    else:
        # ── Plain AE path ─────────────────────────────────────────
        model   = LSTMAutoencoder(config)
        trainer = ModelTrainer(model, config)

        train_losses, val_losses, best_epoch = train_with_val(
            model, trainer, train_norm, val_seqs,
            epochs=args.epochs, batch_size=args.batch_size
        )

        model.save(args.model_out)

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
