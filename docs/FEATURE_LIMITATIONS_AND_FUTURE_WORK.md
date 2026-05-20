# Keterbatasan Fitur & Rencana Perbaikan — Security xApp

Dokumen ini dibuat untuk keperluan **T50 (Pengujian dan Analisis)**. Isinya mencatat keterbatasan yang ditemukan selama pengujian beserta analisis akar masalahnya. Semua perbaikan, peningkatan model, dan implementasi fitur lanjutan yang disebutkan di sini direncanakan untuk dikerjakan pada fase **Buku TA**.

---

## 1. Arsitektur Telemetri Saat Ini vs yang Ideal

### Kondisi saat ini: KPM Style 1 (Cell-Level)

```
gNB (srsRAN)
  └─► E2SM-KPM Style 1 (Format 1 Indication)
        └─► Metrik AGREGAT seluruh sel (semua UE digabung)
              └─► LSTM Autoencoder (1 baris per epoch = seluruh sel)
```

**Masalah utama**: Tidak ada isolasi per-UE. Jika ada 2 UE di jaringan, metrik UE attacker dan UE korban tercampur dalam satu baris data.

### Yang seharusnya: KPM Style 4 (Per-UE via Format 3)

```
gNB (srsRAN)
  └─► E2SM-KPM Style 4 (Format 3 Indication, condition-based)
        └─► Metrik PER-RNTI (setiap UE terpisah)
              └─► LSTM Autoencoder (1 baris per epoch per RNTI)
```

---

## 2. Fitur yang Digunakan Saat Ini (10 Fitur KPM Cell-Level)

> ⚠️ `DRB.UEThpDl/UL`, `DRB.RlcSduVolume*`, dan throughput metrics lainnya **selalu 0** di srsRAN KPM DU. Fitur yang benar-benar berisi data hanyalah PRB metrics. Fitur 3–5 dan 8–10 adalah fitur turunan (*engineered features*) yang dihitung dari PRB DU.

| # | Nama Fitur CSV | Sumber Raw | Keterangan |
|---|---------------|------------|------------|
| 1 | `prb_usage_dl_ratio` | `RRU.PrbUsedDl` / `(PrbUsedDl+PrbAvailDl)` | Utilisasi PRB DL, 0–1 |
| 2 | `prb_usage_ul_ratio` | `RRU.PrbUsedUl` / `(PrbUsedUl+PrbAvailUl)` | Utilisasi PRB UL, 0–1 |
| 3 | `cqi` | `CQI` (keep-last di srsRAN — lihat §3.4.2) | Channel Quality, 0–15 |
| 4 | `rach_preamble` | `RACH.PreambleDedCell` | Jumlah RACH attempt sel |
| 5 | `air_delay_ul` | `DRB.AirIfDelayUl` | Delay UL air interface (ms) |
| 6 | `prb_direction` | `(prb_ul − prb_dl)/(prb_total + ε)` | Arah dominansi UL/DL, [−1, +1] |
| 7 | `prb_total` | `prb_dl + prb_ul` | Utilisasi PRB total |
| 8 | `prb_dl_delta` | `prb_dl[t] − prb_dl[t−1]` | Laju perubahan PRB DL |
| 9 | `prb_ul_delta` | `prb_ul[t] − prb_ul[t−1]` | Laju perubahan PRB UL |
| 10 | `prb_burst_index` | `log(1+prb_total) / (rolling_mean + ε)` | Intensitas burst relatif terhadap window |

**Semua fitur adalah agregat seluruh sel — tidak ada RNTI, tidak ada isolasi per-UE.** Korelasi antar fitur ditunjukkan di `eval_figures/fig7_feature_correlation.png`: `cqi` dan `air_delay_ul` berkorelasi tinggi (r=0.60), `prb_total` dengan `prb_ul` (r=0.77) dan `prb_dl` (r=0.61). `rach_preamble` hampir tidak berkorelasi dengan fitur lain (r < 0.1), konsisten dengan sifatnya sebagai sinyal independen event-driven.

---

## 3. Fitur yang Kurang (Seharusnya Ada)

### 3.1 Fitur KPM Per-UE (via Style 4/Format 3) — *Dicoba, gagal karena keterbatasan srsRAN*

Fitur-fitur ini seharusnya tersedia via E2SM-KPM Style 4. Kode handler `fill_report_style_4()` sudah diimplementasikan di xApp, namun **pengambilan data per-UE tidak berhasil**. Investigasi menunjukkan bahwa srsRAN hanya mengiklankan satu RAN Function (ID 2, KPM) di pesan E2 SETUP, dan KPM indication yang diterima selalu dalam format cell-aggregate (Format 1), bukan Format 3 per-UE. Selain itu, handler FORMAT_3 di `xapp_sec_moni.c` juga belum lengkap (lihat §5.3).

| # | Nama Fitur | Keterangan |
|---|-----------|------------|
| 1 | `rnti` | Identifier unik per UE — kunci untuk isolasi serangan |
| 2 | `prb_ul_per_ue` | PRB uplink per UE (bukan agregat sel) |
| 3 | `prb_dl_per_ue` | PRB downlink per UE |
| 4 | `dl_tbs_per_ue` | Transport Block Size downlink per UE |
| 5 | `ul_tbs_per_ue` | Transport Block Size uplink per UE |
| 6 | `cqi_per_ue` | Channel Quality Indicator per UE |
| 7 | `sinr_per_ue` | Signal-to-Noise Ratio per UE |

**Dampak ketidakhadiran fitur ini**: Model LSTM tidak dapat membedakan UE yang menyerang dengan UE yang berperilaku normal jika ada lebih dari 1 UE di jaringan. Seluruh data tercampur → FPR meningkat drastis (dari 0.44% menjadi 12.67% saat 2 UE aktif).

### 3.2 Fitur MAC SM Per-UE (via Custom E2SM FlexRIC) — *Dicoba, gagal karena srsRAN tidak support*

