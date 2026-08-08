# src/detection/gru_autoencoder.py
import os
import numpy as np
import torch
import torch.nn as nn
from typing import List, Tuple

from src.detection.lstm_autoencoder import TemporalAttention


class GRUEncoder(nn.Module):
    def __init__(self, input_size: int, hidden_sizes: List[int], latent_dim: int,
                 bidirectional: bool = True):
        super().__init__()
        if len(hidden_sizes) < 2:
            raise ValueError(f"GRUEncoder requires at least 2 hidden sizes, got {hidden_sizes}")
        D = 2 if bidirectional else 1
        self.bidirectional = bidirectional
        self.gru1 = nn.GRU(input_size=input_size,
                           hidden_size=hidden_sizes[0],
                           batch_first=True,
                           bidirectional=bidirectional)
        self.gru2 = nn.GRU(input_size=hidden_sizes[0] * D,
                           hidden_size=hidden_sizes[1],
                           batch_first=True,
                           bidirectional=bidirectional)
        self.attention = TemporalAttention(hidden_sizes[1] * D)
        self.fc = nn.Linear(hidden_sizes[1] * D, latent_dim)
        self.dropout = nn.Dropout(p=0.1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.gru1(x)
        out = self.dropout(out)
        out, _ = self.gru2(out)
        out = self.dropout(out)
        context = self.attention(out)
        return self.fc(context)


class GRUDecoder(nn.Module):
    def __init__(self, latent_dim: int, hidden_sizes: List[int],
                 output_size: int, seq_len: int):
        super().__init__()
        if len(hidden_sizes) < 2:
            raise ValueError(f"GRUDecoder requires at least 2 hidden sizes, got {hidden_sizes}")
        self.seq_len = seq_len
        self.fc = nn.Linear(latent_dim, hidden_sizes[0])
        self.gru1 = nn.GRU(input_size=hidden_sizes[0],
                           hidden_size=hidden_sizes[1],
                           batch_first=True)
        self.gru2 = nn.GRU(input_size=hidden_sizes[1],
                           hidden_size=output_size,
                           batch_first=True)
        self.dropout = nn.Dropout(p=0.1)

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        hidden = self.fc(z).unsqueeze(1).repeat(1, self.seq_len, 1)
        out, _ = self.gru1(hidden)
        out = self.dropout(out)
        out, _ = self.gru2(out)
        return out


class GRUAutoencoder(nn.Module):
    def __init__(self, config: dict):
        super().__init__()
        mc = config.get('gru_model', {})
        dc = config.get('detection', {})
        self.input_features = mc.get('input_features', 16)
        self.encoder_hidden = mc.get('encoder_hidden', [64, 32])
        self.decoder_hidden = mc.get('decoder_hidden', [32, 64])
        self.latent_dim     = mc.get('latent_dim', 32)
        self.bidirectional  = mc.get('bidirectional', True)
        self.seq_len        = dc.get('sequence_length', 10)

        self.encoder = GRUEncoder(
            input_size=self.input_features,
            hidden_sizes=self.encoder_hidden,
            latent_dim=self.latent_dim,
            bidirectional=self.bidirectional,
        )
        self.decoder = GRUDecoder(
            latent_dim=self.latent_dim,
            hidden_sizes=self.decoder_hidden,
            output_size=self.input_features,
            seq_len=self.seq_len,
        )
        self.anomaly_threshold    = None
        self.reconstruction_mean  = None
        self.reconstruction_std   = None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))

    def compute_reconstruction_error(self, x: torch.Tensor) -> torch.Tensor:
        with torch.no_grad():
            return torch.mean((x - self.forward(x)) ** 2, dim=(1, 2))

    def fit_threshold(self, normal_errors: np.ndarray, percentile: float = 99.0):
        self.reconstruction_mean = float(np.mean(normal_errors))
        self.reconstruction_std  = float(np.std(normal_errors))
        self.anomaly_threshold   = float(np.percentile(normal_errors, percentile))

    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        torch.save({
            'model_state_dict':   self.state_dict(),
            'anomaly_threshold':  self.anomaly_threshold,
            'reconstruction_mean': self.reconstruction_mean,
            'reconstruction_std':  self.reconstruction_std,
            'config': {
                'input_features': self.input_features,
                'encoder_hidden': self.encoder_hidden,
                'decoder_hidden': self.decoder_hidden,
                'latent_dim':     self.latent_dim,
                'seq_len':        self.seq_len,
                'bidirectional':  self.bidirectional,
            },
        }, path)
        print(f"[GRU-AE] Model saved to {path}")

    @classmethod
    def load(cls, path: str, config: dict) -> 'GRUAutoencoder':
        if not os.path.exists(path):
            raise FileNotFoundError(f"[GRU-AE] Model file not found: {path}")
        state = torch.load(path, map_location='cpu', weights_only=False)
        saved_cfg = state.get('config', {})
        merged = dict(config)
        merged['gru_model'] = {
            'input_features': saved_cfg.get('input_features', config.get('gru_model', {}).get('input_features', 16)),
            'encoder_hidden': saved_cfg.get('encoder_hidden', [64, 32]),
            'decoder_hidden': saved_cfg.get('decoder_hidden', [32, 64]),
            'latent_dim':     saved_cfg.get('latent_dim', 32),
            'bidirectional':  saved_cfg.get('bidirectional', True),
        }
        merged['detection'] = {**config.get('detection', {}), 'sequence_length': saved_cfg.get('seq_len', 10)}
        model = cls(merged)
        model.load_state_dict(state['model_state_dict'])
        model.anomaly_threshold   = state.get('anomaly_threshold')
        model.reconstruction_mean = state.get('reconstruction_mean')
        model.reconstruction_std  = state.get('reconstruction_std')
        print(f"[GRU-AE] Model loaded from {path}  seq_len={model.seq_len}")
        return model


