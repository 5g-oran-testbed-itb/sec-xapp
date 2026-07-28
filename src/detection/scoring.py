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
