# GRU Autoencoder Dual Ensemble Design

**Tanggal:** 2026-06-01
**Status:** Approved

---

## Goal

Buat GRU Autoencoder dual ensemble sebagai alternatif arsitektur dari LSTM ensemble (v16+v22), untuk perbandingan di thesis. GRU dipilih karena arsitektur lebih ringan (~25% lebih sedikit parameter) dengan kemampuan temporal yang sebanding.

---

## Konteks: Baseline LSTM Ensemble

Model pembanding: **v16+v22 dual LSTM ensemble**
- v16 (thresh=0.21): spesialis DL Flood (93.7% recall)
- v22 (thresh=0.5): spesialis RRC Storm (85.8% recall)
- Ensemble: 98.17% overall recall, FPR 2.84%

GRU ensemble dirancang untuk meniru filosofi ini dengan spesialisasi yang lebih deliberate via perbedaan `seq_len`.

---

## Architecture

### GRU-A — Short Window (Flood Specialist)

- `seq_len = 10` (1 detik @ 100ms polling)
- **Encoder:** BiGRU(18→64) → BiGRU(128→32) → TemporalAttention → FC(latent=32)
- **Decoder:** FC(32→32) → repeat(10) → GRU(32→64) → GRU(64→18)
- Target attack: UL Flood, DL Flood, Burst ON/OFF

### GRU-B — Long Window (RRC Storm Specialist)

- `seq_len = 30` (3 detik @ 100ms polling)
- **Encoder:** BiGRU(18→64) → BiGRU(128→32) → TemporalAttention → FC(latent=32)
- **Decoder:** FC(32→32) → repeat(30) → GRU(32→64) → GRU(64→18)
- Target attack: RRC Storm (pola slow-developing yang butuh konteks lebih panjang)

Kedua model identik secara arsitektur kecuali `seq_len`. Spesialisasi muncul secara organik dari perbedaan temporal window.

### GRU vs LSTM — Perbedaan Teknis

| | LSTM | GRU |
|---|---|---|
| Gates | 3 (input, forget, output) | 2 (reset, update) |
| Cell state | Ya (h + c) | Tidak (h saja) |
| Parameter | ~baseline | ~75% dari LSTM |
| Inference | ~baseline | Lebih cepat |

GRUEncoder mengganti `nn.LSTM` dengan `nn.GRU` — tidak ada cell state, sehingga output `(out, h)` bukan `(out, h, c)`.

---

## Ensemble Logic

```python
class GRUEnsemble:
    def score(self, window_30):
        # GRU-B: pakai full 30 timestep
        # GRU-A: pakai 10 timestep terakhir dari window yang sama
        score_a = model_a.compute_reconstruction_error(window_30[-10:])
        score_b = model_b.compute_reconstruction_error(window_30)
        return max(score_a, score_b)

    def is_anomaly(self, window_30):
        score_a = model_a.compute_reconstruction_error(window_30[-10:])
        score_b = model_b.compute_reconstruction_error(window_30)
        return (score_a > model_a.threshold) or (score_b > model_b.threshold)
```

Input ke ensemble selalu buffer 30 timestep — GRU-B pakai semua, GRU-A slicing `[-10:]`. Threshold masing-masing model di-fit secara independen dari validation set (percentile-based, sama dengan LSTM).

---

## File Structure

```
src/detection/
  gru_autoencoder.py          ← GRUEncoder, GRUDecoder, GRUAutoencoder, GRUEnsemble

train_gru.py                  ← training script (mirror train_lstm.py)
export_onnx_gru.py            ← ONNX export GRU-A dan GRU-B
evaluate_gru.py               ← eval per-attack (output format sama dengan evaluate_detection.py)

models/
  gru_autoencoder_A_v1.pt
  gru_autoencoder_A_v1_threshold.json
  gru_autoencoder_B_v1.pt
  gru_autoencoder_B_v1_threshold.json

security_model_gru_A.onnx
security_model_gru_B.onnx
```

**Tidak diubah:** `lstm_autoencoder.py`, `train_lstm.py`, `export_onnx.py`, C xApp, docker-compose.
LSTM tetap aktif di produksi. GRU adalah implementasi paralel untuk evaluasi.

---

## Training

Training data sama persis dengan LSTM:
- Train: `csv/dataset_training_clean.csv` (benign only, label=0)
- Val: `csv/dataset_validation_clean.csv`
- Scaler: `models/scaler.pkl` (MinMaxScaler yang sudah di-fit dari LSTM training — dipakai ulang agar normalisasi konsisten)

```bash
# GRU-A
python3 train_gru.py \
  --train csv/dataset_training_clean.csv \
  --val   csv/dataset_validation_clean.csv \
  --seq-len 10 \
  --model-out models/gru_autoencoder_A_v1.pt \
  --threshold-out models/gru_autoencoder_A_v1_threshold.json \
  --epochs 150 --batch-size 32 --lr 0.001

# GRU-B
python3 train_gru.py \
  --train csv/dataset_training_clean.csv \
  --val   csv/dataset_validation_clean.csv \
  --seq-len 30 \
  --model-out models/gru_autoencoder_B_v1.pt \
  --threshold-out models/gru_autoencoder_B_v1_threshold.json \
  --epochs 150 --batch-size 32 --lr 0.001
```

Hyperparameter default: hidden=[64,32], latent=32, epochs=150, batch=32, lr=0.001, threshold percentile=99.0.

---

## ONNX Export

`export_onnx_gru.py` menghasilkan dua file terpisah. Pipeline wrapper sama dengan LSTM: MinMaxScaler di-bake ke dalam ONNX graph, output = anomaly score per sample (0–1).

```bash
python3 export_onnx_gru.py \
  --model-a models/gru_autoencoder_A_v1.pt \
  --threshold-a models/gru_autoencoder_A_v1_threshold.json \
  --model-b models/gru_autoencoder_B_v1.pt \
  --threshold-b models/gru_autoencoder_B_v1_threshold.json \
  --scaler models/scaler.pkl
# Output: security_model_gru_A.onnx, security_model_gru_B.onnx
```

---

## Evaluasi & Perbandingan

```bash
python3 evaluate_gru.py \
  --model-a models/gru_autoencoder_A_v1.pt \
  --model-b models/gru_autoencoder_B_v1.pt \
  --test csv/dataset_attack_mei.csv \
  --out results/eval_results_gru_ensemble_v1.json
```

Output JSON format identik dengan `eval_results_attack_mei_rule3c.json` — langsung bisa dibandingkan.

**Metrik perbandingan thesis:**

| Metrik | LSTM ensemble (v16+v22) | GRU ensemble (target) |
|--------|------------------------|----------------------|
| Overall recall | 98.17% | TBD |
| FPR normal | 2.84% | TBD |
| UL Flood recall | 99.2% | TBD |
| DL Flood recall | 99.4% | TBD |
| Burst recall | ~98% | TBD |
| RRC Storm recall | 94.0% | TBD |
| Model params | ~baseline | ~75% dari LSTM |
| Inference latency | ~baseline | TBD (expected lebih cepat) |

---

## Tidak Perlu

- Perubahan C xApp atau detection pipeline produksi
- Dataset baru — pakai dataset yang sama dengan LSTM
- Scaler baru — pakai `models/scaler.pkl` yang sudah ada
- Perubahan `lstm_autoencoder.py` atau file LSTM lainnya
