# Opsi B — tabel metrik hasil run

Ringkasan angka saja. Justifikasi, batas validitas, dan pembahasan ada di
[opsi_b_recalibration_results.md](opsi_b_recalibration_results.md).

**Tanggal run:** 30 Juli 2026 · **Seed:** 42 · **Sumber:** `results/opsi_b/opsi_b.json`

## Konfigurasi yang dievaluasi

| Aspek | Nilai |
|---|---|
| Bobot loss pelatihan | **uniform** — semua bobot fitur = 1 |
| Model LSTM | `models/ablation_loss/lstm_ue_lossuniform.pt` (best epoch 89) |
| Model GRU | `models/ablation_loss/gru_ue_lossuniform.pt` (best epoch 85) |
| Skoring anomali | **benign-calibrated**, `w_j = 1/(median(e_j)+MAD(e_j)+ε)` di-cap `10 × median(w)` |
| Sumber bobot skoring | residual benign `dataset_validation_ue_juni.csv` saja |
| Bobot Scheme A (attack-informed) | **tidak dipakai sama sekali** |
| Set kalibrasi ambang | `csv/dataset_validation_ue_juni.csv` (1772 window, seluruhnya benign) |
| Set pengujian | `csv/dataset_attack_ue_juni.csv` (7959 window = 5723 benign + 2236 serangan) |
| `seq_len` | 30, window dibentuk per-RNTI |

> **Catatan istilah.** "Uniform" di sini berarti **bobot loss pelatihan**, bukan
> skoring. Skoringnya terbobot benign. Jangan tulis "uniform" telanjang di naskah —
> pada `scoring_comparison_results.md` istilah itu berarti *skoring* tanpa bobot,
> konfigurasi kolaps (GRU UL Flood 28,40%) yang justru tidak dipilih. Gunakan
> frasa **"pelatihan MSE seragam + skoring terbobot benign"**.

## Terminologi — "ablasi" atau "uniform"?

Keduanya, dan istilahnya bertingkat. **Ablasi** adalah nama *eksperimennya*;
**uniform** adalah nama *lengan yang dipilih* dari eksperimen itu.

```
Ablasi bobot loss (eksperimen)  →  2 lengan  →  lengan "uniform" diadopsi
   docs/loss_ablation_results.md      uniform      → dipakai di Opsi B
                                      benign       → tidak dipakai
```

Itulah sebabnya berkas modelnya berada di `models/ablation_loss/` tetapi bernama
`*_lossuniform.pt`: folder menunjukkan **asal** (hasil studi ablasi), nama berkas
menunjukkan **lengan** yang dipakai. Keduanya konsisten, bukan dua hal berbeda.

Ada **dua ablasi berbeda** yang muncul dalam pembahasan proyek ini — jangan
tertukar:

| Ablasi | Yang divariasikan | Status |
|---|---|---|
| **Ablasi bobot loss** | bobot loss pelatihan: uniform vs benign | ✅ **dijalankan** — `loss_ablation_results.md`, lengan uniform dipilih |
| Ablasi fitur (19 vs 15) | membuang 4 fitur burst index dari input | ❌ **tidak dijalankan** — dinilai tidak diperlukan (lihat catatan di bawah) |

Ablasi fitur tidak dijalankan karena dua alasan: pangsa bobot skoring keempat
fitur burst hanya 0,161% (LSTM) dan 0,221% (GRU) dari total, sehingga pengaruh
marginalnya pada keputusan ML sudah terbaca tanpa pelatihan ulang; dan skema 19
fitur sudah dibekukan serta dideploy ke ONNX dan `sec_ids_ue.c`, sehingga
kesimpulan "buang" pun tidak dapat ditindaklanjuti tanpa membangun ulang seluruh
pipeline. Justifikasi keempat fitur burst bersandar pada rasional desain,
ortogonalitas (maks $r = 0{,}37$ terhadap 15 fitur basis, sementara antar fitur
basis mencapai $r = 0{,}96$), dan bobot setara 21,05% di loss pelatihan uniform.

## Proses perhitungan — pakai weighted MSE atau tidak?

Dua tahap, dan **hanya tahap kedua yang berbobot**:

| Tahap | Pembobotan | Rumus efektif |
|---|---|---|
| Pelatihan AE | **tidak** — semua $w_j = 1$ | MSE biasa |
| Skoring anomali | **ya** — weighted MSE benign-calibrated | $S = \sum_j w_j e_j / \sum_j w_j$ |

