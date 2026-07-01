import json
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

def plot_curve(json_path, title, output_path):
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    train_losses = data['train']
    val_losses = data['val']
    best_epoch = data.get('best_epoch')
    
    epochs = np.arange(1, len(train_losses) + 1)
    
    plt.figure(figsize=(8, 5))
    plt.plot(epochs, train_losses, color='#2196F3', linewidth=2, label='Train Loss')
    plt.plot(epochs, val_losses, color='#FF9800', linewidth=2, linestyle='--', label='Val Loss')
    
    if best_epoch:
        plt.axvline(best_epoch, color='#795548', linestyle=':', linewidth=1.5,
                    label=f'Best Epoch ({best_epoch})')
        best_val = val_losses[best_epoch - 1]
        plt.plot(best_epoch, best_val, 'D', color='#795548', markersize=7)
        plt.annotate(f'Best: Epoch {best_epoch}\nVal Loss = {best_val:.6f}',
                     xy=(best_epoch, best_val),
                     xytext=(best_epoch + len(epochs)*0.05, best_val * 1.15),
                     fontsize=9, color='#795548',
                     arrowprops=dict(arrowstyle='->', color='#795548', lw=1))
                     
    plt.xlabel('Epoch', fontsize=11)
    plt.ylabel('MSE Loss', fontsize=11)
    plt.title(title, fontsize=13, fontweight='bold')
    plt.legend(fontsize=9)
    plt.grid(True, alpha=0.3)
    
    # Text box for final values
    final_t = train_losses[-1]
    final_v = val_losses[-1]
    plt.text(0.95, 0.70,
             f'Final Train Loss: {final_t:.6f}\nFinal Val Loss: {final_v:.6f}',
             transform=plt.gca().transAxes, ha='right', va='top', fontsize=9,
             bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8, edgecolor='gray'))
             
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"Saved: {output_path}")

os.makedirs('eval_figures/per_ue_v5', exist_ok=True)
plot_curve('models/lstm_ue_v5_losses.json', 'LSTM-Autoencoder Learning Curve (v5)', 'eval_figures/per_ue_v5/eval_learning_curve_lstm.png')
plot_curve('models/gru_ue_v5_losses.json', 'GRU-Autoencoder Learning Curve (v5)', 'eval_figures/per_ue_v5/eval_learning_curve_gru.png')
