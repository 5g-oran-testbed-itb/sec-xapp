#!/usr/bin/env python3
"""Derive benign-scale loss weights from a pass-1 (uniform-loss) AE.

w_j = 1/(median(e_j)+MAD(e_j)+eps), capped — from per-feature benign TRAINING
residuals. Frozen constants (no attack info). See loss-ablation design spec.
"""
import argparse, json, os, pickle, sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "train"))  # for train_gru_ue

from evaluate_per_ue_v2 import GRU_CFG, LSTM_CFG, build_windows
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.lstm_autoencoder import LSTMAutoencoder
from src.detection.feature_schema_ue import FEATURE_NAMES
from src.detection.scoring import benign_calibrated_weights, per_feature_residuals_from_windows
from train_gru_ue import load_csv, df_to_raw   # same feature pipeline as training (label==0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", choices=["gru", "lstm"], required=True)
    ap.add_argument("--model", required=True)     # pass-1 uniform model .pt
    ap.add_argument("--scaler", required=True)
    ap.add_argument("--train", default="csv/dataset_training_ue_juni.csv")
    ap.add_argument("--seq-len", type=int, default=30)
    ap.add_argument("--out", required=True)        # weights JSON
    args = ap.parse_args()

    cfg = GRU_CFG if args.arch == "gru" else LSTM_CFG
    Model = GRUAutoencoder if args.arch == "gru" else LSTMAutoencoder
    model = Model.load(args.model, cfg)
    with open(args.scaler, "rb") as f:
        scaler = pickle.load(f)

    raw = df_to_raw(load_csv(args.train))                      # benign training only
    wins = build_windows(scaler.transform(raw).astype("float32"), args.seq_len)
    res = per_feature_residuals_from_windows(model, wins)      # (N, F)
    w = benign_calibrated_weights(res)                         # (F,)

    d = {f: float(w[i]) for i, f in enumerate(FEATURE_NAMES)}
    with open(args.out, "w") as f:
        json.dump(d, f, indent=2)
    print(f"[derive:{args.arch}] wrote {args.out}")
    print("  weights:", {k: round(v, 4) for k, v in d.items()})


if __name__ == "__main__":
    main()
