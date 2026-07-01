# Proposal Struktur Bab IV: Pengujian dan Analisis
**Topik Tugas Akhir:** Sistem Deteksi Anomali Hibrida (LSTM-Autoencoder & Rule-Based) dan Mitigasi (E2SM-RC) Loop Tertutup pada O-RAN

Struktur ini disusun dengan menyelaraskan format penulisan dari **Panduan Buku Tugas Akhir Teknik Telekomunikasi ITB (Oktober 2023)**, buku TA Faiq Fayyadil Rahman, dan buku TA Partner (Slicing/RRM/RF). Bab ini bertujuan untuk memverifikasi fungsionalitas subsistem pada tahap pengujian desain serta menyajikan analisis kinerja sistem deteksi dan mitigasi secara kuantitatif.

---

## Rancangan Struktur Bab IV

### BAB IV PENGUJIAN DAN ANALISIS

#### 4.1 Pengujian Desain
Bagian ini memaparkan skenario verifikasi fungsionalitas untuk memastikan setiap modul berjalan sesuai dengan spesifikasi rancangan (Bab III). Pengujian dilakukan secara bertahap dari level subsistem hingga integrasi sistem secara keseluruhan.

*   **4.1.1 Subsistem Integrasi dan Komunikasi E2**
    *   **Tujuan**: Memverifikasi keberhasilan jabat tangan (*handshake*) subscription E2AP/E42, penerimaan berkala *E2SM-KPM Indication Message* Format 4 (1 Hz), keberhasilan parsing RNTI/metrik radio, serta *decoding* pesan biner ASN.1.
    *   **Data & Visualisasi**: Screenshot log koneksi socket SCTP/TCP, tabel verifikasi parameter KPM ter-decode.
*   **4.1.2 Subsistem Deteksi Anomali Hibrida**
    *   **Tujuan**: Memverifikasi pemuatan berkas model ONNX (`lstm_ue_v4.onnx`), keberhasilan inferensi runtime C API, pembacaan nilai *Reconstruction Error* (MSE), dan eksekusi parallel penapisan aturan fisik radio (R1–R5).
    *   **Data & Visualisasi**: Screenshot log inisialisasi sesi C API ONNX Runtime, tabel hasil verifikasi pemicuan aturan (R1–R5) per skenario.
*   **4.1.3 Subsistem Dashboard Visualisasi dan Monitoring**
    *   **Tujuan**: Memverifikasi proses penulisan log CSV berkala oleh logger C xApp, penarikan data log asinkron oleh eksportir Python (`csv_exporter.py`) pada Port 8000, scraping oleh Prometheus Server, dan rendering metrik pada Grafana Dashboard.
    *   **Data & Visualisasi**: Screenshot terminal logging CSV, log scraping HTTP Prometheus, dan rendering panel dashboard Grafana.
*   **4.1.4 Pengujian Sistem Terintegrasi (E2E Closed-Loop)**
    *   **Tujuan**: Memverifikasi fungsionalitas sistem kontrol loop tertutup secara utuh. Alur pengujian dimulai dari pengiriman lalu lintas data normal, injeksi serangan banjir data (DoS), deteksi anomali (Stage 1), eskalasi ke kondisi kritis (Stage 2), pengiriman instruksi mitigasi *E2SM-RC Control Request*, hingga penurunan alokasi PRB penyerang (PRB Throttling max 5%) oleh gNodeB scheduler.
    *   **Data & Visualisasi**: Diagram skenario pengujian E2E, diagram alir urutan kejadian waktu pengujian.

#### 4.2 Analisis Hasil Pengujian
Bagian ini berfokus pada analisis data hasil pengujian secara kritis, menyajikan pembuktian matematis keandalan model AI, overhead komputasi, dan efektivitas mitigasi loop tertutup.

*   **4.2.1 Analisis Kinerja Komunikasi E2 dan Latensi Pemrosesan xApp**
    *   **Analisis**: Mengukur overhead waktu pemrosesan internal xApp. Parameter yang dianalisis meliputi latensi *decoding* ASN.1 pesan KPM, latensi eksekusi inferensi ONNX, jeda waktu pembuatan pesan kontrol E2SM-RC, dan total latensi loop tertutup (*E2E closed-loop latency*).
    *   **Visualisasi**: Grafik distribusi latensi pemrosesan, tabel rata-rata jeda waktu.
*   **4.2.2 Analisis Performa Model AI (LSTM-AE) dan Deteksi Hibrida**
    *   **Analisis**: 
        *   Justifikasi korelasi antar-fitur fisik radio yang diekstrak untuk memperkuat alasan pemilihan 19 fitur.
        *   Analisis kurva pelatihan model LSTM-AE (*loss vs epoch*) untuk membuktikan model tidak mengalami *overfitting* atau *underfitting*.
        *   Analisis performa deteksi hibrida dengan membandingkan performa *Rule-Only*, *ML-Only* (LSTM-AE), dan *Hybrid (Rule-Based ∪ ML)* menggunakan metrik *Accuracy, Precision, Recall, F1-Score,* dan *False Positive Rate* (FPR).
        *   Pembuktian efektivitas ambang batas keputusan optimal ($Th = 0,025266$) melalui kurva ROC dan nilai AUC.
    *   **Visualisasi**: Heatmap korelasi fitur, grafik kurva pelatihan, grafik distribusi Reconstruction Error normal vs anomali, Confusion Matrix, dan Kurva ROC-AUC.
