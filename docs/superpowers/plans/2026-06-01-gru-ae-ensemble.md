# GRU Autoencoder Dual Ensemble Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementasi GRU Autoencoder dual ensemble (GRU-A seq_len=10, GRU-B seq_len=30) sebagai alternatif arsitektur dari LSTM ensemble v16+v22 untuk perbandingan thesis.

**Architecture:** Dua GRUAutoencoder dengan seq_len berbeda (10 dan 30) di-ensemble via OR dari threshold masing-masing. GRUEncoder menggunakan BiGRU + TemporalAttention (import dari lstm_autoencoder.py), GRUDecoder menggunakan GRU unidirectional. Training reuse scaler.pkl yang sudah ada dari LSTM — tidak fit scaler baru.

**Tech Stack:** Python 3.12, PyTorch (nn.GRU), scikit-learn MinMaxScaler (reused), ONNX opset 14, onnxruntime, pandas, numpy

---

## File Map

| File | Status | Tanggung jawab |
|------|--------|----------------|
| `src/detection/gru_autoencoder.py` | CREATE | GRUEncoder, GRUDecoder, GRUAutoencoder, GRUEnsemble |
| `tests/test_gru_autoencoder.py` | CREATE | Unit tests untuk semua class |
| `train_gru.py` | CREATE | Training script (mirror train_lstm.py, reuse scaler.pkl) |
| `evaluate_gru.py` | CREATE | Evaluasi per-attack, output JSON kompatibel dengan baseline |
| `export_onnx_gru.py` | CREATE | Export GRU-A dan GRU-B ke ONNX terpisah |
| `src/detection/lstm_autoencoder.py` | READ-ONLY | Import TemporalAttention dari sini |
| `src/detection/feature_schema.py` | READ-ONLY | Import FEATURE_NAMES, FEATURE_WEIGHTS |
| `train_lstm.py` | READ-ONLY | Referensi pola training, weighted_mse, prepare_sequences |

---

### Task 1: GRUAutoencoder class + unit tests

**Files:**
- Create: `src/detection/gru_autoencoder.py`
- Create: `tests/test_gru_autoencoder.py`

**Context:**
- `TemporalAttention` ada di `src/detection/lstm_autoencoder.py:13-25` — import langsung, jangan duplikasi
- `FEATURE_NAMES` di `src/detection/feature_schema.py` punya 16 entries (default `input_features`)
- GRU vs LSTM: `nn.GRU` return `(out, h_n)`, bukan `(out, (h_n, c_n))` — unpacking `out, _ = self.gru(x)` works
- BiGRU hidden_size=64: output shape `(batch, seq_len, 128)` karena bidirectional doubles hidden dim
- `decoder_hidden=[32,64]` artinya fc: latent→32, gru1: 32→64, gru2: 64→n_features

- [ ] **Step 1: Tulis failing test untuk GRUAutoencoder forward pass**

```python
# tests/test_gru_autoencoder.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import torch
import numpy as np
import pytest
from src.detection.gru_autoencoder import GRUAutoencoder, GRUEnsemble


def _mini_config(seq_len=10):
    return {
        'gru_model': {
            'input_features': 4,
            'encoder_hidden': [8, 4],
            'decoder_hidden': [4, 8],
            'latent_dim': 4,
            'bidirectional': True,
        },
        'detection': {'sequence_length': seq_len},
    }


def test_forward_output_shape():
    model = GRUAutoencoder(_mini_config(seq_len=10))
    x = torch.randn(3, 10, 4)
    out = model(x)
    assert out.shape == (3, 10, 4), f"expected (3,10,4), got {out.shape}"


def test_reconstruction_error_shape():
    model = GRUAutoencoder(_mini_config(seq_len=10))
    x = torch.randn(5, 10, 4)
    err = model.compute_reconstruction_error(x)
    assert err.shape == (5,), f"expected (5,), got {err.shape}"
    assert (err >= 0).all()


def test_fit_threshold_sets_values():
    model = GRUAutoencoder(_mini_config())
    errors = np.array([0.01, 0.02, 0.03, 0.04, 0.05])
    model.fit_threshold(errors, percentile=80.0)
    assert model.anomaly_threshold == pytest.approx(np.percentile(errors, 80.0))
    assert model.reconstruction_mean == pytest.approx(np.mean(errors))


def test_save_load_roundtrip(tmp_path):
    model = GRUAutoencoder(_mini_config())
    errors = np.random.rand(100).astype(np.float32) * 0.1
    model.fit_threshold(errors, percentile=99.0)
    path = str(tmp_path / "gru_test.pt")
    model.save(path)
    model2 = GRUAutoencoder.load(path, _mini_config())
    assert model2.anomaly_threshold == pytest.approx(model.anomaly_threshold)


def test_ensemble_score_uses_correct_slicing():
    cfg_a = _mini_config(seq_len=4)
    cfg_b = _mini_config(seq_len=8)
    model_a = GRUAutoencoder(cfg_a)
    model_b = GRUAutoencoder(cfg_b)
    # fit dummy thresholds
    model_a.fit_threshold(np.zeros(10), percentile=99.0)
    model_b.fit_threshold(np.zeros(10), percentile=99.0)
    ensemble = GRUEnsemble(model_a, model_b)
    window_8 = torch.randn(1, 8, 4)
    combined, score_a, score_b = ensemble.score(window_8)
    assert isinstance(combined, float)
    assert combined == max(score_a, score_b)


def test_ensemble_is_anomaly_returns_bool():
    cfg_a = _mini_config(seq_len=4)
    cfg_b = _mini_config(seq_len=8)
    model_a = GRUAutoencoder(cfg_a)
    model_b = GRUAutoencoder(cfg_b)
    model_a.fit_threshold(np.ones(10) * 999.0, percentile=99.0)  # threshold sangat tinggi → normal
    model_b.fit_threshold(np.ones(10) * 999.0, percentile=99.0)
    ensemble = GRUEnsemble(model_a, model_b)
    window_8 = torch.randn(1, 8, 4)
    flagged, _, _, _ = ensemble.is_anomaly(window_8)
    assert flagged is False
```

