#!/usr/bin/env python3
"""
plot_learning_curves_v6.py
Plots the training and validation learning curves for:
1. LSTM-AE v6 (models/lstm_ue_v6_losses.json)
2. GRU-AE v6 (models/gru_ue_v6_losses.json)
"""

import json
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

def plot_curve(json_path, title, output_path, model_name, train_color='#4E79A7', val_color='#F28E2B', marker_color='#E15759'):
    if not os.path.exists(json_path):
        print(f"[!] File not found: {json_path}")
        return
        
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    train_losses = data['train']
    val_losses = data['val']
    best_epoch = data.get('best_epoch')
    
    epochs = np.arange(1, len(train_losses) + 1)
    
    # Modern style settings
    plt.figure(figsize=(9, 5.5))
    plt.rcParams['font.sans-serif'] = 'DejaVu Sans'
    plt.rcParams['axes.edgecolor'] = '#CCCCCC'
    plt.rcParams['axes.linewidth'] = 0.8
    
    # Plot curves
    plt.plot(epochs, train_losses, color=train_color, linewidth=2.5, label='Training Loss')
    plt.plot(epochs, val_losses, color=val_color, linewidth=2.5, linestyle='--', label='Validation Loss')
    
    # Highlight best epoch
    if best_epoch:
        best_val = val_losses[best_epoch - 1]
        plt.axvline(best_epoch, color=marker_color, linestyle=':', linewidth=1.5, alpha=0.8,
                    label=f'Best Epoch ({best_epoch})')
        plt.plot(best_epoch, best_val, 'o', color=marker_color, markersize=8, markeredgecolor='white', markeredgewidth=1.5)
        
        # Position the annotation intelligently
        xytext_offset = (best_epoch + len(epochs) * 0.05, best_val + (max(val_losses) - min(val_losses)) * 0.1)
        plt.annotate(
            f'Optimal Checkpoint\nEpoch {best_epoch}\nVal Loss: {best_val:.6f}',
            xy=(best_epoch, best_val),
            xytext=xytext_offset,
            fontsize=9.5,
            fontweight='bold',
            color='#333333',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#F5F5F5', edgecolor='#DDDDDD', alpha=0.9),
            arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0.2', color=marker_color, lw=1.5)
        )
                     
    plt.xlabel('Epochs', fontsize=12, fontweight='bold', labelpad=8)
    plt.ylabel('Mean Squared Error (MSE)', fontsize=12, fontweight='bold', labelpad=8)
    plt.title(title, fontsize=14, fontweight='bold', pad=15, color='#222222')
    
    # Grid customization
    plt.grid(True, which='both', linestyle=':', color='#E0E0E0', alpha=0.7)
    
    # Legend customization
    plt.legend(frameon=True, facecolor='white', edgecolor='#E0E0E0', framealpha=0.9, fontsize=10, loc='upper right')
    
    # Final values text box in bottom-left/top-right
    final_t = train_losses[-1]
    final_v = val_losses[-1]
    info_text = f"Final Metrics:\n• Train Loss: {final_t:.6f}\n• Val Loss:   {final_v:.6f}"
    
    plt.text(0.05, 0.08, info_text, transform=plt.gca().transAxes, fontsize=10,
             bbox=dict(boxstyle='round,pad=0.6', facecolor='white', alpha=0.95, edgecolor='#CCCCCC'))
             
    plt.tight_layout()
    plt.savefig(output_path, dpi=200)
    plt.close()
    print(f"[OK] Saved training curve chart to {output_path}")

def main():
    os.makedirs('eval_figures/per_ue_v6', exist_ok=True)
    
    # 1. Plot LSTM v6 learning curve
    plot_curve(
        'models/lstm_ue_v6_losses.json',
        'LSTM-Autoencoder (v6) Learning Curve - per UE',
        'eval_figures/per_ue_v6/eval_learning_curve_lstm.png',
        'LSTM-AE v6',
        train_color='#1F77B4', # Deep Blue
        val_color='#FF7F0E',   # Amber Orange
        marker_color='#D62728' # Vibrant Red
    )
    
    # 2. Plot GRU v6 learning curve
    plot_curve(
        'models/gru_ue_v6_losses.json',
        'GRU-Autoencoder (v6) Learning Curve - per UE',
        'eval_figures/per_ue_v6/eval_learning_curve_gru.png',
        'GRU-AE v6',
        train_color='#2CA02C', # Emerald Green
        val_color='#9467BD',   # Royal Purple
        marker_color='#D62728' # Vibrant Red
    )

if __name__ == '__main__':
    main()
