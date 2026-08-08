"""
evaluate_ue_models.py — Evaluasi GRU-UE v1 dan LSTM-UE v1 (per-UE 15-feature models)

Menghasilkan:
  - FPR/TNR dari dataset validation benign (full 15-feature)
  - Confusion matrix dari dataset_testing_v3.csv (partial: hanya 8/15 fitur tersedia,
    throughput features = 0 karena tidak ada di dataset cell-level)

Usage:
  python3 evaluate_ue_models.py
  python3 evaluate_ue_models.py --val csv/dataset_validation_ue_juni.csv \
      --attack csv/dataset_testing_v3.csv --output results/eval_ue_v1.json
"""

import argparse, json, os, pickle, sys
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.lstm_autoencoder import LSTMAutoencoder
from src.detection.feature_schema_ue import FEATURE_NAMES, NUM_FEATURES

LABEL_NAMES = {0: "Benign", 1: "UL Flood", 2: "DL Flood", 3: "Burst ON/OFF", 4: "RRC Storm", 5: "RF Jammer"}
ATTACK_KEY  = {1, 2, 3, 4, 5, 6, 7}


# ─── feature extraction ───────────────────────────────────────────────────────

# Mapping from per-UE feature name to cell-level CSV column (or None if not available)
CELL_COLUMN_MAP = {
    "prb_usage_dl_ratio": "prb_usage_dl_ratio",
    "prb_usage_ul_ratio": "prb_usage_ul_ratio",
    "prb_direction":      "prb_direction",
    "prb_total":          "prb_total",
    "prb_ul_delta":       "prb_ul_delta",
}
THROUGHPUT_ZERO = {"thp_dl_kbps", "thp_ul_kbps", "thp_total_kbps",
                   "thp_ul_delta", "thp_dl_delta", "traffic_direction", "ul_efficiency"}
ROLLING_COMPUTED = {"prb_ul_roll_mean", "prb_ul_roll_std", "ul_persistence"}


def extract_ue_features_from_csv(rows: list[dict]) -> np.ndarray:
    """
    Build (N, 15) feature matrix from per-UE CSV rows.
    All 15 features present natively.
    """
    X = np.zeros((len(rows), NUM_FEATURES), dtype=np.float32)
    for i, row in enumerate(rows):
        for j, name in enumerate(FEATURE_NAMES):
            X[i, j] = float(row.get(name, 0.0))
    return X


def extract_partial_features_from_cell_csv(rows: list[dict]) -> np.ndarray:
    """
    Build (N, 15) feature matrix from cell-level CSV rows.
    8 of 15 features are computable; throughput-based 7 stay 0.
    """
    N = len(rows)
    # Extract raw arrays first
    prb_ul = np.array([float(r.get("prb_usage_ul_ratio", 0.0)) for r in rows], dtype=np.float32)

    X = np.zeros((N, NUM_FEATURES), dtype=np.float32)
    for i, row in enumerate(rows):
        for j, name in enumerate(FEATURE_NAMES):
            if name in CELL_COLUMN_MAP:
                X[i, j] = float(row.get(CELL_COLUMN_MAP[name], 0.0))
            elif name in THROUGHPUT_ZERO:
                X[i, j] = 0.0
            elif name == "prb_ul_roll_mean":
                start = max(0, i - 9)
                X[i, j] = float(np.mean(prb_ul[start:i+1]))
            elif name == "prb_ul_roll_std":
                start = max(0, i - 9)
                X[i, j] = float(np.std(prb_ul[start:i+1]))
            elif name == "ul_persistence":
                start = max(0, i - 9)
                window = prb_ul[start:i+1]
                X[i, j] = float(np.mean(window > 0))
    return X


# ─── inference helpers ────────────────────────────────────────────────────────

def build_windows(X: np.ndarray, seq_len: int) -> np.ndarray:
    """Sliding windows: shape (N-seq_len+1, seq_len, features)."""
    N = X.shape[0]
    if N < seq_len:
        return np.empty((0, seq_len, X.shape[1]), dtype=np.float32)
    idx = np.arange(N - seq_len + 1)
    return np.stack([X[i:i+seq_len] for i in idx], axis=0)


def score_gru(model: GRUAutoencoder, scaler, X: np.ndarray, batch: int = 256) -> np.ndarray:
    Xs = scaler.transform(X)
    wins = build_windows(Xs, model.seq_len)
    if len(wins) == 0:
        return np.array([])
    scores = []
    model.eval()
    for i in range(0, len(wins), batch):
        t = torch.tensor(wins[i:i+batch])
        scores.append(model.compute_reconstruction_error(t).numpy())
    return np.concatenate(scores)


def score_lstm(model: LSTMAutoencoder, scaler, X: np.ndarray, batch: int = 256) -> np.ndarray:
    Xs = scaler.transform(X)
    wins = build_windows(Xs, model.seq_len)
    if len(wins) == 0:
        return np.array([])
    scores = []
    model.eval()
    with torch.no_grad():
        for i in range(0, len(wins), batch):
            t = torch.tensor(wins[i:i+batch])
            err = model.compute_reconstruction_error(t)
            scores.append(err.numpy())
    return np.concatenate(scores)