*   **4.2.3 Analisis Overhead Komputasi RIC Node dan Dampak Mitigasi terhadap Kualitas Jaringan**
    *   **Analisis**:
        *   Evaluasi dampak aksi mitigasi E2SM-RC (*PRB Throttling*) terhadap penurunan throughput UE penyerang (DoS) dan pemulihan kapasitas throughput UE normal (*co-existing UE*).
        *   Analisis overhead penggunaan CPU (%) dan memori RAM (MB) pada tumpukan kontainer RIC Node (xApp, Grafana, Prometheus) untuk membuktikan efisiensi komputasi *C-native* xApp.
    *   **Visualisasi**: Grafik temporal throughput UE penyerang vs UE normal sebelum dan sesudah mitigasi aktif, grafik overhead CPU & RAM kontainer RIC stack (berdasarkan data cAdvisor).

---

## Daftar Gambar yang Dibutuhkan untuk Bab IV

### A. Pengujian Desain (4.1)
1.  **Gambar 4.1**: Screenshot terminal log koneksi socket SCTP E2AP antara E2 Agent gNodeB dengan FlexRIC, dan socket TCP E42 antara FlexRIC dengan xApp.
2.  **Gambar 4.2**: Screenshot log inisialisasi sesi C API ONNX Runtime yang menunjukkan model `lstm_ue_v4.onnx` berhasil dimuat dengan CPU Memory Arena di RIC Node.
3.  **Gambar 4.3**: Screenshot baris log CSV yang ditulis oleh logger xApp dan log ekspor Prometheus Exporter Python (HTTP Port 8000).
4.  **Gambar 4.4**: Diagram skenario konfigurasi topologi pengujian terintegrasi E2E (injeksi data normal, serangan DoS via USRP, dan RIC Node).

### B. Analisis Hasil Pengujian (4.2)
5.  **Gambar 4.5**: Grafik *Boxplot* atau *Bar Chart* distribusi latensi pemrosesan per tahap di dalam xApp (jeda decoding ASN.1, jeda inferensi, jeda penyusunan E2SM-RC).
6.  **Gambar 4.6**: Heatmap matriks korelasi fitur nirkabel (Pearson Correlation Coefficient) untuk 19 fitur masukan untuk menunjukkan keterkaitan temporal metrik.
7.  **Gambar 4.7**: Grafik kurva pembelajaran (*Learning Curve*) model LSTM-AE yang memplot *Training Loss* dan *Validation Loss* terhadap jumlah epoch pelatihan (1 s.d. 100).
8.  **Gambar 4.8**: Grafik kurva distribusi *Reconstruction Error* (Loss) untuk menunjukkan pemisahan yang jelas antara subset data normal dengan subset data serangan DoS (Uplink/Downlink flood) untuk membuktikan posisi threshold optimal $0,025266$.
9.  **Gambar 4.9**: Gambar *Confusion Matrix* hasil pengujian hibrida paralel.
10. **Gambar 4.10**: Kurva ROC (*Receiver Operating Characteristic*) dan nilai AUC (*Area Under Curve*) model LSTM-AE untuk pengujian anomali.
11. **Gambar 4.11**: Grafik laju data (*Throughput* dalam kbps) target UE penyerang sebelum, selama, dan setelah dikenakan mitigasi E2SM-RC (PRB Throttling max 5%).
12. **Gambar 4.12**: Grafik laju data (*Throughput* dalam kbps) UE normal (*co-existing UE*) untuk membuktikan bahwa ketika serangan dimitigasi, throughput UE normal kembali pulih ke tingkat baseline normal (karena PRB scheduler terbebaskan).
13. **Gambar 4.13**: Grafik beban kerja utilisasi CPU (%) dari tumpukan kontainer RIC Node (xApp, cAdvisor, Prometheus, Grafana) sepanjang durasi pengujian E2E (15 menit).
14. **Gambar 4.14**: Grafik konsumsi memori RAM (MB) dari tumpukan kontainer RIC Node sepanjang durasi pengujian E2E.

---

## Daftar Tabel yang Dibutuhkan untuk Bab IV

1.  **Tabel 4.1**: Hasil Verifikasi Parameter E2SM-KPM Indication Message Format 4 (membandingkan data mentah srsRAN scheduler dengan data hasil parsing xApp).
2.  **Tabel 4.2**: Hasil Verifikasi Aktivasi Aturan Fisik (R1–R5) per skenario serangan (menguji apakah serangan Uplink Flood memicu R1, Downlink Flood memicu R2, dll.).
3.  **Tabel 4.3**: Tabel Statistik Latensi Pemrosesan (Minimum, Rata-rata, Maksimum, dan Standar Deviasi dalam milidetik).
4.  **Tabel 4.4**: Perbandingan Performa Klasifikasi Deteksi (Akurasi, Presisi, Recall, F1-Score, dan FPR) antara metode Rule-Based (R1-R5), ML-Only (LSTM-AE), dan Hybrid (Rule ∪ ML).
5.  **Tabel 4.5**: Perbandingan Kecepatan Deteksi (*Detection Delay* dalam milidetik/detik) antara metode Rule-Based, ML-Only, dan Hybrid di berbagai tingkat intensitas serangan.