Upaya implementasi CSV recorder untuk MAC SM per-UE sudah dilakukan: fungsi `csv_mac_write()` dan struct `csv_mac_trainer_t` diimplementasikan di `xapp_sec_moni.c`, dan file output `mac_per_ue_*.csv` berhasil dibuat. Namun file selalu kosong (hanya header) karena **srsRAN tidak mengiklankan MAC SM (RAN Function ID 142) dalam E2 SETUP** — hanya mengiklankan KPM (ID 2). Akibatnya, `has_ran_func(e2_nodes, 0, 142)` mengembalikan `false`, subscription MAC SM tidak dikirim, dan callback `sm_cb_mac()` tidak pernah dipanggil.

Ini adalah keterbatasan fundamental platform: **MAC SM adalah Custom E2SM FlexRIC yang tidak diimplementasikan di srsRAN**.

| # | Nama Fitur | Keterangan |
|---|-----------|------------|
| 1 | `dl_aggr_tbs` | Total bytes downlink per UE per epoch |
| 2 | `ul_aggr_tbs` | Total bytes uplink per UE per epoch |
| 3 | `dl_aggr_prb` | PRB downlink per UE (MAC level) |
| 4 | `ul_aggr_prb` | PRB uplink per UE (MAC level) |
| 5 | `dl_aggr_retx_prb` | Retransmission PRB downlink per UE |
| 6 | `ul_aggr_retx_prb` | Retransmission PRB uplink per UE |
| 7 | `rach_id` | RACH preamble ID per UE (bukan agregat) |
| 8 | `frame` / `slot` | Timestamp granular (frame/slot level) |

**Catatan penting tentang MAC SM**: MAC SM adalah **Custom E2SM** (OID: `0.0.0.0.0.0.0.0.1.142.0`), bukan E2SM standar O-RAN. Payload menggunakan FlatBuffers (bukan ASN.1). Transport via E2AP tetap standar O-RAN. Artinya: interoperabilitas terbatas pada ekosistem FlexRIC.

### 3.3 Fitur yang Tidak Tersedia di srsRAN KPM DU — *Keterbatasan platform*

Beberapa fitur KPM standar O-RAN yang didefinisikan di **3GPP TS 28.552** tidak diimplementasikan oleh srsRAN:

| Fitur | Alasan Tidak Tersedia |
|-------|----------------------|
| `DRB.UEThpDl` / `DRB.UEThpUl` | KPM Format 3 metric — srsRAN mengirim 0 untuk semua throughput metrics |
| `RRU.PrbUsedDl` per slice | Slicing tidak aktif di testbed |
| `L1M.RS-SINR.Bin*` | Histogram SINR — tidak diimplementasikan di srsRAN |
| `HO.ExeSucc` | Handover — tidak relevan (single-cell testbed) |

### 3.4 Perilaku srsRAN KPM yang Menyimpang dari Standar

Investigasi pada 14 Mei 2026 menemukan dua perilaku srsRAN yang tidak sesuai standar, keduanya berdampak pada deteksi:

**3.4.1 — SIZE(0) MeasurementData (APER Decode Failure)**

Saat UE melakukan detach/reattach (misal airplane mode toggle), srsRAN mengirim KPM Indication Message dengan `MeasurementData` berisi 0 record. Ini melanggar constraint `SIZE(1..65535)` di ASN.1 schema KPM v3.00, sehingga APER decoder FlexRIC menolak pesan tersebut.

```
srsRAN kirim: MeasurementData { items: [] }  ← SIZE(0), invalid per 3GPP TS 28.552
ASN.1 decoder: FAIL (code=2, consumed=0)     ← pesan dibuang, tidak ada metrik diekstrak
```

**Dampak awal**: Callback `sm_cb_kpm` tidak menerima data apapun saat UE toggle → semua counter di-reset ke nilai lama (keep-last).

**Dimanfaatkan sebagai sinyal deteksi**: Setiap kegagalan decode ini dihitung sebagai `empty_ind_rate`. Burst kegagalan menandakan RRC storm. Lihat Rule 3b di §4.3.

**3.4.2 — CQI Keep-Last Policy**

Untuk KPM indication yang *berhasil* di-decode, srsRAN melaporkan CQI cell-level sebagai agregat nilai terakhir yang diketahui (*keep-last*), bukan nilai live. Setelah UE detach, CQI tidak di-reset ke 0 melainkan tetap 15 (nilai terakhir saat UE masih connect).

```
Ekspektasi:    UE detach → CQI = 0 (tidak ada UE aktif)
Realitas srsRAN: UE detach → CQI = 15 (keep-last dari UE sebelumnya)
```

**Dampak**: Fitur CQI tidak dapat diandalkan sebagai sinyal RRC storm pada testbed srsRAN. Rule 2 (Signaling Storm) yang bergantung pada `cqi < 5` tidak pernah terpicu untuk serangan airplane toggle.

---

## 4. Keterbatasan Deteksi Akibat Fitur yang Kurang

### 4.1 UL Flood Tidak Terdeteksi oleh LSTM — Keterbatasan Fundamental Cell-Level KPM

**Akar masalah bukan pada dataset, melainkan pada keterbatasan discriminative power fitur cell-level KPM itu sendiri.**

Investigasi pada 14 Mei 2026 menemukan bahwa iperf3 UL speedtest — baik UDP maupun TCP, dengan target bitrate berapapun (30M, 50M, 80M) — selalu menghasilkan PRB UL ~90-95% pada testbed 5G ini. Penyebabnya adalah perilaku greedy scheduler srsRAN:

```
iperf3 push data → socket buffer UE penuh
                 → UE melaporkan BSR (Buffer Status Report) tinggi ke gNB
                 → srsRAN scheduler alokasi PRB UL MAKSIMUM
                 → PRB UL ≈ 90%+ regardless of iperf3 -b target
```

Kontrol bitrate iperf3 (`-b`) hanya mengatur injection rate dari aplikasi ke socket, bukan transmission rate radio. Scheduler tidak mengenal "target throughput aplikasi" — hanya melihat buffer UE penuh.

Akibatnya, dari perspektif cell-level KPM:

| Skenario | PRB UL | Throughput | Buffer | Scheduler |
|----------|--------|------------|--------|-----------|
| Benign UL speedtest (TCP/UDP) | ~90-95% | tinggi | penuh | greedy alokasi max |
| UL Flood attack (UDP) | ~90-95% | tinggi | penuh | greedy alokasi max |
| **Distribusi KPM** | **≈ identik** | **≈ identik** | **≈ identik** | **≈ identik** |