def align_labels(labels: np.ndarray, seq_len: int) -> np.ndarray:
    """Window label = label of last timestep in window."""
    return labels[seq_len - 1:]


# ─── metrics ──────────────────────────────────────────────────────────────────

def compute_metrics(scores: np.ndarray, labels: np.ndarray, threshold: float) -> dict:
    preds = (scores >= threshold).astype(int)
    is_attack = np.isin(labels, list(ATTACK_KEY)).astype(int)

    TP = int(((preds == 1) & (is_attack == 1)).sum())
    FP = int(((preds == 1) & (is_attack == 0)).sum())
    TN = int(((preds == 0) & (is_attack == 0)).sum())
    FN = int(((preds == 0) & (is_attack == 1)).sum())

    precision = TP / (TP + FP) if (TP + FP) > 0 else 0.0
    recall    = TP / (TP + FN) if (TP + FN) > 0 else 0.0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    fpr       = FP / (FP + TN) if (FP + TN) > 0 else 0.0
    accuracy  = (TP + TN) / len(labels) if len(labels) > 0 else 0.0

    return {
        "TP": TP, "FP": FP, "TN": TN, "FN": FN,
        "precision": round(precision * 100, 2),
        "recall":    round(recall    * 100, 2),
        "f1":        round(f1        * 100, 2),
        "fpr_pct":   round(fpr       * 100, 2),
        "accuracy":  round(accuracy  * 100, 2),
    }


def per_label_recall(scores: np.ndarray, labels: np.ndarray, threshold: float) -> dict:
    preds = (scores >= threshold).astype(int)
    result = {}
    for lbl, name in LABEL_NAMES.items():
        mask = (labels == lbl)
        if mask.sum() == 0:
            continue
        detected = int(preds[mask].sum())
        total    = int(mask.sum())
        result[name] = {"detected": detected, "total": total,
                        "recall_pct": round(detected / total * 100, 2)}
    return result


