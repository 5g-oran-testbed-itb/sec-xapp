"""
Export per-UE GRU or LSTM autoencoder to ONNX.

Pipeline baked into ONNX:
  raw features → MinMaxScaler → autoencoder → weighted MSE scalar

Input ONNX  : raw (unscaled) features, float32[1, 30, 19]
Output ONNX : weighted MSE scalar, float32[1]  (compare > threshold in C)

Weighted MSE uses Scheme A weights from feature_schema_ue.FEATURE_WEIGHTS.
Output is on the same scale as evaluate_per_ue_v2.py — use recalibrated
P97 threshold (not the training-time uniform-MSE threshold).

Usage:
  ./venv/bin/python3 export_onnx_ue.py \\
      --arch gru \\
      --model  models/gru_ue_v4.pt \\
      --scaler models/gru_ue_v4_scaler.pkl \\
      --out    models/gru_ue_v4.onnx

  ./venv/bin/python3 export_onnx_ue.py \\
      --arch lstm \\
      --model  models/lstm_ue_v4.pt \\
      --scaler models/lstm_ue_v4_scaler.pkl \\
      --out    models/lstm_ue_v4.onnx
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
from src.detection.feature_schema_ue import NUM_FEATURES, FEATURE_NAMES, FEATURE_WEIGHTS

SEQ_LEN = 30

_WEIGHT_VEC = torch.tensor(
    [FEATURE_WEIGHTS.get(n, 1.0) for n in FEATURE_NAMES], dtype=torch.float32
)


class ONNXPerUEWrapper(nn.Module):
    """Wraps autoencoder with scaler pre-processing and weighted MSE output.

    Supports MinMaxScaler (v4+) and RobustScaler (v1, legacy).
    Unified linear transform: x_scaled = x * a + b
      - MinMaxScaler: a = scale_,  b = min_
      - RobustScaler: a = 1/scale_, b = -center_/scale_

    Output: Scheme A weighted MSE averaged over timesteps — same scoring
    as evaluate_per_ue_v2.py score_ml().
    """

    def __init__(self, model: nn.Module, scaler, weights: torch.Tensor):
        super().__init__()
        self.model = model

        if hasattr(scaler, 'min_'):   # MinMaxScaler
            a = torch.tensor(scaler.scale_, dtype=torch.float32)
            b = torch.tensor(scaler.min_,   dtype=torch.float32)
        else:                          # RobustScaler (legacy)
            inv_scale = 1.0 / torch.tensor(scaler.scale_, dtype=torch.float32).clamp(min=1e-8)
            a = inv_scale
            b = -torch.tensor(scaler.center_, dtype=torch.float32) * inv_scale

        self.a = nn.Parameter(a, requires_grad=False)
        self.b = nn.Parameter(b, requires_grad=False)
        w_norm = weights / weights.sum()
        self.w = nn.Parameter(w_norm, requires_grad=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x_scaled = x * self.a + self.b
        recon = self.model(x_scaled)
        fe = (x_scaled - recon) ** 2          # (B, T, F)
        fe_mean = fe.mean(dim=1)               # (B, F) — mean over timesteps
        score = (fe_mean * self.w).sum(dim=-1) # (B,)   — weighted sum (w already normalized)
        return score


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

    print(f"[2/4] Loading scaler from {args.scaler} ...")
    with open(args.scaler, "rb") as f:
        scaler = pickle.load(f)
    scaler_type = "MinMaxScaler" if hasattr(scaler, 'min_') else "RobustScaler"
    n_feat = len(scaler.scale_)
    print(f"      {scaler_type}  {n_feat} features  scale[0]={scaler.scale_[0]:.6f}")

    print("[3/4] Wrapping model (scaler + weighted MSE) ...")
    wrapped = ONNXPerUEWrapper(base_model, scaler, _WEIGHT_VEC)
    wrapped.eval()

    dummy = torch.zeros(1, SEQ_LEN, NUM_FEATURES, dtype=torch.float32)
    with torch.no_grad():
        dummy_score = wrapped(dummy)
    print(f"      Dummy forward OK — weighted_mse={dummy_score.item():.6f}")

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
        dynamo=False,
    )
    size_kb = os.path.getsize(args.out) / 1024
    print(f"[OK] {args.out}  ({size_kb:.1f} KB)")
    print(f"     Input : float32[1, {SEQ_LEN}, {NUM_FEATURES}] — raw features (unscaled)")
    print(f"     Output: float32[1] — weighted MSE (Scheme A, compare > threshold in C)")


if __name__ == "__main__":
    main()
