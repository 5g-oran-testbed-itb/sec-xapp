# Bobot skoring benign-calibrated — penurunan dan tabel lengkap 19 fitur

Konstanta pembobotan $w_j$ yang dipakai skoring anomali pada konfigurasi final
(pelatihan MSE seragam + skoring terbobot benign), beserta contoh perhitungannya
tahap demi tahap.

**Sumber:** `results/opsi_b/opsi_b.json` field `results.<arch>.weights` ·
Implementasi: [`src/detection/scoring.py`](../src/detection/scoring.py)
`benign_calibrated_weights()` · Verifikasi: `verify_scoring_math.py`

**Data penurunan:** 1.772 window benign `csv/dataset_validation_ue_juni.csv`.
Tidak ada satu pun window serangan atau label serangan yang masuk ke perhitungan
ini — inilah yang menjaga sifat *one-class* pipeline.

---

## 1. Rumusnya

Untuk setiap fitur $j = 1 \dots 19$, dengan $e_{n,j}$ = residual rekonstruksi
kuadrat fitur $j$ pada window $n$ (sudah dirata-rata atas 30 timestep):

$$m_j = \operatorname*{median}_{n \in \text{Val}} e_{n,j} \qquad\qquad
d_j = \operatorname*{median}_{n \in \text{Val}} \left| e_{n,j} - m_j \right| \;\;(\text{MAD})$$

$$r_j = \frac{1}{m_j + d_j + \varepsilon}, \quad \varepsilon = 10^{-6}
\qquad\qquad c = 10 \cdot \operatorname*{median}_j (r_j)
\qquad\qquad \boxed{w_j = \min(r_j,\, c)}$$

Tiga hal yang dilakukan rumus ini:

1. **Invers skala** — fitur yang residual benign-nya kecil mendapat bobot besar,
   karena penyimpangan sedikit pun di fitur itu bermakna.
2. **MAD, bukan simpangan baku** — MAD tahan terhadap pencilan; residual benign
   punya ekor panjang (sesi validasi memuat speedtest), dan simpangan baku akan
   tertarik oleh segelintir window ekstrem.
3. **Cap $c$** — mencegah satu fitur ber-residual mendekati nol mendominasi skor.
   Tanpa cap, `thp_ul_delta` pada LSTM akan mendapat $r_j = 73.448$, yaitu **49,4×**
   bobot fitur median — cap membatasinya ke 10×.

Skor akhir sebuah window adalah rata-rata terbobotnya:
$S_n = \sum_j w_j e_{n,j} / \sum_j w_j$. Karena ada pembagi $\sum_j w_j$, **skala
absolut $w_j$ tidak berpengaruh** — yang menentukan hanya rasio antar fitur. Itu
sebabnya angka mentahnya boleh terlihat besar (belasan ribu).

---

## Mengapa MinMaxScaler saja tidak cukup

Pertanyaan yang wajar: MinMaxScaler sudah menyeragamkan seluruh fitur ke rentang
$[0, 1]$ — bukankah itu sudah menyetarakan kontribusi antar fitur? **Tidak**, dan
alasannya bukan argumen teoretis melainkan sudah terukur.

### Bukti langsung: eksperimen terkontrol sudah dijalankan

MinMaxScaler **aktif di kedua kasus**. Konfigurasi "skoring uniform" pada
[opsi_b_metrics.md](opsi_b_metrics.md) §Bukti persis berarti *"MinMaxScaler saja,
tanpa pembobotan tambahan"* — dan ia gagal:

| Model | Skoring | Recall | F1 | AUC | **RoQ** |
|---|---|---:|---:|---:|---:|
| LSTM | benign-calibrated | 98,43% | 96,22% | 0,9902 | **98,79%** ✅ |
| LSTM | uniform (hanya MinMax) | 89,53% | 91,75% | 0,9682 | **72,39%** ❌ |
| GRU | benign-calibrated | 98,08% | 96,46% | 0,9895 | **98,26%** ✅ |
| GRU | uniform (hanya MinMax) | 91,06% | 92,38% | 0,9725 | **77,08%** ❌ |

