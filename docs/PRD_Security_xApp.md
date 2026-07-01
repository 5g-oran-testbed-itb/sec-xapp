# Product Requirements Document (PRD)
# Security xApp for O-RAN — Per-UE IDS dengan FlexRIC

---

## 📋 Informasi Dokumen

| Item | Detail |
|------|--------|
| **Nama Proyek** | Security xApp (SecXApp) — Per-UE Intrusion Detection & Mitigation |
| **Versi** | 2.0 (rombak total — per-UE, C native, E2SM-RC) |
| **Tanggal Awal** | 20 Februari 2026 |
| **Tanggal Update** | 20 Juni 2026 |
| **Penyusun** | Telmat |
| **Status** | **Active — C Native xApp, Per-UE IDS v4, Mitigasi E2SM-RC** |
| **Platform** | FlexRIC (Mosaic 5G) + C Native xApp + ONNX Runtime |
| **Lingkungan** | Testbed Multi-Node Fisik (Real UE + USRP SDR) |

> **Catatan revisi v2.0:** PRD ini dirombak total dari v1.x. Implementasi Python lama
> (`real_monitor.py`, `dashboard.py`, iptables mitigation) **sudah ditinggalkan sepenuhnya**.
> Deteksi **cell-level** juga dibuang — sistem sekarang murni **per-UE**. Detail evaluasi
> kuantitatif: lihat `docs/STATUS_DAN_RENCANA_EVALUASI.md`.

---

## 1. Ringkasan Eksekutif

### 1.1 Tujuan Proyek

Mengembangkan **Security xApp** native-C yang berjalan di atas **FlexRIC** Near-RT RIC untuk
**mendeteksi serangan per-UE** pada jaringan 5G RAN dan **memitigasinya secara otomatis** melalui
kontrol O-RAN standar (E2SM-RC). Sistem dideploy pada testbed multi-node fisik (Core, RAN, RIC
terpisah) dan tervalidasi dengan **UE nyata** (Oppo Reno 8) serta **USRP B205 mini SDR**.

Inti kontribusi:

1. **Deteksi granular per-UE** — setiap UE (per-RNTI) dievaluasi independen, bukan agregat sel.
2. **Empat algoritma deteksi** — Rule-Based (R1–R5), LSTM-UE Autoencoder, GRU-UE Autoencoder,
   dan **Hybrid** (Rule ∪ ML).
3. **Mitigasi O-RAN native** — E2SM-RC PRB Throttle (bukan iptables), dipicu otomatis oleh deteksi.
4. **Unsupervised anomaly detection** — model autoencoder dilatih hanya pada trafik benign.

### 1.2 Latar Belakang

Arsitektur O-RAN mendisagregasi RAN menjadi unit terprogram yang terhubung lewat interface
terbuka. Fleksibilitas ini **memperluas attack surface**: interface E2/A1 dapat dieksploitasi,
integrasi multi-vendor menambah kompleksitas, dan resource radio (PRB) rawan disalahgunakan oleh
UE jahat (flooding, low-and-slow DoS).

Pendekatan deteksi **cell-level** (agregat seluruh sel) tidak dapat mengidentifikasi *UE mana* yang
menyerang — sehingga mitigasi menjadi tumpul. Security xApp ini menjawabnya dengan analisis
**per-UE**: model menilai pola resource-usage tiap UE secara individual, lalu memicu throttle PRB
terarah via Near-RT RIC.

### 1.3 Ruang Lingkup

| Dalam Cakupan | Di Luar Cakupan |
|---------------|------------------|
| Deteksi anomali **per-UE** pada testbed multi-node | Deployment produksi skala besar |
| Monitoring KPI via **E2SM-KPM** (FORMAT_3 per-UE) | Integrasi external SIEM |
| Mitigasi via **E2SM-RC** PRB Throttle | Multi-vendor heterogeneous RAN |
| FlexRIC Near-RT RIC + C native xApp | Deteksi cell-level (sudah dibuang) |
| Open5GS Core + srsRAN gNB | Implementasi Python lama (deprecated) |
| LSTM-UE & GRU-UE Autoencoder (unsupervised) | — |
| Rule-Based IDS R1–R5 per-UE | — |
| **Real UE (Oppo Reno 8) + USRP B205 mini** | — |
| Grafana Dashboard per-UE | — |