**Ini bukan masalah kualitas dataset — ini keterbatasan fundamental E2SM-KPM cell-level.** KPM tidak mengekspos MAC queue semantics, packet entropy, flow metadata, atau application intent. Yang terlihat hanya resource consumption agregat.

**Mengapa tidak bisa diselesaikan dengan menghapus speedtest dari training:**

Jika speedtest dihapus dari training, LSTM akan memflag semua high-UL traffic sebagai anomali — termasuk upload file sah pengguna — karena scheduler tetap mengalokasikan PRB maksimum untuk traffic legitimate manapun. False positive rate akan melonjak di deployment nyata.

**Fitur yang sebenarnya discriminative antara benign vs flood:**

Perbedaan sejati antara speedtest dan UL flood bukan pada nilai instantaneous PRB, melainkan pada **pola temporal dan persistensi**:

| Karakteristik | Benign UL speedtest | UL Flood attack |
|---------------|--------------------|-----------------| 
| Durasi | Pendek–sedang (10–30s), ada idle | Kontinu, tidak ada idle gap |
| Variabilitas | Ada fluktuasi alami | Monoton, variance rendah |
| Pola burstiness | `prb_burst_index` bervariasi | `prb_burst_index` flat tinggi |
| PRB DL bersamaan | Ada ACK traffic (TCP) | Hampir nol (UDP, no ACK) |

Fitur temporal ini tersedia di LSTM (`prb_burst_index`, `prb_ul_delta`, sliding window 10 timestep), namun karena training mengandung sustained speedtest, model tidak dapat mempelajari pembedaannya.

**Dampak pada evaluasi**: LSTM recall UL Flood = **0.3%** (`eval_figures/fig8_per_attack_recall.png`). Distribusi anomaly score UL Flood hampir identik dengan Normal traffic — keduanya menumpuk di rentang 0.05–0.30, jauh di bawah threshold 0.5 (`eval_figures/fig2_score_distribution.png`). AUC keseluruhan model 0.733 (`eval_figures/fig4_roc_curve.png`) tertekan signifikan oleh kegagalan deteksi UL Flood dan RF Burst.

**Saat ini**: UL Flood terdeteksi oleh **rule-based detector** (Rule 1: `PRB_UL > 90% AND PRB_DL < 3%` dan Rule 4: `RLC_UL > 15 Mbps AND PRB_UL > 80%`). Guard `PRB_DL < 3%` di Rule 1 secara implisit membedakan UDP flood (tanpa ACK, DL ≈ 0) dari TCP speedtest (ada ACK, DL > 3%).

### 4.2 DL Flood Terdeteksi karena "Keberuntungan" Scaler

DL Flood terdeteksi bukan karena fitur yang baik, melainkan karena nilai `prb_dl_ratio` saat serangan (0.919) **melebihi** batas scaler training (0.6226) → clipping ke 1.0 → reconstruction error tinggi.

Ini adalah **deteksi yang fragile**: jika serangan lebih pelan (prb_dl < 0.62), tidak terdeteksi.

Namun hasilnya pada dataset pengujian sangat baik: **recall DL Flood = 97.8%** (`eval_figures/fig8_per_attack_recall.png`). Timeline deteksi (`eval_figures/fig5_detection_timeline_dlflood.png`) menunjukkan alert pertama muncul **4 detik** setelah serangan dimulai, dengan anomaly score konsisten di atas threshold 0.5 sepanjang episode serangan (score rata-rata 0.55–0.88, peak ~1.38).

### 4.3 RRC Storm (Signaling Storm) — ✅ Kini Terdeteksi via `empty_ind_rate`

**Sebelum 14 Mei 2026 — tidak terdeteksi karena dua masalah:**

1. **CQI keep-last** (§3.4.2): CQI tidak turun ke 0 saat UE detach → Rule 2 (cqi < 5) tidak pernah aktif.
2. **Kegagalan APER decode** (§3.4.1): Pesan-pesan kosong langsung dibuang sebelum memasuki callback → `empty_ind_rate` tidak pernah terakumulasi karena deteksi terhenti di early return.

**Solusi yang diimplementasikan (14 Mei 2026):**

Diperkenalkan sinyal baru `empty_ind_rate` — counter kumulatif per window (~90ms) yang menghitung berapa kali KPM Indication gagal di-decode karena `MeasurementData.len == 0`. Ini adalah *side-effect* langsung dari UE detach/reattach:

```
Airplane toggle → UE detach → srsRAN kirim SIZE(0) KPM → APER fail → empty_ind_rate++
```

**Rule 3b di `sec_ids.c`** menggunakan sinyal ini:
```
Kondisi: empty_ind_rate >= 2 per window AND prb_ul < 30% AND prb_dl < 30%
         selama >= 3 window berturut-turut (~270ms)
Output:  [ALERT] RRC_STORM (UE churn) | severity=WARNING
```

Guard PRB < 30% memastikan rule ini tidak overlap dengan serangan data-plane (UL/DL Flood) yang juga menghasilkan kegagalan decode saat UE berpindah status.

**Keterbatasan yang tersisa:**
- `empty_ind_rate` **belum** masuk ke CSV training → LSTM belum bisa menggunakan sinyal ini.
- Jika srsRAN diperbaiki untuk tidak mengirim SIZE(0), sinyal ini hilang. Perlu fitur alternatif (misalnya `rach_preamble` spike via Rule 3).
- Rule 2 (CQI < 5) tetap tidak berfungsi di testbed srsRAN karena CQI keep-last.

### 4.4 RF Burst — Tidak Terdeteksi oleh LSTM (Recall = 0.0%)

**Akar masalah: inversi metrik — jamming membuat PRB terlihat seperti idle, bukan seperti serangan.**

Ketika USRP B205 mini memancarkan sinyal interferensi di frekuensi yang sama, UE mengalami degradasi radio yang bersifat fisik:

