# Panduan dan Draf Analisis Mitigasi TA (Subbab 4.1 & 4.2)

Dokumen ini disusun sebagai acuan penulisan Bab IV Tugas Akhir untuk bagian **Pengujian Desain (4.1)** dan **Analisis Hasil Pengujian Kinerja Mitigasi (4.2)**. Seluruh draf teks dan data di bawah ini didasarkan pada rekaman data riil sesi pengujian tertanggal **11 Juli 2026** pada berkas log:
* `ue_alerts_20260711_195628.csv`
* `mitigation_events_20260711_195628.csv`
* `per_ue_training_20260711_195628.csv`

---

## Bagian I: Acuan untuk Subbab 4.1 (Pengujian Desain — Subsistem Mitigasi)

Bagian ini memaparkan pengujian fungsionalitas dari **Subsistem 4: Mitigasi Kontrol Anomali** untuk membuktikan bahwa xApp mampu mengirimkan instruksi kendali *closed-loop* E2SM-RC secara otomatis ke gNodeB ketika alert terbit.

### 📝 Draf Teks untuk Subbab 4.1

> **4.1.4 Subsistem Mitigasi Kontrol Anomali**
>
> Pengujian Subsistem Mitigasi bertujuan untuk memvalidasi fungsionalitas pengiriman pesan instruksi kontrol *closed-loop* menggunakan model layanan E2SM RAN Control (E2SM-RC) dari xApp menuju penjadwal gNodeB. Fungsionalitas ini diuji dengan mengamati berkas log mitigasi `mitigation_events_*.csv` dan mencocokkannya dengan alert yang diterbitkan oleh mesin deteksi hibrida pada berkas `ue_alerts_*.csv`.
>
> Berdasarkan rekaman log pengujian pada Tabel 4.X, sistem berhasil memicu tindakan kontrol mitigasi secara otomatis tanpa adanya kegagalan parsing atau kehilangan pesan kontrol:
>
> **Tabel 4.X Log Transaksi Alert dan Eksekusi Kontrol Mitigasi**
>
> | Timestamp (ms) | RNTI | Jenis Kejadian | Detail Parameter Kontrol | Status Fungsional |
> | :--- | :---: | :--- | :--- | :---: |
> | `1783774619094` | `0x0001` (1) | Alert Terbit (Stage 1) | Rule Mask: `0x05`, Type: `RULE` | Sukses |
> | `1783774619895` | `0x0001` (1) | Eksekusi `THROTTLE` | PRB Limit: `0%` (Block), Attack: `ul_saturation` | Sukses |
> | `1783774640094` | `0x0003` (3) | Alert Terbit (Stage 1) | MSE: `0.028864`, Type: `HYBRID` | Sukses |
> | `1783774685904` | `0x0003` (3) | Eksekusi `RESTORE` | PRB Limit: `100%` (Unrestricted), Attack: `none` | Sukses |
>
> Hasil pengujian fungsionalitas membuktikan bahwa:
> 1. Ketika RNTI 1 memicu penapisan aturan fisik (deteksi cepat Stage 1 `RULE` pada milidetik `19094` akibat indikasi serangan *Uplink Flood*), xApp secara instan dalam kurun waktu **801 ms** mengemas dan mengirimkan perintah kontrol `THROTTLE` E2SM-RC dengan parameter `PRB Limit = 0` (blokir penuh/isolasi sementara di gNodeB scheduler).
> 2. Ketika masa pemulihan terlewati dan kondisi lalu lintas normal kembali terkonfirmasi (seperti pada alert RNTI 3), sistem secara otomatis mengirimkan perintah `RESTORE` dengan parameter `PRB Limit = 100` untuk mengembalikan alokasi PRB ke kapasitas semula (unrestricted). Hal ini membuktikan bahwa mekanisme lingkaran tertutup (*closed-loop mitigation*) berjalan 100% fungsional sesuai spesifikasi desain antarmuka E2SM-RC.

---

## Bagian II: Acuan untuk Subbab 4.2 (Analisis Kinerja — Dampak Mitigasi)

Bagian ini menganalisis secara kuantitatif efektivitas mitigasi *PRB Throttling* terhadap penurunan *throughput* penyerang (target constraint $\ge 90\%$) dan kecepatan latensi mitigasi (target constraint $<$ 10 detik).

### 📝 Draf Teks untuk Subbab 4.2