Notasi: $F = 19$ fitur, $T = 30$ timestep per window, $B$ ukuran batch,
$x$ nilai ternormalisasi (MinMaxScaler), $\hat{x}$ rekonstruksi AE.

### Tahap 1 — Loss pelatihan (tanpa pembobotan)

Implementasi di `train_lstm_ue.py` / `train_gru_ue.py` (`weighted_mse`):

$$\mathcal{L} = \frac{1}{B \cdot T \cdot F}\sum_{b=1}^{B}\sum_{t=1}^{T}\sum_{j=1}^{F} w_j\left(\hat{x}_{b,t,j} - x_{b,t,j}\right)^2$$

Untuk lengan uniform, `load_loss_weights("uniform", …)` mengembalikan vektor
satuan, sehingga $w_j = 1\;\forall j$ dan rumus di atas menjadi **MSE biasa**.
Rata-rata diambil pada ketiga sumbu sekaligus, jadi setiap fitur menyumbang tepat
$1/19 = 5{,}26\%$ ke gradien, dan keempat fitur burst bersama $4/19 = 21{,}05\%$.

### Tahap 2 — Skor anomali (weighted MSE)

**Langkah A — residual per fitur.** Rata-rata hanya pada sumbu waktu; sumbu fitur
tetap terpisah, sehingga hasilnya vektor, belum skalar:

$$e_{n,j} = \frac{1}{T}\sum_{t=1}^{T}\left(\hat{x}_{n,t,j} - x_{n,t,j}\right)^2, \qquad \mathbf{e}_n \in \mathbb{R}^{19}$$

**Langkah B — bobot dari residual benign validasi saja** (tanpa label serangan,
sehingga sifat *one-class* pipeline tidak berubah):

$$m_j = \operatorname*{median}_{n \in \text{Val}} e_{n,j}, \qquad d_j = \operatorname*{median}_{n \in \text{Val}} \left| e_{n,j} - m_j \right| \;(\text{MAD})$$

$$r_j = \frac{1}{m_j + d_j + \varepsilon}, \quad \varepsilon = 10^{-6}, \qquad c = 10 \cdot \operatorname*{median}_j (r_j), \qquad w_j = \min(r_j,\, c)$$

Makna: $w_j$ besar bila residual benign fitur $j$ **kecil dan stabil** —
penyimpangan sedikit pun di fitur itu bermakna. Cap $c$ mencegah satu fitur
ber-residual mendekati nol mendominasi skor. Terukur: $c = 14{.}864{,}5$ (LSTM,
4/19 fitur kena cap) dan $c = 14{.}338{,}4$ (GRU, 3/19 kena cap).

**Langkah C — rata-rata terbobot antar fitur:**

$$S_n = \frac{\sum_{j=1}^{19} w_j\, e_{n,j}}{\sum_{j=1}^{19} w_j}$$

**Keputusan akhir:**

$$\text{Hybrid}_n = \text{rule}_n \;\lor\; \left(S_n > Th\right), \qquad Th = \min\left\{Th : \mathrm{FPR}_{\text{Hybrid}}(\text{Val}) \le \text{target}\right\}$$

### Catatan skala ambang

Untuk window pertama berkas serangan pada LSTM: $S = 0{,}00064245$ sedangkan
rata-rata tak berbobot $\bar{e} = 0{,}02753693$ — **42,86× lebih besar** (GRU:
36,41×). Perbedaan skala ambang antar skema pembobotan adalah **artefak
penskalaan bobot**, bukan bukti rekonstruksi yang lebih baik. Jangan bandingkan
nilai ambang lintas skema tanpa menyebut persentilnya.

### Bukti bahwa pembobotan skoring menentukan

Model uniform yang sama, prosedur kalibrasi sama, target FPR(Val) ≤ 5% sama —
hanya bobot skoringnya diganti:

| Model | Skoring | Th | Recall | F1 | FPR(Attack) | FPR(Val) | AUC | **RoQ** |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| LSTM | benign | 0,015817 | 98,43% | 96,22% | 2,41% | 4,97% | 0,9902 | **98,79%** ✅ |
| LSTM | uniform | 0,034145 | 89,53% | 91,75% | 2,20% | 4,97% | 0,9682 | **72,39%** ❌ |
| GRU | benign | 0,014309 | 98,08% | 96,46% | 2,06% | 4,97% | 0,9895 | **98,26%** ✅ |
| GRU | uniform | 0,027905 | 91,06% | 92,38% | 2,38% | 4,97% | 0,9725 | **77,08%** ❌ |