```
RF jamming aktif → SINR turun drastis
               → UE tidak bisa decode PDCCH (scheduling grant)
               → UE tidak bisa transmit → PRB UL ≈ 0
               → gNB tidak bisa decode PUSCH → PRB UL juga ≈ 0 di scheduler
               → srsRAN melaporkan: prb_ul_ratio ≈ 0, prb_dl_ratio ≈ 0
```

Dari perspektif LSTM, pola ini **identik dengan kondisi idle benign** (tidak ada UE aktif). Distribusi anomaly score RF Burst (`eval_figures/fig2_score_distribution.png`) bahkan menumpuk di score 0.05–0.12 — **lebih rendah dari Normal traffic**. Ini karena model dilatih pada banyak data idle (74.3% Normal dari `eval_figures/fig6_dataset_distribution.png`), sehingga pola PRB rendah direkonstruksi dengan error sangat kecil.

**Mengapa `air_delay_ul` tidak membantu:**
- Saat UE tidak bisa transmit sama sekali, `DRB.AirIfDelayUl` srsRAN melaporkan 0 (tidak ada transmisi yang bisa diukur delay-nya), bukan nilai tinggi
- Bahkan jika nilainya naik, `air_delay_ul` berkorelasi tinggi dengan `cqi` (r=0.60, `eval_figures/fig7_feature_correlation.png`). Karena `cqi` tetap 15 akibat keep-last policy (§3.4.2), signal `air_delay_ul` yang anomalous teredam oleh mismatch korelasi ini

**Kesimpulan**: RF Burst tidak dapat dideteksi oleh LSTM berbasis KPM cell-level karena **efek serangan tidak terlihat di layer yang diobservasi KPM**. Deteksi RF jamming membutuhkan fitur physical-layer (L1M.RS-SINR histogram, interference measurement) yang tidak diimplementasikan oleh srsRAN.

### 4.5 Burst ON/OFF — Deteksi Sangat Lemah (Recall = 3.7%)

**Akar masalah: mismatch antara skala waktu serangan dan panjang window LSTM.**

Serangan Burst ON/OFF bekerja dengan pola periodik: traffic tinggi 3–7 detik, lalu berhenti 2–6 detik, berulang selama 120 detik. Deteksi idealnya mengenali **ritme periodik** ini. Namun terdapat tiga hambatan:

**Hambatan 1 — Window terlalu pendek untuk menangkap satu siklus penuh:**
```
LSTM window: 10 timestep × 120ms = 1.2 detik
Satu siklus ON/OFF: (3–7s ON) + (2–6s OFF) = 5–13 detik total

→ LSTM hanya melihat ~10% dari satu siklus dalam satu inference
→ Selama fase ON: terlihat seperti traffic normal tinggi
→ Selama fase OFF: terlihat seperti idle normal
→ Transisi ON→OFF dan OFF→ON jarang tertangkap dalam satu window
```

**Hambatan 2 — `prb_burst_index` menggunakan rolling mean yang adaptif:**
```
prb_burst_index = log(1 + prb_total) / (rolling_mean + ε)
```
Rolling mean dihitung dari 5 timestep terakhir (~600ms). Saat serangan berlangsung lama, rolling mean ikut naik mengikuti rata-rata traffic attack, sehingga `prb_burst_index` kembali mendekati 1.0 dan tidak lagi terlihat anomalous. Fitur ini sensitif terhadap burst sesaat, bukan terhadap pola periodik jangka menengah.

**Hambatan 3 — Rendahnya kontribusi `prb_burst_index` dalam loss reconstruction:**
Dari feature correlation heatmap (`eval_figures/fig7_feature_correlation.png`), `prb_burst_index` berkorelasi rendah dengan semua fitur lain (r=0.12–0.36). LSTM cenderung mendominasi reconstruction error dari fitur yang saling berkorelasi kuat (`prb_total`, `prb_ul`, `prb_dl`). Fitur burst yang orthogonal mendapat bobot lebih kecil dalam total MSE loss.

**Implikasi**: Burst ON/OFF memiliki recall 3.7% — hanya episode di mana transisi ON/OFF kebetulan jatuh dalam window LSTM sedemikian rupa sehingga `prb_burst_index` cukup tinggi untuk melewati threshold.

### 4.6 Signaling Storm — Terdeteksi Parsial oleh LSTM (Recall = 10.7%), Rule-Based Belum Optimal di CSV

Recall Signaling Storm via LSTM = **10.7%** (`eval_figures/fig8_per_attack_recall.png`). Deteksi parsial berasal dari perubahan kecil di `rach_preamble` dan `prb_direction` yang sesekali melewati threshold. 

**Catatan penting**: Sinyal `empty_ind_rate` yang mampu mendeteksi serangan ini (via Rule 3b di `sec_ids.c`) **belum dimasukkan ke dalam CSV training**. LSTM saat ini tidak memiliki akses ke informasi ini. Penambahan `empty_ind_rate` sebagai kolom fitur di CSV adalah satu perbaikan konkret yang bisa langsung dilakukan untuk meningkatkan kemampuan LSTM mendeteksi serangan ini di Buku TA.

---

## 5. Masalah Teknis yang Ditemukan selama T50

### 5.1 LSTM MAC SM Path: Feature Mismatch (Dead Code)

Di `xapp_sec_moni.c`, kode `sm_cb_mac()` berisi panggilan ke ONNX inference engine, **namun fitur yang dikirim tidak sesuai** dengan fitur yang digunakan saat training model:

```
ONNX model dilatih dengan: prb_usage_ul_ratio, prb_usage_dl_ratio, rach_preamble, ...  (KPM features)
sm_cb_mac() mengirim:      dl_aggr_tbs, ul_aggr_prb, dl_aggr_retx_prb, ...             (MAC features)
```

Jika `sm_cb_mac` terpanggil, inferensi akan menghasilkan output **tidak valid** (garbage in, garbage out). Tidak ada runtime check untuk mendeteksi mismatch ini. Dalam praktiknya ini adalah dead code karena srsRAN tidak mengiklankan MAC SM — `sm_cb_mac` tidak pernah dipanggil (lihat §3.2).

### 5.2 csv_mac_write: Diimplementasikan tapi Dead Code