def load_csv(path: str) -> list[dict]:
    import csv
    STR_COLS = {"datetime", "alert_type"}
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({k: (v if k in STR_COLS else float(v)) for k, v in r.items()})
    return rows


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--val",    default="csv/dataset_validation_ue_juni.csv")
    ap.add_argument("--attack", default="csv/dataset_testing_v3.csv")
    ap.add_argument("--gru-model",  default="models/gru_ue_v1.pt")
    ap.add_argument("--lstm-model", default="models/lstm_ue_v1.pt")
    ap.add_argument("--gru-scaler",  default="models/gru_ue_v1_scaler.pkl")
    ap.add_argument("--lstm-scaler", default="models/lstm_ue_v1_scaler.pkl")
    ap.add_argument("--gru-threshold",  default="models/gru_ue_v1_threshold.json")
    ap.add_argument("--lstm-threshold", default="models/lstm_ue_v1_threshold.json")
    ap.add_argument("--output", default="results/eval_ue_v1.json")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    # ── Load models ─────────────────────────────────────────────────────────
    print("[*] Loading GRU-UE v1...")
    gru_cfg = {"gru_model": {"input_features": NUM_FEATURES, "bidirectional": True},
               "detection": {"sequence_length": 10}}
    gru = GRUAutoencoder.load(args.gru_model, gru_cfg)
    gru.eval()
    with open(args.gru_scaler, "rb") as f: gru_scaler = pickle.load(f)
    gru_thresh = json.load(open(args.gru_threshold))["threshold"]
    print(f"    threshold={gru_thresh:.0f}  seq_len={gru.seq_len}")

    print("[*] Loading LSTM-UE v1...")
    lstm_cfg = {"lstm_model": {"input_features": NUM_FEATURES, "bidirectional": False},
                "detection": {"sequence_length": 10}}
    lstm_state = torch.load(args.lstm_model, map_location="cpu", weights_only=False)
    lstm_saved = lstm_state.get("config", {})
    lstm_model_cfg = {
        "lstm_model": {
            "input_features": lstm_saved.get("input_features", NUM_FEATURES),
            "hidden_sizes":   lstm_saved.get("hidden_sizes", [64, 32]),
            "latent_dim":     lstm_saved.get("latent_dim", 32),
            "bidirectional":  lstm_saved.get("bidirectional", False),
        },
        "detection": {"sequence_length": lstm_saved.get("seq_len", 10)},
    }
    lstm = LSTMAutoencoder(lstm_model_cfg)
    lstm.load_state_dict(lstm_state["model_state_dict"])
    lstm.anomaly_threshold = lstm_state.get("anomaly_threshold")
    lstm.eval()
    with open(args.lstm_scaler, "rb") as f: lstm_scaler = pickle.load(f)
    lstm_thresh = json.load(open(args.lstm_threshold))["threshold"]
    print(f"    threshold={lstm_thresh:.0f}  seq_len={lstm.seq_len}")

    results = {}

    # ── BENIGN VALIDATION (full 15-feature) ──────────────────────────────────
    print(f"\n[*] Evaluating on benign validation set: {args.val}")
    val_rows = load_csv(args.val)
    val_labels_raw = np.array([int(r.get("label", 0)) for r in val_rows])
    X_val = extract_ue_features_from_csv(val_rows)

    gru_val_scores  = score_gru(gru, gru_scaler, X_val)
    lstm_val_scores = score_lstm(lstm, lstm_scaler, X_val)
    val_labels = align_labels(val_labels_raw, gru.seq_len)

    gru_val_m  = compute_metrics(gru_val_scores,  val_labels, gru_thresh)
    lstm_val_m = compute_metrics(lstm_val_scores, val_labels, lstm_thresh)

    print(f"  GRU   → FPR={gru_val_m['fpr_pct']:.2f}%  TP={gru_val_m['TP']}  TN={gru_val_m['TN']}  FP={gru_val_m['FP']}")
    print(f"  LSTM  → FPR={lstm_val_m['fpr_pct']:.2f}%  TP={lstm_val_m['TP']}  TN={lstm_val_m['TN']}  FP={lstm_val_m['FP']}")

    results["benign_validation"] = {
        "dataset": args.val,
        "n_rows":  len(val_rows),
        "note":    "Full 15-feature evaluation — all features native from per-UE CSV",
        "GRU_UE_v1":  {"threshold": gru_thresh,  **gru_val_m},
        "LSTM_UE_v1": {"threshold": lstm_thresh, **lstm_val_m},
    }

    # ── LABELED ATTACK DATASET (partial 8/15 features) ───────────────────────
    if os.path.exists(args.attack):
        print(f"\n[*] Evaluating on attack dataset (PARTIAL): {args.attack}")
        print("    WARNING: 7 throughput features (thp_*, ul_efficiency, traffic_direction)")
        print("             set to 0 — cell-level dataset has no throughput data.")
        atk_rows = load_csv(args.attack)
        atk_labels_raw = np.array([int(r.get("label", 0)) for r in atk_rows])
        X_atk = extract_partial_features_from_cell_csv(atk_rows)

        gru_atk_scores  = score_gru(gru, gru_scaler, X_atk)
        lstm_atk_scores = score_lstm(lstm, lstm_scaler, X_atk)
        atk_labels = align_labels(atk_labels_raw, gru.seq_len)

        gru_atk_m  = compute_metrics(gru_atk_scores,  atk_labels, gru_thresh)
        lstm_atk_m = compute_metrics(lstm_atk_scores, atk_labels, lstm_thresh)

        gru_per_label  = per_label_recall(gru_atk_scores,  atk_labels, gru_thresh)
        lstm_per_label = per_label_recall(lstm_atk_scores, atk_labels, lstm_thresh)

        print(f"\n  [GRU-UE] Confusion Matrix:")
        print(f"    TP={gru_atk_m['TP']}  FP={gru_atk_m['FP']}  TN={gru_atk_m['TN']}  FN={gru_atk_m['FN']}")
        print(f"    Precision={gru_atk_m['precision']:.1f}%  Recall={gru_atk_m['recall']:.1f}%  F1={gru_atk_m['f1']:.1f}%  FPR={gru_atk_m['fpr_pct']:.2f}%")
        print(f"  Per-label recall (GRU):")
        for k, v in gru_per_label.items():
            print(f"    {k:20s}: {v['recall_pct']:5.1f}%  ({v['detected']}/{v['total']})")

        print(f"\n  [LSTM-UE] Confusion Matrix:")
        print(f"    TP={lstm_atk_m['TP']}  FP={lstm_atk_m['FP']}  TN={lstm_atk_m['TN']}  FN={lstm_atk_m['FN']}")
        print(f"    Precision={lstm_atk_m['precision']:.1f}%  Recall={lstm_atk_m['recall']:.1f}%  F1={lstm_atk_m['f1']:.1f}%  FPR={lstm_atk_m['fpr_pct']:.2f}%")
        print(f"  Per-label recall (LSTM):")
        for k, v in lstm_per_label.items():
            print(f"    {k:20s}: {v['recall_pct']:5.1f}%  ({v['detected']}/{v['total']})")

        results["attack_partial"] = {
            "dataset": args.attack,
            "n_rows":  len(atk_rows),
            "note": (
                "PARTIAL evaluation — only 8/15 per-UE features available from cell-level dataset. "
                "thp_dl_kbps, thp_ul_kbps, thp_total_kbps, thp_ul_delta, thp_dl_delta, "
                "ul_efficiency, traffic_direction all set to 0. "
                "Recall values are underestimates of true detection capability."
            ),
            "GRU_UE_v1":  {"threshold": gru_thresh,  **gru_atk_m, "per_label": gru_per_label},
            "LSTM_UE_v1": {"threshold": lstm_thresh, **lstm_atk_m, "per_label": lstm_per_label},
        }

    # ── Save ─────────────────────────────────────────────────────────────────
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n[*] Results saved to {args.output}")


if __name__ == "__main__":
    main()