Bila skoring juga dibuat uniform, **RoQ jatuh di bawah target 85%** dan kriteria
penerimaan gagal. Tiga kelas lain hampir tak terpengaruh karena lantai aturannya
sudah tinggi; RoQ punya lantai aturan terendah (65,28%) sehingga paling
bergantung pada ML — dan 72,39% hanya sedikit di atas lantai itu, artinya
kontribusi ML nyaris hilang. Jadi pembobotan skoring **tidak dapat dihapus**;
itulah yang membuat RoQ terdeteksi.

Reproduksi seluruh angka di bagian ini:

```bash
./venv/bin/python3 verify_scoring_math.py
```

Skrip itu memeriksa ulang penurunan bobot dari rumus, memverifikasi
$\sum w e / \sum w$ terhadap implementasi library, dan menghitung tabel
benign-vs-uniform di atas. Berhenti dengan kode keluar 1 bila ada yang meleset.

## 1. Ambang batas hasil kalibrasi

Target utama (**B-utama**): Th terendah sehingga Hybrid FPR(Val) ≤ 5,0%.

| Model | Th | Persentil (val benign) | Persentil (attack benign) |
|---|---:|---:|---:|
| LSTM | 0,015817 | P97,29 | P97,73 |
| GRU | 0,014309 | P97,01 | P98,08 |

## 2. Metrik global — B-utama

| Model | Konfigurasi | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | AUC |
|---|---|---:|---:|---:|---:|---:|---:|
| LSTM | Rule-Only | 85,78% | 97,51% | 91,27% | 0,86% | 2,93% | N/A |
| LSTM | ML-Only | 97,09% | 94,35% | 95,70% | 2,27% | 2,71% | 0,9902 |
| LSTM | **Hybrid** | **98,43%** | **94,10%** | **96,22%** | **2,41%** | **4,97%** | N/A |
| GRU | Rule-Only | 85,78% | 97,51% | 91,27% | 0,86% | 2,93% | N/A |
| GRU | ML-Only | 95,66% | 95,11% | 95,38% | 1,92% | 2,99% | 0,9895 |
| GRU | **Hybrid** | **98,08%** | **94,89%** | **96,46%** | **2,06%** | **4,97%** | N/A |

FPR(Attack) adalah **hasil pengukuran di luar sampel**, bukan setelan — berkas
serangan tidak pernah tersentuh pemilihan Th. Inilah angka yang memikul klaim
kepatuhan constraint, dan **seluruh selang kepercayaannya berada di bawah 5%**
(lihat §6 dan §9), sehingga kepatuhan tahan terhadap ketidakpastian sampling.

**AUC dan himpunan negatifnya.** AUC bersifat *threshold-independent*, tetapi
bergantung pada window mana yang menjadi negatif. Nilai di tabel memakai
**window benign berkas serangan** sebagai negatif (n = 5.723), yaitu kurva yang
sumbu-x-nya FPR(Attack) — konsisten dengan titik operasi yang dilaporkan dan dengan
figur ROC. Bila negatifnya diganti dataset validasi (n = 1.772), AUC menjadi
**0,9931** (LSTM) dan **0,9909** (GRU); itu kurva lintas-sesi yang sumbu-x-nya
FPR(Val) — besaran berbeda, bukan koreksi. Keduanya dicatat di JSON pada field
`metadata.auc`.

## 3. Recall per kelas serangan — B-utama

| Model | Konfigurasi | UL Flood | DL Flood | Burst | RoQ |
|---|---|---:|---:|---:|---:|
| — | Rule-Only | 97,18% | 96,76% | 95,03% | 65,28% |
| LSTM | ML-Only | 97,65% | 89,68% | 99,03% | 98,26% |
| LSTM | **Hybrid** | **98,12%** | **96,76%** | **99,03%** | **98,79%** |
| GRU | ML-Only | 96,24% | 87,61% | 98,21% | 96,51% |
| GRU | **Hybrid** | **97,65%** | **96,76%** | **98,76%** | **98,26%** |

Seluruh kelas melewati target 85% dengan margin minimal 11,76 poin. DL Flood Hybrid
tepat 96,76% — sama dengan lantai aturan R2, dan tidak berubah pada Th mana pun.

## 4. Matriks konfusi — B-utama (berkas serangan)

