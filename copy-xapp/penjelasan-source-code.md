# Penjelasan Source Code Security xApp (C Native)

Dokumen ini menjelaskan implementasi source code Security xApp yang dikembangkan pada fase T50.
Source code terdiri dari empat file utama: `xapp_sec_moni.c` sebagai program utama,
`sec_ids.c`/`sec_ids.h` sebagai modul rule-based IDS, `ue_tracker.c`/`ue_tracker.h`
sebagai modul pelacak UE per-RNTI, dan `my_xapp_kpm.conf` sebagai konfigurasi koneksi dan
subscription E2SM-KPM.

---

## 1. Konfigurasi xApp: `my_xapp_kpm.conf`

File konfigurasi ini dibaca oleh binary `xapp_sec_moni` pada saat startup melalui argumen
`-c`. File ini menentukan parameter koneksi ke Near-RT RIC dan daftar metrik KPM yang
disubscribe ke gNB melalui protokol E2AP/E2SM.

**Gambar 1. Konfigurasi Subscription KPM (`my_xapp_kpm.conf`)**

Parameter konfigurasi yang relevan adalah:

- `NearRT_RIC_IP = "10.91.2.2"` — alamat IP Near-RT RIC pada testbed
- `E42_Port = 36422` — port E42 untuk koneksi xApp ke RIC
- `name = "KPM", time = 10` — subscription KPM dengan periode 10 ms
- `ran_type = "ngran_gNB_DU"` — target node adalah gNB-DU (Distributed Unit) srsRAN
- `format = 1` — menggunakan KPM Format 1 (cell-aggregate indication)

File ini mendaftarkan 15 metrik KPM untuk disubscribe, antara lain `RRU.PrbUsedDl`,
`RRU.PrbUsedUl`, `CQI`, `RACH.PreambleDedCell`, dan `DRB.AirIfDelayUl`. Namun demikian,
sebagian metrik seperti `DRB.UEThpDl`, `DRB.UEThpUl`, `RSRP`, dan `RSRQ` tidak
diimplementasikan oleh srsRAN pada lapisan KPM DU sehingga selalu mengembalikan nilai 0.
Hanya 10 metrik yang menghasilkan data nyata dan digunakan sebagai fitur LSTM.

---

## 2. Program Utama: `xapp_sec_moni.c`

File ini merupakan inti dari xApp dengan ukuran lebih dari 1.400 baris kode C. Program ini
dibangun di atas FlexRIC E42 xApp API dan mengimplementasikan seluruh alur kerja xApp mulai
dari koneksi ke Near-RT RIC, subscription service model E2, penerimaan indikasi KPM,
inferensi LSTM via ONNX Runtime, hingga pengiriman perintah mitigasi E2SM-RC.

**Gambar 2. Struktur Utama `xapp_sec_moni.c`**

### 2.1 Inisialisasi dan Koneksi E2

Pada tahap inisialisasi, program membaca argumen baris perintah termasuk path file
konfigurasi (`-c`), label skenario (`--label`), dan flag mitigasi (`--mitigate`). Program
kemudian menginisialisasi ONNX Runtime, membuka file CSV untuk pencatatan dataset, dan
mendaftarkan callback handler untuk setiap service model yang disubscribe (KPM, MAC, RLC,
RC).

Subscription E2SM-KPM dilakukan dengan menggunakan format action definition yang
dikonfigurasi sesuai report style yang didukung gNB. Fungsi `fill_report_style_1()` dan
`fill_report_style_4()` menyiapkan parameter subscription untuk masing-masing format
(cell-aggregate dan per-UE). Fungsi `fill_report_style_5()` juga tersedia namun tidak
digunakan pada testbed ini. Pada srsRAN, hanya KPM Format 1 yang diterima karena gNB tidak
mengirimkan indication dalam Format 3 atau Format 5.

### 2.2 Callback Penerimaan Data KPM: `sm_cb_kpm()`