- [ ] **Step 2: Jalankan test untuk verifikasi FAIL**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 -m pytest tests/test_gru_autoencoder.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'src.detection.gru_autoencoder'`

- [ ] **Step 3: Buat direktori tests jika belum ada**

```bash
mkdir -p /home/telmat/sec-xapp/tests
touch /home/telmat/sec-xapp/tests/__init__.py
```

- [ ] **Step 4: Implementasi `src/detection/gru_autoencoder.py`**

```python
# src/detection/gru_autoencoder.py
import os
import numpy as np
import torch
import torch.nn as nn
from typing import List, Tuple

from src.detection.lstm_autoencoder import TemporalAttention


class GRUEncoder(nn.Module):
    def __init__(self, input_size: int, hidden_sizes: List[int], latent_dim: int,
                 bidirectional: bool = True):
        super().__init__()
        D = 2 if bidirectional else 1
        self.bidirectional = bidirectional
        self.gru1 = nn.GRU(input_size=input_size,
                           hidden_size=hidden_sizes[0],
                           batch_first=True,
                           bidirectional=bidirectional)
        self.gru2 = nn.GRU(input_size=hidden_sizes[0] * D,
                           hidden_size=hidden_sizes[1],
                           batch_first=True,
                           bidirectional=bidirectional)
        self.attention = TemporalAttention(hidden_sizes[1] * D)
        self.fc = nn.Linear(hidden_sizes[1] * D, latent_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.gru1(x)
        out, _ = self.gru2(out)
        context = self.attention(out)
        return self.fc(context)


class GRUDecoder(nn.Module):
    def __init__(self, latent_dim: int, hidden_sizes: List[int],
                 output_size: int, seq_len: int):
        super().__init__()
        self.seq_len = seq_len
        self.fc = nn.Linear(latent_dim, hidden_sizes[0])
        self.gru1 = nn.GRU(input_size=hidden_sizes[0],
                           hidden_size=hidden_sizes[1],
                           batch_first=True)
        self.gru2 = nn.GRU(input_size=hidden_sizes[1],
                           hidden_size=output_size,
                           batch_first=True)

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        hidden = self.fc(z).unsqueeze(1).repeat(1, self.seq_len, 1)
        out, _ = self.gru1(hidden)
        out, _ = self.gru2(out)
        return out


class GRUAutoencoder(nn.Module):
    def __init__(self, config: dict):
        super().__init__()
        mc = config.get('gru_model', {})
        dc = config.get('detection', {})
        self.input_features = mc.get('input_features', 16)
        self.encoder_hidden = mc.get('encoder_hidden', [64, 32])
        self.decoder_hidden = mc.get('decoder_hidden', [32, 64])
        self.latent_dim     = mc.get('latent_dim', 32)
        self.bidirectional  = mc.get('bidirectional', True)
        self.seq_len        = dc.get('sequence_length', 10)

        self.encoder = GRUEncoder(
            input_size=self.input_features,
            hidden_sizes=self.encoder_hidden,
            latent_dim=self.latent_dim,
            bidirectional=self.bidirectional,
        )
        self.decoder = GRUDecoder(
            latent_dim=self.latent_dim,
            hidden_sizes=self.decoder_hidden,
            output_size=self.input_features,
            seq_len=self.seq_len,
        )
        self.anomaly_threshold    = None
        self.reconstruction_mean  = None
        self.reconstruction_std   = None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))

    def compute_reconstruction_error(self, x: torch.Tensor) -> torch.Tensor:
        with torch.no_grad():
            return torch.mean((x - self.forward(x)) ** 2, dim=(1, 2))

    def fit_threshold(self, normal_errors: np.ndarray, percentile: float = 99.0):
        self.reconstruction_mean = float(np.mean(normal_errors))
        self.reconstruction_std  = float(np.std(normal_errors))
        self.anomaly_threshold   = float(np.percentile(normal_errors, percentile))

    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        torch.save({
            'model_state_dict':   self.state_dict(),
            'anomaly_threshold':  self.anomaly_threshold,
            'reconstruction_mean': self.reconstruction_mean,
            'reconstruction_std':  self.reconstruction_std,
            'config': {
                'input_features': self.input_features,
                'encoder_hidden': self.encoder_hidden,
                'decoder_hidden': self.decoder_hidden,
                'latent_dim':     self.latent_dim,
                'seq_len':        self.seq_len,
                'bidirectional':  self.bidirectional,
            },
        }, path)
        print(f"[GRU-AE] Model saved to {path}")

    @classmethod
    def load(cls, path: str, config: dict) -> 'GRUAutoencoder':
        state = torch.load(path, map_location='cpu', weights_only=False)
        saved_cfg = state.get('config', {})
        # Rebuild config dari saved state agar seq_len selalu match
        merged = dict(config)
        merged['gru_model'] = {
            'input_features': saved_cfg.get('input_features', config.get('gru_model', {}).get('input_features', 16)),
            'encoder_hidden': saved_cfg.get('encoder_hidden', [64, 32]),
            'decoder_hidden': saved_cfg.get('decoder_hidden', [32, 64]),
            'latent_dim':     saved_cfg.get('latent_dim', 32),
            'bidirectional':  saved_cfg.get('bidirectional', True),
        }
        merged['detection'] = {'sequence_length': saved_cfg.get('seq_len', 10)}
        model = cls(merged)
        model.load_state_dict(state['model_state_dict'])
        model.anomaly_threshold   = state.get('anomaly_threshold')
        model.reconstruction_mean = state.get('reconstruction_mean')
        model.reconstruction_std  = state.get('reconstruction_std')
        print(f"[GRU-AE] Model loaded from {path}  seq_len={model.seq_len}")
        return model


