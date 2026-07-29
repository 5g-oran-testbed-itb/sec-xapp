# AE Loss-Weighting Ablation (Non-Attack Training Loss) — Design

**Status:** Draft (awaiting user review)
**Date:** 2026-07-29
**Topic:** Menghapus bobot Scheme A (attack-derived) dari *loss training* AE, dan mengukur efeknya lewat matched-pair ablation.

---

## 1. Masalah

Model AE deploy (`gru_ue_v5.pt`, `lstm_ue_v6.pt`) dilatih dengan `weighted_mse` yang memakai **bobot Scheme A** ([`train_gru_ue.py:142`](../../../train_gru_ue.py), [`train_lstm_ue.py:126`](../../../train_lstm_ue.py)). Karena bobot Scheme A diturunkan dari rasio error serangan/benign, *loss training* pun membawa jejak informasi serangan — jalur kebocoran yang belum ditutup oleh perbaikan skoring benign-calibrated. (Untuk LSTM bahkan val-loss/early-stopping ikut berbobot Scheme A di baris 137.)

## 2. Tujuan

1. Latih ulang AE dengan **loss non-attack**, memakai **benign-scale weighted loss** (pilihan user).
2. Ukur efeknya secara adil lewat **matched-pair ablation** (semua identik kecuali loss).
3. Tentukan konfigurasi loss terbaik yang bebas kebocoran untuk pipeline final.

**Non-tujuan:** mengubah arsitektur/`seq_len`/skema fitur; mengganti model deploy sebelum hasil ablation dievaluasi; mengumpulkan data serangan baru (Track B).

## 3. Resolusi sirkularitas — two-pass

Benign-scale loss butuh residual dari model terlatih (sirkular). Diselesaikan dua tahap:

1. **Pass-1:** latih AE dengan **uniform MSE** (bobot=1; bebas serangan).
2. **Derive:** hitung residual kuadrat per-fitur pada **data training benign** dari model pass-1; bobot `w_j = 1/(median(e_j)+MAD(e_j)+ε)`, di-cap `10×median(w)` (reuse [`benign_calibrated_weights`](../../../src/detection/scoring.py)). Simpan ke JSON. Bobot **beku** (konstanta, bebas serangan).
3. **Pass-2:** latih ulang AE dari nol dengan bobot beku itu di loss.

Bobot loss diturunkan dari **training benign** (bukan validation) agar validation tetap murni untuk early-stopping/threshold. Pipeline fitur mengikuti `df_to_raw()` training apa adanya (konsisten antar-varian).

## 4. Matched-pair ablation

Per arsitektur (GRU, LSTM), tiga varian loss — **semua** dengan skrip, hyperparameter, `seq_len=30`, scaler, dan data identik:

| Varian | Bobot loss | Sumber |
|---|---|---|
| `schemea` | Scheme A | `FEATURE_WEIGHTS` (baseline lama/bocor, dilatih ulang utk perbandingan adil) |
| `uniform` | 1 (semua fitur) | — (= Pass-1) |
| `benign` | `1/(median+MAD)` | residual training benign model uniform (Pass-2) |

Total 6 model. Konfigurasi tetap: dropout 0.1 (default model), patience 15, max epoch 200, batch 32, lr 1e-3, MinMaxScaler fit di training. Arsitektur = default `train_*_ue.py` (tidak diubah).

## 5. Penamaan artefak

Folder baru `models/ablation_loss/` — model deploy v5/v6 **tidak disentuh**:
```
models/ablation_loss/gru_ue_loss{schemea,uniform,benign}.pt  (+ _scaler.pkl, _threshold.json)
models/ablation_loss/lstm_ue_loss{schemea,uniform,benign}.pt (+ ...)
models/ablation_loss/gru_ue_lossbenign_weights.json          (bobot loss beku)
models/ablation_loss/lstm_ue_lossbenign_weights.json
```

## 6. Evaluasi

Setiap varian di-skor dengan **benign-calibrated scoring** (bobot skor diturunkan ulang dari residual validation tiap model — konsisten dgn keputusan leakage-free sebelumnya), threshold dikalibrasi ke **Hybrid FPR(Attack) < 3%** (reuse `evaluate_scoring_comparison.py`).

Laporkan tabel gabungan: (varian loss × model × {Rule/ML/Hybrid}) → Recall, Precision, F1, FPR(Attack), FPR(Val), AUC, recall per-kelas. Plus figur perbandingan varian loss di folder baru `eval_figures/loss_ablation/`.

## 7. Aturan keputusan

- Jika `benign` ≥ `uniform` (dan keduanya ≈/≥ `schemea`) → adopsi **benign-scale loss** sebagai konfigurasi final bebas-kebocoran.
- Jika `benign` ≈ `uniform` → adopsi **uniform loss** (lebih sederhana, tanpa two-pass) — ablation membenarkan pilihan yang lebih murah.
- Jika `schemea` unggul jelas → catat bahwa loss weighting attack-informed memberi keuntungan nyata (dan itu trade-off vs kebersihan), lalu putuskan sadar.

## 8. Permukaan implementasi (untuk fase plan)

- Modifikasi [`train_gru_ue.py`](../../../train_gru_ue.py) & [`train_lstm_ue.py`](../../../train_lstm_ue.py): tambah `--loss-weights {schemea,uniform,benign}` + `--loss-weights-json <path>`; helper `select_loss_weights()` yang mengembalikan tensor bobot. Default `schemea` (backward-compatible).
- Skrip baru `derive_loss_weights.py`: pass-1 model → bobot benign-scale JSON.
- Skrip baru `run_loss_ablation.py`: orkestrasi train 6 model + derive + evaluasi + tabel gabungan (reuse `evaluate_scoring_comparison`).
- Skrip figur `plot_loss_ablation.py` → `eval_figures/loss_ablation/`.
- Unit test: `select_loss_weights()` (uniform→ones, schemea→dict, benign→JSON), reuse test bobot benign yang ada.
- Tidak menyentuh xApp C runtime.

## 9. Kriteria keberhasilan

- 6 model terlatih di `models/ablation_loss/`; v5/v6 utuh.
- Tabel ablation (varian × model × config) + figur perbandingan tersimpan.
- Keputusan §7 terdokumentasi di `docs/loss_ablation_results.md`.
- Semua varian dievaluasi dengan skoring & threshold-calibration yang identik (hanya loss training yang beda).

## 10. Batasan yang diakui

- Menutup **training-loss leakage**; **version-selection** & jaminan mutlak tetap butuh test set baru (lihat `docs/scoring_comparison_results.md` §Limitations).
- Benign-scale loss berisiko mendistorsi AE (menekan lebih jauh fitur yang residual benign-nya sudah kecil); justru itu yang diuji ablation — `uniform` jadi pembanding netral.
