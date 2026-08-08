# Grouped Feature Ablation — Laporan

Ablasi kelompok fitur pada dua model final: **LSTM-Autoencoder** *unidirectional*
dan **BiGRU-Autoencoder** *bidirectional*. Pipeline, dataset, pembagian fitur, dan
aturan kalibrasi identik untuk kedua arsitektur.

**Tanggal run:** 5–6 Agustus 2026 · **Seed:** 42 · **Sel:** 12/12 selesai, 0 gagal
**Sumber:** `results/grouped_feature_ablation/` · **Manifest:** `dataset_manifest.json`

Reproduksi:

```bash
./venv/bin/python3 run_grouped_ablation.py --seeds 42
./venv/bin/python3 aggregate_grouped_ablation.py
./venv/bin/python3 plot_grouped_ablation.py
```

---

## Protokol

| Aspek | Ketentuan |
|---|---|
| Pelatihan | setiap sel dilatih ulang dari awal pada training benign, loss uniform |
| Bobot skoring | benign-calibrated, **diturunkan ulang per konfigurasi** (dimensinya ikut berubah) |
| Ambang | dikalibrasi ulang **hanya pada validasi benign**, target Hybrid FPR(Val) ≤ 5% |
| Berkas uji | dibuka **hanya setelah** model dan ambang dibekukan |
| Cabang rule | **tidak diablasi** — R1–R5 mengindeks fitur secara posisional dan bukan komponen terlatih, sehingga selalu dievaluasi pada 19 fitur penuh |
| Hasil utama | **ML-Only** |
| Hasil pendukung | Hybrid |
| Arsitektur | LSTM tetap unidirectional, GRU tetap bidirectional; hidden size, latent dim, optimizer, batch size, epoch maksimum, dan early stopping mengikuti konfigurasi final masing-masing. Hanya dimensi masukan/keluaran yang berubah |

Jumlah parameter LSTM dan BiGRU **tidak** disamakan. Tujuan eksperimen adalah
mengukur sensitivitas tiap arsitektur final terhadap kelompok fitur, bukan
mengisolasi pengaruh jenis sel rekuren.

### Verifikasi baseline

Sebelum eksperimen dijalankan, baseline GRU seed 42 diwajibkan mereproduksi
konfigurasi deployment. **11 dari 11 metrik cocok dengan selisih nol** — ambang
0,014309; Hybrid recall 98,08%, precision 94,89%, F1 96,46%, FPR(Val) 4,97%,
FPR(Attack) 2,06%; ROC-AUC ML 0,9895; recall per kelas 97,65 / 96,76 / 98,76 /
98,26. LSTM `full_19` seed 42 juga mereproduksi Opsi B persis.

### Definisi konfigurasi

Tujuh kelompok mempartisi 19 fitur tepat satu kali (di-*assert* pada
`src/detection/feature_groups.py`), tetapi konfigurasinya sengaja bertumpang
tindih: `ul_efficiency` adalah rasio besaran PRB terhadap throughput sehingga
ikut terhapus bersama kedua keluarga. Interpretasinya adalah kontribusi
**keluarga informasi**, bukan kontribusi yang dapat dijumlahkan.

| Konfigurasi | Fitur tersisa | Yang dihapus |
|---|---:|---|
| `full_19` | 19 | — |
| `no_burst` | 15 | 4 burst index |
| `no_temporal_family` | 9 | delta, rolling, persistence, seluruh burst index |
| `no_throughput_family` | 10 | throughput mentah, total, delta, direction, throughput burst, `ul_efficiency` |
| `no_prb_family` | 8 | PRB mentah, direction, total, delta, rolling, persistence, PRB burst, `ul_efficiency` |
| `base_only_4` | 4 | seluruh fitur turunan; menyisakan `prb_usage_dl_ratio`, `prb_usage_ul_ratio`, `thp_dl_kbps`, `thp_ul_kbps` |

---

# BAGIAN 1 — Ablasi checkpoint deployment (seed 42)

## 1.1 Metrik global ML-Only