class GRUEnsemble:
    """
    Ensemble dua GRUAutoencoder dengan seq_len berbeda.
    model_a: seq_len pendek (misal 10) — spesialis Flood/Burst
    model_b: seq_len panjang (misal 30) — spesialis RRC Storm

    Input selalu window sepanjang model_b.seq_len.
    GRU-A pakai slice [-model_a.seq_len:] dari window yang sama.
    """

    def __init__(self, model_a: GRUAutoencoder, model_b: GRUAutoencoder):
        self.model_a = model_a
        self.model_b = model_b

    def score(self, window: torch.Tensor) -> Tuple[float, float, float]:
        """
        window shape: (1, model_b.seq_len, n_features)
        Returns: (combined_score, score_a, score_b)
        combined = max(score_a, score_b)
        """
        slice_a = window[:, -self.model_a.seq_len:, :]
        score_a = float(self.model_a.compute_reconstruction_error(slice_a)[0])
        score_b = float(self.model_b.compute_reconstruction_error(window)[0])
        return max(score_a, score_b), score_a, score_b

    def is_anomaly(self, window: torch.Tensor) -> Tuple[bool, float, float, float]:
        """
        Returns: (is_anomaly, combined_score, score_a, score_b)
        is_anomaly = True jika salah satu model melampaui threshold-nya.
        """
        combined, score_a, score_b = self.score(window)
        thr_a = self.model_a.anomaly_threshold or float('inf')
        thr_b = self.model_b.anomaly_threshold or float('inf')
        flagged = (score_a > thr_a) or (score_b > thr_b)
        return flagged, combined, score_a, score_b
```

- [ ] **Step 5: Jalankan test untuk verifikasi PASS**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 -m pytest tests/test_gru_autoencoder.py -v
```

Expected:
```
tests/test_gru_autoencoder.py::test_forward_output_shape PASSED
tests/test_gru_autoencoder.py::test_reconstruction_error_shape PASSED
tests/test_gru_autoencoder.py::test_fit_threshold_sets_values PASSED
tests/test_gru_autoencoder.py::test_save_load_roundtrip PASSED
tests/test_gru_autoencoder.py::test_ensemble_score_uses_correct_slicing PASSED
tests/test_gru_autoencoder.py::test_ensemble_is_anomaly_returns_bool PASSED
6 passed
```

- [ ] **Step 6: Commit**

```bash
git add src/detection/gru_autoencoder.py tests/test_gru_autoencoder.py tests/__init__.py
git commit -m "feat: add GRUAutoencoder and GRUEnsemble classes with unit tests"
```

---

### Task 2: train_gru.py

**Files:**
- Create: `train_gru.py`

**Context:**
- Reuse `scaler.pkl` yang sudah ada — JANGAN fit scaler baru. Scaler sudah di-fit dari benign training data untuk LSTM.
- `weighted_mse` dan `prepare_sequences` pola sama dengan `train_lstm.py:93-103`.
- `_add_computed_features` di-copy dari `train_lstm.py:57-90` — fungsi ini wajib agar kolom CV/rolling tersedia.
- Default `--model-out` beda dari LSTM: `models/gru_autoencoder_A_v1.pt`
- Config key: `'gru_model'` (bukan `'lstm_model'`), supaya GRUAutoencoder.__init__ baca dari dict yang benar.
- `bidirectional=False` di encoder hidden config tidak dipakai — default selalu `True` di GRUAutoencoder.

- [ ] **Step 1: Buat `train_gru.py`**

