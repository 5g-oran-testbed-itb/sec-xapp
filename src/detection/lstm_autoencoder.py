"""
LSTM-Autoencoder Model untuk Deteksi Anomali
Bagian dari Security xApp Prototype
"""

import torch
import torch.nn as nn
import numpy as np
from typing import List, Tuple, Optional
import os


class TemporalAttention(nn.Module):
    """Self-attention over timesteps — memberi bobot lebih ke timestep yang anomalous.
    Input: (batch, seq_len, hidden_dim)  Output: (batch, hidden_dim) weighted context."""

    def __init__(self, hidden_dim: int):
        super().__init__()
        self.attn = nn.Linear(hidden_dim, 1)

    def forward(self, lstm_out: torch.Tensor) -> torch.Tensor:
        scores  = self.attn(lstm_out).squeeze(-1)          # (batch, seq_len)
        weights = torch.softmax(scores, dim=1).unsqueeze(-1)  # (batch, seq_len, 1)
        context = (lstm_out * weights).sum(dim=1)          # (batch, hidden_dim)
        return context


class LSTMEncoder(nn.Module):
    """BiLSTM Encoder - Kompresi sequence ke latent space dengan temporal attention.
    Bidirectional: tiap layer output hidden_size * 2 (forward + backward)."""

    def __init__(self, input_size: int, hidden_sizes: List[int], latent_dim: int,
                 bidirectional: bool = True):
        super().__init__()
        self.input_size = input_size
        self.hidden_sizes = hidden_sizes
        self.latent_dim = latent_dim
        self.bidirectional = bidirectional
        D = 2 if bidirectional else 1

        self.lstm1 = nn.LSTM(input_size=input_size,
                             hidden_size=hidden_sizes[0],
                             batch_first=True,
                             bidirectional=bidirectional)
        self.lstm2 = nn.LSTM(input_size=hidden_sizes[0] * D,
                             hidden_size=hidden_sizes[1],
                             batch_first=True,
                             bidirectional=bidirectional)

        self.attention = TemporalAttention(hidden_sizes[1] * D)
        self.fc = nn.Linear(hidden_sizes[1] * D, latent_dim)
        self.dropout = nn.Dropout(p=0.2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm1(x)
        out = self.dropout(out)
        out, _ = self.lstm2(out)               # (batch, seq_len, hidden2*D)
        out = self.dropout(out)
        context = self.attention(out)          # (batch, hidden2*D)
        latent  = self.fc(context)             # (batch, latent_dim)
        return latent


class LSTMDecoder(nn.Module):
    """LSTM Decoder - Rekonstruksi sequence dari latent space"""
    
    def __init__(self, latent_dim: int, hidden_sizes: List[int], output_size: int, seq_len: int):
        super().__init__()
        self.latent_dim = latent_dim
        self.hidden_sizes = hidden_sizes
        self.output_size = output_size
        self.seq_len = seq_len
        
        # Projection dari latent space
        self.fc = nn.Linear(latent_dim, hidden_sizes[0])
        
        # LSTM layers
        self.lstm1 = nn.LSTM(
            input_size=hidden_sizes[0],
            hidden_size=hidden_sizes[1],
            batch_first=True
        )
        self.lstm2 = nn.LSTM(
            input_size=hidden_sizes[1],
            hidden_size=output_size,
            batch_first=True
        )
        self.dropout = nn.Dropout(p=0.2)
        
    def forward(self, z: torch.Tensor) -> torch.Tensor:
        # z shape: (batch, latent_dim)
        batch_size = z.size(0)
        
        # Expand latent vector ke sequence
        hidden = self.fc(z)
        hidden = hidden.unsqueeze(1).repeat(1, self.seq_len, 1)
        
        out, _ = self.lstm1(hidden)
        out = self.dropout(out)
        out, _ = self.lstm2(out)

        return out


# ─── VAE ──────────────────────────────────────────────────────────────────────

class LSTMVariationalEncoder(nn.Module):
    """VAE Encoder: output μ dan log σ² — memungkinkan KL divergence sebagai sinyal anomali."""

    def __init__(self, input_size: int, hidden_sizes: List[int], latent_dim: int):
        super().__init__()
        self.lstm1   = nn.LSTM(input_size,        hidden_sizes[0], batch_first=True)
        self.lstm2   = nn.LSTM(hidden_sizes[0],   hidden_sizes[1], batch_first=True)
        self.attention = TemporalAttention(hidden_sizes[1])
        self.fc_mu     = nn.Linear(hidden_sizes[1], latent_dim)
        self.fc_logvar = nn.Linear(hidden_sizes[1], latent_dim)

    def forward(self, x: torch.Tensor):
        out, _ = self.lstm1(x)
        out, _ = self.lstm2(out)
        context = self.attention(out)
        return self.fc_mu(context), self.fc_logvar(context)


class LSTMVariationalAutoencoder(nn.Module):
    """
    LSTM Variational Autoencoder untuk deteksi anomali.

    Anomaly score = MSE(recon, x) + β * KL(q(z|x) || N(0,1))

    KL term mendeteksi representasi latent yang "aneh" — bahkan ketika
    rekonstruksi masih cukup baik (kasus DL Flood, Burst OFF phase).
    """

    def __init__(self, config: dict):
        super().__init__()
        mc = config.get('lstm_model', {})
        dc = config.get('detection', {})

        self.input_features = mc.get('input_features', 18)
        self.encoder_hidden = mc.get('encoder_hidden', [64, 32])
        self.decoder_hidden = mc.get('decoder_hidden', [32, 64])
        self.latent_dim     = mc.get('latent_dim', 32)
        self.seq_len        = dc.get('sequence_length', 10)

        self.encoder = LSTMVariationalEncoder(
            input_size=self.input_features,
            hidden_sizes=self.encoder_hidden,
            latent_dim=self.latent_dim,
        )
        self.decoder = LSTMDecoder(
            latent_dim=self.latent_dim,
            hidden_sizes=self.decoder_hidden,
            output_size=self.input_features,
            seq_len=self.seq_len,
        )
        self.anomaly_threshold = None

    def reparameterize(self, mu: torch.Tensor, logvar: torch.Tensor) -> torch.Tensor:
        if self.training:
            return mu + torch.exp(0.5 * logvar) * torch.randn_like(mu)
        return mu  # inference: gunakan μ langsung (deterministic)

    def forward(self, x: torch.Tensor):
        mu, logvar   = self.encoder(x)
        z            = self.reparameterize(mu, logvar)
        reconstructed = self.decoder(z)
        return reconstructed, mu, logvar

    def elbo_loss(self, x: torch.Tensor, recon: torch.Tensor,
                  mu: torch.Tensor, logvar: torch.Tensor,
                  beta: float = 0.01) -> tuple:
        recon_loss = torch.mean((x - recon) ** 2)
        kl_loss    = -0.5 * torch.mean(1 + logvar - mu.pow(2) - logvar.exp())
        return recon_loss + beta * kl_loss, recon_loss.detach().item(), kl_loss.detach().item()

    def compute_anomaly_scores(self, x: torch.Tensor, beta_inf: float = 1.0) -> torch.Tensor:
        """Score per sample = MSE_recon + β_inf * KL. Lebih tinggi = lebih anomalous."""
        with torch.no_grad():
            mu, logvar    = self.encoder(x)
            recon         = self.decoder(mu)  # pakai μ, tanpa sampling
            recon_err     = torch.mean((x - recon) ** 2, dim=(1, 2))       # (batch,)
            kl_per_sample = -0.5 * torch.mean(1 + logvar - mu.pow(2) - logvar.exp(), dim=1)
            return recon_err + beta_inf * kl_per_sample

    def fit_threshold(self, normal_scores: np.ndarray, percentile: float = 99.0):
        self.anomaly_threshold = float(np.percentile(normal_scores, percentile))

    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        torch.save({
            'model_state_dict':  self.state_dict(),
            'anomaly_threshold': self.anomaly_threshold,
            'config': {
                'input_features': self.input_features,
                'encoder_hidden': self.encoder_hidden,
                'decoder_hidden': self.decoder_hidden,
                'latent_dim':     self.latent_dim,
                'seq_len':        self.seq_len,
            }
        }, path)
        print(f"[LSTM-VAE] Model saved to {path}")

    @classmethod
    def load(cls, path: str, config: dict) -> 'LSTMVariationalAutoencoder':
        state = torch.load(path, map_location='cpu', weights_only=False)
        model = cls(config)
        model.load_state_dict(state['model_state_dict'])
        model.anomaly_threshold = state.get('anomaly_threshold')
        print(f"[LSTM-VAE] Model loaded from {path}")
        return model


class LSTMAutoencoder(nn.Module):
    """
    LSTM-Autoencoder untuk deteksi anomali
    
    Arsitektur:
    - Encoder: LSTM(8, 64) → LSTM(64, 32) → FC(32)
    - Decoder: FC(32) → LSTM(32, 64) → LSTM(64, 8)
    """
    
    def __init__(self, config: dict):
        super().__init__()
        
        model_config = config.get('lstm_model', {})
        detection_config = config.get('detection', {})
        
        self.input_features = model_config.get('input_features', 12)
        self.encoder_hidden = model_config.get('encoder_hidden', [64, 32])
        self.decoder_hidden = model_config.get('decoder_hidden', [32, 64])
        self.latent_dim = model_config.get('latent_dim', 32)
        self.bidirectional = model_config.get('bidirectional', True)
        self.seq_len = detection_config.get('sequence_length', 30)

        self.encoder = LSTMEncoder(
            input_size=self.input_features,
            hidden_sizes=self.encoder_hidden,
            latent_dim=self.latent_dim,
            bidirectional=self.bidirectional,
        )
        
        self.decoder = LSTMDecoder(
            latent_dim=self.latent_dim,
            hidden_sizes=self.decoder_hidden,
            output_size=self.input_features,
            seq_len=self.seq_len
        )
        
        # Normalization parameters
        self.data_mean = None
        self.data_std = None

        # Threshold untuk anomaly detection
        self.anomaly_threshold = None
        self.reconstruction_mean = None
        self.reconstruction_std = None
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        latent = self.encoder(x)
        reconstructed = self.decoder(latent)
        return reconstructed
    
    def compute_reconstruction_error(self, x: torch.Tensor) -> torch.Tensor:
        """Compute reconstruction error (MSE) per sample"""
        with torch.no_grad():
            reconstructed = self.forward(x)
            # MSE per sample
            error = torch.mean((x - reconstructed) ** 2, dim=(1, 2))
        return error
    
    def compute_stepwise_error(self, x: torch.Tensor) -> torch.Tensor:
        """Compute reconstruction error (MSE) per timestep"""
        with torch.no_grad():
            reconstructed = self.forward(x)
            # MSE per timestep per sample: shape (batch, seq_len)
            error = torch.mean((x - reconstructed) ** 2, dim=2)
        return error

    def fit_threshold(self, normal_errors: np.ndarray, percentile: float = 99.5):
        """
        Fit threshold berdasarkan reconstruction error dari data normal.
        Menggunakan percentile-based threshold (distribution-free).

        Args:
            normal_errors: Array of reconstruction errors dari data normal
            percentile: Percentile untuk threshold (default 99.5 → FPR ≈ 0.5%)
        """
        self.reconstruction_mean = float(np.mean(normal_errors))
        self.reconstruction_std = float(np.std(normal_errors))
        self.anomaly_threshold = float(np.percentile(normal_errors, percentile))

    def fit_scaler(self, data: np.ndarray):
        """Fit normalization parameters"""
        self.data_mean = np.mean(data, axis=0)
        self.data_std = np.std(data, axis=0) + 1e-8

    def transform(self, data: np.ndarray) -> np.ndarray:
        """Apply normalization"""
        if self.data_mean is None or self.data_std is None:
            return data
        return (data - self.data_mean) / self.data_std
    
    def is_anomaly(self, reconstruction_error: float) -> Tuple[bool, float]:
        """
        Check apakah sample adalah anomali
        
        Returns:
            (is_anomaly, anomaly_score): Boolean dan score (higher = more anomalous)
        """
        if self.anomaly_threshold is None:
            raise ValueError("Threshold not fitted. Call fit_threshold() first.")
        
        anomaly_score = (reconstruction_error - self.reconstruction_mean) / self.reconstruction_std
        is_anomaly = reconstruction_error > self.anomaly_threshold
        
        return is_anomaly, anomaly_score
    
    def save(self, path: str):
        """Save model dan parameters"""
        state = {
            'model_state_dict': self.state_dict(),
            'anomaly_threshold': self.anomaly_threshold,
            'reconstruction_mean': self.reconstruction_mean,
            'reconstruction_std': self.reconstruction_std,
            'data_mean': self.data_mean,
            'data_std': self.data_std,
            'config': {
                'input_features': self.input_features,
                'encoder_hidden': self.encoder_hidden,
                'decoder_hidden': self.decoder_hidden,
                'latent_dim': self.latent_dim,
                'seq_len': self.seq_len,
                'bidirectional': self.bidirectional,
            }
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save(state, path)
        print(f"[LSTM-AE] Model saved to {path}")
    
    @classmethod
    def load(cls, path: str, config: dict) -> 'LSTMAutoencoder':
        """Load model dari file"""
        state = torch.load(path, map_location='cpu', weights_only=False)
        
        model = cls(config)
        model.load_state_dict(state['model_state_dict'])
        model.anomaly_threshold = state.get('anomaly_threshold')
        model.reconstruction_mean = state.get('reconstruction_mean')
        model.reconstruction_std = state.get('reconstruction_std')
        model.data_mean = state.get('data_mean')
        model.data_std = state.get('data_std')
        
        print(f"[LSTM-AE] Model loaded from {path}")
        return model


class ModelTrainer:
    """Trainer untuk LSTM-Autoencoder"""
    
    def __init__(self, model: LSTMAutoencoder, config: dict):
        self.model = model
        self.config = config
        
        model_config = config.get('lstm_model', {})
        self.learning_rate = model_config.get('learning_rate', 0.001)
        self.epochs = model_config.get('epochs', 50)
        self.batch_size = model_config.get('batch_size', 32)
        
        self.optimizer = torch.optim.Adam(model.parameters(), lr=self.learning_rate)
        self.criterion = nn.MSELoss()
        
    def prepare_sequences(self, data: np.ndarray) -> np.ndarray:
        """
        Prepare data ke sequences untuk LSTM
        
        Args:
            data: (num_samples, num_features)
        Returns:
            sequences: (num_sequences, seq_len, num_features)
        """
        seq_len = self.model.seq_len
        sequences = []
        
        for i in range(len(data) - seq_len + 1):
            sequences.append(data[i:i + seq_len])
        
        return np.array(sequences)
    
    def train(self, train_data: np.ndarray) -> List[float]:
        """
        Train model
        
        Args:
            train_data: (num_samples, num_features) - Data normal saja!
            
        Returns:
            List of training losses
        """
        sequences = self.prepare_sequences(train_data)
        dataset = torch.FloatTensor(sequences)
        
        dataloader = torch.utils.data.DataLoader(
            dataset, 
            batch_size=self.batch_size, 
            shuffle=True
        )
        
        losses = []
        self.model.train()
        
        print(f"[Trainer] Starting training for {self.epochs} epochs...")
        print(f"[Trainer] Data shape: {sequences.shape}")
        
        for epoch in range(self.epochs):
            epoch_loss = 0.0
            num_batches = 0
            
            for batch in dataloader:
                self.optimizer.zero_grad()
                
                reconstructed = self.model(batch)
                loss = self.criterion(reconstructed, batch)
                
                loss.backward()
                self.optimizer.step()
                
                epoch_loss += loss.item()
                num_batches += 1
            
            avg_loss = epoch_loss / num_batches
            losses.append(avg_loss)
            
            if (epoch + 1) % 10 == 0:
                print(f"[Trainer] Epoch {epoch + 1}/{self.epochs}, Loss: {avg_loss:.6f}")
        
        # Fit threshold setelah training
        self.model.eval()
        with torch.no_grad():
            errors = self.model.compute_reconstruction_error(dataset).numpy()
        
        percentile = self.config.get('detection', {}).get('anomaly_threshold_percentile', 99.5)
        self.model.fit_threshold(errors, percentile)
        
        return losses