Fungsi `sm_cb_kpm()` dipanggil secara asinkron setiap kali Near-RT RIC meneruskan
indication message E2SM-KPM dari gNB. Callback ini mengisi struct `cell_metrics_t` dengan
nilai metrik terbaru, kemudian memanggil `rule_based_detect()` dan `csv_trainer_write()`
dalam satu window waktu ~90 ms.

**Gambar 3. Alur Pemrosesan pada Callback `sm_cb_kpm()`**

Terdapat penanganan khusus untuk kasus di mana srsRAN mengirimkan KPM indication dengan
`MeasurementData` berisi 0 record (SIZE(0)), yang melanggar constraint ASN.1 standar
`SIZE(1..65535)`. Kondisi ini terjadi saat UE melakukan detach atau reattach (contoh:
airplane mode toggle). APER decoder FlexRIC menolak pesan tersebut dan mengakibatkan
`meas_data_lst_len == 0`. Setiap kejadian decode kosong ini dihitung sebagai penambahan
`g_cell.empty_ind_rate` untuk kemudian digunakan oleh Rule 3b sebagai sinyal proxy RRC
storm.

Gate waktu 90 ms diterapkan untuk menangani kondisi di mana srsRAN memanggil
`sm_cb_kpm()` beberapa kali dalam satu periode konfigurasi (sekali per entri RNTI
internal). Tanpa gate ini, satu periode KPM dapat menghasilkan banyak baris duplikat
di file CSV.

### 2.3 Inferensi LSTM via ONNX Runtime

Fungsi `init_onnx()` memuat model `security_model.onnx` yang berisi LSTM Autoencoder
beserta MinMaxScaler yang sudah di-bake ke dalam model pada saat ekspor. Fungsi
`run_inference()` menerima buffer fitur berukuran `[1, 10, 12]` (batch × window × fitur)
dan menjalankan inferensi untuk menghasilkan anomaly score.

```
Input ONNX:  [batch=1, timestep=10, features=12]
Output ONNX: [batch=1, score=1]  — threshold 0.5 sudah di-bake
```

Jika anomaly score melebihi threshold 0.5, program mencetak peringatan `[ALERT] ANOMALY
DETECTED`. Buffer fitur menggunakan mekanisme sliding window: setelah setiap inference,
data digeser satu posisi ke depan sehingga window selalu berisi 10 timestep terbaru.

**Implementasi ini berbeda dari rencana awal di dokumen T40** yang menggunakan arsitektur
Python xApp dengan library Kafka sebagai transport. Pada T50, implementasi dilakukan dalam
C native yang terintegrasi langsung dengan FlexRIC E42 API tanpa middleware tambahan,
sehingga latensi end-to-end dari penerimaan KPM hingga inferensi lebih rendah.

### 2.4 Pencatatan Dataset: `csv_trainer_write()`

Fungsi ini menerima snapshot `cell_metrics_t` dan menuliskan satu baris ke file CSV
training dengan 12 kolom: timestamp, datetime, 10 fitur terengineering, dan label skenario.
Fitur yang ditulis adalah hasil komputasi dari raw metrics KPM:

| Kolom CSV | Komputasi |
|-----------|-----------|
| `prb_usage_dl_ratio` | `PrbUsedDl / (PrbUsedDl + PrbAvailDl)` |
| `prb_usage_ul_ratio` | `PrbUsedUl / (PrbUsedUl + PrbAvailUl)` |
| `cqi` | Langsung dari KPM (keep-last di srsRAN) |
| `rach_preamble` | Langsung dari KPM |
| `air_delay_ul` | Langsung dari KPM |
| `prb_direction` | `(prb_ul − prb_dl) / (prb_total + ε)` |
| `prb_total` | `prb_dl_ratio + prb_ul_ratio` |
| `prb_dl_delta` | `prb_dl[t] − prb_dl[t−1]` |
| `prb_ul_delta` | `prb_ul[t] − prb_ul[t−1]` |
| `prb_burst_index` | `log(1 + prb_total) / (rolling_mean + ε)` |