class GRUEnsemble:
    """
    Ensemble dua GRUAutoencoder dengan seq_len berbeda.
    model_a: seq_len pendek (misal 10) — spesialis Flood/Burst
    model_b: seq_len panjang (misal 30) — spesialis RRC Storm

    Input selalu window sepanjang model_b.seq_len.
    GRU-A pakai slice [-model_a.seq_len:] dari window yang sama.
    """

    def __init__(self, model_a: GRUAutoencoder, model_b: GRUAutoencoder):
        self.model_a = model_a
        self.model_b = model_b

    def score(self, window: torch.Tensor) -> Tuple[float, float, float]:
        """
        window shape: (1, model_b.seq_len, n_features)
        Returns: (combined_score, score_a, score_b)
        combined = max(score_a, score_b)
        """
        slice_a = window[:, -self.model_a.seq_len:, :]
        score_a = float(self.model_a.compute_reconstruction_error(slice_a)[0])
        score_b = float(self.model_b.compute_reconstruction_error(window)[0])
        return max(score_a, score_b), score_a, score_b

    def is_anomaly(self, window: torch.Tensor) -> Tuple[bool, float, float, float]:
        """
        Returns: (is_anomaly, combined_score, score_a, score_b)
        is_anomaly = True jika salah satu model melampaui threshold-nya.
        """
        if self.model_a.anomaly_threshold is None:
            raise RuntimeError("GRU-A anomaly_threshold not set — call fit_threshold() before is_anomaly()")
        if self.model_b.anomaly_threshold is None:
            raise RuntimeError("GRU-B anomaly_threshold not set — call fit_threshold() before is_anomaly()")
        thr_a = self.model_a.anomaly_threshold
        thr_b = self.model_b.anomaly_threshold
        combined, score_a, score_b = self.score(window)
        flagged = (score_a > thr_a) or (score_b > thr_b)
        return flagged, combined, score_a, score_b
