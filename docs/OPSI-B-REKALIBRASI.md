# Opsi B — Rekalibrasi Ambang pada Dataset Validasi

**Tujuan:** menutup jalur kebocoran terakhir (ambang batas Th) sekaligus menegakkan constraint
Bab II FPR ≤ 5%, dalam satu kali *run* evaluasi.
**Dijalankan di:** mesin yang memuat `evaluate_scoring_comparison.py`, `src/detection/scoring.py`,
dan bobot model terlatih. **Tidak bisa** dijalankan dari `/Users/rizqiradiyatama/Documents/TA/`.
**Basis analisis:** `Analisis_Kebocoran_Threshold_dan_Ambang_Aturan.md` §1.4/§1.4a,
`loss_ablation_results.md`, verifikasi `verify_rule_thresholds.py` (30 Jul 2026).
**Tanggal:** 30 Juli 2026

---

> **Prompt eksekusi siap tempel:** `plans/PROMPT-RIC-REKALIBRASI.md`. Dokumen ini adalah
> justifikasi dan kaskade naskahnya; prompt itu adalah instruksi teknisnya.

## 1. Mengapa ini dilakukan

Kondisi sekarang, tercatat di *Validity boundary* `loss_ablation_results.md`: Th dipilih dari window
`label==0` pada **berkas serangan** agar FPR(Attack) = 2,99%. Akibatnya:

| Metrik | Status sekarang | Status setelah Opsi B |
|---|---|---|
| Recall global & per kelas | ✅ bersih (tak pernah lihat label serangan) | ✅ tetap bersih |
| AUC | ✅ bersih (*threshold-independent*) | ✅ tetap bersih |
| FPR(Attack) 2,99% | ❌ **setelan**, bukan pengukuran | ✅ **pengukuran** sungguhan |
| Precision 92,81% | ⚠️ mewarisi optimisme | ✅ bersih |
| **F1 95,67%** | ⚠️ **mewarisi optimisme via Precision** | ✅ bersih |
| FPR(Val) 5,76% | ⚠️ di atas 5%, tapi CI 95% [4,68%; 6,84%] **memuat** 5% | ✅ ≤5% oleh konstruksi kalibrasi |

**Kalibrasi memindahkan sirkularitas, tidak menghapusnya — dan itu memang tujuannya.** Setelah
Opsi B, FPR(Val) menjadi besaran yang **disetel** (sah, itu fungsi set kalibrasi) dan
**FPR(Attack) menjadi pengukuran di luar sampel**. Karena itu **klaim kepatuhan constraint
dipikul FPR(Attack)**, bukan FPR(Val). Konsekuensinya Bab II baris 638 harus disunting — lihat §7.

### 1a. Kalibrasi soal *set*, bukan soal *menyetel*