Label skenario ditulis dari variabel global `g_label` yang dapat diubah secara real-time
melalui file `/tmp/xapp_label` tanpa perlu me-restart xApp (*hot-label switching*).

### 2.5 Mitigasi E2SM-RC: `rc_send_prb_quota()`

Fungsi `rc_send_prb_quota()` mengirimkan perintah E2SM-RC Control Style 2 ke gNB melalui
Near-RT RIC untuk membatasi alokasi PRB suatu slice. Perintah ini digunakan sebagai
mekanisme mitigasi serangan data-plane (UL/DL Flood, Burst ON/OFF).

Parameter yang dikirimkan adalah `max_prb_pct = 5` saat serangan terdeteksi (severity=2)
dan `max_prb_pct = 100` saat pemulihan. Terdapat cooldown 30 detik antara aksi throttle
dan restore untuk mencegah osilasi berulang.

Fitur mitigasi ini dinonaktifkan secara default (`g_mitigate_enabled = 0`) karena
diketahui terdapat srsRAN Bug #468 yang menyebabkan proses gNB crash setelah menerima
pesan RC Control. Mitigasi hanya diaktifkan melalui argumen `--mitigate` pada deployment
yang sudah menggunakan versi srsRAN yang telah diperbaiki.

### 2.6 Callback MAC, RLC, dan RC

Selain KPM, program juga mendaftarkan callback untuk tiga service model lain:

- `sm_cb_mac()` — menerima statistik MAC per-UE dan meneruskannya ke `ue_tracker_mac_update()` serta `csv_mac_write()`. Namun karena srsRAN tidak mengiklankan MAC SM (RAN Function ID 142) dalam pesan E2 SETUP, callback ini tidak pernah dipanggil pada testbed ini.
- `sm_cb_rlc()` — menerima statistik RLC per-Radio Bearer dan mencetaknya ke stdout untuk keperluan diagnostik.
- `sm_cb_rc()` — menerima konfirmasi RC Indication dari gNB setelah perintah mitigasi dikirim.

---

## 3. Modul Rule-Based IDS: `sec_ids.c` / `sec_ids.h`

File `sec_ids.h` mendefinisikan struct `cell_metrics_t` yang merupakan snapshot metrik sel
per window KPM, serta antarmuka fungsi `ids_init()`, `ids_reset()`, dan
`rule_based_detect()`. File `sec_ids.c` mengimplementasikan enam rule deteksi yang
dievaluasi setiap kali `rule_based_detect()` dipanggil dari `sm_cb_kpm()`.

**Gambar 4. Rule-Based IDS pada `sec_ids.c`**

Setiap rule menggunakan counter window berurutan (*consecutive-window counter*) untuk
memastikan kondisi anomali berlangsung setidaknya 3 window (~360 ms atau ~270 ms tergantung
periode) sebelum alert diterbitkan. Pendekatan ini menghindari false positive dari lonjakan
PRB sesaat yang bersifat transien.

### Rule 1 — PRB Overload (Sustained Unidirectional Flood)

Rule ini mendeteksi saturasi PRB secara unidirectional: `PRB_DL > 90%` dengan `PRB_UL <
3%` untuk DL flood, atau `PRB_UL > 90%` dengan `PRB_DL < 3%` untuk UL flood. Guard PRB
arah berlawanan yang rendah membedakan UDP flood (tanpa ACK, DL ≈ 0) dari TCP speedtest
yang memiliki traffic ACK di arah sebaliknya. Severity: CRITICAL (2).

### Rule 2 — Signaling Storm / RRC Flood (MAC Heuristic)