```python
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

    # Load existing scaler — tidak fit baru agar normalisasi identik dengan LSTM
    if not os.path.exists(args.scaler):
        print(f"Error: {args.scaler} tidak ditemukan. Jalankan train_lstm.py dulu.")
        sys.exit(1)
    with open(args.scaler, 'rb') as f:
        scaler = pickle.load(f)
    print(f"[*] Reusing scaler: {args.scaler}  ({len(scaler.data_min_)} features)")

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
```

- [ ] **Step 2: Verifikasi syntax**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 -c "import train_gru; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add train_gru.py
git commit -m "feat: add train_gru.py for GRU-A/B training (reuses scaler.pkl)"
```

---

### Task 3: evaluate_gru.py

**Files:**
- Create: `evaluate_gru.py`

**Context:**
- Eval menggunakan PyTorch langsung (bukan ONNX) untuk mode GRU-only
- `RuleBasedIDS` dan `_build_eval_json` di-copy dari `evaluate_detection.py` — jangan re-import (beda module, beda tes)
- Output JSON format identik dengan `evaluate_detection.py` supaya hasilnya langsung bisa dibandingkan
- GRUEnsemble di dalam class `GRUDetector` yang maintain sliding window 30 timestep
- Evaluasi menjalankan LSTM baseline (dual v16+v22) secara paralel untuk tabel perbandingan langsung

- [ ] **Step 1: Buat `evaluate_gru.py`**

```python
# evaluate_gru.py
"""
Evaluasi GRU Autoencoder Dual Ensemble vs LSTM Baseline.

Usage:
  # GRU ensemble only
  ./venv/bin/python3 evaluate_gru.py \\
    --model-a models/gru_autoencoder_A_v1.pt \\
    --model-b models/gru_autoencoder_B_v1.pt \\
    --csv csv/dataset_attack_mei.csv \\
    --output results/eval_results_gru_ensemble_v1.json

  # GRU ensemble + LSTM baseline side-by-side
  ./venv/bin/python3 evaluate_gru.py \\
    --model-a models/gru_autoencoder_A_v1.pt \\
    --model-b models/gru_autoencoder_B_v1.pt \\
    --csv csv/dataset_attack_mei.csv \\
    --compare-lstm \\
    --lstm-a security_model_v16_raw.onnx --thresh-a 0.21 \\
    --lstm-b security_model_v22.onnx     --thresh-b 0.5
"""

import argparse
import csv
import datetime
import json
import os
import pickle
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.detection.gru_autoencoder import GRUAutoencoder, GRUEnsemble
from src.detection.feature_schema import FEATURE_NAMES
from evaluate_detection import RuleBasedIDS, _build_eval_json, ATTACK_KEY, LABEL_NAMES

try:
    import onnxruntime as ort
    HAS_ORT = True
except ImportError:
    HAS_ORT = False


def load_csv_rows(path):
    STR_COLS = {"datetime", "alert_type"}
    rows = []
    with open(path, newline='') as f:
        for r in csv.DictReader(f):
            rows.append({k: float(v) if k not in STR_COLS else v for k, v in r.items()})
    return rows


class GRUDetector:
    """
    Sliding window detector wrapping GRUEnsemble.
    Maintains buffer of max(seq_len_a, seq_len_b) = seq_len_b timesteps.
    Raises alert (severity=1) if either model's score > its threshold
    for >=3 consecutive windows (matches LSTMDetector behavior).
    """

    def __init__(self, model_a: GRUAutoencoder, model_b: GRUAutoencoder,
                 scaler_path: str = "models/scaler.pkl"):
        self.ensemble  = GRUEnsemble(model_a, model_b)
        self.seq_len_b = model_b.seq_len
        self.n_feat    = len(FEATURE_NAMES)
        self.window    = np.zeros((self.seq_len_b, self.n_feat), dtype=np.float32)
        self.filled    = 0
        self.anomaly_cnt = 0
        self.last_combined = 0.0
        self.last_score_a  = 0.0
        self.last_score_b  = 0.0

        with open(scaler_path, 'rb') as f:
            self.scaler = pickle.load(f)

    def update(self, row: dict):
        feat = np.array([row.get(f, 0.0) for f in FEATURE_NAMES], dtype=np.float32)
        feat = self.scaler.transform(feat.reshape(1, -1))[0]
        feat = np.clip(feat, 0.0, 1.0)

        self.window = np.roll(self.window, -1, axis=0)
        self.window[-1] = feat
        if self.filled < self.seq_len_b:
            self.filled += 1
            return 0, 0.0, 0.0, 0.0

        w_tensor = torch.FloatTensor(self.window).unsqueeze(0)
        flagged, combined, score_a, score_b = self.ensemble.is_anomaly(w_tensor)
        self.last_combined = combined
        self.last_score_a  = score_a
        self.last_score_b  = score_b

        if flagged:
            self.anomaly_cnt += 1
        else:
            self.anomaly_cnt = 0

        sev = 1 if self.anomaly_cnt >= 3 else 0
        return sev, combined, score_a, score_b


