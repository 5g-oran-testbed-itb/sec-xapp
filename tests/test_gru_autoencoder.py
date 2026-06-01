"""Unit tests for GRUAutoencoder and GRUEnsemble."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import torch
import numpy as np
import pytest
from src.detection.gru_autoencoder import GRUAutoencoder, GRUEnsemble


def _mini_config(seq_len=10):
    return {
        'gru_model': {
            'input_features': 4,
            'encoder_hidden': [8, 4],
            'decoder_hidden': [4, 8],
            'latent_dim': 4,
            'bidirectional': True,
        },
        'detection': {'sequence_length': seq_len},
    }


def test_forward_output_shape():
    model = GRUAutoencoder(_mini_config(seq_len=10))
    x = torch.randn(3, 10, 4)
    out = model(x)
    assert out.shape == (3, 10, 4), f"expected (3,10,4), got {out.shape}"


def test_reconstruction_error_shape():
    model = GRUAutoencoder(_mini_config(seq_len=10))
    x = torch.randn(5, 10, 4)
    err = model.compute_reconstruction_error(x)
    assert err.shape == (5,), f"expected (5,), got {err.shape}"
    assert (err >= 0).all()


def test_fit_threshold_sets_values():
    model = GRUAutoencoder(_mini_config())
    errors = np.array([0.01, 0.02, 0.03, 0.04, 0.05])
    model.fit_threshold(errors, percentile=80.0)
    assert model.anomaly_threshold == pytest.approx(np.percentile(errors, 80.0))
    assert model.reconstruction_mean == pytest.approx(np.mean(errors))


def test_save_load_roundtrip(tmp_path):
    model = GRUAutoencoder(_mini_config())
    errors = np.random.rand(100).astype(np.float32) * 0.1
    model.fit_threshold(errors, percentile=99.0)
    path = str(tmp_path / "gru_test.pt")
    model.save(path)
    model2 = GRUAutoencoder.load(path, _mini_config())
    assert model2.anomaly_threshold == pytest.approx(model.anomaly_threshold)
    x = torch.randn(1, 10, 4)
    model.eval()
    model2.eval()
    assert torch.allclose(model(x), model2(x), atol=1e-5), "Loaded model weights differ from original"


def test_ensemble_score_uses_correct_slicing():
    cfg_a = _mini_config(seq_len=4)
    cfg_b = _mini_config(seq_len=8)
    model_a = GRUAutoencoder(cfg_a)
    model_b = GRUAutoencoder(cfg_b)
    model_a.fit_threshold(np.ones(10) * 0.1, percentile=99.0)
    model_b.fit_threshold(np.ones(10) * 0.1, percentile=99.0)
    ensemble = GRUEnsemble(model_a, model_b)
    window_8 = torch.randn(1, 8, 4)
    combined, score_a, score_b = ensemble.score(window_8)
    assert isinstance(combined, float)
    assert combined == max(score_a, score_b)


def test_ensemble_is_anomaly_returns_bool():
    cfg_a = _mini_config(seq_len=4)
    cfg_b = _mini_config(seq_len=8)
    model_a = GRUAutoencoder(cfg_a)
    model_b = GRUAutoencoder(cfg_b)
    model_a.fit_threshold(np.ones(10) * 999.0, percentile=99.0)
    model_b.fit_threshold(np.ones(10) * 999.0, percentile=99.0)
    ensemble = GRUEnsemble(model_a, model_b)
    window_8 = torch.randn(1, 8, 4)
    flagged, _, _, _ = ensemble.is_anomaly(window_8)
    assert flagged is False