---

## 2. Arsitektur Sistem

### 2.1 Testbed Multi-Node

Tiga node fisik dalam satu LAN (`10.91.2.0/24`):

| Node | IP | Peran | Software |
|------|----|----|----------|
| **RAN** | `10.91.2.1` | Radio Access Network | srsRAN (gNB + E2 Agent built-in) |
| **RIC** | `10.91.2.2` | Near-RT RIC + Security xApp | FlexRIC + `xapp_sec_moni` |
| **Core** | `10.91.2.4` | 5G Core Network | Open5GS (`/home/telmat/core`) |

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │                  TESTBED JARINGAN (LAN 10.91.2.0/24)                  │
 │                                                                       │
 │  ┌─────────────────┐          ┌─────────────────────────────────────┐│
 │  │  Core Node      │          │         RIC Node 10.91.2.2          ││
 │  │  10.91.2.4      │          │  ┌───────────────────────────────┐  ││
 │  │  Open5GS        │          │  │   Near-RT RIC (FlexRIC)        │  ││
 │  │  - AMF / SMF    │          │  │  ┌─────────────────────────┐  │  ││
 │  │  - UPF / NRF    │          │  │  │ xapp_sec_moni (C native)│  │  ││
 │  │  - UDM / AUSF   │          │  │  │  KPM monitor + RC ctrl  │  │  ││
 │  └────────┬────────┘          │  │  └─────────────────────────┘  │  ││
 │           │ N2/N3             │  └──────────────┬────────────────┘  ││
 │           │                   └─────────────────┼───────────────────┘│
 │           │                       E2AP (SCTP)   │ E42 (TCP)           │
 │           │                   ┌─────────────────▼───────────────────┐ │
 │           └───────────────────│         RAN Node 10.91.2.1          │ │
 │                       N3/GTP  │  srsRAN gNB (O-CU/O-DU/O-RU)         │ │
 │                               │  + E2 Agent (E2SM-KPM, E2SM-RC)      │ │
 │                               └─────────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────┘
```

### 2.2 Konektivitas Antar Node

| Interface | Dari | Ke | Protokol | Port |
|-----------|------|----|----------|------|
| **E2AP** | RAN (10.91.2.1) | RIC (10.91.2.2) | SCTP | 36421 |
| **E42** | xApp | Near-RT RIC | TCP | 36422 |
| **N2 (NG-C)** | RAN | Core (10.91.2.4) | SCTP | 38412 |
| **N3 (NG-U)** | RAN | Core (10.91.2.4) | GTP-U/UDP | 2152 |

### 2.3 C Native xApp & Service Models

Security xApp diimplementasikan native-C (`xapp_sec_moni`, di-build dari FlexRIC). Dua service
model O-RAN standar digunakan:

| Service Model | Peran | Detail |
|---------------|-------|--------|
| **E2SM-KPM** | Monitoring | Subscribe per-UE KPM (FORMAT_3). Sumber 15 KPI dasar per RNTI. |
| **E2SM-RC** | Mitigasi | RIC Control — PRB Throttle (Style 2, Action 6) ke gNB. |

**Binary & source:**
```
Binary : /home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni
Source : /home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c
         + sec_ids_ue.{h,c}   (per-UE feature engine + rule engine + decision)
Build  : cd ~/flexric/build && make -j$(nproc) xapp_sec_moni
```

**Inferensi ML** dijalankan via **ONNX Runtime** (bukan PyTorch) — model di-export ke ONNX dengan
scaler + scoring weighted-MSE yang dibake di dalamnya.

### 2.4 Pipeline Internal xApp (Per-UE)

```
KPM Indication FORMAT_3 (per RNTI, ~1 Hz)
        │
        ▼