Fungsi `csv_mac_write()`, struct `csv_mac_trainer_t`, dan inisialisasi file `mac_per_ue_*.csv` di `main()` berhasil dikompilasi dan dijalankan. File CSV terbentuk dengan header yang benar. Namun semua file selalu kosong karena sumber datanya (`sm_cb_mac`) tidak pernah dipanggil — alasannya sama dengan §3.2 dan §5.1: srsRAN tidak mengiklankan MAC SM.

### 5.3 FORMAT_3 Handler: Parsial, Tidak Pernah Menerima Data

Handler `FORMAT_3` di `xapp_sec_moni.c` hanya mengisi sebagian fitur dari struktur per-RNTI. Namun masalah yang lebih mendasar adalah handler ini tidak pernah dipanggil dengan data nyata: srsRAN selalu mengirim indication dalam Format 1 (cell-aggregate), tidak pernah Format 3 (per-UE). Perubahan format memerlukan modifikasi pada sisi srsRAN, bukan hanya konfigurasi xApp.

---

## 6. Rencana Perbaikan untuk Buku TA

### 6.0 Framing T50 → Buku TA

Hasil evaluasi T50 menunjukkan:

> *Sistem C xApp berhasil diintegrasikan dengan Near-RT RIC dan mampu melakukan deteksi serta mitigasi secara near real-time. Namun, evaluasi juga menemukan bahwa fitur E2SM-KPM Style 1 yang bersifat cell-level memiliki keterbatasan diskriminatif untuk membedakan beberapa serangan, terutama UL Flood dan RF Burst, karena metrik yang diamati hanya merepresentasikan konsumsi resource agregat, bukan perilaku per-UE atau karakteristik flow.*

> *Oleh karena itu, perbaikan pada Buku TA difokuskan pada dua arah: peningkatan fitur temporal yang dapat dilakukan langsung pada pipeline saat ini, serta migrasi menuju telemetry per-UE melalui KPM Style 4/Format 3 agar deteksi dapat dilakukan berdasarkan RNTI secara terpisah.*

**Catatan penting**: KPM Style 4 per-RNTI **tidak** dijanjikan sebagai perbaikan cepat — implementasinya bergantung penuh pada dukungan srsRAN yang belum tersedia. Framing yang tepat: *target arsitektural Buku TA*, bukan perbaikan langsung dari T50.

---

### 6.0.1 Ringkasan Perbaikan: Jangka Pendek (tanpa ubah platform)

Perbaikan ini dapat dikerjakan tanpa mengubah srsRAN, tanpa rekam ulang dataset benign, dan tanpa perubahan infrastruktur E2.

| Prioritas | Perbaikan | Tujuan |
|-----------|-----------|--------|
| P1 | Tambahkan `empty_ind_rate` ke CSV dan input LSTM | Meningkatkan deteksi Signaling/RRC Storm |
| P2 | Optimasi threshold LSTM berbasis ROC/F1 | Meningkatkan recall tanpa menaikkan FPR berlebihan |
| P3 | Tambah rule temporal persistence untuk UL/DL Flood | Mengurangi false positive akibat speedtest |
| P4 | Tambah rule sudden-drop untuk RF Burst/Jamming | Deteksi jamming saat PRB tiba-tiba turun seperti idle |
| P5 | Tambah fitur rolling variance / window lebih panjang untuk Burst ON/OFF | Menangkap pola periodik yang tidak terlihat pada window 1.2 detik |

### 6.0.2 Ringkasan Perbaikan: Arsitektural Jangka Menengah

Perbaikan ini memerlukan perubahan di sisi srsRAN (gNB) atau rekam ulang dataset skala penuh.

| Prioritas | Perbaikan | Tujuan |
|-----------|-----------|--------|
| P6 | Implementasi KPM Style 4 / Format 3 per-UE | Menghilangkan overlap antar UE pada cell-level KPI |
| P7 | Rekam ulang dataset per-RNTI | Training LSTM berbasis perilaku UE, bukan agregat sel |
| P8 | Retrain model LSTM dengan fitur per-UE | Meningkatkan recall UL Flood, Burst, dan multi-UE robustness |

### 6.0.3 Ringkasan Perbaikan: Lanjutan

| Prioritas | Perbaikan | Tujuan |
|-----------|-----------|--------|
| P9 | Integrasi MAC/RLC telemetry jika didukung srsRAN | Menambah fitur BSR, TBS, retransmission, packet/queue behavior |
| P10 | Deteksi multi-anomali berbasis korelasi temporal | Mendeteksi kombinasi flood + signaling storm secara simultan |
| P11 | Mitigasi per-RNTI / per-slice lebih presisi | Menghindari throttle seluruh sel/slice, hanya throttle RNTI attacker |

### 6.0.4 Urutan Implementasi Realistis (Target Sebelum Juli)

Urutan ini disusun berdasarkan ketergantungan teknis dan ketersediaan platform, dari yang paling dapat langsung dikerjakan hingga yang bergantung pada prasyarat eksternal:

1. Tambahkan `empty_ind_rate` ke CSV dan retrain LSTM **(P1)** — tidak ada prasyarat, dampak langsung ke Signaling Storm
2. Optimasi threshold dari ROC curve **(P2)** — tidak ada prasyarat, dataset sekarang sudah cukup
3. Tambahkan rule temporal persistence untuk UL/DL Flood **(P3)**
4. Tambahkan rule sudden-drop untuk RF Burst **(P4)**
5. Perpanjang window LSTM atau tambah rolling variance untuk Burst ON/OFF **(P5)**
6. Investigasi KPM Style 4 per-RNTI — **setelah** P1–P5 selesai **(P6–P8)**

---

*Detail teknis setiap perbaikan dijabarkan pada sub-seksi "Perbaikan 1–8" berikut.*

---

### Perbaikan 1 — Per-RNTI Detection via KPM Style 4

**Tujuan**: Menghilangkan masalah overlap distribusi benign vs flood pada cell-level dan membuat CSV training per-RNTI.

**Prasyarat** (blokir utama di T50): srsRAN perlu dikonfigurasi atau di-patch untuk mengirimkan KPM indication dalam Format 3 (per-UE), bukan hanya Format 1 (cell-aggregate). Ini memerlukan perubahan di sisi gNB, bukan hanya di xApp.