def run_gru_evaluation(csv_path, model_a_path, model_b_path,
                       output_path=None, scaler="models/scaler.pkl",
                       compare_lstm=False,
                       lstm_a=None, thresh_a=0.21,
                       lstm_b=None, thresh_b=0.5):
    print(f"Loading dataset: {csv_path}")
    rows = load_csv_rows(csv_path)
    print(f"  {len(rows)} rows")

    print(f"Loading GRU-A: {model_a_path}")
    model_a = GRUAutoencoder.load(model_a_path, {})
    model_a.eval()
    print(f"  seq_len={model_a.seq_len}  threshold={model_a.anomaly_threshold:.6f}")

    print(f"Loading GRU-B: {model_b_path}")
    model_b = GRUAutoencoder.load(model_b_path, {})
    model_b.eval()
    print(f"  seq_len={model_b.seq_len}  threshold={model_b.anomaly_threshold:.6f}")

    gru_det = GRUDetector(model_a, model_b, scaler_path=scaler)
    ids     = RuleBasedIDS()

    # Optional LSTM baseline
    lstm_det = None
    if compare_lstm and HAS_ORT and lstm_a and lstm_b:
        from evaluate_detection import DualLSTMDetector
        lstm_det = DualLSTMDetector(lstm_a, thresh_a, lstm_b, thresh_b)
        print(f"LSTM baseline: {lstm_a} (thresh={thresh_a})  +  {lstm_b} (thresh={thresh_b})")

    labels, rule_sev, gru_sev, hybrid_sev = [], [], [], []
    lstm_sev_arr = []
    scores_a_arr, scores_b_arr = [], []

    for r in rows:
        now_ms = int(r["timestamp_ms"])
        label  = int(r["label"])
        rsev, _ = ids.detect(r, now_ms)
        gsev, combined, sa, sb = gru_det.update(r)
        fsev = max(rsev, gsev)

        labels.append(label)
        rule_sev.append(rsev)
        gru_sev.append(gsev)
        hybrid_sev.append(fsev)
        scores_a_arr.append(sa)
        scores_b_arr.append(sb)

        if lstm_det:
            result = lstm_det.update(r, now_ms)
            lstm_sev_arr.append(result[0])

    labels     = np.array(labels)
    rule_sev   = np.array(rule_sev)
    gru_sev    = np.array(gru_sev)
    hybrid_sev = np.array(hybrid_sev)
    y_true     = (labels != 0).astype(int)

    def print_metrics(name, pred_sev):
        from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
        y_pred = (pred_sev >= 1).astype(int)
        print(f"\n{'='*55}\n  {name}\n{'='*55}")
        print(f"  Accuracy : {accuracy_score(y_true, y_pred):.4f}")
        print(f"  Precision: {precision_score(y_true, y_pred, zero_division=0):.4f}")
        print(f"  Recall   : {recall_score(y_true, y_pred, zero_division=0):.4f}")
        print(f"  F1       : {f1_score(y_true, y_pred, zero_division=0):.4f}")
        normal_mask = labels == 0
        fp = (pred_sev[normal_mask] >= 1).sum()
        print(f"  FPR      : {fp/normal_mask.sum():.2%} ({fp}/{normal_mask.sum()})")

    print_metrics("Rule-Based IDS", rule_sev)
    print_metrics("GRU Ensemble", gru_sev)
    print_metrics("Hybrid (Rule + GRU)", hybrid_sev)

    if lstm_det and lstm_sev_arr:
        lstm_sev_np = np.array(lstm_sev_arr)
        lstm_hybrid = np.maximum(rule_sev, lstm_sev_np)
        print_metrics("LSTM Ensemble (baseline)", lstm_sev_np)
        print_metrics("Hybrid (Rule + LSTM baseline)", lstm_hybrid)

    # Per-attack breakdown
    print(f"\n{'='*55}")
    print("  Per-Attack Detection Rate (Hybrid Rule+GRU, Stage1+)")
    print(f"{'='*55}")
    print(f"  {'Label':<18} {'Total':>6} {'Det':>7} {'Rate':>8}")
    for lbl, key in ATTACK_KEY.items():
        mask  = labels == lbl
        total = mask.sum()
        if total == 0:
            continue
        det = (hybrid_sev[mask] >= 1).sum()
        print(f"  {LABEL_NAMES[lbl]:<18} {total:>6} {det:>7} {det/total:>7.1%}")

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        result = _build_eval_json(labels, rule_sev, gru_sev, hybrid_sev, y_true, csv_path)
        result['model_a'] = model_a_path
        result['model_b'] = model_b_path
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"\n[OK] Results saved: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-a",  required=True)
    parser.add_argument("--model-b",  required=True)
    parser.add_argument("--csv",      default="csv/dataset_attack_mei.csv")
    parser.add_argument("--output",   default=None)
    parser.add_argument("--scaler",   default="models/scaler.pkl")
    parser.add_argument("--compare-lstm", action="store_true")
    parser.add_argument("--lstm-a",   default="security_model_v16_raw.onnx")
    parser.add_argument("--thresh-a", type=float, default=0.21)
    parser.add_argument("--lstm-b",   default="security_model_v22.onnx")
    parser.add_argument("--thresh-b", type=float, default=0.5)
    args = parser.parse_args()

    run_gru_evaluation(
        csv_path=args.csv,
        model_a_path=args.model_a,
        model_b_path=args.model_b,
        output_path=args.output,
        scaler=args.scaler,
        compare_lstm=args.compare_lstm,
        lstm_a=args.lstm_a, thresh_a=args.thresh_a,
        lstm_b=args.lstm_b, thresh_b=args.thresh_b,
    )