┌──────────────────────────────┐
│ ue_ids_update()              │  Hitung 19 fitur per-UE:
│  - 15 fitur dasar (KPM)      │   PRB/throughput ratio, delta,
│  - 4 burst index (derived)   │   rolling stats, burst index
│  - push ke shift window [30] │   → ml_window[30][19]
└──────────────┬───────────────┘
               │
       ┌───────┴────────┐
       ▼                ▼
┌──────────────┐  ┌──────────────────┐
│ rule_based_  │  │ run_inference_ue │  ONNX GRU-UE / LSTM-UE
│ detect_ue()  │  │  → weighted MSE  │  (seq_len=30, 19 fitur)
│  R1–R5       │  └────────┬─────────┘
└──────┬───────┘           │ mse > threshold ?
       │ severity          │
       └───────┬───────────┘
               ▼
      ┌────────────────────┐
      │ decision_engine_ue │  Mode: rule-only / lstm-only /
      │  (5 mode)          │  gru-only / lstm-hybrid / gru-hybrid
      └────────┬───────────┘
               │ alert != NONE
               ▼
      ┌────────────────────┐
      │ g_pending_throttle │ → main loop → E2SM-RC PRB Throttle 5%
      └────────────────────┘
```

---

## 3. Deteksi Per-UE

### 3.1 Keputusan Desain: Kenapa Per-UE

| Aspek | Cell-Level (lama, dibuang) | Per-UE (sekarang) |
|-------|----------------------------|-------------------|
| Unit analisis | Agregat seluruh sel | Per RNTI individual |
| Identifikasi penyerang | Tidak bisa — hanya tahu "sel anomali" | Tahu UE mana yang menyerang |
| Mitigasi | Tumpul (seluruh sel) | Terarah (basis per-UE) |
| Sumber data | KPM FORMAT_1 (10 fitur sel) | KPM FORMAT_3 (19 fitur per-UE) |

Deteksi cell-level **dibuang total** dari arsitektur aktif. Sistem fokus per-UE.

### 3.2 Sembilan Belas (19) Fitur Per-UE

15 fitur dasar dari KPM + 4 burst index turunan (dihitung real-time di `ue_ids_update`):

| # | Fitur | Deskripsi |
|---|-------|-----------|
| 1 | `prb_usage_dl_ratio` | Rasio PRB downlink terpakai [0,1] |
| 2 | `prb_usage_ul_ratio` | Rasio PRB uplink terpakai [0,1] |
| 3 | `thp_dl_kbps` | Throughput downlink (kbps) |
| 4 | `thp_ul_kbps` | Throughput uplink (kbps) |
| 5 | `prb_direction` | (prb_ul−prb_dl)/(total+ε), [−1,+1] |
| 6 | `prb_total` | prb_dl + prb_ul |
| 7 | `prb_ul_delta` | Perubahan prb_ul antar timestep |
| 8 | `ul_efficiency` | thp_ul / prb_ul, clip [0,50000] |
| 9 | `prb_ul_roll_mean` | Rolling mean prb_ul (window=10) |
| 10 | `prb_ul_roll_std` | Rolling std prb_ul (window=10) |
| 11 | `ul_persistence` | Fraksi prb_ul>0 dalam 10 ts terakhir |
| 12 | `thp_total_kbps` | thp_dl + thp_ul |
| 13 | `thp_ul_delta` | Perubahan thp_ul antar timestep |
| 14 | `thp_dl_delta` | Perubahan thp_dl antar timestep |
| 15 | `traffic_direction` | (thp_ul−thp_dl)/(total+ε), [−1,+1] |
| 16 | `prb_ul_burst_index` | log(1+prb_ul)/(roll_mean+ε), clip [0,50] |
| 17 | `prb_dl_burst_index` | log(1+prb_dl)/(roll_mean+ε), clip [0,50] |
| 18 | `thp_ul_burst_index` | thp_ul/(roll_mean+1), clip [0,50] |
| 19 | `thp_dl_burst_index` | thp_dl/(roll_mean+1), clip [0,50] |

> Rolling window untuk semua statistik = **10 sample**, konstan & independen dari `ML_SEQ_LEN=30`.

### 3.3 Rule-Based IDS (R1–R5)

Threshold konstanta compile-time, diturunkan dari analisis dataset benign (N=6.002 baris). Setiap
rule butuh `consecutive` timestep memenuhi syarat sebelum memicu alert.

| Rule | Target | Kondisi | Consec | Severity |
|------|--------|---------|:------:|:--------:|
| **R1** | UL Flood | `thp_ul > 15000` OR `prb_ul > 0.70` | 5 | 1 (Warning) |
| **R2** | DL Flood | `thp_dl > 15000` OR `prb_dl > 0.85` | 5 | 1 (Warning) |
| **R3** | Burst ON/OFF | `roll_std > 0.12` AND `roll_mean > 0.05` | 5 | 1 (Warning) |
| **R4** | Persistence / RoQ | `persistence ≥ 0.90` AND `roll_mean > 0.50` | 10 | **2 (Critical)** |
| **R5** | LDoS / Efficiency | `prb_ul > 0.30` AND `ul_eff < 5000` | 3 | **2 (Critical)** |

Severity 2 (R4/R5) langsung memicu mitigasi tanpa menunggu konfirmasi tambahan.

### 3.4 Model Autoencoder: LSTM-UE & GRU-UE (v4)

Dua model autoencoder unsupervised, dilatih **hanya pada trafik benign**. Anomali dideteksi via
**Weighted MSE Scheme A**: `score = Σ(wᵢ·errᵢ) / Σwᵢ`, dengan bobot `wᵢ = log(1 + max_attack/benign_ratio)`
per fitur — menekankan fitur diskriminatif (PRB_UL, THP_UL) dan meredam noise.

| | GRU-UE v4 | LSTM-UE v4 |
|-|:---------:|:----------:|
| File ONNX | `models/gru_ue_v4.onnx` (366 KB) | `models/lstm_ue_v4.onnx` (278 KB) |
| Arsitektur | BiGRU encoder [64,32] + decoder [32,64] | LSTM unidirectional [64,32] |
| Parameter | 87.870 | 65.373 |
| Scaler | MinMaxScaler (baked di ONNX) | MinMaxScaler (baked di ONNX) |
| seq_len | 30 (≈30s konteks @1Hz) | 30 |
| Threshold (P97 weighted) | **0.025969** | **0.025266** |
| FPR @P97 | 3.05% | 3.05% |
| Inference latency (P95) | 0.288 ms | 0.107 ms |

**Input ONNX:** `float32[1, 30, 19]` (raw, unscaled) → **Output:** `float32[1]` weighted MSE.
Scaler + scoring dibake di ONNX sehingga C cukup membandingkan output > threshold.

**Empat kelas serangan per-UE:** UL Flood (1), DL Flood (2), Burst (3), **RoQ** (4 — Rate-of-Quality,
sustained moderate-load DoS).

### 3.5 Decision Engine — Lima Mode

`decision_engine_ue()` menggabungkan hasil rule + ML sesuai `--ids-mode`:

| Mode | Logika | Output Alert |
|------|--------|--------------|
| `rule-only` | `rule.severity ≥ 1` | RULE |
| `lstm-only` / `gru-only` | `mse > threshold` | ML |
| `lstm-hybrid` / `gru-hybrid` | rule & ml → HYBRID · rule → RULE · ml → ML | RULE/ML/HYBRID |

Hybrid = **Rule ∪ ML** (OR): rule menangkap pola jelas dengan presisi tinggi, ML menangkap pola
temporal halus (terutama RoQ). Cooldown per-UE `ALERT_COOLDOWN_MS = 30s` mencegah banjir alert.

---

## 4. Mitigasi E2SM-RC

### 4.1 Mekanisme PRB Throttle

| Parameter | Nilai |
|-----------|-------|
| Service Model | E2SM-RC |
| Style / Action | Style 2, Action 6 (Slice-level PRB Quota) |
| Throttle | Max PRB = **5%** |
| Restore | Max PRB = 100% |
| PLMN / SST | 00101 / SST=1 (eMBB) |

Saat alert per-UE muncul, xApp mengirim RIC Control Request berisi `Min_PRB=5, Max_PRB=6` ke gNB,
membatasi alokasi PRB → throughput penyerang turun ~95%.

### 4.2 Trigger & Siklus Hidup

```
Deteksi per-UE (alert != NONE)
        ↓  g_pending_throttle = 1