| Model | Konfigurasi | Fitur | Th | Recall | Δ | Precision | F1 | Δ | ROC-AUC | FPR(Val) | FPR(Test) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LSTM | `full_19` | 19 | 0,015817 | **97,09%** | — | 94,35% | **95,70%** | — | 0,9902 | 2,71% | 2,27% |
| LSTM | `no_throughput_family` | 10 | 0,011456 | 96,47% | −0,62 | 92,93% | 94,67% | −1,03 | 0,9892 | 3,16% | 2,87% |
| LSTM | `base_only_4` | 4 | 0,023831 | 89,89% | −7,20 | 95,53% | 92,63% | −3,07 | 0,9905 | 2,65% | 1,64% |
| LSTM | `no_prb_family` | 8 | 0,017898 | 86,27% | −10,82 | 96,11% | 90,93% | −4,77 | 0,9903 | 2,93% | 1,36% |
| LSTM | `no_burst` | 15 | 0,020925 | 74,02% | **−23,07** | 93,61% | 82,67% | −13,03 | 0,9830 | 2,88% | 1,97% |
| LSTM | `no_temporal_family` | 9 | 0,054062 | 67,08% | **−30,01** | 96,22% | 79,05% | −16,65 | 0,9879 | 2,31% | 1,03% |
| GRU | `full_19` | 19 | 0,014309 | **95,66%** | — | 95,11% | **95,38%** | — | 0,9895 | 2,99% | 1,92% |
| GRU | `no_throughput_family` | 10 | 0,008460 | 97,05% | **+1,39** | 92,81% | 94,88% | −0,50 | 0,9888 | 3,05% | 2,94% |
| GRU | `no_burst` | 15 | 0,015045 | 94,90% | −0,76 | 94,69% | 94,80% | −0,58 | 0,9909 | 2,71% | 2,08% |
| GRU | `base_only_4` | 4 | 0,030958 | 81,53% | −14,13 | 95,80% | 88,09% | −7,29 | 0,9894 | 2,43% | 1,40% |
| GRU | `no_temporal_family` | 9 | 0,023419 | 79,61% | −16,05 | 94,63% | 86,47% | −8,91 | 0,9882 | 2,54% | 1,76% |
| GRU | `no_prb_family` | 8 | 0,016660 | 69,86% | **−25,80** | 94,78% | 80,43% | −14,95 | 0,9877 | 2,82% | 1,50% |

## 1.2 Recall per kelas ML-Only

**LSTM-AE** (angka dalam kurung = perubahan terhadap `full_19`):

| Konfigurasi | UL Flood | DL Flood | Burst | RoQ |
|---|---:|---:|---:|---:|
| `full_19` | 97,65% | 89,68% | 99,03% | 98,26% |
| `no_burst` | 94,84% (−2,81) | **0,00%** (−89,68) | 98,76% (−0,27) | 71,72% (−26,54) |
| `no_temporal_family` | **25,59%** (−72,06) | 87,32% (−2,36) | 98,21% (−0,82) | 51,34% (−46,92) |
| `no_throughput_family` | 98,59% (+0,94) | 81,71% (−7,97) | 99,17% (+0,14) | 99,33% (+1,07) |
| `no_prb_family` | 86,38% (−11,27) | 88,50% (−1,18) | 80,00% (−19,03) | 91,29% (−6,97) |
| `base_only_4` | 91,08% (−6,57) | 84,37% (−5,31) | 97,10% (−1,93) | 84,72% (−13,54) |

**BiGRU-AE:**

| Konfigurasi | UL Flood | DL Flood | Burst | RoQ |
|---|---:|---:|---:|---:|
| `full_19` | 96,24% | 87,61% | 98,21% | 96,51% |
| `no_burst` | 95,31% (−0,93) | 85,25% (−2,36) | 97,79% (−0,42) | 96,25% (−0,26) |
| `no_temporal_family` | 77,70% (−18,54) | 86,43% (−1,18) | 97,93% (−0,28) | 59,79% (−36,72) |
| `no_throughput_family` | 98,83% (+2,59) | 88,50% (+0,89) | 98,90% (+0,69) | 98,12% (+1,61) |
| `no_prb_family` | 73,24% (−23,00) | 85,55% (−2,06) | 79,17% (−19,04) | **51,74%** (−44,77) |
| `base_only_4` | 84,51% (−11,73) | 84,07% (−3,54) | 96,41% (−1,80) | 64,21% (−32,30) |