```

- [ ] **Step 2: Verifikasi syntax**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 -c "import evaluate_gru; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add evaluate_gru.py
git commit -m "feat: add evaluate_gru.py for GRU ensemble evaluation"
```

---

### Task 4: export_onnx_gru.py

**Files:**
- Create: `export_onnx_gru.py`

**Context:**
- Wrapper class `ONNXGRUWrapper` identik dengan `ONNXSecurityWrapper` di `export_onnx.py:33-69` tapi gunakan `GRUAutoencoder` sebagai `self.model`
- Export dua file terpisah: `security_model_gru_A.onnx` dan `security_model_gru_B.onnx`
- dummy input shape: `[1, seq_len, n_features]` — seq_len dari model (10 untuk A, 30 untuk B)
- opset_version=14, dynamic_axes batch_size, output name='score'

- [ ] **Step 1: Buat `export_onnx_gru.py`**

```python
# export_onnx_gru.py
"""
Export GRU-A dan GRU-B ke ONNX.

Usage:
  ./venv/bin/python3 export_onnx_gru.py \\
    --model-a models/gru_autoencoder_A_v1.pt \\
    --model-b models/gru_autoencoder_B_v1.pt \\
    --scaler  models/scaler.pkl
# Output: security_model_gru_A.onnx, security_model_gru_B.onnx
"""

import argparse
import json
import os
import pickle
import sys

import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.feature_schema import FEATURE_NAMES, FEATURE_WEIGHTS as _FW_DICT


class ONNXGRUWrapper(nn.Module):
    """
    Wraps full GRU-AE pipeline ke ONNX:
      Raw features → MinMaxScaler → GRU-AE → weighted MSE → anomaly score
    score > 0.5  →  anomali (mirrors ONNXSecurityWrapper dari export_onnx.py)
    """

    def __init__(self, gru_model: GRUAutoencoder, scaler, threshold: float):
        super().__init__()
        self.model = gru_model

        data_min   = torch.tensor(scaler.data_min_, dtype=torch.float32)
        data_range = torch.tensor(scaler.data_max_ - scaler.data_min_, dtype=torch.float32)
        data_range = torch.clamp(data_range, min=1e-8)

        fw_raw  = torch.tensor([_FW_DICT.get(n, 1.0) for n in FEATURE_NAMES], dtype=torch.float32)
        fw_norm = fw_raw / fw_raw.mean()

        self.data_min     = nn.Parameter(data_min,   requires_grad=False)
        self.data_range   = nn.Parameter(data_range, requires_grad=False)
        self.feat_weights = nn.Parameter(fw_norm,    requires_grad=False)
        self.threshold    = nn.Parameter(
            torch.tensor([threshold], dtype=torch.float32), requires_grad=False
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x_norm = torch.clamp((x - self.data_min) / self.data_range, 0.0, 1.0)
        recon  = self.model(x_norm)
        error  = torch.mean((x_norm - recon) ** 2 * self.feat_weights, dim=(1, 2))
        return 0.5 * (error / self.threshold)


def export_model(model_path, scaler, out_path):
    print(f"\n[*] Exporting {model_path} → {out_path}")
    model = GRUAutoencoder.load(model_path, {})
    model.eval()

    threshold = model.anomaly_threshold
    if threshold is None:
        thr_path = model_path.replace('.pt', '_threshold.json')
        if os.path.exists(thr_path):
            with open(thr_path) as f:
                threshold = json.load(f)['threshold']
        else:
            print(f"Error: no threshold found for {model_path}")
            sys.exit(1)

    wrapped = ONNXGRUWrapper(model, scaler, threshold)
    wrapped.eval()

    seq_len = model.seq_len
    n_feat  = len(FEATURE_NAMES)
    dummy   = torch.zeros(1, seq_len, n_feat, dtype=torch.float32)

    with torch.no_grad():
        score = wrapped(dummy)
    print(f"  Dummy forward OK — score={score.item():.6f}  seq_len={seq_len}  n_feat={n_feat}")

    torch.onnx.export(
        wrapped, dummy, out_path,
        export_params=True,
        opset_version=14,
        do_constant_folding=True,
        input_names=['input'],
        output_names=['score'],
        dynamic_axes={'input': {0: 'batch_size'}, 'score': {0: 'batch_size'}},
    )
    mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"  [OK] {out_path}  ({mb:.2f} MB)  threshold={threshold:.6f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-a", default="models/gru_autoencoder_A_v1.pt")
    parser.add_argument("--model-b", default="models/gru_autoencoder_B_v1.pt")
    parser.add_argument("--scaler",  default="models/scaler.pkl")
    parser.add_argument("--out-a",   default="security_model_gru_A.onnx")
    parser.add_argument("--out-b",   default="security_model_gru_B.onnx")
    args = parser.parse_args()

    for p in [args.model_a, args.model_b, args.scaler]:
        if not os.path.exists(p):
            print(f"Error: {p} not found.")
            sys.exit(1)

    with open(args.scaler, 'rb') as f:
        scaler = pickle.load(f)
    print(f"[*] Scaler loaded: {len(scaler.data_min_)} features")

    export_model(args.model_a, scaler, args.out_a)
    export_model(args.model_b, scaler, args.out_b)

    print(f"\n=== Summary ===")
    print(f"  GRU-A: {args.out_a}  (seq_len=10, flood specialist)")
    print(f"  GRU-B: {args.out_b}  (seq_len=30, RRC storm specialist)")
    print(f"  Input  → raw features [batch, seq_len, {len(FEATURE_NAMES)}]")
    print(f"  Output → anomaly score  (>0.5 = anomali)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verifikasi syntax**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 -c "import export_onnx_gru; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add export_onnx_gru.py
git commit -m "feat: add export_onnx_gru.py for GRU-A/B ONNX export"
```