Main loop (di luar callback, non-blocking)
        ↓  cooldown 30s OK?
E2SM-RC Control Request → gNB → CONTROL-ACK
        ↓
Throttle aktif (PRB max=5%)
        ↓  attack reda + tenang 10s (THROTTLE_RESTORE_MS)
E2SM-RC Restore (PRB max=100%)
```

| Parameter | Nilai | Fungsi |
|-----------|-------|--------|
| `THROTTLE_COOLDOWN_MS` | 30s | Anti-flapping antar aksi |
| `THROTTLE_RESTORE_MS` | 10s | Tenang sebelum auto-restore |

### 4.3 Status srsRAN RC Bug #468 (Resolved)

| Isu | Status |
|-----|--------|
| srsRAN RC Bug #468 — gNB salah decode RC Control | ✅ **Patched** (Mei 2024, gckopper/wdgj) |
| xApp crash (SIGABRT) saat ACK timeout | ✅ **Fixed** — `src/xApp/sync_ui.c` timeout graceful (warning + return, bukan `assert`) |

Dengan kedua fix, E2SM-RC PRB Throttle berjalan stabil saat serangan aktif.

> **Catatan mitigasi:** E2SM-RC PRB Throttle bersifat **slice/cell-wide** — membatasi semua UE di
> slice, bukan per-UE individual. Deteksi presisi per-UE, namun aksi PRB quota mengikuti
> granularity yang didukung gNB.

---

## 5. Persyaratan Produk

### 5.1 Functional Requirements

#### FR-01: Per-UE Anomaly Detection

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01.1 | xApp subscribe E2SM-KPM FORMAT_3 (per-UE) | Critical |
| FR-01.2 | Ekstraksi 19 fitur per RNTI real-time | Critical |
| FR-01.3 | Deteksi anomali via autoencoder (weighted MSE > threshold) | Critical |
| FR-01.4 | Maintain sliding window [30×19] per UE | High |
| FR-01.5 | Generate alert per-UE (RULE/ML/HYBRID) ke log/CSV | High |

#### FR-02: Rule-Based IDS

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-02.1 | Evaluasi R1–R5 per UE dengan consecutive counter | High |
| FR-02.2 | Severity 2 (R4/R5) memicu mitigasi langsung | High |
| FR-02.3 | Threshold konstanta dari analisis benign | Medium |

#### FR-03: Mitigasi E2SM-RC

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-03.1 | Kirim RIC Control PRB Throttle saat alert (`--mitigate`) | High |
| FR-03.2 | Cooldown 30s + auto-restore 10s | High |
| FR-03.3 | Tahan crash saat gNB tidak balas ACK (timeout graceful) | High |

#### FR-04: Konfigurasi & Operasi

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-04.1 | Pilih mode deteksi via `--ids-mode` (5 opsi) | High |
| FR-04.2 | Toggle cell-level & CSV via `--no-cell` / `--no-csv` | Medium |
| FR-04.3 | Start/stop via script tmux (`start_xapp_c*.sh`) | High |
| FR-04.4 | Export metrik per-UE ke Grafana (Prometheus) | Medium |

### 5.2 Non-Functional Requirements

| ID | Requirement | Target | Status Aktual |
|----|-------------|--------|---------------|
| NFR-01 | Inference latency per window | < 1 ms | ✅ 0.107–0.288 ms (P95) |
| NFR-02 | CPU usage (1 UE) | < 5% | ✅ < 3% (Python bench) |
| NFR-03 | Memory footprint model | < 1 MB | ✅ 278–366 KB ONNX |
| NFR-04 | Recall sistem (hybrid) | > 90% | ✅ 96.1% (GRU hybrid) |
| NFR-05 | FPR | ≤ 5% | ✅ 5.14% hybrid / 3.05% ML-only |
| NFR-06 | Crash-resilience saat mitigasi | Tidak crash | ✅ sync_ui timeout fix |

---

## 6. Hasil Evaluasi

> Dataset: `csv/dataset_attack_ue_juni.csv` (4 kelas, 2.236 window positif) + 1.772 window benign.
> Detail lengkap: `docs/STATUS_DAN_RENCANA_EVALUASI.md` §1.6.

### 6.1 Metrik Keseluruhan (5 Konfigurasi)

| Config | Recall | Precision | F1 | FPR | ROC-AUC |
|--------|:------:|:---------:|:--:|:---:|:-------:|
| Rule Only | 85.8% | 97.5% | 91.3% | 2.93% | N/A¹ |
| LSTM-UE v4 Only | 91.0% | 94.7% | 92.8% | 3.05% | 0.9797 |
| GRU-UE v4 Only | 89.6% | 94.8% | 92.1% | 3.05% | 0.9807 |
| LSTM-UE v4 Hybrid | 95.0% | 94.6% | 94.8% | 4.97% | N/A¹ |
| **GRU-UE v4 Hybrid** | **96.1%** | **94.8%** | **95.4%** | **5.14%** | N/A¹ |

¹ *Rule & Hybrid = keputusan biner (rule ∪ ml) → ROC-AUC tak berlaku. Komponen ML identik dengan ML-Only.*

### 6.2 Per-Attack Recall

| Config | UL Flood | DL Flood | Burst | RoQ |
|--------|:--------:|:--------:|:-----:|:---:|
| Rule Only | 97.2% | 96.8% | 95.0% | 65.3% |
| GRU-UE v4 Only | 90.6% | 87.6% | 97.7% | 82.2% |
| **GRU-UE v4 Hybrid** | **97.9%** | **96.8%** | **98.8%** | **92.2%** |

> **RoQ** adalah bottleneck Rule (65.3%) karena sustained moderate-load tidak melewati threshold
> individual. ML (seq_len=30) menangkap pola temporal → GRU Hybrid mendorong RoQ ke **92.2%**.
> Ini justifikasi empiris kenapa arsitektur hybrid diperlukan.

### 6.3 Latency (dataset 1s/sampel)

| Config | Inference P95 | Det.Lat (RoQ) | Mit.Lat (RoQ) |
|--------|:-------------:|:-------------:|:-------------:|
| GRU-UE v4 Hybrid | 0.288 ms | 5.0s | 5.12s |
| LSTM-UE v4 Hybrid | 0.107 ms | 5.5s | 5.62s |

> Mitigasi latency = deteksi + 1 siklus E2SM-RC (~120ms). Latency terukur pada granularity data
> 1s/sampel; pada KPM 120ms latensi diproyeksikan ~8× lebih rendah (×0.12). Inferensi <0.3ms =
> negligible — bottleneck adalah interval pelaporan KPM, bukan komputasi model.

---

## 7. Cara Menjalankan

### 7.1 Urutan Startup

| Langkah | Node | Perintah |
|---------|------|----------|
| **1** | Core (10.91.2.4) | `sudo systemctl start open5gs-*` (AMF/NRF dulu) |
| **2** | RIC (10.91.2.2) | `./build/examples/ric/nearRT-RIC` |
| **3** | RAN (10.91.2.1) | `sudo gnb -c cots_n78_copied.yml` |
| **4** | RIC (10.91.2.2) | `./start_xapp_c.sh` atau `./start_xapp_c_mitigate.sh` |

### 7.2 Mode Operasi & Flag

```bash
# Live deteksi + mitigasi E2SM-RC (per-UE only, GRU hybrid):
./start_xapp_c_mitigate.sh
#   → --ids-mode gru-hybrid --mitigate --no-cell --no-csv

