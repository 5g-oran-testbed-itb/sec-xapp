# Two-Stage Hybrid Detection for O-RAN Security xApp — Design Spec

**Tanggal:** 16 Mei 2026  
**Konteks:** Buku TA — Pengembangan lanjutan dari T50 (xapp_sec_moni C native)  
**Scope:** Approach A (Minimal-risk) — rule-based two-stage, LSTM Autoencoder dipertahankan

---

## Goal

Mengembangkan arsitektur two-stage hybrid detection pada `xapp_sec_moni` untuk:
1. Memberikan early warning resource saturation dalam <1 detik (Stage 1)
2. Mengurangi false positive dari trafik benign high-load (speedtest) via persistence confirmation (Stage 2)
3. Mempertahankan LSTM Autoencoder sebagai anomaly score layer tanpa perubahan model

**Framing akademik:**
> Stage 1 provides near-RT early warning within sub-second latency, while Stage 2 performs persistence-based confirmation before mitigation to reduce false positives from benign high-load traffic such as speedtest.

---

## Arsitektur

Satu C binary `xapp_sec_moni` dengan tiga layer:

```
KPM Indication (120ms)
        │
        ▼
┌─────────────────────────────────┐
│  Stage 1: Fast Anomaly Indication│  → RESOURCE_SATURATION_WARNING (<400ms)
│  (rule_based_detect() diperluas) │
└────────────────┬────────────────┘
                 │ WARNING triggers Stage 2 counter
                 ▼
┌─────────────────────────────────┐
│  Stage 2: Persistence Validator │  → CRITICAL_CONFIRMED (≥30s sustained)
│  (state baru di sec_ids_state_t)│
└────────────────┬────────────────┘
                 │ CRITICAL unlocks mitigation authorization
                 ▼
┌──────────────────────────────────────┐
│  Mitigation Authorization Layer      │  → E2SM-RC PRB Throttle (hanya jika --mitigate)
│  (rc_send_prb_quota() existing)      │
└──────────────────────────────────────┘
        │
        ▼ (paralel, tidak memblokir)
┌─────────────────────────────────┐
│  ONNX Anomaly Score             │  → anomaly_score ke CSV + log (existing, unchanged)
│  (existing inference pipeline)  │
└─────────────────────────────────┘
```

**Prinsip perubahan:**
- Tidak ada binary baru, tidak ada Python runtime baru
- Perubahan terlokalisir di `sec_ids.c`, `sec_ids.h`, dan sedikit di `xapp_sec_moni.c`
- `ue_tracker.c` tidak diubah (tetap dead code untuk future work)

---

## Section 1 — Stage 1: Fast Anomaly Indication

### Terminologi

Stage 1 **tidak mengklaim "attack detected"**. Output-nya adalah:

```
RESOURCE_SATURATION_WARNING
```

Karena PRB saturation bisa disebabkan trafik benign (speedtest). Stage 1 hanya menyatakan anomali resource, bukan intent serangan.

### Rules yang Diperluas/Ditambahkan

| Rule | Kondisi Trigger | Alert Type | Status |
|------|----------------|------------|--------|
| R1 (update) | `prb_ul_ratio > 0.80` | `ul_saturation` | diperluas dari >0.85 |
| R2 (update) | `prb_dl_ratio > 0.80` AND `prb_ul_ratio < 0.30` | `dl_saturation` | diperluas |
| R3b (existing) | `empty_ind_rate >= 2` per window | `rrc_storm` | tidak berubah |
| **R7 (baru)** | PRB sudden collapse + scheduling inactivity | `radio_degradation_suspicion` | baru |
| **R8 (baru)** | `prb_burst_index > 2.0` di ≥ 2 window berturut, lalu turun, lalu naik lagi | `periodic_burst_anomaly` | baru |

### Output Stage 1

```c
typedef enum {
    ALERT_NONE = 0,
    ALERT_UL_SATURATION,
    ALERT_DL_SATURATION,
    ALERT_RRC_STORM,
    ALERT_RADIO_DEGRADATION_SUSPICION,
    ALERT_PERIODIC_BURST_ANOMALY,
} alert_type_t;

typedef struct {
    int          severity;          /* 0=normal, 1=WARNING, 2=CRITICAL */
    alert_type_t alert_type;
    long long    stage1_ts_ms;      /* epoch ms saat WARNING pertama */
} stage1_result_t;
```

