"""Pure anomaly-scoring math for per-UE autoencoder detection.

Isolated from evaluate_per_ue_v2.py so the weight/score logic is unit-testable
without loading a trained model. See
docs/superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md
"""
import numpy as np


def weighted_score(residuals: np.ndarray, weight_vec: np.ndarray) -> np.ndarray:
    """Weighted mean of per-feature residuals.

    residuals: (N, F) per-feature squared reconstruction error (mean over time).
    weight_vec: (F,) non-negative weights.
    Returns: (N,) float32 anomaly score = sum(w*e) / sum(w).
    """
    w = np.asarray(weight_vec, dtype=np.float64)
    res = np.asarray(residuals, dtype=np.float64)
    return ((res * w).sum(axis=1) / w.sum()).astype(np.float32)


def benign_calibrated_weights(residuals: np.ndarray,
                              eps: float = 1e-6,
                              cap_mult: float = 10.0) -> np.ndarray:
    """Weights from benign residual scale only (no attack labels).

    Higher weight for features whose benign residual is small AND stable, via
    inverse (median + MAD). Capped at cap_mult * median(raw weight) so a
    near-zero-residual feature cannot dominate the score.

    residuals: (N, F) per-feature squared residuals on BENIGN windows.
    Returns: (F,) float32 weight vector.
    """
    res = np.asarray(residuals, dtype=np.float64)
    med = np.median(res, axis=0)                       # (F,)
    mad = np.median(np.abs(res - med), axis=0)         # (F,)
    raw = 1.0 / (med + mad + eps)                       # (F,)
    cap = cap_mult * np.median(raw)
    return np.minimum(raw, cap).astype(np.float32)


def make_weight_vec(scoring: str,
                    feature_names: list,
                    attack_weight_dict: dict,
                    benign_residuals=None) -> np.ndarray:
    """Return the (F,) weight vector for a scoring scheme.

    scoring: "uniform" | "attack" | "benign".
      - uniform: all ones.
      - attack:  Scheme A weights from attack_weight_dict (attack-informed).
      - benign:  benign_calibrated_weights(benign_residuals) (attack-free).
    """
    n = len(feature_names)
    if scoring == "uniform":
        return np.ones(n, dtype=np.float32)
    if scoring == "attack":
        return np.array([attack_weight_dict.get(f, 1.0) for f in feature_names],
                        dtype=np.float32)
    if scoring == "benign":
        if benign_residuals is None or len(benign_residuals) == 0:
            raise ValueError("benign scoring requires non-empty benign_residuals")
        return benign_calibrated_weights(benign_residuals)
    raise ValueError(f"unknown scoring mode: {scoring!r}")


def load_loss_weights(mode: str, feature_names: list,
                      attack_weight_dict: dict, json_path: str = None) -> np.ndarray:
    """Training-loss weight vector for the ablation.

    mode: "uniform" (ones) | "schemea" (attack_weight_dict) | "benign" (from json_path).
    Returns (F,) float32 aligned to feature_names.
    """
    n = len(feature_names)
    if mode == "uniform":
        return np.ones(n, dtype=np.float32)
    if mode == "schemea":
        return np.array([attack_weight_dict.get(f, 1.0) for f in feature_names],
                        dtype=np.float32)
    if mode == "benign":
        if not json_path:
            raise ValueError("benign loss-weights requires json_path")
        import json
        with open(json_path) as f:
            d = json.load(f)
        return np.array([float(d[f]) for f in feature_names], dtype=np.float32)
    raise ValueError(f"unknown loss-weights mode: {mode!r}")


def per_feature_residuals_from_windows(model, wins, batch: int = 256) -> np.ndarray:
    """Per-feature squared reconstruction error (mean over time) for each window.

    model: torch autoencoder returning (B, seq, F).
    wins: (N, seq, F) float32 scaled windows (from build_windows).
    Returns: (N, F) float32 residuals. Empty (0, F) if no windows.

    NOTE: torch is imported lazily so the pure functions above stay import-cheap.
    """
    import torch
    if len(wins) == 0:
        f = wins.shape[-1] if wins.ndim == 3 else 0
        return np.zeros((0, f), dtype=np.float32)
    model.eval()
    parts = []
    for i in range(0, len(wins), batch):
        chunk = torch.tensor(wins[i:i + batch])
        with torch.no_grad():
            recon = model(chunk)
            fe = ((recon - chunk) ** 2).mean(dim=1)   # (B, F)
        parts.append(fe.numpy())
    return np.concatenate(parts).astype(np.float32)