Jadi ini bukan spekulasi: dengan penskalaan yang sama persis, menghilangkan
pembobotan menjatuhkan RoQ di bawah target 85%.

### Sebab utama: keduanya bekerja pada objek yang berbeda

MinMaxScaler menyetarakan **nilai masukan**. Skor anomali menjumlahkan **residual
rekonstruksi**. Keduanya tidak sama, dan yang kedua tidak dikendalikan oleh yang
pertama.

Setelah MinMaxScaler diterapkan, pada 1.772 window benign validasi:

| Besaran | Rentang antar 19 fitur |
|---|---:|
| Simpangan baku nilai **masukan** terskala | 0,02107 – 0,31793 = **15×** |
| Median **residual** benign $m_j$ | 8,119e-06 – 6,272e-02 = **7.725×** |

Penskalaan berhasil menekan keragaman masukan menjadi 15×, tetapi besaran yang
sebenarnya masuk ke skor masih berbeda **7.725×**. Selisih tiga orde besaran itulah
yang harus dinetralkan pembobotan.

### Bukti bahwa penskalaan ulang tidak dapat memperbaikinya

Kalau skala residual semata-mata turunan dari skala masukan, memperbaiki scaler akan
cukup. Kenyataannya tidak. Dua pasangan pembanding dari data yang sama:

| Pasangan | std masukan terskala | Rasio masukan | Median residual | Rasio residual |
|---|---:|---:|---:|---:|
| `prb_ul_delta` | 0,02116 | **1,00×** | 3,184e-05 | **3,9×** |
| `thp_ul_delta` | 0,02107 | (praktis identik) | 8,119e-06 | |
| `thp_ul_kbps` | 0,09173 | **1,50×** | 8,733e-06 | **29,8×** |
| `prb_usage_dl_ratio` | 0,13757 | | 2,601e-04 | |

Pasangan pertama menentukan: `prb_ul_delta` dan `thp_ul_delta` punya sebaran
masukan yang **praktis identik** setelah penskalaan, namun residualnya berbeda
**3,9×**. Perbedaan itu berasal dari seberapa baik autoencoder dapat merekonstruksi
masing-masing fitur — bukan dari skalanya. Tidak ada transformasi masukan apa pun
yang dapat menyamakannya, karena penyebabnya berada di sisi model.

Secara agregat, korelasi Spearman antara sebaran masukan dan median residual adalah
$\rho = 0{,}870$. Tinggi — jadi penskalaan memang menjelaskan sebagian besar, dan
MinMaxScaler tetap perlu. Tetapi **24% variasi peringkat tidak dijelaskannya**, dan
sisa itulah yang menentukan apakah RoQ terdeteksi 98,79% atau 72,39%.

### Empat perbedaan struktural

| Aspek | MinMaxScaler | Pembobotan benign-calibrated |
|---|---|---|
| **Tahap pipeline** | pra-model (sebelum masuk AE) | pasca-model (setelah residual dihitung) |
| **Objek** | nilai fitur masukan $x_j$ | residual rekonstruksi $e_j$ |
| **Statistik** | min & maks — **2 titik ekstrem**, rapuh terhadap pencilan | median & MAD — **tahan pencilan** |
| **Data acuan** | dataset pelatihan | dataset validasi (residual model terlatih) |

Kerapuhan statistiknya terlihat nyata di data. Karena min/maks ditentukan dua titik
ekstrem, massa data benign dapat menempati sepotong kecil saja dari rentang
$[0,1]$ nominal:

| Fitur | Rentang p05–p95 setelah MinMax | Okupansi efektif |
|---|---|---:|
| `thp_ul_kbps` | 0,0000 – 0,0355 | **3,6%** dari $[0,1]$ |
| `prb_usage_ul_ratio` | 0,0000 – 0,0842 | 8,4% |
| `ul_persistence` | 0,0000 – 1,0000 | 100% |
| `traffic_direction` | 0,0215 – 1,0000 | 98% |

