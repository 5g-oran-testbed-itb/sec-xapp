# Verifikasi Gambar Bab IV (Pengujian dan Analisis)

Tabel berikut menunjukkan hasil penelusuran dan pemetaan berkas gambar yang dibutuhkan untuk draf **Bab IV (Daftar Gambar)** terhadap berkas yang saat ini tersedia di folder `eval_figures/per_ue_v4` dan folder lainnya di workspace `~/sec-xapp`.

## Tabel Pemetaan & Verifikasi Gambar

| No. Gambar | Deskripsi di Bab IV | Status | Nama Berkas / Lokasi Saat Ini | Catatan & Tindakan Lanjut |
| :--- | :--- | :--- | :--- | :--- |
| **Gambar 4.1** | Screenshot log koneksi socket SCTP E2AP & TCP E42 | **BELUM ADA** | - | Perlu diambil langsung via terminal/GUI screenshot saat FlexRIC & srsRAN berjalan. |
| **Gambar 4.2** | Screenshot log inisialisasi sesi C API ONNX Runtime (`lstm_ue_v4.onnx`) | **BELUM ADA** | - | Perlu diambil dari terminal output xApp C native saat pertama kali dijalankan. |
| **Gambar 4.3** | Screenshot baris log CSV logger xApp dan Prometheus Exporter | **BELUM ADA** | - | Perlu diambil dari stdout/file log CSV dan Prometheus `/metrics` endpoint. |
| **Gambar 4.4** | Diagram skenario konfigurasi topologi pengujian terintegrasi E2E | **BELUM ADA** | - | Perlu digambar menggunakan tool eksternal (Draw.io/Visio/Inkscape) atau menggunakan `docs/flowchart_sistem_terbaru.svg` jika dirasa sesuai. |
| **Gambar 4.5** | Grafik distribusi latensi pemrosesan per tahap di xApp | **TERSEDIA** | `eval_figures/per_ue_v4/eval_latency_lstm.png`<br>`eval_figures/per_ue_v4/eval_latency_gru.png` | Tersedia terpisah versi LSTM (~66 KB) dan GRU (~61 KB), siap digunakan. |
| **Gambar 4.6** | Heatmap matriks korelasi fitur nirkabel (19 fitur) | **TERSEDIA** | `eval_figures/per_ue_v4/fig1_feature_correlation_19f.png` | Berkas berukuran 337.6 KB, mencakup visualisasi korelasi Pearson. |
| **Gambar 4.7** | Grafik kurva pembelajaran (*Learning Curve*) model LSTM-AE | **TERSEDIA** | `eval_figures/per_ue_v4/fig4_training_curves_v4.png` | Berkas berukuran 174.9 KB, menampilkan Loss vs Epoch. |
| **Gambar 4.8** | Grafik kurva distribusi *Reconstruction Error* (Loss) normal vs serangan | **TERSEDIA** | `eval_figures/per_ue_v4/eval_reconstruction_error_lstm.png`<br>`eval_figures/per_ue_v4/eval_reconstruction_error_gru.png` | Tersedia terpisah versi LSTM (~56 KB) dan GRU (~57 KB). Menampilkan sebaran data training, validation, dan serangan. |
| **Gambar 4.9** | *Confusion Matrix* hasil pengujian hibrida paralel | **TERSEDIA** | `eval_figures/per_ue_v4/eval_confusion_lstm.png`<br>`eval_figures/per_ue_v4/eval_confusion_gru.png` | Tersedia terpisah versi LSTM (~130 KB) dan GRU (~128 KB) berisi visualisasi matriks konfusi beserta ringkasan metrik. |
| **Gambar 4.10** | Kurva ROC dan nilai AUC model LSTM-AE | **TERSEDIA** | `eval_figures/per_ue_v4/eval_roc_lstm.png`<br>`eval_figures/per_ue_v4/eval_roc_gru.png` | Tersedia terpisah versi LSTM (~67 KB) dan GRU (~66 KB) memuat grafik kurva ROC serta perbandingan nilai AUC. |
| **Gambar 4.11** | Grafik laju data (*Throughput*) target UE penyerang sebelum/saat/setelah mitigasi | **BELUM ADA** | - | Eksperimen mitigasi (E3/E4) belum dijalankan (`results/eval_results_mitigation_ul.json` masih berstatus "Belum"). Grafik perlu digenerate setelah pengujian mitigasi. |
| **Gambar 4.12** | Grafik laju data (*Throughput*) UE normal (*co-existing*) | **BELUM ADA** | - | Sama seperti di atas, bergantung pada jalannya eksperimen mitigasi E3. |
| **Gambar 4.13** | Grafik beban kerja utilisasi CPU (%) kontainer RIC Node | **TERBAGI** | Tersebar di `eval_figures/cpu-ric/{scenario}/` | Di folder `cpu-ric` terdapat gambar `sar-ric-label-{scenario}.png` dan `pidstat-ric-label-{scenario}.png` per-skenario serangan (benign, ul-flood, dl-flood, dll.), namun **belum digabungkan** ke folder `per_ue_v4/` sebagai grafik E2E tunggal. |
| **Gambar 4.14** | Grafik konsumsi memori RAM (MB) kontainer RIC Node | **TERBAGI** | Tersebar di `eval_figures/cpu-ric/{scenario}/` | Sama dengan analisis Gambar 4.13. |

---

## Gambar Tambahan (Bonus) di `per_ue_v4`

Ada beberapa gambar di folder `eval_figures/per_ue_v4` yang saat ini tidak tercatat dalam struktur usulan Bab IV Anda, tetapi memiliki nilai ilmiah tinggi jika dimasukkan:

1. **`eval_per_class_lstm.png` & `eval_per_class_gru.png`**: Grafik performa deteksi (recall/F1/precision) untuk setiap jenis serangan per-UE (terpisah versi LSTM dan GRU). Sangat cocok mendukung analisis di **Subbab 4.2.2**.
2. **`fig2_scheme_a_feature_importance.png`**: Diagram kontribusi/bobot fitur (*feature importance*) berdasarkan Scheme A. Sangat berguna untuk memperkuat alasan pemilihan fitur di **Subbab 4.2.2**.
3. **`fig3_feature_distribution_top5.png`**: Plot distribusi nilai untuk 5 fitur terpenting.

## Rekomendasi Langkah Selanjutnya

1. **Pengambilan Screenshot Pengujian Desain**: 
   Jalankan subsistem secara langsung untuk menangkap log SCTP (4.1), inisialisasi ONNX (4.2), dan log CSV/Prometheus (4.3).
2. **Jalankan Eksperimen Mitigasi**: 
   Selesaikan eksperimen E3/E4 agar data *throughput* penyerang vs normal (4.11 & 4.12) dapat direkam dan diplot menggunakan Python matplotlib.
