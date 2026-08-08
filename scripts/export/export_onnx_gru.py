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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
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