Stage 1 warning **tidak langsung trigger mitigasi**. Mitigasi hanya aktif setelah Stage 2 confirm via Mitigation Authorization Layer.

---

## Section 2 — Stage 2: Persistence Validator

### Justifikasi Threshold Persistence

Persistence threshold pada Stage 2 dipilih berdasarkan observasi empiris terhadap perilaku trafik benign dan trafik serangan pada testbed. Pengujian menunjukkan bahwa speedtest benign pada aplikasi nyata (seperti Ookla Speedtest) **pada testbed kami** menghasilkan saturasi PRB yang tinggi, namun bersifat sementara (transient) dan umumnya berlangsung sekitar **15–40 detik**. Sebaliknya, simulasi UL/DL Flood menghasilkan utilisasi PRB tinggi yang berlangsung secara kontinu selama **lebih dari 120 detik** tanpa recovery period yang signifikan.

Oleh karena itu, Stage 2 menggunakan persistence threshold **30 detik** sebagai batas bawah untuk membedakan transient resource saturation akibat aktivitas pengguna normal dengan sustained saturation yang konsisten dengan perilaku serangan flooding. Dengan pendekatan ini, Stage 1 tetap memenuhi requirement near-real-time melalui early warning sub-second latency, sedangkan Stage 2 berfungsi sebagai Mitigation Authorization Layer untuk menurunkan false positive pada kondisi high-throughput benign traffic.

### State Baru

```c
typedef struct {
    /* Stage 2 persistence counters */
    long long  saturation_start_ms;     /* epoch ms saat saturation mulai */
    long long  saturation_duration_ms;  /* durasi saturation kumulatif */
    int        burst_cycle_count;       /* jumlah ON→OFF→ON cycle */
    int        rrc_storm_window_count;  /* berapa window consecutive RRC storm */
    int        rf_suspicion_window_count; /* berapa window consecutive RF suspicion */
    long long  recovery_start_ms;       /* epoch ms saat recovery mulai */
    
    /* Stage 2 configuration (configurable, not hardcoded) */
    long long  cfg_saturation_confirm_ms;   /* default: 30000 — empiris dari speedtest 15-40s vs flood >120s */
    int        cfg_burst_cycle_threshold;   /* default: 3 */
    int        cfg_rrc_storm_confirm_win;   /* default: 4 */
    int        cfg_rf_suspicion_confirm_win;/* default: 5 */
    long long  cfg_recovery_confirm_ms;     /* default: 5000 */
} stage2_state_t;
```

### Threshold CRITICAL

Threshold menggunakan **durasi (ms), bukan jumlah window murni** — lebih robust terhadap jitter 120ms sampling:

| Alert Type | CRITICAL Condition | Default | Justifikasi |
|------------|-------------------|---------|-------------|
| `ul_saturation` / `dl_saturation` | `saturation_duration_ms >= cfg_saturation_confirm_ms` | 30000ms (30s) | Speedtest observed in testbed ≤40s transient; flood >120s sustained |
| `periodic_burst_anomaly` | `burst_cycle_count >= cfg_burst_cycle_threshold` dalam 60s | 3 cycles | Burst ON/OFF attack pola berulang |
| `rrc_storm` | `rrc_storm_window_count >= cfg_rrc_storm_confirm_win` | 4 windows | RRC storm sustained berbeda dari transient detach |
| `radio_degradation_suspicion` | `rf_suspicion_window_count >= cfg_rf_suspicion_confirm_win` | 5 windows | Sudden collapse sustained ≠ scheduling gap normal |

**Reset Stage 2**: jika PRB kembali normal selama `cfg_recovery_confirm_ms` (default 5000ms), semua counter di-reset.

### Output Stage 2

```
severity = CRITICAL_CONFIRMED
stage2_confirmation_time_ms = stage2_confirm_ts - stage1_ts
```

Stage 2 CRITICAL → unlock Mitigation Authorization Layer jika `--mitigate` aktif.

---

## Section 3 — ONNX Anomaly Score Integration

**Tidak ada perubahan pada model atau inference pipeline.**

Perubahan hanya di cara output digunakan:
- `anomaly_score` (float, existing) ditulis ke CSV sebagai kolom tambahan
- Dicetak ke log bersama `alert_type` dan `stage` untuk traceability
- Digunakan sebagai *supporting evidence* — bukan trigger mitigasi