| Model | Konfigurasi | TN | FP | FN | TP |
|---|---|---:|---:|---:|---:|
| — | Rule-Only | 5.674 | 49 | 318 | 1.918 |
| LSTM | ML-Only | 5.593 | 130 | 65 | 2.171 |
| LSTM | Hybrid | 5.585 | 138 | 35 | 2.201 |
| GRU | ML-Only | 5.613 | 110 | 97 | 2.139 |
| GRU | Hybrid | 5.605 | 118 | 43 | 2.193 |

Baris Normal berjumlah 5.723 dan baris Anomaly berjumlah 2.236 di setiap konfigurasi.

## 5. Frontier sensitivitas — target FPR(Val) 5,0% / 4,5% / 4,0%

Seluruh nilai adalah konfigurasi Hybrid.

| Model | Target FPR(Val) | Th | P(val) | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | RoQ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LSTM | **5,0%** | 0,015817 | P97,29 | 98,43% | 94,10% | 96,22% | 2,41% | 4,97% | 98,79% |
| LSTM | 4,5% | 0,020268 | P97,86 | 97,85% | 95,17% | 96,49% | 1,94% | 4,46% | 97,45% |
| LSTM | 4,0% | 0,025787 | P98,48 | 96,02% | 95,98% | 96,00% | 1,57% | 3,95% | 92,49% |
| GRU | **5,0%** | 0,014309 | P97,01 | 98,08% | 94,89% | 96,46% | 2,06% | 4,97% | 98,26% |
| GRU | 4,5% | 0,018915 | P97,74 | 97,00% | 95,51% | 96,25% | 1,78% | 4,46% | 95,44% |
| GRU | 4,0% | 0,022145 | P98,59 | 95,93% | 95,76% | 95,84% | 1,66% | 3,95% | 92,36% |

Recall per kelas pada target ketat (4,0%): LSTM 97,42 / 96,76 / 98,48 / 92,49;
GRU 97,18 / 96,76 / 98,48 / 92,36. Semua tetap di atas 85%.

## 6. Ketidakpastian setiap angka FPR

Selang kepercayaan 95% metode **Wilson score**. *Episode* = deretan window
berurutan yang menyala, dihitung per-UE. *Alert* = yang lolos cooldown 30 detik
(`ALERT_COOLDOWN_MS`).

**Definisi waktu terpantau (satu definisi untuk kedua set):** cadence window
per-UE tepat 1 Hz, jadi paparan = **jumlah window / 3600**, diterapkan pada
himpunan window yang persis dipakai menghitung FPR itu — seluruh 1.772 window
validasi, dan 5.723 window `label==0` berkas serangan. Alarm palsu hanya dapat
terjadi saat trafik benign, sehingga waktu-benign adalah penyebut yang benar;
window serangan bukan paparan alarm palsu.

- Validasi: 1.772 / 3600 = **0,4922 UE-jam**
- Serangan (window benign): 5.723 / 3600 = **1,5897 UE-jam**

| Model | Konfigurasi | Set | FPR | CI 95% | Window | Episode | Alert | Alert/UE-jam |
|---|---|---|---:|---|---:|---:|---:|---:|
| — | Rule-Only | Attack | 0,86% | [0,65% ; 1,13%] | 49 / 5.723 | 8 | 6 | 3,77 |
| — | Rule-Only | Val | 2,93% | [2,24% ; 3,83%] | 52 / 1.772 | 6 | 5 | 10,16 |
| LSTM | ML-Only | Attack | 2,27% | [1,92% ; 2,69%] | 130 / 5.723 | 10 | 8 | 5,03 |
| LSTM | ML-Only | Val | 2,71% | [2,05% ; 3,57%] | 48 / 1.772 | 4 | 3 | 6,09 |
| LSTM | Hybrid | Attack | 2,41% | [2,04% ; 2,84%] | 138 / 5.723 | 11 | 9 | 5,66 |
| LSTM | Hybrid | Val | 4,97% | [4,05% ; 6,08%] | 88 / 1.772 | 8 | 5 | 10,16 |
| GRU | ML-Only | Attack | 1,92% | [1,60% ; 2,31%] | 110 / 5.723 | 8 | 7 | 4,40 |
| GRU | ML-Only | Val | 2,99% | [2,29% ; 3,89%] | 53 / 1.772 | 2 | 3 | 6,09 |
| GRU | Hybrid | Attack | 2,06% | [1,72% ; 2,46%] | 118 / 5.723 | 9 | 8 | 5,03 |
| GRU | Hybrid | Val | 4,97% | [4,05% ; 6,08%] | 88 / 1.772 | 5 | 5 | 10,16 |

