"""
Export LSTM-Autoencoder / LSTM-VAE ke ONNX untuk inferensi di C xApp.

Pipeline end-to-end yang dibake ke dalam ONNX:
  Raw features → MinMaxScaler normalize → LSTM-AE/VAE → anomaly score

Input ONNX  : raw (unnormalized) features, shape [batch, seq_len=10, n_features=18]
Output ONNX : anomaly score per sample; score > 0.5 berarti anomali (error > threshold)

Usage:
  # Plain AE (default — v8):
  ./venv/bin/python3 export_onnx.py

  # VAE:
  ./venv/bin/python3 export_onnx.py --vae --model models/lstm_autoencoder_v13.pt \
      --threshold models/lstm_autoencoder_v13_threshold.json --beta 0.01
"""

import argparse
import json
import os
import pickle
import sys

import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.detection.lstm_autoencoder import LSTMAutoencoder, LSTMVariationalAutoencoder
from src.detection.feature_schema import FEATURE_NAMES, FEATURE_WEIGHTS as _FW_DICT


class ONNXSecurityWrapper(nn.Module):
    """
    Wrapper PyTorch untuk plain AE — membungkus seluruh pipeline deteksi:
      1. MinMaxScaler normalization (dibake dari scaler.pkl)
      2. LSTM-AE forward pass
      3. MSE reconstruction error per sample
      4. Normalisasi ke anomaly score (score > 0.5 = anomali)
    """

    def __init__(self, lstm_model: LSTMAutoencoder, scaler, threshold: float):
        super().__init__()
        self.model = lstm_model

        data_min   = torch.tensor(scaler.data_min_, dtype=torch.float32)
        data_range = torch.tensor(scaler.data_max_ - scaler.data_min_, dtype=torch.float32)
        data_range = torch.clamp(data_range, min=1e-8)

        # feature weights dari feature_schema — normalisasi agar mean=1 (skala MSE tidak berubah)
        fw_raw = torch.tensor([_FW_DICT.get(n, 1.0) for n in FEATURE_NAMES], dtype=torch.float32)
        fw_norm = fw_raw / fw_raw.mean()

        self.data_min     = nn.Parameter(data_min,   requires_grad=False)
        self.data_range   = nn.Parameter(data_range, requires_grad=False)
        self.feat_weights = nn.Parameter(fw_norm,    requires_grad=False)
        self.threshold    = nn.Parameter(
            torch.tensor([threshold], dtype=torch.float32), requires_grad=False
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x_norm = (x - self.data_min) / self.data_range
        x_norm = torch.clamp(x_norm, 0.0, 1.0)
        reconstructed = self.model(x_norm)
        # weighted MSE: bobot per-fitur broadcast ke (batch, seq_len, n_features)
        error = torch.mean((x_norm - reconstructed) ** 2 * self.feat_weights, dim=(1, 2))
        # score > 0.5  →  weighted_error > threshold  →  anomali
        score = 0.5 * (error / self.threshold)
        return score


class ONNXVAEWrapper(nn.Module):
    """
    Wrapper PyTorch untuk VAE — pipeline:
      1. MinMaxScaler normalization
      2. LSTM-VAE encoder → μ (deterministic inference)
      3. LSTM-VAE decoder dari μ
      4. anomaly score = MSE_recon + β_inf * KL  →  normalisasi ke 0.5 threshold
    """

    def __init__(self, vae_model: LSTMVariationalAutoencoder, scaler,
                 threshold: float, beta_inf: float):
        super().__init__()
        self.model = vae_model

        data_min   = torch.tensor(scaler.data_min_, dtype=torch.float32)
        data_range = torch.tensor(scaler.data_max_ - scaler.data_min_, dtype=torch.float32)
        data_range = torch.clamp(data_range, min=1e-8)

        self.data_min   = nn.Parameter(data_min,   requires_grad=False)
        self.data_range = nn.Parameter(data_range, requires_grad=False)
        self.threshold  = nn.Parameter(
            torch.tensor([threshold], dtype=torch.float32), requires_grad=False
        )
        self.beta_inf   = nn.Parameter(
            torch.tensor([beta_inf], dtype=torch.float32), requires_grad=False
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x_norm = (x - self.data_min) / self.data_range
        x_norm = torch.clamp(x_norm, 0.0, 1.0)

        mu, logvar = self.model.encoder(x_norm)
        recon      = self.model.decoder(mu)  # pakai μ tanpa sampling (deterministic)
        recon_err  = torch.mean((x_norm - recon) ** 2, dim=(1, 2))
        kl         = -0.5 * torch.mean(1 + logvar - mu.pow(2) - logvar.exp(), dim=1)
        raw_score  = recon_err + self.beta_inf * kl

        # score > 0.5  →  raw_score > threshold  →  anomali
        score = 0.5 * (raw_score / self.threshold)
        return score


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vae",       action="store_true",
                        help="Export VAE model (default: plain AE)")
    parser.add_argument("--model",     type=str, default=None,
                        help="Path model .pt (default: v8 untuk AE, v13 untuk VAE)")
    parser.add_argument("--threshold", type=str, default=None,
                        help="Path threshold JSON")
    parser.add_argument("--scaler",    type=str, default="models/scaler.pkl")
    parser.add_argument("--out",       type=str, default="security_model.onnx")
    parser.add_argument("--beta",      type=float, default=0.01,
                        help="β untuk VAE inference scoring (default: 0.01)")
    parser.add_argument("--seq-len",   type=int,   default=10,
                        help="Panjang sequence LSTM (default: 10)")
    parser.add_argument("--num-features", type=int, default=None,
                        help="Gunakan N fitur pertama dari FEATURE_NAMES (default: semua)")
    args = parser.parse_args()

    global FEATURE_NAMES
    if args.num_features is not None and args.num_features < len(FEATURE_NAMES):
        FEATURE_NAMES = FEATURE_NAMES[:args.num_features]

    if args.vae:
        model_path     = args.model     or "models/lstm_autoencoder_v13.pt"
        threshold_path = args.threshold or "models/lstm_autoencoder_v13_threshold.json"
    else:
        model_path     = args.model     or "models/lstm_autoencoder_v8.pt"
        threshold_path = args.threshold or "models/lstm_autoencoder_v8_threshold.json"

    scaler_path = args.scaler
    onnx_path   = args.out

    config = {
        'lstm_model': {
            'input_features': len(FEATURE_NAMES),
            'encoder_hidden': [64, 32],
            'decoder_hidden': [32, 64],
            'latent_dim': 32,
            'bidirectional': False,
        },
        'detection': {
            'sequence_length': args.seq_len,
            'anomaly_threshold_percentile': 99.5,
        }
    }

    for path in [model_path, scaler_path, threshold_path]:
        if not os.path.exists(path):
            print(f"Error: {path} tidak ditemukan.")
            sys.exit(1)

    print(f"[1/4] Loading {'VAE' if args.vae else 'AE'} model dari {model_path}...")
    if args.vae:
        base_model = LSTMVariationalAutoencoder.load(model_path, config)
    else:
        base_model = LSTMAutoencoder.load(model_path, config)
    base_model.eval()

    print(f"[2/4] Loading MinMaxScaler dari {scaler_path}...")
    with open(scaler_path, 'rb') as f:
        scaler = pickle.load(f)
    print(f"      {len(scaler.data_min_)} features")
    for i, name in enumerate(FEATURE_NAMES):
        print(f"      [{i}] {name:30s}  min={scaler.data_min_[i]:.4f}  max={scaler.data_max_[i]:.4f}")

    print(f"[3/4] Loading threshold dari {threshold_path}...")
    with open(threshold_path) as f:
        thresh_data = json.load(f)
    threshold = thresh_data['threshold']
    print(f"      μ={thresh_data['mu']:.6f}  σ={thresh_data['sigma']:.6f}")
    pct_label = f"P{thresh_data.get('percentile', 99.5)}"
    print(f"      T={threshold:.6f}  ({pct_label})  FPR={thresh_data['fpr_pct']:.2f}%  source={thresh_data['source']}")

    print(f"[4/4] Wrapping model dan export ke {onnx_path}...")
    if args.vae:
        beta_inf = thresh_data.get('beta_inf', args.beta)
        wrapped  = ONNXVAEWrapper(base_model, scaler, threshold, beta_inf)
        print(f"      VAE β_inf={beta_inf}")
    else:
        wrapped = ONNXSecurityWrapper(base_model, scaler, threshold)
    wrapped.eval()

    dummy_input = torch.zeros(1, args.seq_len, len(FEATURE_NAMES), dtype=torch.float32)

    with torch.no_grad():
        dummy_score = wrapped(dummy_input)
    print(f"      Dummy forward pass OK — score={dummy_score.item():.6f} "
          f"({'anomali' if dummy_score.item() > 0.5 else 'normal'})")

    torch.onnx.export(
        wrapped,
        dummy_input,
        onnx_path,
        export_params=True,
        opset_version=14,
        do_constant_folding=True,
        input_names=['input'],
        output_names=['score'],
        dynamic_axes={'input': {0: 'batch_size'}, 'score': {0: 'batch_size'}}
    )

    size_mb = os.path.getsize(onnx_path) / 1024 / 1024
    print(f"[OK] ONNX tersimpan: {onnx_path}  ({size_mb:.2f} MB)")

    mode_str = f"LSTM-VAE (β_inf={thresh_data.get('beta_inf', args.beta)})" if args.vae else "LSTM-AE (mean-MSE)"
    print(f"\n=== Summary ===")
    print(f"  Model      : {model_path}  [{mode_str}]")
    print(f"  Scaler     : MinMaxScaler  ({len(FEATURE_NAMES)} features, dibake ke ONNX)")
    print(f"  Threshold  : {threshold:.6f}  ({pct_label} dari val set, FPR={thresh_data['fpr_pct']:.2f}%)")
    print(f"  ONNX output: {onnx_path}  ({size_mb:.2f} MB)")
    print(f"\n  Input  → raw features [batch, 10, {len(FEATURE_NAMES)}] (belum dinormalisasi)")
    print(f"  Output → anomaly score per sample  (score > 0.5 = anomali)")


if __name__ == "__main__":
    main()
