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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
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

    def _load_model_with_threshold(model_path):
        m = GRUAutoencoder.load(model_path, {})
        m.eval()
        if m.anomaly_threshold is None:
            thresh_path = model_path.replace('.pt', '_threshold.json')
            if os.path.exists(thresh_path):
                with open(thresh_path) as f:
                    m.anomaly_threshold = json.load(f)['threshold']
            else:
                raise FileNotFoundError(f"Threshold not in .pt and {thresh_path} not found")
        return m

    print(f"Loading GRU-A: {model_a_path}")
    model_a = _load_model_with_threshold(model_a_path)
    print(f"  seq_len={model_a.seq_len}  threshold={model_a.anomaly_threshold:.6f}")

    print(f"Loading GRU-B: {model_b_path}")
    model_b = _load_model_with_threshold(model_b_path)
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