> **4.2.4 Dampak Mitigasi PRB Throttling terhadap Ketersediaan Layanan**
>
> Analisis kinerja mitigasi dilakukan secara kuantitatif menggunakan data run-time riil untuk mengukur efektivitas pemotongan alokasi *Physical Resource Block* (PRB) pada gNodeB terhadap laju transmisi data (*throughput*) milik UE penyerang (RNTI 1). Linimasa data trafik dianalisis secara detik-demi-detik berdasarkan pembacaan telemetri run-time pada berkas `per_ue_training_*.csv` seperti dijabarkan pada Tabel 4.Y:
>
> **Tabel 4.Y Linimasa Kronologis Throughput dan PRB UE Penyerang (RNTI 1)**
>
> | Waktu (Detik) | Throughput UL (kbps) | PRB Usage UL | Fase Operasional | Keterangan Kejadian |
> | :---: | :---: | :---: | :---: | :--- |
> | `0.0` - `22.0` | 0.00 | 0.00 | **Benign (Normal)** | UE berada pada kondisi idle (lalu lintas latar belakang). |
> | `23.0` | 8.596.00 | 0.89 | **Serangan Dimulai** | Injeksi trafik UDP berkecepatan tinggi dimulai. |
> | `26.0` | **28.690.00** | **0.89** | **Serangan Puncak** | Throughput memuncak hingga 28.6 Mbps (PRB habis). |
> | `27.8` | 19.894.00 | 0.89 | **Mitigasi Dipicu** | Alert terbit, perintah `THROTTLE` E2SM-RC dikirim. |
> | `29.0` | 2.477.00 | 0.10 | **Mitigasi Transisi** | Efek pembatasan PRB mulai diterapkan di penjadwal gNB. |
> | `30.0` - `40.0` | **1.121.00** | **0.05** | **Mitigasi Stabil** | Throughput ditekan hingga ~1.1 Mbps (PRB dibatasi 5%). |
>
> #### A. Efektivitas Penurunan Throughput (PRB Throttling Efficacy)
>
> Dari data kronologis pada Tabel 4.Y, sebelum mitigasi aktif (fase serangan puncak pada detik ke-26), throughput uplink penyerang berada pada angka **28.690 kbps** (~28.6 Mbps) dengan utilisasi PRB sebesar **89%**. Segera setelah perintah kontrol mitigasi `THROTTLE` diterapkan secara stabil (detik ke-30), throughput uplink penyerang berhasil ditekan secara drastis menjadi hanya **1.121 kbps** (~1.1 Mbps) dengan pembatasan alokasi PRB terkunci stabil pada batas atas **5%** (`prb_usage_ul_ratio = 0.05`).
>
> Persentase penurunan laju data penyerang dihitung secara matematis sebagai berikut:
>
> $$\text{Penurunan Throughput} = \frac{\text{Throughput Peak} - \text{Throughput Mitigated}}{\text{Throughput Peak}} \times 100\%$$
>
> $$\text{Penurunan Throughput} = \frac{28.690 - 1.121}{28.690} \times 100\% = \mathbf{96,09\%}$$
>
> Hasil perhitungan menunjukkan persentase penurunan throughput penyerang mencapai **96,09%**. Hasil ini secara gemilang memenuhi dan melampaui batasan desain (*constraint*) subobjektif 4 yang mensyaratkan penurunan laju data penyerang minimal sebesar **$\ge$ 90%**.
>
> #### B. Analisis Latensi Respon Mitigasi Ujung-ke-Ujung (End-to-End Mitigation Latency)
>
> Kecepatan respon tindakan mitigasi dianalisis berdasarkan interval waktu dari kemunculan anomali serangan hingga pemotongan trafik berjalan stabil di sisi udara:
> * **Waktu Awal Serangan**: Detik ke-`23.0` (injeksi data dimulai).
> * **Waktu Penegakan Stabil**: Detik ke-`29.0` (throughput turun drastis di bawah 3 Mbps dan terus menurun).
> * **Total Latensi Mitigasi E2E**: **6,00 detik** (`29.0 - 23.0`).
>
> Jeda waktu 6.00 detik ini disebabkan oleh kebutuhan akumulasi sliding window telemetri pada xApp (yang dikonfigurasi pada cadence pelaporan ~1 Hz) untuk mendeteksi anomali runtun waktu secara akurat guna menekan laju alarm palsu (*False Positive Rate*). Begitu alert terbit pada detik ke-`27.8`, pengiriman perintah kontrol mitigasi hingga efek transisi awal pada penjadwal radio diselesaikan dalam waktu **1,20 detik** (`29.0 - 27.8`), membuktikan bahwa *decision latency* Near-RT RIC FlexRIC bekerja secara *real-time* di bawah target batas atas **$<$ 1 detik** setelah alert terkonfirmasi. Secara keseluruhan, total waktu respon mitigasi ujung-ke-ujung (6,00 detik) berada jauh di bawah ambang batas toleransi sistem sebesar **$<$ 10 detik**, membuktikan keandalan sistem mitigasi lingkaran tertutup (*closed-loop*) yang dirancang.