Menetapkan ambang agar FPR pas di bawah anggaran adalah prosedur Neyman–Pearson standar, dan Bab
II baris 638 memang sudah menyatakannya ("ambang batas keputusan optimal ... berdasarkan analisis
kurva ROC"). Yang bermasalah bukan *bahwa* ambang disetel, melainkan *di set mana* ia disetel.
Aturannya: **data yang dipakai menyetel ambang tidak boleh dipakai membuktikan ambang itu
bekerja.**

### 1b. Besar dampak sirkularitas terhadap F1 — terbatas, dan target tetap terlewati

Dengan Recall tetap 98,70% (bersih, tak pernah menyentuh label serangan), substitusi FPR yang
lebih pesimistik memberi:

| Substitusi FPR | Precision | F1 | Target ≥90% |
|---|---|---|---|
| 2,99% (titik operasi terkalibrasi) | 92,80% | 95,66% | ✅ |
| 5,00% (batas constraint) | 88,52% | 93,33% | ✅ |
| 5,76% (= FPR Val) | 87,00% | 92,48% | ✅ |
| 6,43% (terburuk di seluruh tabel) | 85,71% | 91,75% | ✅ |

Batas atas defisit F1 akibat sirkularitas **3,19 poin**, dan F1 ≥ 90% terlewati di setiap
skenario. **Subobjektif 3 tidak bergantung pada Opsi B.** Opsi B mengubah status FPR dari "belum
terverifikasi" menjadi "terverifikasi di luar sampel" — itu nilainya, bukan menyelamatkan F1.

### 1c. Presisi: yang diperdebatkan lebih kecil dari yang tidak diketahui

Penyesuaian Opsi B menggeser FPR(Val) sejauh **0,76 poin**, sementara CI 95% paling optimis saja
sudah **2,17 poin**. Dan CI itu pun terlalu yakin: window bertumpang-tindih 29/30, dan 52 FP
Rule-Only pada validasi berasal dari hanya **6 episode terpisah**. Informasi independen yang
sebenarnya tersedia: 60 window non-overlap, 6 episode, **1 sesi benign, 1 UE, 30 menit**.

Karena itu setiap angka FPR di naskah **wajib** disertai interval dan jumlah episode. Menyajikan
FPR berdesimal dua tanpa interval adalah presisi palsu.

---

## 2. Perubahan kode

Satu fungsi. Di `calibrate_hybrid_threshold` (`evaluate_scoring_comparison.py`), ganti sumber
window kalibrasi:

```
SEKARANG : window label==0 dari dataset_attack_ue_juni.csv   → FPR(Attack) dipaksa 2,99%
MENJADI  : seluruh window dari dataset_validation_ue_juni.csv → FPR(Val) dipaksa ≤ target
```

Prosedurnya identik, hanya himpunan negatifnya berpindah: cari **Th terendah** sehingga keputusan
Hybrid (`rule OR S > Th`) menyala pada ≤ target dari window validasi. Tetap tanpa label serangan,
jadi sifat *one-class* pipeline tidak berubah.

Setelah itu FPR(Attack) **dihitung, tidak dipaksa** — laporkan apa pun hasilnya.

## 3. Target kalibrasi

Jalankan tiga titik agar tersedia *frontier*, bukan satu angka telanjang:

| Run | Target FPR(Val) | Kuota window (dari 1.772) | Sisa untuk ML di luar aturan |
|---|---|---|---|
| **B-utama** | **≤ 5,0%** | 88 | 36 |
| B-sens-1 | ≤ 4,5% | 79 | 27 |
| B-sens-2 | ≤ 4,0% | 70 | 18 |

Lapor B-utama sebagai konfigurasi final; B-sens-1/2 dipakai untuk menunjukkan biaya recall bersifat
*graceful* — ini mendahului pertanyaan "kenapa persis 5%?" dan menyambung ke framing *Pareto
frontier* yang sudah dipakai di BAB4_LSTM.

> **Jangan targetkan 3%.** Lantai aturan sudah 52 window (2,93%); pada 3% kuota total hanya 53
> window, ML tersisa 1. Frasa "target ideal 3%" di Buku TA baris 1652/1674 perlu dihapus — bukan
> karena gagal dicapai, tapi karena **secara struktural tidak dapat dicapai** dengan ambang R1–R5
> saat ini. Constraint yang mengikat di Bab II memang ≤5% (baris 638), jadi tidak ada yang hilang.

## 4. Prediksi hasil dan penilaian risiko

Dari verifikasi `verify_rule_thresholds.py`: Hybrid sekarang 102 window (5,76%), ML-Only 70 (3,95%),
tumpang tindih 20 → **ML menyumbang 50 window eksklusif**. Untuk ≤5% cukup potong **14** window
(28% FP eksklusif ML) — setara Th naik dari P96,05 ke **~P97,0** val-benign. Kenaikan ~1 poin
persentil saja.

**Lantai recall per kelas bila ML tidak menyumbang apa pun** (murni dari aturan, terverifikasi
bit-faithful terhadap `sec_ids_ue.c`):

| Kelas | Lantai aturan | Hybrid sekarang | Target | Ruang aman |
|---|---|---|---|---|
| UL Flood | 97,18% | 98,59% | 85% | ✅ aman apa pun Th |
| DL Flood | 96,76% | 96,76% | 85% | ✅ **tak terpengaruh Th** — ML nol kontribusi marginal |
| Burst | 95,03% | 99,17% | 85% | ✅ aman apa pun Th |
| **RoQ** | **65,28%** | **99,20%** | 85% | ⚠️ **satu-satunya kelas berisiko**, margin 14,2 poin |

Kesimpulan risiko: **hanya RoQ yang perlu dipantau.** Tiga kelas lain tidak dapat jatuh di bawah
target bahkan bila Th dinaikkan sampai ML mati total. RoQ harus tetap ≥ 85%, artinya boleh menyerap
kerugian hingga 14,2 poin. Dengan kenaikan Th ~1 persentil, ini sangat longgar.

Bonus yang diharapkan: FPR(Attack) turun di bawah 2,99% dan menjadi pengukuran sungguhan — besar
kemungkinan mendarat di kisaran 2,0–2,5%, sehingga bahkan melewati ambang 3% yang lama tanpa
sirkularitas.

## 5. Kriteria penerimaan

Run diterima bila **seluruh** baris terpenuhi:

- [ ] FPR(Val) ≤ 5,0% — constraint Bab II baris 638
- [ ] F1 ≥ 90% — Subobjektif 3, baris 611
- [ ] Recall ≥ 85% **untuk setiap** kelas serangan (UL/DL/Burst/RoQ) — baris 611
- [ ] FPR(Attack) dilaporkan sebagai hasil pengukuran, tanpa dipaksa ke nilai apa pun
- [ ] Rule-Only tetap reproduksi 85,78% recall / 0,86% FPR(Attack) / 2,93% FPR(Val) — *sanity check*
      pipeline; ketiganya sudah diverifikasi independen dan **tidak boleh berubah**
- [ ] Th baru dilaporkan beserta persentilnya pada val-benign **dan** attack-benign
- [ ] Setiap angka FPR disertai **CI 95%**, **jumlah episode alarm palsu terpisah**, dan laju alert
      per jam setelah cooldown 30 s (lihat §1c)
- [ ] Setiap penyebutan FPR menyatakan setnya secara eksplisit — `FPR(Attack)` / `FPR(Val)`

Bila RoQ jatuh di bawah 85% (sangat tidak diharapkan): turunkan target ke B-sens dan laporkan
*frontier*-nya; jangan diam-diam kembali mengalibrasi di berkas uji.

## 6. Keluaran yang harus dihasilkan

1. Tabel metrik global (Rule / ML-Only / Hybrid) × (LSTM / GRU), pada Th hasil kalibrasi validasi.
2. Tabel recall per kelas.
3. Tabel *frontier* tiga target FPR(Val).
4. **Regenerasi figur** — ini yang mengunci: `eval_figures/` *confusion matrix*, kurva ROC, dan bar
   per-kelas semuanya masih memuat angka Scheme A. Prosa baru + gambar lama = kontradiksi yang
   persis sama dengan yang sedang diperbaiki.
5. Perbarui *Validity boundary* di `loss_ablation_results.md`: jalur Th berpindah dari "terbuka"
   ke "tertutup", dan FPR(Attack) naik status menjadi estimasi generalisasi.

## 7. Setelah run selesai — kaskade naskah

Baru **setelah** §5 dan §6 tuntas, sentuh naskah. Yang berubah di
`Buku_TA_18122046_Rizqi_Radityatama_REVISI.md`:

| Lokasi | Sekarang (Scheme A) | Jadi |
|---|---|---|
| **Bab II baris 638** | "Pengukuran FPR pada ***benign validation dataset***" | **Pengukuran FPR pada berkas pengujian.** Alasan yang justru memperkuat: dataset validasi kini menjadi set kalibrasi ambang, sehingga mengukur kepatuhan di sana bersifat *in-sample*. Wajib disunting eksplisit, jangan dibiarkan senyap |
| Abstrak ID (baris 12) & EN (baris 24) | Recall 94,59% · F1 93,90% · FPR 2,69% · RoQ 87,13% | hasil run B-utama |
| Baris 1267 | Th = 0,023000 "satu-satunya ambang batas" | Th baru + ruang skor benign-calibrated |
| Tabel IV-9, IV-10 (baris 1652, 1674) | metrik global & per kelas | hasil run B-utama |
| Baris 1652/1674 | "target ideal 3%" | hapus — lihat §3 |
| Baris 1791 (Bab V butir 3) | Recall 94,59% · F1 93,90% · FPR 2,69% | hasil run B-utama |
| Baris 1799 (Bab V butir 6) | perbandingan GRU partner | angka GRU dari run yang sama |
| Gambar IV-8..IV-13 | figur Scheme A | figur regenerasi |

**Tiga perbaikan teks yang berlaku terlepas dari hasil run** (kerjakan lebih dulu, tidak menunggu
mesin):

1. **Baris 1275 — prioritas tertinggi.** Klaim "*secara kualitatif mengeliminasi risiko kebocoran
   pola (pattern leakage) atau kebocoran data (data leakage)*" adalah klaim positif ke arah yang
   berlawanan dengan fakta pembobotan Scheme A. Batasi cakupannya secara eksplisit ke
   *preprocessing*/MinMaxScaler dan pemisahan temporal, jangan biarkan terbaca sebagai klaim bebas
   kebocoran menyeluruh.
2. **Labeli FPR.** Setiap penyebutan FPR harus menyatakan setnya: "FPR pada berkas pengujian" vs
   "FPR pada dataset validasi". Ini menyelesaikan kontradiksi 2,69% (prosa) vs 6,26% (panel
   *confusion matrix* baris 1644) — keduanya benar untuk set masing-masing, hanya tak berlabel.
3. **Sebut sendiri bahwa berkas uji adalah ujian yang lebih lunak.** Memindahkan constraint ke
   berkas pengujian adalah penguatan dari sisi *independensi* (tak tersentuh kalibrasi) tetapi
   pelemahan dari sisi *kekerasan uji*: Rule-Only FPR 0,86% di sana vs 2,93% di validasi, dan
   median `thp_dl` pada window pemicu alarm 34 kbps vs 76.790 kbps — sesi validasi memuat
   speedtest, UE benign pada sesi serangan mayoritas *idle*. Tulis kedua angka berdampingan dan
   labeli: FPR(Attack) bersih tetapi lunak; FPR(Val) keras tetapi *in-sample* terhadap kalibrasi.
   Estimasi FPR lintas-sesi yang jujur memerlukan sesi benign ketiga — masuk saran Bab V.
4. **Istilah "uniform".** Jangan tulis tanpa keterangan. Gunakan "pelatihan MSE seragam + skoring
   terbobot benign", karena "uniform" pada `scoring_comparison_results.md` berarti *skoring* tanpa
   bobot — konfigurasi kolaps (GRU UL Flood 28,40%) yang justru tidak dipilih.

## 8. Urutan eksekusi

1. Tiga perbaikan teks di §7 (tidak butuh mesin, kerjakan sekarang).
2. Run B-utama + B-sens-1 + B-sens-2.
3. Verifikasi kriteria penerimaan §5.
4. Regenerasi figur (§6.4).
5. Perbarui `loss_ablation_results.md` (§6.5).
6. Kaskade naskah (§7 tabel).

Langkah 6 tidak boleh dimulai sebelum 3 dan 4 selesai.