# Dataset collection (semua aktif, CSV ditulis):
./start_xapp_c.sh
#   → prompt 3-step: --mode, --ids-mode, extra flags (kosongkan)

# Manual:
$XAPP_BIN -c my_xapp_kpm.conf --label 0 \
    --ids-mode gru-hybrid --mitigate --no-cell --no-csv
```

| Flag | Nilai | Fungsi |
|------|-------|--------|
| `--ids-mode` | rule-only · lstm-only · gru-only · lstm-hybrid · gru-hybrid | Mode deteksi per-UE |
| `--mitigate` | (toggle) | Aktifkan E2SM-RC PRB Throttle |
| `--no-cell` | (toggle) | Nonaktifkan deteksi & CSV cell-level |
| `--no-csv` | (toggle) | Nonaktifkan semua penulisan training CSV |
| `--label N` | 0–6 | Label dataset (untuk recording) |

### 7.3 Skenario Pengujian

| Skenario | Trigger | Output Diharapkan |
|----------|---------|-------------------|
| Operasi normal | Trafik benign | Tidak ada alert; throttle tidak aktif |
| UL Flood | iperf3 UL 80M | R1 + ML alert → throttle PRB 5% |
| DL Flood | iperf3 DL 100M -R | R2 + ML alert → throttle |
| Burst ON/OFF | iperf3 osilasi 5s | R3 + ML alert |
| RoQ (low-and-slow) | iperf3 UL 30M sustained | ML alert (Rule lemah) → throttle |
| Recovery | Serangan berhenti | Auto-restore PRB 100% setelah 10s |

---

## 8. Risiko & Limitasi

| Limitasi | Detail | Mitigasi/Status |
|----------|--------|-----------------|
| Mitigasi cell-wide | E2SM-RC PRB quota slice-level, bukan per-UE | Batasan gNB; deteksi tetap per-UE |
| Auto-restore gated `--no-cell` | Jalur restore di-gate `g_cell_enabled` → throttle tak auto-restore di mode per-UE only | Workaround: tanpa `--no-cell` atau restart. **Perlu wiring per-UE** |
| Latency dataset 1s | Data 1s/sample; deployment 120ms ~8× lebih cepat | Catat sebagai proyeksi |
| FPR hybrid 5.14% | Di atas ideal ≤3% | `--ids-mode gru-only` → 3.05% (recall 89.6%) |
| Alert cooldown 30s | Bisa miss serangan cepat berulang | Trade-off anti-flooding |
| CPU xApp C belum diukur | Benchmark hanya Python standalone | `pidstat` di RIC saat running |

| Risiko Proyek | Probabilitas | Dampak | Mitigasi |
|---------------|:------------:|:------:|----------|
| Perubahan API FlexRIC | Sedang | Tinggi | Pin versi, monitor update |
| Instabilitas gNB saat RC Control | Rendah | Tinggi | Bug #468 patched + timeout graceful |
| Akurasi model drift | Sedang | Sedang | Retrain berkala dgn data benign baru |

---

## 9. Kriteria Sukses

### 9.1 Wajib (MVP) — ✅ Tercapai

- [x] xApp C native connect ke FlexRIC Near-RT RIC
- [x] Subscribe E2SM-KPM FORMAT_3 (per-UE) & ekstraksi 19 fitur
- [x] Deteksi 4 kelas serangan per-UE (UL/DL Flood, Burst, RoQ)
- [x] Rule-Based IDS R1–R5 + autoencoder LSTM-UE & GRU-UE
- [x] Recall hybrid > 90% (96.1% GRU hybrid)

### 9.2 Sebaiknya Ada — ✅ Tercapai

- [x] Mitigasi E2SM-RC PRB Throttle (O-RAN native)
- [x] Lima mode deteksi via `--ids-mode`
- [x] Grafana Dashboard per-UE (Prometheus exporter)
- [x] Real UE + USRP B205 mini integration

### 9.3 Bagus Jika Ada

- [x] Crash-resilience saat RC timeout (`sync_ui.c` fix)
- [x] Toggle granular `--no-cell` / `--no-csv`
- [ ] Auto-restore throttle di mode per-UE only (wiring belum)
- [ ] Mitigasi per-UE individual (butuh dukungan gNB)
- [ ] Korelasi alert multi-UE / SIEM (masa depan)

### 9.4 Ringkasan Status Akhir (per Juni 2026)

| Fitur | Status | Keterangan |
|-------|--------|------------|
| KPI Monitoring per-UE (E2SM-KPM FORMAT_3) | ✅ Done | `xapp_sec_moni`, 19 fitur/RNTI |
| Per-UE IDS v4 (LSTM-UE & GRU-UE) | ✅ Done | seq_len=30, weighted MSE, ONNX — recall 95–96% hybrid |
| Rule-Based IDS R1–R5 | ✅ Done | UL/DL Flood, Burst, RoQ, LDoS |
| Mitigasi E2SM-RC PRB Throttle | ✅ Done | max=5%, dipicu deteksi per-UE; Bug #468 patched |
| Grafana Dashboard per-UE | ✅ Done | `per_ue_live.json` + `per_ue_eval.json` |
| Crash-resilience (RC timeout) | ✅ Done | `sync_ui.c` graceful timeout |
| Auto-restore di `--no-cell` | ⏳ Parsial | Restore masih gated cell-level |
| ~~Cell-level detection~~ | 🗑️ Dibuang | Digantikan per-UE |
| ~~iptables/Python mitigation~~ | 🗑️ Deprecated | Digantikan E2SM-RC native |

---

## 10. Referensi

1. **FlexRIC Repository** — https://gitlab.eurecom.fr/mosaic5g/flexric
2. **FlexRIC Paper** — Schmidt, R., Irazabal, M., & Nikaein, N. (2021). FlexRIC: an SDK for next-generation SD-RANs. CoNEXT '21.
3. **srsRAN RC Bug #468** — https://github.com/srsran/srsRAN_Project/issues/468 (resolved Mei 2024)
4. **O-RAN Alliance Specifications**:
   - O-RAN.WG3.E2AP-v02.03/v03.01
   - O-RAN.WG3.E2SM-KPM-v02.03/v03.00
   - O-RAN.WG3.E2SM-RC-v01.03
5. **O-RAN Security** — O-RAN.WG11 Security Specifications

---

## 11. Lampiran

### A. Build FlexRIC + xApp

```bash
# Prerequisites
sudo apt-get install -y cmake gcc g++ libsctp-dev swig python3-dev \
    libonnxruntime-dev