Jadi "semua fitur berada di $[0,1]$" benar secara nominal tetapi menyesatkan: 90%
data benign `thp_ul_kbps` sebenarnya berdesak di 3,6% pertama rentangnya, sedangkan
`ul_persistence` memakai seluruhnya — perbedaan okupansi efektif **28×** yang tidak
tampak dari pernyataan "sudah dinormalisasi".

### Batas $[0,1]$ pun tidak dijamin saat inferensi

`MinMaxScaler.transform()` tidak memotong nilai di luar rentang pelatihan. Pada
data yang dievaluasi:

| Set | Min terskala | Maks terskala | Nilai > 1 | Nilai < 0 |
|---|---:|---:|---:|---:|
| Validasi | −0,2313 | 1,2786 | 57 | 4 |
| Serangan | −0,1990 | 1,1823 | 224 | 2 |

Nilai terskala tertinggi pada berkas serangan adalah `thp_dl_delta` = 1,18. Ini
perilaku yang diharapkan — serangan memang di luar rentang benign — tetapi
menegaskan bahwa MinMaxScaler adalah normalisasi *referensi pelatihan*, bukan
jaminan rentang saat inferensi.

### Ringkasnya

MinMaxScaler dan pembobotan benign-calibrated **saling melengkapi, bukan
menggantikan**. Scaler menyamakan satuan fisik yang berbeda (kbps vs rasio vs
indeks) supaya AE dapat dilatih sama sekali; pembobotan menyamakan **keterandalan
residual** setiap fitur setelah model terlatih. Yang pertama tidak dapat mengetahui
yang kedua, karena keterandalan residual baru ada **setelah** modelnya dilatih.

> **Untuk naskah:** jangan menulis bahwa MinMaxScaler "sudah menyetarakan
> kontribusi antar fitur". Yang benar: MinMaxScaler menyetarakan **skala masukan**,
> sedangkan pembobotan benign-calibrated menyetarakan **skala residual** — dan
> perbedaan 7.725× pada residual setelah penskalaan adalah buktinya.

---

## 2. Contoh perhitungan — empat fitur LSTM

### Contoh A — fitur yang kena cap: `thp_ul_kbps`

Langkah 1, median residual benign atas 1.772 window:

$$m_j = 0{,}0000087330$$

Langkah 2, MAD:

$$d_j = 0{,}0000086421$$

Langkah 3, bobot mentah:

$$r_j = \frac{1}{0{,}0000087330 + 0{,}0000086421 + 0{,}000001} = \frac{1}{0{,}0000183751} = 54.421{,}53$$

Langkah 4, cap. Median seluruh 19 nilai $r_j$ adalah $1.486{,}454$, sehingga
$c = 10 \times 1.486{,}454 = 14.864{,}541$. Karena $r_j > c$:

$$w_j = \min(54.421{,}53;\; 14.864{,}541) = \mathbf{14.864{,}541}$$

Cap memotongnya menjadi **27,3%** dari nilai mentah.

### Contoh B — fitur yang tidak kena cap: `prb_usage_ul_ratio`

$$m_j = 0{,}0000614924 \qquad d_j = 0{,}0000609672$$

$$r_j = \frac{1}{0{,}0000614924 + 0{,}0000609672 + 0{,}000001} = \frac{1}{0{,}0001234596} = 8.099{,}82$$

$8.099{,}82 < 14.864{,}541$, jadi $w_j = \mathbf{8.099{,}82}$ apa adanya.

### Contoh C — fitur burst: `prb_ul_burst_index`

$$m_j = 0{,}0178796388 \qquad d_j = 0{,}0139951585$$

$$r_j = \frac{1}{0{,}0178796388 + 0{,}0139951585 + 0{,}000001} = \frac{1}{0{,}0318757966} = \mathbf{31{,}37}$$