**Langkah implementasi**:
1. Investigasi konfigurasi srsRAN agar E2SM-KPM melaporkan Format 3 per-RNTI
2. Lengkapi `FORMAT_3` indication handler di `xapp_sec_moni.c`
3. Tambahkan fungsi `csv_kpm_per_rnti_write()` untuk menyimpan data per RNTI ke CSV
4. Rekam ulang dataset benign dan serangan dengan format per-RNTI
5. Retrain LSTM Autoencoder dengan fitur per-RNTI baru
6. Evaluasi ulang — UL Flood (saat ini 0.3%), RF Burst (0.0%), Burst ON/OFF (3.7%) diharapkan naik signifikan

**Dampak yang diharapkan**: UL Flood dapat terdeteksi LSTM karena fitur per-UE mengekspos perbedaan persistensi dan entropy yang tidak terlihat di cell-level.

### Perbaikan 2 — Perbaiki LSTM MAC SM Path

**Tujuan**: Aktifkan deteksi LSTM berbasis fitur MAC SM per-UE yang lebih kaya.

**Langkah implementasi**:
1. Tambahkan `csv_mac_write()` di `sm_cb_mac()` — rekam fitur MAC SM per RNTI
2. Buat `train_lstm_mac.py` — training script terpisah untuk fitur MAC SaM
3. Simpan model baru sebagai `lstm_mac_model.onnx`
4. Update `sm_cb_mac()` untuk load model yang benar (bukan model KPM)
5. Validasi: UL Flood seharusnya terdeteksi karena `ul_aggr_tbs` per UE berbeda jauh dari benign speedtest

### ~~Perbaikan 3~~ ✅ SELESAI di T50 — Mitigasi via E2SM-RC Style 2 (PRB Throttle)

**Status**: Diimplementasikan pada 11 Mei 2026.

**Yang sudah dilakukan**:
- `rc_send_prb_quota()` di `xapp_sec_moni.c` (lines 411–561): mengirim E2SM-RC Control Style 2 dengan RRM Policy Ratio (Max PRB = 5% saat attack, 100% saat restore)
- Main loop (lines 1419–1450): throttle diterapkan otomatis saat `rule_based_detect()` return CRITICAL, dengan 30s cooldown dan 10s calm sebelum restore
- `start_xapp_c_mitigate.sh`: script startup dedicated dengan flag `--mitigate`
- `slicing: - sst: 1, sd: 0` ditambahkan ke `cots_n78_copied.yml` di RAN node agar RC quota punya target slice yang valid
- srsRAN Bug #468 dikonfirmasi sudah fixed: versi srsRAN yang dipakai telah di-refactor total menggunakan API `rrm_policy_ratio_list` baru

**Cara pakai**: Gunakan `./start_xapp_c_mitigate.sh` untuk mode deteksi + mitigasi aktif.

**Keuntungan terealisasi**: Mitigasi di Layer 2 (MAC scheduler via RIC), native O-RAN, tanpa SSH ke Core Node.

### Perbaikan 4 — Korelasi Multi-Anomali

**Tujuan**: Deteksi serangan yang menggabungkan UL Flood + Signaling Storm secara bersamaan.

**Langkah implementasi**:
1. Tambahkan korelasi temporal antar anomali dari RNTI yang sama
2. Buat scoring system: UL Flood + Signaling Storm dari RNTI sama dalam 10 detik → confidence naik
3. Turunkan threshold CRITICAL dari 3 consecutive ke 2 jika multi-anomali terdeteksi

### Perbaikan 5 — Tambahkan `empty_ind_rate` ke CSV Training (Signaling Storm LSTM)

**Tujuan**: Memberikan LSTM sinyal langsung tentang RRC storm, sehingga dapat mendeteksi pola airplane-mode toggle tanpa bergantung sepenuhnya pada Rule 3b.

**Konteks**: `empty_ind_rate` sudah diakumulasi di xApp dan sudah digunakan di rule-based detector (Rule 3b), namun **belum ditulis ke CSV** di pemanggilan `csv_trainer_write()`. LSTM saat ini tidak memiliki akses ke sinyal ini sehingga recall Signaling Storm via LSTM hanya 10.7%.

**Langkah implementasi**:
1. Di `xapp_sec_moni.c`, tambahkan kolom `empty_ind_rate` ke struct `csv_trainer_t` dan fungsi `csv_trainer_write()`
2. Perbarui header CSV dari 10 kolom menjadi 11 kolom (tambahkan `empty_ind_rate` di posisi terakhir)
3. Rekam ulang dataset serangan Signaling Storm (tidak perlu rekam ulang benign — kolom baru akan bernilai 0 di benign)
4. Perbarui `feature_schema.py` dan `train_lstm.py` untuk input 11 fitur
5. Perbarui `export_onnx.py` → shape input ONNX berubah dari `[batch, 10, 10]` menjadi `[batch, 10, 11]`
6. Tambahkan `empty_ind_rate` ke MinMaxScaler (`scaler.pkl`) — domain range: `[0, 10]` (max ~10 kegagalan/window saat airplane toggle aktif)
7. Retrain dan evaluasi: expected recall Signaling Storm naik signifikan dari 10.7%

**Keuntungan**: Ini perubahan paling cepat diimplementasikan di antara semua perbaikan — tidak memerlukan perubahan srsRAN, tidak memerlukan rekam ulang seluruh dataset, hanya penambahan satu kolom di C dan Python.

**Catatan ketergantungan**: Jika srsRAN diperbaiki untuk tidak mengirim SIZE(0) KPM, sinyal ini hilang. Sebaiknya tambahkan juga `rach_preamble` spike count sebagai fitur komplementer.

### Perbaikan 6 — Perluas Window LSTM dan Tambah Fitur Temporal Variance (Burst ON/OFF)

**Tujuan**: Membuat LSTM mampu menangkap ritme periodik serangan Burst ON/OFF yang durasinya 5–13 detik per siklus.