---

### Task 5: Train GRU-A (seq_len=10)

**Files:**
- Produces: `models/gru_autoencoder_A_v1.pt`, `models/gru_autoencoder_A_v1_threshold.json`, `models/gru_autoencoder_A_v1_losses.json`

**Context:**
- GRU-A adalah spesialis flood/burst — seq_len=10 (1 detik window)
- Verifikasi FPR harus < 3% pada val set; jika >5% turunkan threshold_percentile ke 97

- [ ] **Step 1: Train GRU-A**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 train_gru.py \
  --train csv/dataset_training_clean.csv \
  --val   csv/dataset_validation_clean.csv \
  --seq-len 10 \
  --epochs 150 \
  --batch-size 32 \
  --lr 0.001 \
  --model-out models/gru_autoencoder_A_v1.pt \
  --threshold-percentile 99.0 \
  2>&1 | tee models/gru_autoencoder_A_v1_train.log
```

Expected output (akhir training):
```
[GRU] Best checkpoint: epoch XX (val=0.XXXXXX)
[GRU-AE] Model saved to models/gru_autoencoder_A_v1.pt
Threshold (P99.0): 0.XXXXXX  FPR=1.XX%
```

Jika FPR > 5%, re-run dengan `--threshold-percentile 97.0`.

- [ ] **Step 2: Verifikasi model tersimpan**

```bash
ls -lh models/gru_autoencoder_A_v1.pt models/gru_autoencoder_A_v1_threshold.json
cat models/gru_autoencoder_A_v1_threshold.json
```

Expected: file ada, `fpr_pct` < 5.0

- [ ] **Step 3: Verifikasi forward pass**

```bash
./venv/bin/python3 -c "
from src.detection.gru_autoencoder import GRUAutoencoder
import torch
m = GRUAutoencoder.load('models/gru_autoencoder_A_v1.pt', {})
m.eval()
x = torch.randn(1, m.seq_len, m.input_features)
err = m.compute_reconstruction_error(x)
print(f'seq_len={m.seq_len}  n_feat={m.input_features}  err={err.item():.6f}  thr={m.anomaly_threshold:.6f}')
"
```

Expected: `seq_len=10  n_feat=16  err=X.XXXXXX  thr=X.XXXXXX`

- [ ] **Step 4: Commit**

```bash
git add models/gru_autoencoder_A_v1.pt models/gru_autoencoder_A_v1_threshold.json \
        models/gru_autoencoder_A_v1_losses.json models/gru_autoencoder_A_v1_train.log
git commit -m "feat: train GRU-A v1 (seq_len=10, flood specialist)"
```

---

### Task 6: Train GRU-B (seq_len=30)

**Files:**
- Produces: `models/gru_autoencoder_B_v1.pt`, `models/gru_autoencoder_B_v1_threshold.json`, `models/gru_autoencoder_B_v1_losses.json`

**Context:**
- GRU-B adalah spesialis RRC Storm — seq_len=30 (3 detik window, konteks lebih panjang)
- Val sequences untuk seq_len=30 lebih sedikit dari seq_len=10 — normal

- [ ] **Step 1: Train GRU-B**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 train_gru.py \
  --train csv/dataset_training_clean.csv \
  --val   csv/dataset_validation_clean.csv \
  --seq-len 30 \
  --epochs 150 \
  --batch-size 32 \
  --lr 0.001 \
  --model-out models/gru_autoencoder_B_v1.pt \
  --threshold-percentile 99.0 \
  2>&1 | tee models/gru_autoencoder_B_v1_train.log
```

- [ ] **Step 2: Verifikasi**

```bash
ls -lh models/gru_autoencoder_B_v1.pt models/gru_autoencoder_B_v1_threshold.json
cat models/gru_autoencoder_B_v1_threshold.json
```

Expected: `fpr_pct` < 5.0

- [ ] **Step 3: Verifikasi forward pass**

```bash
./venv/bin/python3 -c "
from src.detection.gru_autoencoder import GRUAutoencoder
import torch
m = GRUAutoencoder.load('models/gru_autoencoder_B_v1.pt', {})
m.eval()
x = torch.randn(1, m.seq_len, m.input_features)
err = m.compute_reconstruction_error(x)
print(f'seq_len={m.seq_len}  n_feat={m.input_features}  err={err.item():.6f}  thr={m.anomaly_threshold:.6f}')
"
```