> **Alert dapat melebihi jumlah episode.** Cooldown 30 s berjalan **per-UE secara
> menerus**, bukan per-episode, sehingga satu episode yang lebih panjang dari 30 s
> menerbitkan lebih dari satu alert. Contoh terverifikasi: baris `GRU · ML-Only ·
> Val` punya 2 episode berdurasi **31,0 s** dan **20,0 s** — yang 31,0 s melewati
> cooldown sehingga menghasilkan 2 alert, total 3 alert dari 2 episode. Sebaliknya,
> episode yang berdekatan dapat tertekan menjadi satu alert. Jadi kolom Episode dan
> Alert tidak berelasi monoton, dan itu memang perilaku yang benar. Durasi setiap
> episode tersimpan di `results/opsi_b/opsi_b.json` (`episode_durations_s`).

Lebar CI FPR(Val) ± 1 poin lebih besar daripada seluruh penyesuaian yang dilakukan
run ini — **jangan kutip FPR tanpa intervalnya.**

## 7. Latensi (Th B-utama)

Sumber: `eval_figures/loss_uniform/eval_per_ue_v2_20260730_194946.json`, model dan
ambang identik dengan tabel di atas.

### 7a. Latensi deteksi — median, rentang, dan n

> ⚠️ **Setiap sel bersandar pada n = 2 atau 3 segmen serangan.** Dispersinya besar
> (beberapa kelas merentang 0–6 s), jadi **jangan kutip mean dua desimal** seolah
> estimasi presisi. Laporkan median + rentang + n. Ini kegagalan presisi palsu yang
> sama seperti pada FPR, dan berlaku sama kerasnya di sini.

| Konfigurasi | Kelas | Median | Rentang [min ; maks] | n |
|---|---|---:|---|---:|
| Rule-Only | UL Flood | 6,00 s | [0,00 ; 6,00] | 3 |
| Rule-Only | DL Flood | 5,00 s | [0,00 ; 6,00] | 3 |
| Rule-Only | Burst | 5,50 s | [5,00 ; 6,00] | 2 |
| Rule-Only | RoQ | 5,50 s | [5,00 ; 6,00] | 2 |
| LSTM ML-Only | UL Flood | 4,00 s | [0,00 ; 4,00] | 3 |
| LSTM ML-Only | DL Flood | 16,00 s | [0,00 ; 18,00] | 3 |
| LSTM ML-Only | Burst | 3,50 s | [3,00 ; 4,00] | 2 |
| LSTM ML-Only | RoQ | 3,50 s | [3,00 ; 4,00] | 2 |
| **LSTM Hybrid** | UL Flood | **4,00 s** | [0,00 ; 4,00] | 3 |
| **LSTM Hybrid** | DL Flood | **5,00 s** | [0,00 ; 6,00] | 3 |
| **LSTM Hybrid** | Burst | **3,50 s** | [3,00 ; 4,00] | 2 |
| **LSTM Hybrid** | RoQ | **3,50 s** | [3,00 ; 4,00] | 2 |
| GRU ML-Only | UL Flood | 4,00 s | [0,00 ; 9,00] | 3 |
| GRU ML-Only | DL Flood | 21,00 s | [0,00 ; 21,03] | 3 |
| GRU ML-Only | Burst | 6,50 s | [3,00 ; 10,00] | 2 |
| GRU ML-Only | RoQ | 12,00 s | [12,00 ; 12,00] | 2 |
| **GRU Hybrid** | UL Flood | **4,00 s** | [0,00 ; 6,00] | 3 |
| **GRU Hybrid** | DL Flood | **5,00 s** | [0,00 ; 6,00] | 3 |
| **GRU Hybrid** | Burst | **4,50 s** | [3,00 ; 6,00] | 2 |
| **GRU Hybrid** | RoQ | **5,50 s** | [5,00 ; 6,00] | 2 |

Yang **tahan** terhadap ukuran sampel kecil dan boleh diklaim: Hybrid tidak pernah
lebih lambat daripada komponen penyusunnya, dan pada DL Flood ia memangkas latensi
ML secara besar (LSTM median 16,00 s → 5,00 s; GRU 21,00 s → 5,00 s) karena aturan
R2 menyala lebih dulu. Selisih sub-detik antar kelas atau antar arsitektur **tidak**
boleh diklaim.

Latensi mitigasi = latensi deteksi + tepat 1,0 s di setiap baris. Ia **turunan by
construction**, bukan pengukuran independen, sehingga tidak menambah bukti apa pun
di luar tabel di atas.

