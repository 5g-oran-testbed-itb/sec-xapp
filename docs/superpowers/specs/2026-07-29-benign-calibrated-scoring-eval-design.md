# Benign-Calibrated Scoring & Leakage-Free Re-Evaluation — Design

**Status:** Draft (awaiting user review)
**Date:** 2026-07-29
**Topic:** Menghapus attack–test leakage pada skema penilaian anomali per-UE dan mengevaluasi ulang secara jujur.

---

## 1. Masalah

Verifikasi kode mengonfirmasi kritik reviewer (PDF hal. 109–118): bobot Weighted MSE ("Scheme A") diturunkan dari rasio *reconstruction error* serangan/benign, lalu **file serangan yang sama** (`csv/dataset_attack_ue_juni.csv`) dipakai untuk pengujian final. Ini evaluasi sirkular / optimistically biased.

Bukti:
- [`src/detection/feature_schema_ue.py:37-39`](../../../src/detection/feature_schema_ue.py) — `FEATURE_WEIGHTS` = `log(max attack/benign reconstruction error ratio)`.
- [`evaluate_per_ue_v2.py:804`](../../../evaluate_per_ue_v2.py) — `--attack` default `csv/dataset_attack_ue_juni.csv`; metrik dihitung dari file itu (baris 390–391).
- [`calibrate_threshold_gru.py:131`](../../../calibrate_threshold_gru.py), [`calibrate_threshold_remote.py:116`](../../../calibrate_threshold_remote.py) — `attack_csv = "csv/dataset_attack_ue_juni.csv"`.
- Riwayat: analisis per-fitur yang melahirkan bobot membaca `dataset_attack_ue_juni.csv`; skema A/B/C dipilih berdasarkan AUC pada file itu.

Kebocoran sekunder (**model-selection leakage**): `seq_len` 10→30 dipilih karena gagal pada RoQ; GRU dipertahankan v5 (bukan v6) karena v6 anjlok di UL Flood; threshold di-*hand-tune*. Semua keputusan ini "melihat" file serangan yang sama.

## 2. Wawasan kunci & strategi

Kebocoran yang dikeluhkan reviewer bersifat spesifik pada **fungsi skor yang memakai label serangan**. Maka skema skor yang **tidak** memakai data serangan saat kalibrasi memperlakukan `dataset_attack_ue_juni.csv` sebagai *held-out* yang sah:

- **Uniform MSE** — bobot=1, threshold=P97 pada benign. Tidak menyentuh serangan.
- **Benign-calibrated MSE** — bobot dari residual benign, threshold dari benign. Tidak menyentuh serangan.

Konsekuensi: angka valid untuk kedua skema itu bisa dihitung **tanpa merekam data baru**. Ini jadi **Track A** (jalur utama). Koleksi test set baru untuk juga membersihkan skema attack-informed + mengurangi model-selection leakage jadi **Track B (opsional)**.

## 3. Tujuan

1. Ganti/ dampingi skema attack-informed dengan **benign-calibrated residual weighting** yang tidak memakai label serangan.
2. Laporkan Uniform & Benign-calibrated pada `dataset_attack_ue_juni.csv` sebagai angka **bebas scoring-leakage** (Track A).
3. Tampilkan skema attack-informed hanya sebagai pembanding yang **jujur dilabeli bias/optimistic**.
4. (Track B, opsional) Rekam test set baru untuk juga memvalidasi attack-informed secara bersih.

**Non-tujuan:** melatih ulang AE, mengubah arsitektur/`seq_len`/versi model, menambah serangan unseen, serangan RRC storm & RF jammer.

## 4. Rambu kejujuran (WAJIB)

1. **Bekukan formula C2 secara a priori.** Parameter benign-calibrated (mean vs median+MAD, nilai cap, ε) ditetapkan dari penalaran/benign **sebelum** file serangan dibuka. Dilarang men-*tuning* parameter dengan mengintip hasil deteksi di serangan (itu *formula-selection leakage*).
2. **Model-selection leakage tetap diakui.** AE (LSTM v6 / GRU v5, `seq_len=30`) arsitekturnya dipilih memakai `dataset_attack_ue_juni.csv`. Jadi C1/C2 di file ini *scoring-clean* tapi *architecture-informed* — bukan estimasi generalisasi yang sepenuhnya bebas bocor. Wajib ditulis di limitasi. (Hanya Track B yang bisa menutup ini.)
3. Threshold C1/C2 dihitung ulang dari benign → kebocoran *hand-tuned threshold* lama tidak ikut terbawa untuk kedua config ini.

## 5. Partisi data

