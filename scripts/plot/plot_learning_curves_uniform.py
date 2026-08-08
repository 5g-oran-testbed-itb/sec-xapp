#!/usr/bin/env python3
"""
plot_learning_curves_uniform.py
Learning curves for the uniform-MSE loss variant of the AE ablation:
1. LSTM-AE uniform (models/ablation_loss/lstm_ue_lossuniform_losses.json)
2. GRU-AE uniform  (models/ablation_loss/gru_ue_lossuniform_losses.json)

Same rendering as plot_learning_curves_v5.py, different source models and
output folder so the deployed v5/v6 figures stay untouched.
"""

import os

from plot_learning_curves_v5 import plot_curve

OUT_DIR = 'eval_figures/loss_uniform'


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    plot_curve(
        'models/ablation_loss/lstm_ue_lossuniform_losses.json',
        'LSTM-AE Learning Curve - per UE',
        os.path.join(OUT_DIR, 'eval_learning_curve_lstm.png'),
        'LSTM-AE',
        train_color='#1F77B4',  # Deep Blue
        val_color='#FF7F0E',    # Amber Orange
        marker_color='#D62728'  # Vibrant Red
    )

    plot_curve(
        'models/ablation_loss/gru_ue_lossuniform_losses.json',
        'GRU-AE Learning Curve - per UE',
        os.path.join(OUT_DIR, 'eval_learning_curve_gru.png'),
        'GRU-AE',
        train_color='#2CA02C',  # Emerald Green
        val_color='#9467BD',    # Royal Purple
        marker_color='#D62728'  # Vibrant Red
    )


if __name__ == '__main__':
    main()