Median residual benign-nya **2.047× lebih besar** daripada `thp_ul_kbps`, jadi bobotnya
turun drastis. Tafsirnya bukan "fitur ini jelek", melainkan: autoencoder sulit
merekonstruksi burst index bahkan pada trafik normal, sehingga lonjakan di fitur
itu tidak dapat dibedakan dari derau.

### Contoh D — bobot terendah: `traffic_direction`

$$m_j = 0{,}0627214015 \qquad d_j = 0{,}0197782498 \qquad r_j = \frac{1}{0{,}0825006515} = \mathbf{12{,}12}$$

Rasio bobot tertinggi terhadap terendah: $14.864{,}541 / 12{,}12 = \mathbf{1.226\times}$.

---

## 3. Tabel lengkap — LSTM-AE

$\operatorname{median}_j(r_j) = 1.486{,}454$ · cap $c = 14.864{,}541$ ·
$\sum_j w_j = 78.739{,}85$ · **4 dari 19 fitur kena cap**

| # | Fitur | median $e_j$ | MAD $e_j$ | $r_j$ mentah | Kena cap | $w_j$ | Pangsa |
|---:|---|---:|---:|---:|:---:|---:|---:|
| 1 | `thp_ul_kbps` | 8,733e-06 | 8,642e-06 | 54.421,53 | ✅ | **14.864,5410** | 18,878% |
| 2 | `prb_ul_delta` | 3,184e-05 | 3,065e-05 | 15.750,11 | ✅ | **14.864,5410** | 18,878% |
| 3 | `thp_ul_delta` | 8,119e-06 | 4,496e-06 | 73.448,59 | ✅ | **14.864,5410** | 18,878% |
| 4 | `prb_ul_roll_mean` | 1,238e-05 | 1,211e-05 | 39.231,14 | ✅ | **14.864,5410** | 18,878% |
| 5 | `prb_usage_ul_ratio` | 6,149e-05 | 6,097e-05 | 8.099,82 | — | 8.099,8179 | 10,287% |
| 6 | `prb_ul_roll_std` | 1,465e-04 | 1,399e-04 | 3.479,54 | — | 3.479,5361 | 4,419% |
| 7 | `prb_usage_dl_ratio` | 2,601e-04 | 2,600e-04 | 1.919,15 | — | 1.919,1505 | 2,437% |
| 8 | `thp_dl_delta` | 3,037e-04 | 2,979e-04 | 1.659,62 | — | 1.659,6217 | 2,108% |
| 9 | `thp_total_kbps` | 3,246e-04 | 3,241e-04 | 1.538,97 | — | 1.538,9658 | 1,954% |
| 10 | `thp_dl_kbps` | 3,361e-04 | 3,357e-04 | 1.486,45 | — | 1.486,4541 | 1,888% |
| 11 | `prb_total` | 6,394e-04 | 6,384e-04 | 781,98 | — | 781,9769 | 0,993% |
| 12 | `ul_persistence` | 4,920e-03 | 4,349e-03 | 107,88 | — | 107,8770 | 0,137% |
| 13 | `ul_efficiency` | 1,064e-02 | 1,063e-02 | 47,01 | — | 47,0087 | 0,060% |
| 14 | `thp_ul_burst_index` | 1,459e-02 | 9,026e-03 | 42,35 | — | 42,3451 | 0,054% |
| 15 | `prb_ul_burst_index` | 1,788e-02 | 1,400e-02 | 31,37 | — | 31,3718 | 0,040% |
| 16 | `prb_dl_burst_index` | 1,914e-02 | 1,762e-02 | 27,20 | — | 27,2031 | 0,035% |
| 17 | `thp_dl_burst_index` | 2,395e-02 | 1,434e-02 | 26,11 | — | 26,1149 | 0,033% |
| 18 | `prb_direction` | 2,569e-02 | 1,950e-02 | 22,13 | — | 22,1266 | 0,028% |
| 19 | `traffic_direction` | 6,272e-02 | 1,978e-02 | 12,12 | — | 12,1211 | 0,015% |