## 1.3 Hasil pendukung — Hybrid

| Model | Konfigurasi | Recall | Δ | F1 | Δ | FPR(Val) | FPR(Test) |
|---|---|---:|---:|---:|---:|---:|---:|
| LSTM | `full_19` | 98,43% | — | 96,22% | — | 4,97% | 2,41% |
| LSTM | `no_throughput_family` | 98,75% | +0,32 | 95,67% | −0,55 | 4,97% | 3,01% |
| LSTM | `no_prb_family` | 97,18% | −1,25 | 96,60% | +0,38 | 4,97% | 1,57% |
| LSTM | `base_only_4` | 94,95% | −3,48 | 95,18% | −1,04 | 4,97% | 1,78% |
| LSTM | `no_burst` | 91,95% | −6,48 | 93,14% | −3,08 | 4,97% | 2,15% |
| LSTM | `no_temporal_family` | 90,12% | −8,31 | 92,88% | −3,34 | 4,97% | 1,54% |
| GRU | `full_19` | 98,08% | — | 96,46% | — | 4,97% | 2,06% |
| GRU | `no_throughput_family` | 98,61% | +0,53 | 95,52% | −0,94 | 4,97% | 3,08% |
| GRU | `no_burst` | 98,12% | +0,04 | 96,29% | −0,17 | 4,97% | 2,22% |
| GRU | `no_temporal_family` | 94,01% | −4,07 | 94,54% | −1,92 | 4,97% | 1,90% |
| GRU | `base_only_4` | 92,75% | −5,33 | 94,27% | −2,19 | 4,97% | 1,57% |
| GRU | `no_prb_family` | 91,46% | −6,62 | 93,38% | −3,08 | 4,97% | 1,73% |

**Cabang rule menutupi sebagian besar penurunan.** Rentang penurunan recall
ML-Only mencapai 30,01 poin (LSTM) dan 25,80 poin (GRU); pada Hybrid rentang itu
menyusut menjadi 8,31 dan 6,62 poin. Kasus paling tegas ada pada LSTM
`no_burst`: recall DL Flood ML-Only jatuh ke **0,00%**, tetapi Hybrid tetap
mencatat recall global 91,95% karena aturan R2 menangkap seluruh kelas itu
secara mandiri. Inilah alasan ML-Only dijadikan hasil utama penilaian kontribusi
fitur.

## 1.4 Analisis lintas arsitektur

**Kelompok fitur dengan penurunan performa terbesar berbeda antararsitektur.** Pada checkpoint
seed 42, penurunan ML-Only recall terbesar terjadi pada kelompok yang berlainan:

| | Penurunan terbesar | Kedua |
|---|---|---|
| LSTM | `no_temporal_family` −30,01 | `no_burst` −23,07 |
| BiGRU | `no_prb_family` −25,80 | `no_temporal_family` −16,05 |

`no_burst` menurunkan LSTM sebesar 23,07 poin tetapi BiGRU hanya 0,76 poin —
selisih arah sensitivitas yang besar. Sebaliknya `no_prb_family` menurunkan
BiGRU 25,80 poin sementara LSTM 10,82 poin.

**Satu kelompok berperilaku sama pada keduanya.** `no_throughput_family` tidak
menurunkan performa pada kedua arsitektur (LSTM −0,62; GRU **+1,39**). Pada
tingkat kelas, konfigurasi ini bahkan menaikkan recall RoQ di kedua model
(+1,07 dan +1,61). Kolom `same_direction` pada `cross_model_comparison.csv`
merekam kesesuaian arah ini per metrik.

**Kelas yang terdampak tidak seragam.** Kelompok dengan penurunan performa terbesar setelah dihapus, per kelas:

| Kelas | LSTM | BiGRU |
|---|---|---|
| **RoQ** | `no_temporal_family` −46,92 | `no_prb_family` −44,77 |
| **Burst** | `no_prb_family` −19,03 | `no_prb_family` −19,04 |
| UL Flood | `no_temporal_family` −72,06 | `no_prb_family` −23,00 |
| DL Flood | `no_burst` −89,68 | `no_burst` −2,36 |