**Akar masalah yang ditangani**:
- Window saat ini 10 timestep × 120ms = **1.2 detik** → hanya menangkap ~10% satu siklus
- `prb_burst_index` menggunakan rolling mean 5-timestep yang adaptif → menghilang saat serangan berkelanjutan

**Langkah implementasi**:

1. **Perpanjang window LSTM**: ubah dari 10 timestep menjadi **50 timestep** (6 detik) atau **100 timestep** (12 detik) di `train_lstm.py` — agar setidaknya satu siklus penuh ON/OFF tertangkap
   ```python
   SEQ_LEN = 50  # 6 detik, cukup untuk satu siklus minimum (5s)
   ```

2. **Tambah engineered features baru** di `feature_schema.py` untuk menangkap variance:
   ```python
   # Fitur 11: rolling std PRB total (window 20 timestep = 2.4s)
   "prb_rolling_std"   = rolling_std(prb_total, window=20)
   # Fitur 12: prb_ul autocorrelation lag-5 (deteksi periodisitas)
   "prb_ul_autocorr"   = autocorr(prb_ul_ratio, lag=5)
   ```
   Benign traffic: `prb_rolling_std` rendah stabil. Burst ON/OFF: `prb_rolling_std` tinggi dan osilasi.

3. Perbarui scaler untuk fitur baru, rekam ulang dataset dengan sampling rate tetap 120ms
4. Retrain dan bandingkan recall Burst ON/OFF dari 3.7%

**Alternatif jangka pendek** (tanpa retrain): Implementasikan rule-based detector di `sec_ids.c` yang mendeteksi **osilasi PRB periodik**: `prb_total` naik-turun ≥ 3 kali dalam 30-timestep window dengan amplitudo > 40% → flag `BURST_ONOFF_SUSPECTED`. Ini tidak bergantung pada LSTM sama sekali.

### Perbaikan 7 — Rule-Based Detector untuk RF Burst Jammer

**Tujuan**: Deteksi RF jamming via heuristik KPM meskipun LSTM tidak dapat mendeteksinya.

**Akar masalah yang ditangani**: RF jamming membuat PRB turun mendekati 0 — identik dengan idle benign — sehingga LSTM memberi anomaly score rendah (di bawah threshold). Recall LSTM = 0.0%.

**Pendekatan yang feasible dalam scope Buku TA**:

**Rule 6: Anomalous Idle Detection (Sudden-Drop Jammer)**

Kondisi yang *tidak* terjadi pada idle benign normal:
```
Kondisi Rule 6:
  (1) prb_ul turun > 50% dari rata-rata 5 window sebelumnya, DAN
  (2) prb_ul_ratio < 5% saat ini, DAN
  (3) air_delay_ul == 0 (tidak ada transmisi, bukan hanya rendah), DAN
  (4) sebelumnya (5 window lalu) prb_ul_ratio > 30% (ada UE aktif sebelumnya)
  selama >= 3 window berturut-turut → flag SUSPECTED_JAMMING (severity=WARNING)
```

Perbedaan dari idle benign: pada idle benign, kondisi (4) tidak terpenuhi karena tidak ada UE aktif sebelum idle. Pada RF jamming, ada UE yang aktif lalu tiba-tiba tidak bisa transmit.

**Langkah implementasi**:
1. Tambahkan `prb_ul_history[5]` rolling buffer ke `cell_metrics_t` di `sec_ids.c`
2. Implementasikan Rule 6 di `rule_based_detect()` dengan kondisi di atas
3. Verifikasi false positive dengan dataset benign: normal idle setelah UE disconnect seharusnya tidak memenuhi kondisi (4)

**Catatan keterbatasan**: Rule ini akan **false negative** jika jammer aktif sejak awal session (tidak ada baseline PRB). Rule ini juga tidak dapat membedakan RF jamming dari kegagalan radio hardware. Deteksi jamming yang lebih robust memerlukan fitur physical-layer (SINR histogram, RSRP measurement) yang tidak tersedia di srsRAN.

### Perbaikan 8 — Optimasi Threshold LSTM via Analisis ROC

**Tujuan**: Meningkatkan overall recall dengan menurunkan threshold dari P99.5 yang terlalu konservatif, berdasarkan tradeoff FPR-TPR dari ROC curve.

**Konteks**: Threshold saat ini P99.5 = 0.005035 (normalized: 0.5 di ONNX) memberikan FPR = 0.50% pada validation set benign, namun overall LSTM recall hanya 23.1%. ROC curve menunjukkan AUC = 0.733 (`eval_figures/fig4_roc_curve.png`), artinya ada ruang untuk meningkatkan TPR dengan menerima FPR sedikit lebih tinggi.

**Analisis tradeoff**:

| Threshold (ONNX) | Estimated FPR | Estimated Recall | Cocok untuk |
|-----------------|---------------|-----------------|------------|
| 0.50 (P99.5, current) | ~0.5% | ~23.1% | FP-intolerant, high precision |
| 0.35 (P98) | ~2% | ~45–55% | Balanced |
| 0.20 (P95) | ~5% | ~60–65% | High recall, tolerate more FP |

**Langkah implementasi**:
1. Generate ROC curve breakdown per serangan (bukan hanya overall) — untuk melihat serangan mana yang paling benefit dari threshold turun
2. Pilih threshold berdasarkan F1-score maksimum atau operating point yang sesuai deployment
3. Update threshold di `export_onnx.py`:
   ```python
   THRESHOLD = 0.35  # ganti dari 0.5, baked ke ONNX sebagai output layer offset
   ```
4. Rebuild ONNX model dan validasi FPR baru pada benign dataset

**Catatan penting**: Menurunkan threshold **tidak membantu RF Burst** (score 0.05–0.12, jauh di bawah threshold berapapun yang reasonable) dan hanya sedikit membantu Burst ON/OFF. Dampak terbesar adalah pada serangan yang sudah mendekati threshold — DL Flood (sudah tinggi, tidak perlu), Signaling Storm (akan naik dari 10.7% signifikan), dan UL Flood (terlimit oleh overlap distribusi, bukan threshold).

---

## 7. Ringkasan Perbandingan Sistem

