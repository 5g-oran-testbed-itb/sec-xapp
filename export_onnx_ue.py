"""
Export per-UE GRU or LSTM autoencoder to ONNX.

Pipeline baked into ONNX:
  raw features → RobustScaler → autoencoder → MSE scalar

Input ONNX  : raw (unscaled) features, float32[1, 10, 15]
Output ONNX : MSE scalar, float32[1]  (compare > threshold in C)

Usage:
  ./venv/bin/python3 export_onnx_ue.py \\
      --arch lstm \\
      --model  models/lstm_ue_v1.pt \\
      --scaler models/lstm_ue_v1_scaler.pkl \\
      --out    models/lstm_ue_v1.onnx

  ./venv/bin/python3 export_onnx_ue.py \\
      --arch gru \\
      --model  models/gru_ue_v1.pt \\
      --scaler models/gru_ue_v1_scaler.pkl \\
      --out    models/gru_ue_v1.onnx
"""

import argparse
import os
import pickle
import sys

import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.lstm_autoencoder import LSTMAutoencoder
from src.detection.feature_schema_ue import NUM_FEATURES

SEQ_LEN = 10


class ONNXPerUEWrapper(nn.Module):
    """Wraps autoencoder with RobustScaler pre-processing and MSE output.

    RobustScaler: x_scaled = (x - center_) / scale_
    Output: mean((x_scaled - reconstruction)^2) over all timesteps and features.
    """

    def __init__(self, model: nn.Module, scaler):
        super().__init__()
        self.model = model
        center = torch.tensor(scaler.center_, dtype=torch.float32)
        scale = torch.tensor(scaler.scale_, dtype=torch.float32)
        scale = torch.clamp(scale, min=1e-8)
        self.center = nn.Parameter(center, requires_grad=False)
        self.scale = nn.Parameter(scale, requires_grad=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x_scaled = (x - self.center) / self.scale
        reconstructed = self.model(x_scaled)
        mse = torch.mean((x_scaled - reconstructed) ** 2, dim=(1, 2))
        return mse


def _gru_config():
    return {
        "gru_model": {
            "input_features": NUM_FEATURES,
            "encoder_hidden": [64, 32],
            "decoder_hidden": [32, 64],
            "latent_dim": 32,
            "bidirectional": True,
        },
        "detection": {"sequence_length": SEQ_LEN},
    }


def _lstm_config():
    return {
        "lstm_model": {
            "input_features": NUM_FEATURES,
            "encoder_hidden": [64, 32],
            "decoder_hidden": [32, 64],
            "latent_dim": 32,
            "bidirectional": False,
        },
        "detection": {"sequence_length": SEQ_LEN},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch",   required=True, choices=["gru", "lstm"])
    parser.add_argument("--model",  required=True)
    parser.add_argument("--scaler", required=True)
    parser.add_argument("--out",    required=True)
    args = parser.parse_args()

    for p in [args.model, args.scaler]:
        if not os.path.exists(p):
            print(f"Error: {p} not found")
            sys.exit(1)

    print(f"[1/4] Loading {args.arch.upper()} model from {args.model} ...")
    if args.arch == "gru":
        base_model = GRUAutoencoder.load(args.model, _gru_config())
    else:
        base_model = LSTMAutoencoder.load(args.model, _lstm_config())
    base_model.eval()

    print(f"[2/4] Loading RobustScaler from {args.scaler} ...")
    with open(args.scaler, "rb") as f:
        scaler = pickle.load(f)
    print(f"      {len(scaler.center_)} features  "
          f"center[0]={scaler.center_[0]:.4f}  scale[0]={scaler.scale_[0]:.4f}")

    print("[3/4] Wrapping model ...")
    wrapped = ONNXPerUEWrapper(base_model, scaler)
    wrapped.eval()

    dummy = torch.zeros(1, SEQ_LEN, NUM_FEATURES, dtype=torch.float32)
    with torch.no_grad():
        dummy_mse = wrapped(dummy)
    print(f"      Dummy forward OK — mse={dummy_mse.item():.6f}")

    print(f"[4/4] Exporting to {args.out} ...")
    torch.onnx.export(
        wrapped,
        dummy,
        args.out,
        export_params=True,
        opset_version=14,
        do_constant_folding=True,
        input_names=["input"],
        output_names=["mse"],
        dynamic_axes={"input": {0: "batch_size"}, "mse": {0: "batch_size"}},
    )
    size_kb = os.path.getsize(args.out) / 1024
    print(f"[OK] {args.out}  ({size_kb:.1f} KB)")
    print(f"     Input : float32[1, {SEQ_LEN}, {NUM_FEATURES}] — raw features")
    print(f"     Output: float32[1] — MSE (compare > threshold in C)")


if __name__ == "__main__":
    main()
