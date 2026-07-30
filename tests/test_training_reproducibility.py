import subprocess

import numpy as np
import torch

from src.detection.training_utils import set_reproducible_seed


def test_set_reproducible_seed_repeats_numpy_and_torch_sequences():
    set_reproducible_seed(31415)
    numpy_first = np.random.permutation(12)
    torch_first = torch.rand(8)

    set_reproducible_seed(31415)
    numpy_second = np.random.permutation(12)
    torch_second = torch.rand(8)

    np.testing.assert_array_equal(numpy_first, numpy_second)
    torch.testing.assert_close(torch_first, torch_second)


def test_set_reproducible_seed_changes_sequences_for_different_seed():
    set_reproducible_seed(10)
    first = torch.rand(8)
    set_reproducible_seed(11)
    second = torch.rand(8)

    assert not torch.equal(first, second)


def test_per_ue_training_clis_expose_seed_option():
    for script in ("train_gru_ue.py", "train_lstm_ue.py"):
        completed = subprocess.run(
            ["venv/bin/python3", script, "--help"],
            check=True,
            capture_output=True,
            text=True,
        )

        assert "--seed" in completed.stdout
        assert "default: 42" in completed.stdout
