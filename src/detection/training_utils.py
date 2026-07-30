"""Shared helpers for reproducible anomaly-model training."""

import random

import numpy as np
import torch


def set_reproducible_seed(seed: int) -> None:
    """Reset Python, NumPy, and PyTorch RNGs to a shared deterministic seed."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