Contoh log output:
```
[KPM] ts=1747390123456 stage=1 alert=ul_saturation severity=WARNING anomaly_score=0.0082
[KPM] ts=1747390153456 stage=2 alert=ul_saturation severity=CRITICAL anomaly_score=0.0094 confirmation_ms=30000
```

---

## Section 4 — R7: Radio-Layer Degradation Suspicion via Sudden Resource Collapse

Rule baru untuk mendeteksi indikasi degradasi radio layer berdasarkan pola sudden resource collapse pada air interface.

**Latar belakang:** CQI pada srsRAN menggunakan keep-last policy — saat UE detach atau channel terdegradasi, CQI tetap bernilai 15 (tidak di-reset). Oleh karena itu, CQI **tidak digunakan** karena tidak reliabel pada platform srsRAN. Sebagai gantinya, radio-layer degradation dideteksi melalui pola sudden PRB collapse dan scheduling inactivity.

**Trigger:**
```c
if (
    prev_prb_total > 0.40 &&      /* sebelumnya ada traffic */
    curr_prb_total < 0.05 &&      /* tiba-tiba collapse */
    air_delay_ul == 0             /* scheduling inactivity — UE tidak dijadwalkan */
) {
    alert_type = ALERT_RADIO_DEGRADATION_SUSPICION;
    severity = WARNING;
}
```

**Framing hasil:** `radio_degradation_suspicion` — suspicious radio-layer degradation pattern. Tidak diklaim sebagai "RF jammer definitively detected" karena PHY-layer telemetry (SINR histogram, RSRP) tidak tersedia via KPM cell-level.

**Stage 2 confirmation:** `rf_suspicion_window_count >= 5` (~600ms sustained collapse) → CRITICAL.

---

## Section 5 — R8: Periodic Burst Anomaly Detection

Rule baru untuk mendeteksi pola alternating saturation intermiten (sebelumnya disebut "Burst ON/OFF").

**`prb_burst_index`** (existing feature): `log(1 + prb_total) / (rolling_mean + ε)`

**Trigger Stage 1 (per window):**
```c
if (prb_burst_index > BURST_INDEX_THRESHOLD) {
    consecutive_burst_count++;
} else {
    if (consecutive_burst_count >= 2) {
        burst_cycle_count++;   /* satu ON→OFF cycle selesai */
    }
    consecutive_burst_count = 0;
}

if (burst_cycle_count >= 1) {
    alert_type = ALERT_PERIODIC_SATURATION;
    severity = WARNING;
}
// BURST_INDEX_THRESHOLD = 2.0 (configurable)
```

**Stage 2 confirmation:** `burst_cycle_count >= cfg_burst_cycle_threshold` (default: 3) dalam window 60 detik → CRITICAL.

**Catatan terminologi:** "Periodic Burst Anomaly" lebih tepat secara akademik dibanding "Burst ON/OFF Attack" karena pattern ini bisa juga muncul pada trafik video streaming yang memiliki burst periodik. Stage 2 dibutuhkan untuk konfirmasi.

---

## Section 6 — CSV Pipeline Update

### Kolom Baru di CSV Output

```
stage1_alert      (int: 0 or 1)
stage2_confirmed  (int: 0 or 1)
alert_type        (string: none/ul_saturation/dl_saturation/rrc_storm/radio_degradation_suspicion/periodic_burst_anomaly)
stage1_latency_ms (long long: ms dari KPM indication ke WARNING, 0 jika no alert)
stage2_confirmation_time_ms (long long: durasi Stage 1→Stage 2, 0 jika belum confirmed)
anomaly_score     (float: ONNX output, existing)
```

### Header CSV Final

```
timestamp_ms,prb_usage_dl_ratio,prb_usage_ul_ratio,cqi,rach_preamble,air_delay_ul,
prb_direction,prb_total,prb_dl_delta,prb_ul_delta,prb_burst_index,
label,stage1_alert,stage2_confirmed,alert_type,
stage1_latency_ms,stage2_confirmation_time_ms,anomaly_score
```

---

## Section 7 — Evaluasi dan Kontribusi Buku TA

### Metrik Evaluasi Baru