> Catatan: Hybrid dapat memiliki latensi lebih rendah daripada **kedua** komponennya
> (GRU UL Flood median 4,00 s vs Rule 6,00 s dan ML 4,00 s) karena latensi adalah
> rata-rata/median dari **minimum per-segmen**, dan $E[\min(X,Y)] \le \min(E[X], E[Y])$
> selalu berlaku. Ini benar secara matematis, bukan bug.

### 7b. Latensi inferensi

| Model | Mean | P95 |
|---|---:|---:|
| LSTM-AE | 0,142 ms | 0,350 ms |
| GRU-AE | 0,458 ms | 0,721 ms |

Ini pengukuran evaluator **Python/PyTorch** pada mesin evaluasi. Untuk klaim
constraint < 0,5 ms di naskah, kutip pengukuran **C-native xApp** (LSTM 0,069 ms,
GRU 0,166 ms) — platform berbeda, keduanya sah untuk klaim yang berbeda, dan
jangan ditukar.

## 6a. Kemurnian label window

Windowing dilakukan **per-RNTI**, sama seperti yang dipakai evaluasi:

| Label window | n | Campuran | % campuran |
|---|---:|---:|---:|
| benign | 5.723 | 203 | 3,5% |
| UL Flood | 426 | 58 | 13,6% |
| DL Flood | 339 | 58 | 17,1% |
| Burst | 725 | 58 | 8,0% |
| RoQ | 746 | 58 | 7,8% |
| **Total** | **7.959** | **435** | **5,47%** |

**94,53% window murni satu label.** Yang campuran pun tepat window batas transisi:
**58 = 2 episode × 29 window** untuk setiap kelas serangan — pola yang konsisten dan
terprediksi dari `seq_len = 30`, bukan derau pelabelan.

> Angka `mixed_pct = 60,12%` pada JSON run lama salah: ia dihitung dengan windowing
> **global** pada array label tergabung. Karena 8 UE terjalin dalam waktu, window
> global mencampur label antar-UE yang tidak pernah berada dalam satu window di
> pipeline sebenarnya. Sudah diperbaiki di `count_mixed_windows_by_rnti()`.

## 8. Sanity check Rule-Only — lulus

Rule-Only tidak bergantung pada Th, jadi wajib reproduksi persis. `eval_opsi_b.py`
memeriksa ini dan berhenti bila meleset.

| Metrik Rule-Only | Wajib | Terukur |
|---|---:|---:|
| Recall global | 85,78% | **85,78%** |
| FPR(Attack) | 0,86% | **0,86%** |
| FPR(Val) | 2,93% | **2,93%** |
| Recall UL Flood | 97,18% | **97,18%** |
| Recall DL Flood | 96,76% | **96,76%** |
| Recall Burst | 95,03% | **95,03%** |
| Recall RoQ | 65,28% | **65,28%** |
| Precision | 97,51% | **97,51%** |

## 9. Kriteria penerimaan

| Kriteria | Hasil |
|---|---|
| **FPR(Attack) ≤ 5% — klaim kepatuhan utama, di luar sampel** | ✅ **LSTM 2,41% CI [2,04% ; 2,84%] · GRU 2,06% CI [1,72% ; 2,46%] — seluruh selang berada di bawah anggaran 5%** |
| FPR(Val) ≤ 5,0% | ✅ 4,97% kedua model — dipenuhi *by construction* kalibrasi; batas atas CI **6,08%** |
| F1 ≥ 90% | ✅ 96,22% LSTM / 96,46% GRU |
| Recall ≥ 85% setiap kelas | ✅ minimum 96,76% (DL Flood) |
| Sanity check Rule-Only cocok persis | ✅ 8 dari 8 baris |
| Th + persentil val-benign & attack-benign | ✅ tabel §1 |
| Setiap FPR disertai CI, episode, laju alert | ✅ tabel §6 |
| Setiap FPR menyebut setnya | ✅ `FPR(Attack)` / `FPR(Val)` |

## 10. Figur pendamping

`eval_figures/final_hybrid/` (PNG + PDF vektor): matriks konfusi 3 panel per model,
kurva ROC dengan titik operasi Rule-Only dan Th baru, bar recall per kelas per
model, dan grafik frontier. `eval_figures/loss_uniform/` memuat figur pelengkap
pada ambang yang sama: distribusi reconstruction error, latensi, dan kurva
pembelajaran.
