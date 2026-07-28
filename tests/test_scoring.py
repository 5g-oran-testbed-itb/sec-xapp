# tests/test_scoring.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
from src.detection.scoring import weighted_score, benign_calibrated_weights


def test_weighted_score_uniform_equals_feature_mean():
    # residuals: 2 windows, 3 features
    res = np.array([[1.0, 2.0, 3.0], [4.0, 4.0, 4.0]], dtype=np.float32)
    w = np.ones(3, dtype=np.float32)
    out = weighted_score(res, w)
    assert out == pytest.approx([2.0, 4.0])  # plain mean over features


def test_weighted_score_respects_weights():
    res = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    w = np.array([3.0, 1.0, 1.0], dtype=np.float32)
    # (1*3 + 0 + 0) / (3+1+1) = 3/5
    assert weighted_score(res, w)[0] == pytest.approx(0.6)


def test_benign_weights_penalize_large_residual_features():
    # feature 0 has small benign residual (stable), feature 1 large (noisy)
    res = np.array([[0.01, 1.0], [0.01, 1.0], [0.02, 1.2]], dtype=np.float32)
    w = benign_calibrated_weights(res)
    assert w[0] > w[1]  # stable feature gets more weight


def test_benign_weights_are_capped():
    # feature 2 has ~zero benign residual → raw weight explodes; cap must bound it
    res = np.array([[1.0, 1.0, 1e-12], [1.0, 1.0, 1e-12], [1.2, 0.9, 1e-12]],
                   dtype=np.float32)
    w = benign_calibrated_weights(res, cap_mult=10.0)
    raw_median = np.median(1.0 / (np.median(res, axis=0)
                                  + np.median(np.abs(res - np.median(res, axis=0)), axis=0)
                                  + 1e-6))
    assert w.max() <= 10.0 * raw_median + 1e-3