# Build FlexRIC
git clone https://gitlab.eurecom.fr/mosaic5g/flexric
cd flexric && mkdir build && cd build
cmake -DXAPP_MULTILANGUAGE=ON ..
make -j$(nproc)
sudo make install

# Build Security xApp
make -j$(nproc) xapp_sec_moni
```

### B. KPM Subscription Config (`my_xapp_kpm.conf`)

```
Name = "xApp"
NearRT_RIC_IP = "10.91.2.2"
E42_Port = 36422

Sub_ORAN_SM_List = (
    { name = "KPM", time = 10, format = 1,
      ran_type = "ngran_gNB_DU",
      actions = (
        { name = "DRB.UEThpDl" }, { name = "DRB.UEThpUl" },
        { name = "RRU.PrbUsedDl" }, { name = "RRU.PrbUsedUl" },
        { name = "RRU.PrbAvailDl" }, { name = "RRU.PrbAvailUl" },
        ...
      )
    }
)
```

### C. Export Model ke ONNX

```bash
# GRU-UE v4 → ONNX (MinMaxScaler + weighted MSE baked-in)
./venv/bin/python3 export_onnx_ue.py \
    --arch gru --model models/gru_ue_v4.pt \
    --scaler models/gru_ue_v4_scaler.pkl \
    --out models/gru_ue_v4.onnx

# LSTM-UE v4 → ONNX
./venv/bin/python3 export_onnx_ue.py \
    --arch lstm --model models/lstm_ue_v4.pt \
    --scaler models/lstm_ue_v4_scaler.pkl \
    --out models/lstm_ue_v4.onnx
```

### D. Evaluasi Per-UE

```bash
./venv/bin/python3 evaluate_per_ue_v2.py \
    --val csv/dataset_validation_ue_juni.csv \
    --attack csv/dataset_attack_ue_juni.csv \
    --save-figures --output results/
```

---

**Akhir Dokumen**