## 4. Tabel lengkap — GRU-AE

$\operatorname{median}_j(r_j) = 1.433{,}836$ · cap $c = 14.338{,}364$ ·
$\sum_j w_j = 75.875{,}42$ · **3 dari 19 fitur kena cap**

| # | Fitur | median $e_j$ | MAD $e_j$ | $r_j$ mentah | Kena cap | $w_j$ | Pangsa |
|---:|---|---:|---:|---:|:---:|---:|---:|
| 1 | `thp_ul_kbps` | 1,107e-05 | 9,408e-06 | 46.566,62 | ✅ | **14.338,3643** | 18,897% |
| 2 | `prb_ul_roll_mean` | 1,634e-05 | 1,384e-05 | 32.070,32 | ✅ | **14.338,3643** | 18,897% |
| 3 | `thp_ul_delta` | 1,087e-05 | 6,764e-06 | 53.670,90 | ✅ | **14.338,3643** | 18,897% |
| 4 | `prb_ul_delta` | 4,450e-05 | 3,599e-05 | 12.271,73 | — | 12.271,7266 | 16,174% |
| 5 | `prb_usage_ul_ratio` | 6,701e-05 | 6,177e-05 | 7.705,02 | — | 7.705,0156 | 10,155% |
| 6 | `prb_ul_roll_std` | 1,198e-04 | 1,116e-04 | 4.302,78 | — | 4.302,7798 | 5,671% |
| 7 | `prb_usage_dl_ratio` | 2,064e-04 | 1,921e-04 | 2.503,31 | — | 2.503,3145 | 3,299% |
| 8 | `thp_dl_kbps` | 2,900e-04 | 2,857e-04 | 1.733,88 | — | 1.733,8839 | 2,285% |
| 9 | `thp_total_kbps` | 3,233e-04 | 3,139e-04 | 1.566,88 | — | 1.566,8795 | 2,065% |
| 10 | `thp_dl_delta` | 3,547e-04 | 3,417e-04 | 1.433,84 | — | 1.433,8364 | 1,890% |
| 11 | `prb_total` | 5,405e-04 | 5,156e-04 | 945,92 | — | 945,9205 | 1,247% |
| 12 | `ul_persistence` | 4,020e-03 | 3,077e-03 | 140,88 | — | 140,8830 | 0,186% |
| 13 | `thp_ul_burst_index` | 1,170e-02 | 7,604e-03 | 51,80 | — | 51,8048 | 0,068% |
| 14 | `ul_efficiency` | 9,828e-03 | 9,700e-03 | 51,21 | — | 51,2070 | 0,067% |
| 15 | `prb_dl_burst_index` | 1,074e-02 | 1,021e-02 | 47,72 | — | 47,7231 | 0,063% |
| 16 | `prb_ul_burst_index` | 1,604e-02 | 1,217e-02 | 35,44 | — | 35,4428 | 0,047% |
| 17 | `thp_dl_burst_index` | 1,926e-02 | 1,132e-02 | 32,70 | — | 32,7015 | 0,043% |
| 18 | `prb_direction` | 2,324e-02 | 1,917e-02 | 23,58 | — | 23,5809 | 0,031% |
| 19 | `traffic_direction` | 5,430e-02 | 1,913e-02 | 13,62 | — | 13,6190 | 0,018% |

---

## 5. Contoh pemakaian bobot pada satu window

Window pertama berkas serangan, model LSTM:

$$S_0 = \frac{\sum_j w_j e_{0,j}}{\sum_j w_j} = \frac{50{,}586411}{78.739{,}85} = \mathbf{0{,}00064245}$$