| Partisi | Isi | Dipakai untuk | Sumber |
|---|---|---|---|
| `benign_train` | benign | Latih AE + fit scaler | `csv/dataset_training_ue_juni.csv` (ada) |
| `benign_val` | benign | Kalibrasi bobot benign (C2) + threshold P97 | `csv/dataset_validation_ue_juni.csv` (ada) |
| `benign_test` | benign (`label==0`) | Negatif untuk FPR final | window `label==0` dari `csv/dataset_attack_ue_juni.csv` (held-out; tak dipakai threshold) |
| `attack_test` | 4 serangan (`label>0`) | Positif untuk recall final | `csv/dataset_attack_ue_juni.csv` (held-out untuk C1/C2) |
| `attack_calib` | 4 serangan | Bobot Scheme A + pemilihan skema — **C3 saja** | `csv/dataset_attack_ue_juni.csv` (→ membuat C3 sirkular; diberi label bias) |
| *(Track B, opsional)* `*_test_baru` | benign + 4 serangan | Test bersih untuk C3 + kurangi model-selection leakage | koleksi baru via `~/xapp/security-scripts/` |

## 6. Tiga konfigurasi

Dijalankan untuk **kedua model** (GRU v5 & LSTM v6) — total 6 evaluasi pada test set yang sama. Dalam tiap model, AE + scaler identik; hanya fungsi skor berbeda; threshold masing-masing = P97 skor pada `benign_val`.

Skor per-window: `S(x) = Σ_j w_j · e_j(x) / Σ_j w_j`, `e_j(x)` = residual kuadrat fitur ke-j.

| Config | `w_j` | Sumber bobot | Status pada `dataset_attack_ue_juni.csv` |
|---|---|---|---|
| **C1 Uniform** | 1 | — | Valid (scoring-clean) |
| **C2 Benign-calibrated** | `1/(median(e_j)+MAD(e_j)+ε)`, di-cap ≤ `10×median(w)` | residual `benign_val` | Valid (scoring-clean) |
| **C3 Attack-informed** | Scheme A `log(rasio)` | `attack_calib` (= file test) | **Bias/sirkular — pembanding saja, dilabeli jujur** |

## 7. Protokol evaluasi (Track A)

1. Bekukan AE + scaler (terlatih pada `benign_train`); bekukan formula C2 (rambu §4.1).
2. Untuk C1 & C2: hitung bobot dari sumbernya, skor pada `benign_val`, tetapkan threshold=P97.
3. **Buka `dataset_attack_ue_juni.csv` sekali.** Pisahkan window `label==0` (→ `benign_test`, FPR) dan `label>0` (→ `attack_test`, recall). Hitung skor C1 & C2, bandingkan ke threshold.
4. Hitung juga C3 di file yang sama untuk pembanding — tandai eksplisit sebagai attack-informed/optimistic.
5. Laporkan per (model × config): recall@FPR (@5% & @3%), AUC, ROC, recall per-kelas (ul_flood/dl_flood/burst/roq), FPR aktual pada `benign_test`.
6. Tidak ada iterasi/tuning setelah file dibuka.

## 8. Aturan keputusan

- **C2 capai target** (mis. ≥80% recall @ ~5% FPR) → adopsi C2; klaim *unsupervised/one-class* penuh, bebas scoring-leakage.
- **Hanya C3 unggul jauh** → dua opsi jujur: (a) jalankan Track B untuk memvalidasi C3 pada attack set terpisah, atau (b) laporkan C2 sebagai metode utama & C3 sebagai *upper-bound attack-informed* dengan disclaimer.
- C1 selalu dilaporkan sebagai baseline/floor jujur.

## 9. Batasan yang diakui

- Track A menutup **scoring-leakage**, bukan **model-selection leakage** (arsitektur/`seq_len`/versi dipilih pada file yang sama). Wajib disebut di ancaman-validitas.
- `benign_val` dipakai untuk bobot C2 sekaligus threshold → optimisme dalam-benign kecil; FPR final diukur pada `benign_test` (`label==0`) yang held-out untuk mengoreksi ini.
- Track B menghapus sisa kebocoran namun butuh rekaman baru; di luar scope wajib.

## 10. Permukaan implementasi (untuk fase plan)

- Skrip baru: hitung bobot benign-calibrated (median+MAD+cap) dari residual `benign_val` → simpan JSON bobot (parameter dibekukan a priori).
- Modifikasi [`evaluate_per_ue_v2.py`](../../../evaluate_per_ue_v2.py): flag `--scoring {uniform,benign,attack}`; pisahkan negatif FPR dari window `label==0`; loop dua model; penulis tabel per (model×config).
- Tidak menyentuh xApp C runtime (murni jalur evaluasi offline).

## 11. Kriteria keberhasilan

- C1 & C2 dievaluasi pada `dataset_attack_ue_juni.csv` tanpa file itu pernah menyentuh bobot/threshold-nya.
- Formula C2 dibekukan sebelum file serangan dibuka; tidak ada tuning pasca-buka.
- Dokumen memuat tabel C1/C2/C3 (C3 dilabeli bias) + pernyataan limitasi model-selection.
