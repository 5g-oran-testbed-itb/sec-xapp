"""
Export LSTM-Autoencoder v2 ke ONNX untuk inferensi di C xApp.

Pipeline end-to-end yang dibake ke dalam ONNX:
  Raw features → MinMaxScaler normalize → LSTM-AE → MSE error → anomaly score

Input ONNX  : raw (unnormalized) features, shape [batch, seq_len=10, n_features=10]
Output ONNX : anomaly score per sample; score > 0.5 berarti anomali (error > threshold)

Usage:
  cd /home/telmat/xapp/security-xapp
  ./venv/bin/python3 export_onnx.py
"""

import json
import os
import pickle
import sys

import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.detection.lstm_autoencoder import LSTMAutoencoder
from src.detection.feature_schema import FEATURE_NAMES


class ONNXSecurityWrapper(nn.Module):
    """
    Wrapper PyTorch yang membungkus seluruh pipeline deteksi ke dalam satu ONNX graph:
      1. MinMaxScaler normalization (dibake dari scaler.pkl)
      2. LSTM-Autoencoder forward pass
      3. MSE reconstruction error per sample
      4. Normalisasi ke anomaly score (score > 0.5 = anomali)

    Ini membuat kode C hanya perlu mengumpan raw feature tensor — tidak ada
    preprocessing di sisi C.
    """

    def __init__(self, lstm_model: LSTMAutoencoder, scaler, threshold: float):
        super().__init__()
        self.model = lstm_model

        # MinMaxScaler: x_scaled = (x - data_min) / (data_max - data_min)
        data_min   = torch.tensor(scaler.data_min_, dtype=torch.float32)
        data_range = torch.tensor(scaler.data_max_ - scaler.data_min_, dtype=torch.float32)
        data_range = torch.clamp(data_range, min=1e-8)  # cegah division by zero fitur konstan

        self.data_min   = nn.Parameter(data_min,   requires_grad=False)
        self.data_range = nn.Parameter(data_range, requires_grad=False)
        self.threshold  = nn.Parameter(
            torch.tensor([threshold], dtype=torch.float32), requires_grad=False
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len, n_features) — nilai raw dari xApp C
        x_norm = (x - self.data_min) / self.data_range
        x_norm = torch.clamp(x_norm, 0.0, 1.0)   # clip ke [0,1] sesuai MinMaxScaler

        reconstructed = self.model(x_norm)

        # MSE reconstruction error per sample
        error = torch.mean((x_norm - reconstructed) ** 2, dim=(1, 2))

        # Anomaly score: 0.5 * (error / threshold)
        # score > 0.5  →  error > threshold  →  anomali
        score = 0.5 * (error / self.threshold)
        return score


def main():
    model_path     = "models/lstm_autoencoder_v5.pt"
    scaler_path    = "models/scaler.pkl"
    threshold_path = "models/lstm_autoencoder_v5_threshold.json"
    onnx_path      = "security_model.onnx"

    config = {
        'lstm_model': {
            'input_features': len(FEATURE_NAMES),
            'encoder_hidden': [64, 32],
            'decoder_hidden': [32, 64],
            'latent_dim': 32,
            'learning_rate': 0.001,
            'epochs': 150,
            'batch_size': 64,
        },
        'detection': {
            'sequence_length': 10,
            'anomaly_threshold_percentile': 99.5,
        }
    }

    for path in [model_path, scaler_path, threshold_path]:
        if not os.path.exists(path):
            print(f"Error: {path} tidak ditemukan.")
            sys.exit(1)

    print(f"[1/4] Loading LSTM model dari {model_path}...")
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
    wrapped = ONNXSecurityWrapper(base_model, scaler, threshold)
    wrapped.eval()

    # Dummy input: satu batch, 10 timestep, 10 fitur — semua nol (benign idle)
    dummy_input = torch.zeros(1, 10, len(FEATURE_NAMES), dtype=torch.float32)

    # Verifikasi forward pass sebelum export
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

    print(f"\n=== Summary ===")
    print(f"  Model      : {model_path}")
    print(f"  Scaler     : MinMaxScaler  ({len(FEATURE_NAMES)} features, dibake ke ONNX)")
    print(f"  Threshold  : {threshold:.6f}  (P{thresh_data.get('percentile', 99.5)} dari val set, FPR={thresh_data['fpr_pct']:.2f}%)")
    print(f"  ONNX output: {onnx_path}  ({size_mb:.2f} MB)")
    print(f"\n  Input  → raw features [batch, 10, {len(FEATURE_NAMES)}] (belum dinormalisasi)")
    print(f"  Output → anomaly score per sample  (score > 0.5 = anomali)")


if __name__ == "__main__":
    main()