| Aspek | Sistem T50 (Saat Ini) | Target Perbaikan (Buku TA) |
|-------|----------------|--------------------------|
| **Granularitas telemetri** | Cell-level (agregat) | Per-RNTI (per-UE) |
| **Sumber fitur LSTM** | KPM Style 1 (10 fitur) | KPM Style 4 + MAC SM (15+ fitur per-UE) |
| **Isolasi attacker** | Tidak langsung (melalui rule-based) | Langsung via RNTI |
| **UL Flood detection** | Hanya rule-based (20 Mbps threshold) | LSTM + rule-based |
| **RRC Storm detection** | ✅ Rule 3b via `empty_ind_rate` (srsRAN side-effect) | LSTM + rule-based (fitur langsung) |
| **Multi-UE robustness** | Rentan (FPR naik dari 0.44% ke 12.67%) | Tidak rentan (per-RNTI independent) |
| **Mitigasi** | ~~iptables Core (Layer 3, SSH)~~ → **E2SM-RC Style 2 ✅** | E2SM-RC per-RNTI (throttle attacker saja, bukan seluruh sel) |
| **Standar E2SM** | KPM standar + RC standar + MAC Custom E2SM | KPM standar + RC standar + MAC Custom E2SM |

---

## 8. Framing untuk Buku TA

**Ringkasan performa LSTM dari evaluasi T50** (referensi figures):

| Serangan | Recall LSTM | Keterangan | Figure |
|----------|------------|------------|--------|
| DL Flood | **97.8%** | Terdeteksi karena scaler clipping | fig8, fig5 |
| Signaling Storm | 10.7% | Parsial; diperkuat Rule 3b | fig8 |
| Burst ON/OFF | 3.7% | Lemah; pola temporal tersamar | fig8 |
| UL Flood | 0.3% | Overlap distribusi dengan speedtest | fig8, fig2 |
| RF Burst | 0.0% | PRB turun saat jamming → terlihat idle | fig8, fig2 |
| **Overall** | **23.1%** | Recall rendah; FPR = 2.2%, AUC = 0.733 | fig3, fig4 |

Model konvergen dengan baik (best epoch 98, tidak overfitting — `eval_figures/fig1_training_loss.png`). Performa rendah bukan karena model buruk, melainkan karena keterbatasan discriminative fitur cell-level KPM untuk sebagian besar jenis serangan.

**Kontribusi T50 yang valid dan akan dilaporkan di Buku TA**:
- Implementasi dual-path detection (LSTM + rule-based) pada testbed O-RAN fisik
- **Active mitigation via E2SM-RC Style 2 PRB Throttle** — native O-RAN, tanpa SSH ke Core Node (diimplementasikan 11 Mei 2026)
- **Deteksi RRC storm via `empty_ind_rate`** — memanfaatkan side-effect srsRAN SIZE(0) KPM sebagai sinyal proxy (diimplementasikan 14 Mei 2026)
- Analisis empiris keterbatasan discriminative E2SM-KPM cell-level untuk deteksi UL flood
- Identifikasi scheduler-driven greedy PRB allocation sebagai penyebab fundamental overlap distribusi benign vs flood

**Limitation yang harus disebutkan eksplisit**:
1. Dataset training menggunakan 1 UE → validasi terbatas pada single-attacker scenario
2. KPM cell-level features tidak dapat mengisolasi attacker jika ada ≥2 UE aktif bersamaan
3. UL Flood hanya terdeteksi via threshold rule-based, bukan LSTM — karena cell-level KPM tidak mengekspos MAC queue semantics atau flow metadata
4. iperf3 UL bitrate tidak dapat dikontrol di atas 5G testbed ini: scheduler srsRAN selalu alokasi PRB maksimum tanpa memandang target throughput aplikasi
5. PRB throttle berlaku untuk seluruh slice (cell-level), belum per-RNTI attacker saja
6. CQI keep-last policy srsRAN membuat Rule 2 (signaling storm via CQI) tidak berfungsi

**Kalimat akademik untuk paper/thesis**:

> *"Experimental evaluation reveals that cell-level O-RAN KPM telemetry exhibits fundamental discriminative limitations between benign high-throughput uplink transfers and malicious uplink flooding attacks. In 5G NR testbeds employing greedy PRB schedulers, any data-intensive uplink application — regardless of target bitrate — saturates available PRBs through scheduler-driven buffer draining. Consequently, instantaneous PRB utilization alone is insufficient for attack classification at the cell-aggregate level. Anomaly detection of uplink flooding must rely on temporal persistence, traffic dynamics, and cross-layer behavioral asymmetry rather than instantaneous resource metrics."*

**Rencana perbaikan di Buku TA** (urutan prioritas implementasi):

| # | Perbaikan | Target Serangan | Kompleksitas | Prasyarat |
|---|-----------|----------------|-------------|-----------|
| 5 | Tambah `empty_ind_rate` ke CSV + retrain | Signaling Storm | Rendah | Tidak ada |
| 8 | Turunkan threshold via ROC analysis | Signaling Storm, partial lainnya | Rendah | Dataset sekarang cukup |
| 6 | Perluas window LSTM + tambah variance features | Burst ON/OFF | Sedang | Rekam ulang dataset |
| 7 | Rule-based RF Burst detector (sudden-drop) | RF Burst | Sedang | Tidak ada |
| 1 | KPM Style 4 per-RNTI + retrain per-UE | UL Flood, semua serangan | Tinggi | Patch srsRAN |

> *"Fase Buku TA akan memulai dari perbaikan yang tidak memerlukan perubahan platform: penambahan sinyal `empty_ind_rate` ke pipeline LSTM (Perbaikan 5), optimasi threshold via ROC analysis (Perbaikan 8), dan implementasi rule-based detector untuk RF Burst dan Burst ON/OFF (Perbaikan 6b, 7). Perbaikan arsitektural jangka panjang — E2SM-KPM Style 4 untuk data per-RNTI — memerlukan perubahan di sisi srsRAN dan direncanakan sebagai perbaikan terakhir yang akan memungkinkan isolasi RNTI attacker, analisis temporal per-UE, dan deteksi UL Flood berbasis LSTM melalui fitur persistensi traffic per-aliran."*