Expected: `seq_len=30  n_feat=16  ...`

- [ ] **Step 4: Commit**

```bash
git add models/gru_autoencoder_B_v1.pt models/gru_autoencoder_B_v1_threshold.json \
        models/gru_autoencoder_B_v1_losses.json models/gru_autoencoder_B_v1_train.log
git commit -m "feat: train GRU-B v1 (seq_len=30, RRC storm specialist)"
```

---

### Task 7: Evaluasi ensemble dan export ONNX

**Files:**
- Produces: `results/eval_results_gru_ensemble_v1.json`, `security_model_gru_A.onnx`, `security_model_gru_B.onnx`

- [ ] **Step 1: Jalankan evaluasi GRU ensemble vs LSTM baseline**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 evaluate_gru.py \
  --model-a models/gru_autoencoder_A_v1.pt \
  --model-b models/gru_autoencoder_B_v1.pt \
  --csv csv/dataset_attack_mei.csv \
  --output results/eval_results_gru_ensemble_v1.json \
  --compare-lstm \
  --lstm-a security_model_v16_raw.onnx --thresh-a 0.21 \
  --lstm-b security_model_v22.onnx     --thresh-b 0.5
```

Expected: tabel per-attack recall, JSON tersimpan di results/

- [ ] **Step 2: Tampilkan ringkasan perbandingan**

```bash
./venv/bin/python3 -c "
import json

gru = json.load(open('results/eval_results_gru_ensemble_v1.json'))
lstm = json.load(open('results/eval_results_attack_mei_rule3c.json'))

print('=== Perbandingan Hybrid (Rule + Model) ===')
print(f'{\"Attack\":<18} {\"LSTM ensemble\":>14} {\"GRU ensemble\":>14}')
print('-'*50)
for key, name in [(\"ul_flood\",\"UL Flood\"),(\"dl_flood\",\"DL Flood\"),(\"burst\",\"Burst ON/OFF\"),(\"rrc_storm\",\"RRC Storm\")]:
    gru_r  = gru.get(\"per_attack\",{}).get(key,{}).get(\"hybrid\",{}).get(\"recall\",0)
    lstm_r = lstm.get(\"per_attack\",{}).get(key,{}).get(\"hybrid\",{}).get(\"recall\",0)
    print(f'{name:<18} {lstm_r:>13.1%} {gru_r:>13.1%}')
"
```

- [ ] **Step 3: Export ke ONNX**

```bash
./venv/bin/python3 export_onnx_gru.py \
  --model-a models/gru_autoencoder_A_v1.pt \
  --model-b models/gru_autoencoder_B_v1.pt \
  --scaler  models/scaler.pkl \
  --out-a   security_model_gru_A.onnx \
  --out-b   security_model_gru_B.onnx
```

Expected:
```
[OK] security_model_gru_A.onnx  (X.XX MB)
[OK] security_model_gru_B.onnx  (X.XX MB)
```

- [ ] **Step 4: Verifikasi ONNX dengan onnxruntime**

```bash
./venv/bin/python3 -c "
import onnxruntime as ort, numpy as np

for path, seq_len in [('security_model_gru_A.onnx', 10), ('security_model_gru_B.onnx', 30)]:
    sess = ort.InferenceSession(path)
    inp  = np.zeros((1, seq_len, 16), dtype=np.float32)
    score = sess.run(['score'], {'input': inp})[0][0]
    print(f'{path}: score={score:.6f}  ({\"anomali\" if score > 0.5 else \"normal\"})')
"
```

Expected: kedua model return score (tanpa error), nilai < 0.5 untuk input zeros

- [ ] **Step 5: Commit final**

```bash
git add results/eval_results_gru_ensemble_v1.json \
        security_model_gru_A.onnx security_model_gru_B.onnx
git commit -m "feat: GRU ensemble evaluation results and ONNX export"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ GRUEncoder BiGRU + TemporalAttention → Task 1
- ✅ GRUDecoder unidirectional → Task 1
- ✅ GRUEnsemble max-score OR-threshold logic → Task 1
- ✅ File terpisah gru_autoencoder.py → Task 1
- ✅ train_gru.py reuse scaler.pkl → Task 2
- ✅ evaluate_gru.py output JSON kompatibel → Task 3
- ✅ export_onnx_gru.py dua file terpisah → Task 4
- ✅ Train GRU-A seq_len=10 → Task 5
- ✅ Train GRU-B seq_len=30 → Task 6
- ✅ Eval + ONNX → Task 7
- ✅ LSTM tidak diubah sama sekali

**Catatan implementer:**
- `TemporalAttention` diimport dari `lstm_autoencoder.py` — jangan copy paste
- `GRUAutoencoder.load()` rebuild config dari saved state — pastikan `seq_len` selalu match checkpoint
- `GRUDetector.update()` normalize tiap row via scaler sebelum masuk window — sama seperti LSTMDetector
- Training panjang (150 epoch × 2 model) — perkirakan 10-30 menit tergantung hardware
- Jika `csv/dataset_training_clean.csv` tidak ada, cek `csv/` directory untuk file training alternatif