Rule ini mendeteksi kondisi traffic control-plane tinggi dengan payload data nyaris nol.
Kondisi: `PRB_avg > 20%`, RLC rate di kedua arah di bawah 100 kbps, dan ada sinyal
control-plane (`CQI < 5` atau `RACH > 0`). Pada testbed srsRAN, guard CQI < 5 tidak
berfungsi karena srsRAN menerapkan kebijakan keep-last pada nilai CQI setelah UE detach
(CQI tetap 15 meskipun UE sudah disconnect). Severity: WARNING (1).

### Rule 3 — RRC Flood via RACH Spike

Rule ini mendeteksi lonjakan `RACH.PreambleDedCell` lebih dari 3× rata-rata historis 10
window terakhir, dengan nilai absolut di atas 5. Ini merupakan sinyal untuk UE yang mencoba
random access berulang kali dalam waktu singkat. Severity: WARNING (1).

### Rule 3b — RRC Storm via Empty Indications

Rule ini merupakan hasil temuan pada 14 Mei 2026. Rule ini mendeteksi RRC storm melalui
`empty_ind_rate`, yaitu counter kumulatif kegagalan decode APER per window akibat srsRAN
mengirimkan KPM indication berisi 0 record saat UE toggle airplane mode. Kondisi: `empty_ind_rate ≥ 2` per window dengan `PRB_UL < 30%` dan `PRB_DL < 30%`, selama minimal 3
window berturut-turut. Guard PRB rendah memastikan rule ini tidak tumpang tindih dengan
serangan data-plane. Severity: WARNING (1).

**Rule ini berbeda dari rencana awal** yang mengandalkan Rule 2 berbasis CQI. Karena CQI
keep-last policy srsRAN membuat Rule 2 tidak pernah aktif, Rule 3b diperkenalkan sebagai
mekanisme deteksi alternatif yang memanfaatkan *side-effect* perilaku non-standar srsRAN.

### Rule 4 — Uplink Flood (RLC-Based)

Rule ini menggunakan RLC SDU volume karena `DRB.UEThpUl` selalu bernilai 0 di srsRAN KPM
DU. Kondisi: `RLC_UL > 15 Mbps`, `PRB_UL > 80%`, dan `RLC_DL < 0.5 Mbps`, selama minimal
3 window. Severity: CRITICAL (2).

### Rule 5 — Downlink Flood (RLC-Based)

Simetris dengan Rule 4 untuk arah downlink. Kondisi: `RLC_DL > 50 Mbps`, `PRB_DL > 80%`,
dan `RLC_UL < 0.5 Mbps`, selama minimal 3 window. Severity: CRITICAL (2).

### Rule 6 — High UL Air Interface Delay (Proxy Jamming)

Rule ini mendeteksi `DRB.AirIfDelayUl > 100 ms` sebagai indikator kemungkinan interferensi
fisik atau RF jamming. Severity: WARNING (1). Tidak ada consecutive-window requirement pada
rule ini karena delay tinggi yang sesaat pun sudah anomalous.

Nilai kembalian `rule_based_detect()` adalah: `0` (normal), `1` (WARNING — gangguan
control-plane atau fisik, PRB throttle tidak efektif), atau `2` (CRITICAL — banjir
data-plane, PRB throttle via E2SM-RC efektif).

---

## 4. Modul Pelacak UE Per-RNTI: `ue_tracker.c` / `ue_tracker.h`

File `ue_tracker.h` mendefinisikan struct `ue_entry_t` untuk menyimpan state satu UE
berdasarkan RNTI-nya, dan struct `ue_tracker_t` sebagai tabel pelacak yang dapat menampung
hingga 64 UE aktif secara bersamaan (`UE_MAX_TRACKED = 64`).

**Gambar 5. Struktur Data `ue_tracker_t`**

Modul ini dirancang untuk menerima data dari MAC SM per-UE yang lebih granular dibandingkan
KPM cell-aggregate. Setiap UE dilacak dengan metrik puncak dalam satu window 1 detik:
`dl_mbps`, `ul_mbps`, `dl_prb`, `ul_prb`, `snr`, `cqi`, dan `bsr` (Buffer Status Report).