**Burst adalah satu-satunya kelas yang menunjukkan penurunan performa setelah
kelompok fitur yang sama dihapus pada kedua arsitektur** — `no_prb_family`, dengan magnitudo nyaris identik (−19,03 dan
−19,04). Untuk RoQ, kelompok terdampak berbeda antararsitektur.

**Fitur burst index memberi kontribusi empiris besar pada LSTM meski pangsa
bobot skoringnya kecil.** Keempat fitur burst hanya memikul 0,162% bobot
skoring benign LSTM, namun menghapusnya dari masukan autoencoder menurunkan
ML-Only recall 23,07 poin. Pangsa bobot skoring mengatur agregasi skor, sedangkan
pada pelatihan loss uniform memberi keempatnya porsi setara 4/19 = 21,05%
sehingga ikut membentuk representasi laten. Pangsa bobot skoring karena itu
bukan penduga kontribusi fitur.

**Konfigurasi dengan fitur paling sedikit tidak selalu paling buruk.**
`base_only_4` (4 fitur) mencatat ML-Only recall 89,89% pada LSTM — lebih tinggi
daripada `no_burst` (74,02%) dan `no_temporal_family` (67,08%) yang
masing-masing menyisakan 15 dan 9 fitur. Pola yang sama muncul pada BiGRU
(81,53% vs 79,61% untuk `no_temporal_family`). Menghapus sebagian fitur turunan
sambil mempertahankan sisanya dapat lebih merugikan daripada menghapus seluruh
fitur turunan. Pengamatan ini bersifat deskriptif pada satu checkpoint dan
mekanismenya tidak diuji pada penelitian ini.

**ROC-AUC hampir tidak membedakan konfigurasi.** Seluruh dua belas sel berada di
rentang 0,9830–0,9909, sementara recall ML-Only berayun 67,08–97,05%. AUC
bersifat *threshold-independent* sehingga tidak menangkap kegagalan pada titik
operasi yang dipakai. Penilaian kontribusi fitur sebaiknya tidak bersandar pada
AUC.

---

# BAGIAN 2 — Sensitivitas baseline terhadap seed (lampiran)

Konfigurasi `full_19` LSTM dijalankan pada lima seed sebelum cakupan eksperimen
dipersempit. Hasil ini dilaporkan sebagai **lampiran** dan **tidak masuk ke
perhitungan kontribusi ablasi Bagian 1**.

## 2.1 Nilai individual per seed — LSTM `full_19`, ML-Only

| Seed | Th | Recall | F1 | ROC-AUC | UL Flood | DL Flood | Burst | RoQ |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **42** | 0,015817 | **97,09%** | **95,70%** | 0,9902 | **97,65%** | **89,68%** | **99,03%** | 98,26% |
| 43 | 0,027496 | 78,80% | 85,14% | 0,9846 | 91,08% | 88,50% | 97,52% | 49,20% |
| 44 | 0,021680 | 84,62% | 89,27% | 0,9870 | 95,77% | 87,02% | 98,34% | 63,81% |
| 45 | 0,021675 | 77,42% | 84,52% | 0,9854 | 94,60% | **5,31%** | 98,21% | 80,16% |
| 46 | 0,019382 | 96,47% | 95,63% | 0,9916 | 96,01% | 88,50% | 98,34% | **98,53%** |

## 2.2 Rentang lima seed

| Metrik | Min | Maks | Rentang | Nilai seed 42 |
|---|---:|---:|---:|---:|
| Recall | 77,42% | 97,09% | **19,67** | 97,09% (maks) |
| F1 | 84,52% | 95,70% | **11,18** | 95,70% (maks) |
| ROC-AUC | 0,9846 | 0,9916 | 0,0070 | 0,9902 |
| Recall UL Flood | 91,08% | 97,65% | 6,57 | 97,65% (maks) |
| Recall DL Flood | 5,31% | 89,68% | **84,37** | 89,68% (maks) |
| Recall Burst | 97,52% | 99,03% | 1,51 | 99,03% (maks) |
| Recall RoQ | 49,20% | 98,53% | **49,33** | 98,26% |

