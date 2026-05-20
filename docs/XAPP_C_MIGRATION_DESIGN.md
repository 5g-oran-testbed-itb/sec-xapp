# Desain Migrasi Security xApp ke Bahasa C Native

## 1. Ringkasan Pemahaman (Understanding Summary)
* **Apa yang dibangun**: xApp O-RAN *native* dalam bahasa C berbasis contoh dari FlexRIC, yang memiliki *engine* ML terintegrasi.
* **Mengapa dibangun**: Untuk mempercepat deteksi keamanan hingga skala Near-RT (10ms - 1s), menghindari jeda dari pengiriman data ke proses eksternal.
* **Bagaimana data diproses**: Menarik langsung data KPM dan RC via antarmuka E2 dalam bentuk biner/array memori, menghilangkan *overhead* penguraian teks/CSV.
* **Bagaimana inferensi dilakukan**: Model LSTM PyTorch akan diekspor, lalu dijalankan secara in-process (di dalam xApp C) menggunakan ONNX Runtime C API.
* **Batasan Utama (*Constraints*)**: Inferensi model ML tidak boleh memblokir (*blocking*) xApp secara signifikan dalam menerima pesan Indikasi E2.
* **Non-Goals Eksplisit**: Sistem ini tidak ditujukan untuk *scaling* komersial dengan ribuan UE, dan tidak menggunakan *backend* Python untuk *real-time inference*.

## 2. Asumsi (Assumptions)
* Skala beroperasi untuk *testbed* dengan 1-3 UE fisik.
* Model PyTorch (LSTM) dapat diekspor secara valid ke bentuk `.onnx`.
* Kehilangan data dalam satu *window* akan mengakibatkan pembatalan inferensi untuk *window* tersebut demi mencegah deteksi cacat (Data di-*reset* secara aman).
* Pengambilan data menggunakan FlexRIC SM Callbacks (KPM dan RC) untuk membedah C-Struct native.

## 3. Log Keputusan (Decision Log)

| Keputusan | Alternatif Dipertimbangkan | Mengapa Dipilih |
|-----------|----------------------------|-----------------|
| **Format Engine ML** | ONNX Runtime (C API) vs LibTorch (C++) vs Tulis Ulang Algoritma di C | ONNX C API sangat ringan, bisa digunakan dengan murni kompilator C (tanpa masalah kompatibilitas *linker* C++), dan teroptimasi untuk inferensi cepat. |
| **Pola Eksekusi xApp** | Eksekusi Sinkron vs Asinkron (Worker Thread) vs Timer Batching | Eksekusi Sinkron (memanggil model langsung di dalam *callback* E2). Paling mudah di-*maintain* dan di-debug. *Overhead/Blocking* sangat kecil (~2ms) sehingga sangat aman untuk skala 1-3 UE. |
| **Metode Ekstraksi Data** | Parsing Payload E2AP Manual vs Baca file CSV *output* E2 vs Panggil via FlexRIC SM Callback | FlexRIC SM Callback. SDK FlexRIC otomatis mende-kode ASN.1/E2AP menjadi C-struct biner `ind_data_t` sehingga ekstraksi dan konversi tipe datanya berjalan dengan latensi ~0 ms. |

## 4. Desain Arsitektur / Implementasi Final
1. **Inisialisasi (`init()`)**: Mengatur memori lingkungan ONNX (`OrtEnv`), memuat `security_model.onnx`, menginisialisasi ukuran Tensor masukan, dan membuat alokasi array (Ring Buffer) untuk masing-masing histori UE (10 timesteps x 12 fitur).
2. **Event Callback (E2 KPM/RC `sm_cb()`)**: 
   - Membaca atribut mentah langsung dari struktur memori *payload*.
   - Mengubah nilai (casting & normalisasi) menjadi presisi `float`.
   - Menggeser indeks Ring Buffer dan memasukkan nilai.
3. **Eksekusi Model (`run_inference()`)**:
   - Terpicu langsung saat 1 *window* penuh.
   - Pointer Array diserahkan ke fungsi `OrtRun()`.
   - *Error check*: Jika fungsi gagal, *memory buffer* direset.
   - *Post-processing*: Jika skor *output* (float) melebihi batas (Threshold), anomali dilaporkan (ditampilkan atau mengirim *control action* kembali via E2).