### 4.1 Fungsi `ue_tracker_mac_update()`

Fungsi ini dipanggil dari `sm_cb_mac()` untuk setiap UE dalam laporan MAC. Jika RNTI sudah
ada dalam tabel, entri yang ada diperbarui dengan nilai puncak (*peak*) dalam window
berjalan. Jika RNTI baru, slot baru dialokasikan. Bila tabel penuh, entri yang paling lama
tidak aktif akan digantikan.

### 4.2 Fungsi `ue_tracker_flush()`

Fungsi ini dipanggil sekali per periode KPM 1 detik untuk mengevaluasi semua UE yang aktif
dan mendeteksi ancaman. Dua jenis deteksi dilakukan:

1. **Cell-level RRC flood**: Jika jumlah RNTI baru dalam satu periode (`new_this_period`)
   melebihi threshold (`thr_new_rnti_per_sec = 3`), maka terdapat indikasi UE baru bermunculan
   dengan cepat (RRC storm).

2. **Per-UE flood**: Jika `ul_mbps` UE melebihi threshold 20 Mbps → `UE_UL_FLOOD`, atau
   `dl_mbps` melebihi 50 Mbps → `UE_DL_FLOOD`. Alert baru diterbitkan setelah
   `UE_ALERT_CONSECUTIVE = 3` periode berturut-turut.

3. **Short-lived UE**: UE yang menghilang setelah kurang dari 2 periode aktif
   (`thr_min_periods`) dianggap sebagai probe atau signaling probe.

**Implementasi modul ini merupakan persiapan untuk fase Buku TA**, di mana data MAC SM
per-RNTI akan tersedia apabila srsRAN sudah mengiklankan MAC SM (RAN Function ID 142).
Pada pengujian T50, callback `sm_cb_mac()` tidak pernah dipanggil karena srsRAN tidak
mengiklankan RAN Function tersebut, sehingga `ue_tracker_flush()` dipanggil dengan state
kosong.

---

## 5. Perbedaan Implementasi dari Rencana Dokumen T40

Implementasi yang diterapkan pada testbed T50 memiliki beberapa perbedaan dari rencana
awal yang telah disusun pada dokumen perencanaan sebelumnya. Perbedaan ini terjadi karena
keterbatasan platform srsRAN yang ditemukan selama pengujian:

| Aspek | Rencana Awal (T40) | Implementasi Aktual (T50) |
|-------|-------------------|--------------------------|
| Bahasa implementasi | Python xApp + Kafka transport | C native xApp + FlexRIC E42 API |
| Sumber fitur LSTM | KPM per-UE (Format 3/Style 4) + MAC SM | KPM cell-aggregate (Format 1/Style 1) saja |
| Fitur throughput | `DRB.UEThpDl/UL` per UE | Selalu 0 di srsRAN — digantikan `prb_burst_index` |
| Deteksi RRC storm | Via penurunan CQI (CQI < 5) | Via `empty_ind_rate` (srsRAN SIZE(0) side-effect) |
| Mitigasi | iptables di Core Node via SSH | E2SM-RC Style 2 PRB Throttle via Near-RT RIC |
| Isolasi attacker | Per-RNTI (rencana) | Cell-level (karena srsRAN tidak kirim Format 3) |

Perubahan dari Python ke C native dilakukan untuk mengurangi dependensi middleware dan
mendapatkan latensi deteksi yang lebih deterministik. Perubahan mekanisme deteksi RRC storm
dari rule berbasis CQI ke rule berbasis `empty_ind_rate` dilakukan setelah ditemukan bahwa
srsRAN menerapkan kebijakan keep-last pada CQI yang menyebabkan Rule 2 tidak pernah aktif
pada testbed ini.