| Metrik | Definisi | Target |
|--------|----------|--------|
| Stage 1 WARNING latency | t(WARNING) - t(attack_start) | < 1000ms |
| Stage 2 CRITICAL confirmation time | t(CRITICAL) - t(WARNING) | ≈ 30s untuk flood, variabel untuk burst |
| Stage 1 False Positive Rate | WARNING/benign_windows | dokumen sebagai tradeoff |
| Stage 2 False Positive Rate | CRITICAL/benign_windows | < Stage 1 FPR |
| Speedtest FP elimination | WARNING saat speedtest vs Flood | Stage 2 tidak confirm speedtest (transient ≤40s < 30s threshold tidak cukup) |

### Framing Kontribusi Akademik

1. **Two-stage near-RT detection**: Stage 1 alert <1s, Stage 2 persistence validation sebelum mitigasi authorization
2. **Terminology yang tepat**: `RESOURCE_SATURATION_WARNING`, bukan "attack detected"
3. **Documented limitation**: LSTM Autoencoder tidak bisa bedakan speedtest vs flood (cell-level KPM constraint) — dijawab oleh Stage 2 empirically
4. **Duration-based threshold**: robust terhadap jitter sampling (ms, bukan window count), dengan justifikasi empiris dari perilaku speedtest vs flood pada testbed

---

## Section 8 — Known Limitations

Limitasi ini perlu didokumentasikan eksplisit pada Buku TA untuk menjaga integritas akademik:

| Limitasi | Detail |
|----------|--------|
| **Mitigation delay** | Stage 2 meningkatkan detection confidence tetapi menambah mitigation delay ~30s. Tradeoff ini disengaja untuk mengurangi false positive. |
| **Tidak dapat membedakan intent** | Sistem tidak dapat membedakan benign vs malicious saturation secara instan pada cell-level KPM. Stage 1 hanya melaporkan anomali resource, bukan intent serangan. |
| **Radio-layer degradation detection heuristik** | Radio-layer degradation suspicion berbasis sudden PRB collapse. Tidak ada SINR histogram atau PHY-layer telemetry karena KPM cell-level tidak menyediakan data tersebut. |
| **Sustained benign uploads** | Trafik benign dengan durasi PRB saturation > 30s (misalnya large file upload berkepanjangan) dapat tetap mencapai Stage 2 CRITICAL. |
| **Cell-level only** | Detection bersifat cell-level, bukan per-RNTI. Tidak bisa mengidentifikasi UE mana yang bertanggung jawab. |
| **CQI tidak reliabel di srsRAN** | srsRAN menggunakan keep-last CQI policy — CQI tidak di-reset saat UE detach, sehingga tidak digunakan sebagai indikator RF degradation. |
| **Speedtest ambiguity di Stage 1** | Stage 1 akan trigger WARNING untuk speedtest benign. Stage 2 menyelesaikan ambiguitas ini via persistence confirmation. |

---

## File yang Diubah

| File | Perubahan |
|------|-----------|
| `sec_ids.h` | Tambah `alert_type_t`, `stage1_result_t`, `stage2_state_t` |
| `sec_ids.c` | Tambah R7, R8; update R1/R2 threshold; implementasi Stage 2 persistence counter |
| `xapp_sec_moni.c` | Pass `stage2_state_t` ke `rule_based_detect()`; update `csv_trainer_write()` dengan kolom baru; update log format |
| `my_xapp_kpm.conf` | Opsional: tambah config key untuk threshold Stage 2 |

**Tidak diubah:** `ue_tracker.c`, `ue_tracker.h`, ONNX model, `train_lstm.py`, `export_onnx.py`

---

## Scope Buku TA vs Future Work

| Komponen | Buku TA (Approach A) | Future Work |
|----------|---------------------|-------------|
| Two-stage rule-based | ✅ wajib | — |
| R7 RF suspicion via sudden collapse | ✅ wajib | — |
| R8 Periodic saturation pattern | ✅ wajib | — |
| LSTM Autoencoder | ✅ dipertahankan (unchanged) | Supervised Classifier (B) |
| Evaluasi Stage 1/Stage 2 latency | ✅ wajib | — |
| Per-RNTI KPM Style 4 | ❌ | Perlu srsRAN fix atau OAI |
| E2SM-MAC per-UE | ❌ | Perlu OAI upgrade |
| Supervised ML Classifier | ❌ | Approach B |