## 2.3 Pembacaan

Pelatihan sensitif terhadap inisialisasi acak. Pada konfigurasi yang identik —
`full_19`, loss uniform, dataset dan protokol yang sama, hanya seed berbeda —
ML-Only recall bergerak dalam rentang 19,67 poin. Kelas RoQ berayun 49,33 poin
dan DL Flood 84,37 poin, dengan satu seed mencatat recall DL Flood 5,31%.

Seed 42 menempati nilai maksimum pada **lima dari tujuh** metrik yang
dilaporkan (recall, F1, dan recall UL/DL/Burst). Pada dua metrik lainnya, seed 46
lebih tinggi: ROC-AUC 0,9916 vs 0,9902 dan recall RoQ 98,53% vs 98,26%.
Angka-angka pada Bagian 1 karena itu menggambarkan satu realisasi pelatihan yang
berada di ujung atas sebaran yang teramati, bukan nilai tengahnya.

---

## Keterbatasan

> Grouped feature ablation dilakukan menggunakan seed 42 yang telah ditetapkan
> sebagai seed default dan digunakan pada checkpoint deployment. Oleh karena itu,
> hasil ablasi menggambarkan sensitivitas konfigurasi model yang diterapkan dan
> bukan estimasi kestabilan lintas inisialisasi. Pengujian tambahan pada
> konfigurasi full_19 menunjukkan variasi performa antarseed yang perlu dikaji
> pada penelitian lanjutan.

Keterbatasan tambahan:

- Kelompok fitur bertumpang tindih pada `ul_efficiency`, sehingga penurunan
  performa antar konfigurasi tidak dapat dijumlahkan.
- Ambang tiap konfigurasi berbeda karena dikalibrasi ulang; perbandingan nilai
  ambang lintas konfigurasi tidak bermakna tanpa menyebut persentilnya.
- FPR(Test) diukur pada berkas serangan yang trafik benign-nya lebih jinak
  daripada dataset validasi, sebagaimana dicatat pada
  `docs/opsi_b_recalibration_results.md`.
- Seluruh hasil bersifat eksploratif dan **bukan dasar untuk memilih ulang model
  final**.

## Sel yang gagal atau tidak valid

Tidak ada. Dua belas sel Bagian 1 dan seluruh sel Bagian 2 selesai dengan status
`ok`. Tidak ada sel yang dihentikan lebih awal karena nilai tidak valid, dan
tidak ada hasil yang dibuang.

Antrean eksperimen awal (2 model × 6 konfigurasi × 5 seed = 60 pelatihan)
dihentikan atas permintaan setelah 13 sel selesai, ketika cakupan dipersempit ke
seed 42. Seluruh hasil yang telah selesai dipertahankan dan dilaporkan pada
Bagian 2.

## Berkas keluaran

`results/grouped_feature_ablation/`

| Berkas | Isi |
|---|---|
| `metrics_by_seed.csv` | seluruh sel, per mode |
| `summary_by_model.csv` | Bagian 1 — metrik + delta terhadap `full_19` seed 42 |
| `cross_model_comparison.csv` | LSTM vs BiGRU, termasuk kolom `same_direction` |
| `per_class_recall.csv` | recall per kelas + delta |
| `seed_sensitivity_appendix.csv` | Bagian 2 — nilai individual per seed |
| `seed_sensitivity_ranges.csv` | min, maks, rentang; sengaja bukan mean |
| `thresholds_by_seed.csv` | ambang tiap sel, kolom `part` memisahkan bagian |
| `training_runtime.csv` | waktu latih, best epoch, jumlah parameter, waktu inferensi |
| `dataset_manifest.json` | cakupan, protokol, hash dataset |

`eval_figures/grouped_feature_ablation/` — 9 figur, PNG + PDF vektor. Palet
Okabe-Ito, pola hatch redundan untuk cetak hitam-putih, bar dimulai dari nol.
**Tanpa error bar**: hanya satu seed yang dijalankan pada Bagian 1, sehingga
interval apa pun akan menyiratkan ketidakpastian yang tidak diukur.