Cocok persis dengan keluaran `weighted_score()`. Sebagai pembanding, rata-rata
**tak** berbobot window yang sama adalah $\bar{e}_0 = 0{,}02753693$ — **42,86×**
lebih besar. Itu murni artefak penskalaan bobot, bukan rekonstruksi yang lebih
baik, dan itulah sebabnya nilai ambang antar skema tidak boleh dibandingkan
langsung tanpa menyebut persentilnya.

Lima fitur penyumbang skor terbesar pada window itu:

| Model | Penyumbang terbesar |
|---|---|
| LSTM | `prb_usage_ul_ratio` 11,4% · `prb_usage_dl_ratio` 10,7% · `prb_total` 7,9% · `thp_total_kbps` 7,4% · `prb_ul_delta` 7,4% |
| GRU | `prb_usage_dl_ratio` 17,6% · `prb_total` 9,4% · `prb_dl_burst_index` 8,4% · `prb_usage_ul_ratio` 8,0% · `thp_total_kbps` 8,0% |

Perhatikan bahwa penyumbang skor **tidak sama** dengan pemilik bobot terbesar:
kontribusi = $w_j \times e_{n,j}$, jadi fitur berbobot besar yang residualnya kecil
pada window itu tetap menyumbang sedikit. Bobot menentukan *kepekaan*, bukan
kontribusi aktual.

---

## 6. Pengamatan yang perlu masuk naskah

**Cap menciptakan bobot kembar.** Keempat fitur ber-cap pada LSTM mendapat nilai
**identik** 14.864,5410, sehingga peringkat 1–4 tidak dapat dibedakan satu sama
lain. Jangan tulis "`thp_ul_delta` adalah fitur terpenting" — setelah cap, ia
setara dengan tiga fitur lain. Urutan $r_j$ mentah (73.448 > 54.421 > 39.231 >
15.750) tidak lagi tercermin di $w_j$.

**Dominasi kelompok uplink.** Lima fitur teratas keduanya berbasis uplink
(`thp_ul_kbps`, `thp_ul_delta`, `prb_ul_delta`, `prb_ul_roll_mean`,
`prb_usage_ul_ratio`) dan bersama-sama memikul **85,8%** bobot pada LSTM serta
**83,0%** pada GRU. Konsisten dengan tiga dari empat kelas serangan yang bersifat
uplink-heavy, dan sepenuhnya diturunkan dari data benign.

**Keempat fitur burst index hanya 0,162% (LSTM) dan 0,221% (GRU)** dari total
bobot skoring. Ini harus disebut apa adanya di naskah — jangan mengklaim fitur
burst mendominasi skor anomali. Peran utamanya ada di **lapisan pelatihan**, di
mana loss uniform memberi keempatnya bobot setara $4/19 = 21{,}05\%$.

**LSTM dan GRU menghasilkan bobot berbeda.** Bobot diturunkan dari residual
masing-masing model, jadi tidak dapat dipertukarkan. Perbedaan paling menonjol:
`prb_ul_delta` kena cap pada LSTM tetapi tidak pada GRU (12.271,73 < 14.338,36),
dan `prb_dl_burst_index` menempati peringkat 15 pada GRU tetapi 16 pada LSTM.

**Bobot ini bukan *feature importance* model.** Ia adalah kebalikan skala residual
benign — ukuran seberapa dapat dipercaya sebuah fitur sebagai indikator, bukan
seberapa besar kontribusinya terhadap deteksi serangan. Menyebutnya "feature
importance" akan mengundang salah tafsir yang sama seperti figur Scheme A lama.

## 7. Reproduksi

```bash
./venv/bin/python3 eval_opsi_b.py          # bobot → results/opsi_b/opsi_b.json
./venv/bin/python3 verify_scoring_math.py  # assert penurunan manual == library
```

`verify_scoring_math.py` menurunkan ulang $m_j$, $d_j$, $r_j$, $c$, dan $w_j$
langsung dari rumus — tanpa memanggil `benign_calibrated_weights()` — lalu
membandingkannya dengan keluaran library. Keluar dengan kode 1 bila meleset.
