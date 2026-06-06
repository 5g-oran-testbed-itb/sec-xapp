"""
Inline GRU Autoencoder for csv_exporter — no dependency on src/.
Mirrors src/detection/gru_autoencoder.py with TemporalAttention inlined.
"""
import os
import pickle
import numpy as np
import torch
import torch.nn as nn
from typing import List


class TemporalAttention(nn.Module):
    def __init__(self, hidden_dim: int):
        super().__init__()
        self.attn = nn.Linear(hidden_dim, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        scores  = self.attn(x).squeeze(-1)
        weights = torch.softmax(scores, dim=1).unsqueeze(-1)
        return (x * weights).sum(dim=1)


class GRUEncoder(nn.Module):
    def __init__(self, input_size: int, hidden_sizes: List[int],
                 latent_dim: int, bidirectional: bool = True):
        super().__init__()
        D = 2 if bidirectional else 1
        self.gru1 = nn.GRU(input_size, hidden_sizes[0],
                            batch_first=True, bidirectional=bidirectional)
        self.gru2 = nn.GRU(hidden_sizes[0] * D, hidden_sizes[1],
                            batch_first=True, bidirectional=bidirectional)
        self.attention = TemporalAttention(hidden_sizes[1] * D)
        self.fc = nn.Linear(hidden_sizes[1] * D, latent_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.gru1(x)
        out, _ = self.gru2(out)
        return self.fc(self.attention(out))


class GRUDecoder(nn.Module):
    def __init__(self, latent_dim: int, hidden_sizes: List[int],
                 output_size: int, seq_len: int):
        super().__init__()
        self.seq_len = seq_len
        self.fc   = nn.Linear(latent_dim, hidden_sizes[0])
        self.gru1 = nn.GRU(hidden_sizes[0], hidden_sizes[1], batch_first=True)
        self.gru2 = nn.GRU(hidden_sizes[1], output_size,    batch_first=True)

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        h = self.fc(z).unsqueeze(1).repeat(1, self.seq_len, 1)
        out, _ = self.gru1(h)
        out, _ = self.gru2(out)
        return out


class GRUAutoencoder(nn.Module):
    def __init__(self, cfg: dict):
        super().__init__()
        self.input_features = cfg.get("input_features", 16)
        enc_h = cfg.get("encoder_hidden", [64, 32])
        dec_h = cfg.get("decoder_hidden", [32, 64])
        latent = cfg.get("latent_dim", 32)
        bidir  = cfg.get("bidirectional", True)
        self.seq_len = cfg.get("seq_len", 10)
        self.encoder = GRUEncoder(self.input_features, enc_h, latent, bidir)
        self.decoder = GRUDecoder(latent, dec_h, self.input_features, self.seq_len)
        self.anomaly_threshold = None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))

    def reconstruction_error(self, x: torch.Tensor) -> float:
        with torch.no_grad():
            err = torch.mean((x - self.forward(x)) ** 2, dim=(1, 2))
            return float(err[0])

    @classmethod
    def load(cls, path: str) -> "GRUAutoencoder":
        if not os.path.exists(path):
            raise FileNotFoundError(f"GRU model not found: {path}")
        state = torch.load(path, map_location="cpu", weights_only=False)
        cfg   = state.get("config", {})
        model = cls(cfg)
        model.load_state_dict(state["model_state_dict"])
        model.anomaly_threshold = state.get("anomaly_threshold")
        model.eval()
        return model


# ── GRU feature columns (must match scaler_gru.pkl fit order) ────────────────
GRU_FEATURE_COLS = [
    "prb_usage_dl_ratio", "prb_usage_ul_ratio", "cqi", "rach_preamble",
    "air_delay_ul", "prb_direction", "prb_total", "prb_dl_delta", "prb_ul_delta",
    "prb_burst_index", "empty_ind_rate", "prb_dl_roll_mean", "prb_dl_roll_std",
    "prb_ul_roll_std", "prb_ul_roll_max", "prb_ul_roll_max_100",
]


def load_scaler(path: str):
    with open(path, "rb") as f:
        return pickle.load(f)


def extract_gru_features(row: dict) -> np.ndarray:
    """Extract 16 GRU features from parsed CSV row dict. Returns shape (16,)."""
    return np.array([row.get(c, 0.0) for c in GRU_FEATURE_COLS], dtype=np.float32)
