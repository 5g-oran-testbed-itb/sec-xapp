# Two-Stage Hybrid Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `xapp_sec_moni` dengan arsitektur two-stage hybrid detection — Stage 1 fast anomaly indication (<400ms) dan Stage 2 persistence-based CRITICAL confirmation (≥30s) sebelum mitigasi aktif.

**Architecture:** Stage 1 berjalan di setiap KPM window melalui `rule_based_detect()` yang diperluas, menghasilkan WARNING dalam <400ms. Stage 2 accumulates `saturation_duration_ms` menggunakan timestamp real (ms), dan baru meng-upgrade severity ke CRITICAL setelah ≥30s sustained — sehingga speedtest benign (observed in testbed: 15–40s transient) tidak mencapai CRITICAL. LSTM Autoencoder tetap tidak berubah; anomaly_score-nya ditulis ke CSV sebagai confidence layer.

**Tech Stack:** C (GCC), FlexRIC `xapp_sec_moni`, ONNX Runtime. Source di `/home/telmat/flexric/examples/xApp/c/monitor/`. Build: `cd ~/flexric/build && make -j$(nproc) xapp_sec_moni`. Test: `./xapp_sec_moni --test`.

---

## File Structure

| File | Perubahan |
|------|-----------|
| `sec_ids.h` | Tambah `alert_type_t`, `ids_detection_state_t`; update `rule_based_detect()` signature; tambah `ids_get_detection_state()` |
| `sec_ids.c` | Tambah Stage 2 state globals; update R1/R2 (80% threshold, Stage 1 WARNING); tambah R7 (radio-layer degradation suspicion); tambah R8 (periodic burst anomaly); update `ids_reset()`; implementasi `ids_get_detection_state()` |
| `xapp_sec_moni.c` | Tambah `g_last_anomaly_score` global; update `run_inference()` untuk store score; update `rule_based_detect()` call dengan `kpm_now_ms`; update `csv_trainer_open()` header; update `csv_trainer_write()` kolom baru; update `test_csv_writer()` untuk cek kolom baru; tambah `test_two_stage_detection()` |

**Tidak diubah:** `ue_tracker.c`, `ue_tracker.h`, model ONNX, `train_lstm.py`, `export_onnx.py`

---

## Task 1: Update sec_ids.h — Tambah Types dan Signatures Baru

**Files:**
- Modify: `/home/telmat/flexric/examples/xApp/c/monitor/sec_ids.h`

- [ ] **Step 1: Tulis test yang gagal untuk memvalidasi tipe baru di sec_ids.h**

Test akan gagal karena tipe belum ada. Buka `/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c` di bagian `test_csv_writer()` dan catat bahwa `--test` menjalankan test. Kita akan tambahkan `test_two_stage_detection()` di Task 6. Untuk sekarang, pastikan header compile dengan menjalankan:

```bash
cd /home/telmat/flexric/build && make -j$(nproc) xapp_sec_moni 2>&1 | head -20
```

Expected: build sukses (baseline). Catat bahwa build berhasil SEBELUM perubahan.

- [ ] **Step 2: Replace seluruh isi sec_ids.h dengan versi baru**

```c
#ifndef SEC_IDS_H
#define SEC_IDS_H

#include <stdint.h>

/*
 * Snapshot metrik sel per window KPM.
 * Diisi oleh sm_cb_kpm() di xapp_sec_moni.c,
 * lalu diserahkan ke rule_based_detect().
 */
typedef struct {
    /* --- Radio Resource (DU KPM) --- */
    float prb_used_dl;      /* RRU.PrbUsedDl            (%)      */
    float prb_used_ul;      /* RRU.PrbUsedUl            (%)      */
    float prb_tot_dl;       /* RRU.PrbTotDl             (count)  */
    float prb_tot_ul;       /* RRU.PrbTotUl             (count)  */
    float prb_avail_dl;     /* RRU.PrbAvailDl           (count)  */
    float prb_avail_ul;     /* RRU.PrbAvailUl           (count)  */
    /* --- Throughput --- */
    float thp_dl_mbps;      /* DRB.UEThpDl / 1000       (Mbps)   */
    float thp_ul_mbps;      /* DRB.UEThpUl / 1000       (Mbps)   */
    /* --- RACH / Delay --- */
    float rach_preamble;    /* RACH.PreambleDedCell     (count)  */
    float air_delay_ul;     /* DRB.AirIfDelayUl         (ms)     */
    /* --- RLC Volume / Quality --- */
    float rlc_vol_dl;       /* DRB.RlcSduTransmittedVolumeDL     */
    float rlc_vol_ul;       /* DRB.RlcSduTransmittedVolumeUL     */
    float rlc_drop_dl;      /* DRB.RlcPacketDropRateDl  (ratio)  */
    float rlc_delay_ul;     /* DRB.RlcDelayUl           (ms)     */
    /* --- Radio Quality --- */
    float cqi;              /* CQI                      (0-15)   */
    float rsrp;             /* RSRP                     (dBm)    */
    float rsrq;             /* RSRQ                     (dB)     */
    /* --- Control Plane (CU-CP KPM) --- */
    float rrc_att;          /* RRC.ConnEstabAtt         (count)  */
    float rrc_succ;         /* RRC.ConnEstabSucc        (count)  */
    /* --- RRC Storm proxy (srsRAN empty KPM indications during UE detach) --- */
    float empty_ind_rate;   /* #empty APER-failed indications since last window */
} cell_metrics_t;

/* Alert type — ditetapkan oleh Stage 1, digunakan di Stage 2 dan CSV */
typedef enum {
    ALERT_NONE = 0,
    ALERT_UL_SATURATION,
    ALERT_DL_SATURATION,
    ALERT_RRC_STORM,
    ALERT_RADIO_DEGRADATION_SUSPICION,
    ALERT_PERIODIC_BURST_ANOMALY,
} alert_type_t;

/* State deteksi dua-stage — dikembalikan oleh ids_get_detection_state()
 * untuk digunakan csv_trainer_write() saat menulis baris CSV.            */
typedef struct {
    int          stage1_alert;               /* 1 jika Stage 1 WARNING aktif */
    int          stage2_confirmed;           /* 1 jika Stage 2 CRITICAL terkonfirmasi */
    alert_type_t alert_type;                 /* tipe anomali aktif */
    long long    stage1_latency_ms;          /* ms dari mulai anomali ke WARNING pertama */
    long long    stage2_confirmation_time_ms;/* ms dari WARNING pertama ke CRITICAL */
} ids_detection_state_t;

/* Panggil sekali saat startup, berikan period KPM agar alert bisa
 * melaporkan durasi serangan dalam milidetik.                      */
void ids_init(uint64_t period_ms);

/* Reset semua consecutive counter (opsional, saat xApp restart).   */
void ids_reset(void);

/* Evaluasi semua rules terhadap snapshot metrik terbaru.
 * Print langsung ke stdout jika ada anomali.
 * now_ms: epoch ms saat callback dipanggil (dari clock_gettime).
 * Return: 0=normal, 1=WARNING (Stage 1 active, Stage 2 belum confirm),
 *         2=CRITICAL (Stage 2 confirmed, mitigasi authorized).            */
int rule_based_detect(cell_metrics_t const* m, long long now_ms);

/* Kembalikan state deteksi terakhir untuk csv_trainer_write().
 * Harus dipanggil setelah rule_based_detect() pada window yang sama.      */
ids_detection_state_t ids_get_detection_state(void);

#endif /* SEC_IDS_H */
```

- [ ] **Step 3: Verify header compile (sebelum sec_ids.c diupdate)**

```bash
cd /home/telmat/flexric/build && make -j$(nproc) xapp_sec_moni 2>&1 | grep -E "error:|warning:" | head -20
```

Expected: Error karena `rule_based_detect` signature mismatch dan `ids_get_detection_state` belum diimplementasi — ini BENAR, artinya header sudah berubah dan compiler mendeteksi mismatch.

---

## Task 2: Update sec_ids.c — Stage 2 State + R1/R2 Threshold Update

**Files:**
- Modify: `/home/telmat/flexric/examples/xApp/c/monitor/sec_ids.c`

Ganti **seluruh isi** file `sec_ids.c` dengan implementasi berikut:

- [ ] **Step 1: Write isi baru sec_ids.c — header, state globals, ids_init, ids_reset**

```c
#include "sec_ids.h"

#include <stdio.h>
#include <string.h>
#include <math.h>

/* ── Internal state ─────────────────────────────────────────────────────── */

#define RACH_HIST 10
#define IDS_BURST_ROLLING 5  /* rolling window depth untuk prb_burst_index (R8) */

static uint64_t g_period_ms = 100;

/* ─── Stage 1 consecutive-window counters ─────────────────────────────── */
static int g_ul_sat_cnt      = 0;   /* ul_saturation consecutive windows */
static int g_dl_sat_cnt      = 0;   /* dl_saturation consecutive windows */
static int g_sig_storm_cnt   = 0;   /* signaling storm (Rule 2) */
static int g_empty_storm_cnt = 0;   /* RRC storm via empty indications (Rule 3b) */
static int g_rf_susp_cnt     = 0;   /* radio_degradation_suspicion (R7) consecutive */

/* ─── Stage 2 persistence state ─────────────────────────────────────────── */
static long long g_stage2_saturation_start_ms = 0;  /* epoch ms saat saturation pertama */
static long long g_stage2_saturation_dur_ms   = 0;  /* durasi kumulatif saturation */
static int       g_stage2_burst_cycle_count   = 0;  /* jumlah ON->OFF->ON cycle (R8) */
static long long g_stage2_burst_window_start_ms = 0;/* window start untuk 60s cycle count */
static int       g_stage2_rrc_storm_cnt       = 0;  /* consecutive windows empty storm (Stage 2) */
static int       g_stage2_rf_susp_cnt         = 0;  /* consecutive windows RF suspicion (Stage 2) */
static long long g_stage2_recovery_start_ms   = 0;  /* epoch ms saat recovery mulai */

/* ─── Stage 2 configuration (empiris dari testbed) ───────────────────── */
static long long g_cfg_saturation_confirm_ms  = 30000; /* speedtest ≤40s, flood >120s */
static int       g_cfg_burst_cycle_threshold  = 3;
static int       g_cfg_rrc_storm_confirm_win  = 4;
static int       g_cfg_rf_susp_confirm_win    = 5;
static long long g_cfg_recovery_confirm_ms    = 5000;

/* ─── Stage 1 event tracking (untuk latency reporting) ───────────────── */
static long long g_stage1_event_start_ms = 0;   /* epoch ms Stage 1 pertama kali aktif */
static int       g_stage1_was_active     = 0;   /* flag: Stage 1 aktif di window sebelumnya */
static long long g_stage2_confirm_ms     = 0;   /* epoch ms Stage 2 terkonfirmasi */
static int       g_stage2_was_confirmed  = 0;

/* ─── R7: Previous PRB total untuk sudden-collapse detection ─────────── */
static float g_prev_prb_total = 0.0f;  /* prb_used_dl + prb_used_ul window sebelumnya */

/* ─── R8: Rolling window untuk prb_burst_index ───────────────────────── */
static float g_burst_rolling[IDS_BURST_ROLLING] = {0};
static int   g_burst_rolling_head  = 0;
static int   g_burst_rolling_count = 0;
static int   g_burst_was_high      = 0;  /* 1 jika window sebelumnya di atas threshold */
static int   g_burst_consec        = 0;  /* consecutive windows di atas threshold */

/* ─── Rolling RACH history untuk spike detection (Rule 3) ─────────────── */
static float g_rach_hist[RACH_HIST] = {0};
static int   g_rach_hist_idx        = 0;

/* ─── Last detection state (untuk ids_get_detection_state()) ─────────── */
static ids_detection_state_t g_last_detection = {0};

/* ── Public API ─────────────────────────────────────────────────────────── */

void ids_init(uint64_t period_ms)
{
    g_period_ms = period_ms;
}

void ids_reset(void)
{
    g_ul_sat_cnt       = 0;
    g_dl_sat_cnt       = 0;
    g_sig_storm_cnt    = 0;
    g_empty_storm_cnt  = 0;
    g_rf_susp_cnt      = 0;

    g_stage2_saturation_start_ms = 0;
    g_stage2_saturation_dur_ms   = 0;
    g_stage2_burst_cycle_count   = 0;
    g_stage2_burst_window_start_ms = 0;
    g_stage2_rrc_storm_cnt       = 0;
    g_stage2_rf_susp_cnt         = 0;
    g_stage2_recovery_start_ms   = 0;

    g_stage1_event_start_ms = 0;
    g_stage1_was_active     = 0;
    g_stage2_confirm_ms     = 0;
    g_stage2_was_confirmed  = 0;

    g_prev_prb_total       = 0.0f;
    g_burst_rolling_head   = 0;
    g_burst_rolling_count  = 0;
    g_burst_was_high       = 0;
    g_burst_consec         = 0;

    g_rach_hist_idx = 0;
    memset(g_rach_hist,       0, sizeof(g_rach_hist));
    memset(g_burst_rolling,   0, sizeof(g_burst_rolling));
    memset(&g_last_detection, 0, sizeof(g_last_detection));
}
```

- [ ] **Step 2: Lanjutkan sec_ids.c — implementasi rule_based_detect() lengkap**

Tambahkan fungsi `rule_based_detect()` setelah blok `ids_reset()`:

```c
int rule_based_detect(cell_metrics_t const* m, long long now_ms)
{
    int severity    = 0;
    int stage1_hit  = 0;
    alert_type_t alert_type = ALERT_NONE;

    static const float EPS = 1e-6f;

    /* ── Rule 1 (update): UL Saturation — Stage 1 WARNING ─────────────────
     * prb_used_ul > 80% → ALERT_UL_SATURATION WARNING.
     * Threshold diperluas dari >90% ke >80% agar lebih sensitif.
     * Tidak ada guard PRB_DL karena Stage 2 menangani FP speedtest.
     * Stage 2: sustained ≥ 30s → CRITICAL (speedtest hanya ≤40s transient). */
    {
        int ul_sat = (m->prb_used_ul > 80.0f);
        if (ul_sat) {
            g_ul_sat_cnt++;
        } else {
            g_ul_sat_cnt = 0;
        }
        if (g_ul_sat_cnt >= 3 && !stage1_hit) {
            printf(">>> [STAGE1-WARNING] UL_SATURATION | PRB_UL=%.0f%% "
                   "selama %d windows (%.0fms)\n",
                   m->prb_used_ul, g_ul_sat_cnt,
                   g_ul_sat_cnt * (float)g_period_ms);
            stage1_hit = 1;
            alert_type = ALERT_UL_SATURATION;
            if (severity < 1) severity = 1;
        }
    }

    /* ── Rule 2 (update): DL Saturation — Stage 1 WARNING ─────────────────
     * prb_used_dl > 80% AND prb_used_ul < 30% → ALERT_DL_SATURATION WARNING.
     * Guard PRB_UL < 30% membedakan dari balanced bidirectional traffic.
     * Stage 2: sustained ≥ 30s → CRITICAL. */
    {
        int dl_sat = (m->prb_used_dl > 80.0f && m->prb_used_ul < 30.0f);
        if (dl_sat) {
            g_dl_sat_cnt++;
        } else {
            g_dl_sat_cnt = 0;
        }
        if (g_dl_sat_cnt >= 3 && !stage1_hit) {
            printf(">>> [STAGE1-WARNING] DL_SATURATION | PRB_DL=%.0f%% PRB_UL=%.0f%% "
                   "selama %d windows (%.0fms)\n",
                   m->prb_used_dl, m->prb_used_ul, g_dl_sat_cnt,
                   g_dl_sat_cnt * (float)g_period_ms);
            stage1_hit = 1;
            alert_type = ALERT_DL_SATURATION;
            if (severity < 1) severity = 1;
        }
    }

    /* ── Rule 2 (lama): Signaling Storm / RRC Flood (MAC heuristic) ────────
     * Tetap tidak berubah — dipertahankan untuk backward compat.
     * CQI < 5 guard tidak aktif di srsRAN (keep-last), tapi RACH guard aktif. */
    {
        float prb_avg = (m->prb_used_dl + m->prb_used_ul) / 2.0f;
        float rlc_rate_dl_kbps = m->rlc_vol_dl * 1000.0f / (float)g_period_ms;
        float rlc_rate_ul_kbps = m->rlc_vol_ul * 1000.0f / (float)g_period_ms;
        int   cqi_degraded  = (m->cqi < 5.0f);
        int   rach_elevated = (m->rach_preamble > 0.0f);
        if (prb_avg > 20.0f
                && rlc_rate_ul_kbps < 100.0f && rlc_rate_dl_kbps < 100.0f
                && m->prb_used_dl < 80.0f && m->prb_used_ul < 80.0f
                && (cqi_degraded || rach_elevated)) {
            g_sig_storm_cnt++;
        } else {
            g_sig_storm_cnt = 0;
        }
        if (g_sig_storm_cnt >= 3) {
            printf(">>> [STAGE1-WARNING] SIGNALING_STORM | PRB=%.0f%% CQI=%.0f RACH=%.0f "
                   "selama %d windows (%.0fms) — gunakan SSH AMF barring\n",
                   prb_avg, m->cqi, m->rach_preamble,
                   g_sig_storm_cnt, g_sig_storm_cnt * (float)g_period_ms);
            if (!stage1_hit) {
                stage1_hit = 1;
                alert_type = ALERT_RRC_STORM;
            }
            if (severity < 1) severity = 1;
        }
    }

    /* ── Rule 3: RRC Flood via RACH Spike ──────────────────────────────────
     * Tidak berubah — WARNING, control-plane. */
    {
        float rach_sum = 0.0f;
        for (int i = 0; i < RACH_HIST; i++) rach_sum += g_rach_hist[i];
        float rach_mean = rach_sum / (float)RACH_HIST;
        g_rach_hist[g_rach_hist_idx % RACH_HIST] = m->rach_preamble;
        g_rach_hist_idx++;
        if (m->rach_preamble > 5.0f && m->rach_preamble > 3.0f * (rach_mean + 1.0f)) {
            printf(">>> [STAGE1-WARNING] RRC_FLOOD (RACH spike) | preamble=%.0f "
                   "mean=%.1f (>3x threshold)\n",
                   m->rach_preamble, rach_mean);
            if (!stage1_hit) {
                stage1_hit = 1;
                alert_type = ALERT_RRC_STORM;
            }
            if (severity < 1) severity = 1;
        }
    }

    /* ── Rule 3b: RRC Storm via Empty Indications — Stage 1 + Stage 2 ──────
     * Threshold Stage 1: ≥2 empty per window untuk ≥3 windows consecutive.
     * Stage 2 threshold: g_cfg_rrc_storm_confirm_win (default 4) windows. */
    {
        if (m->empty_ind_rate >= 2.0f && m->prb_used_ul < 30.0f && m->prb_used_dl < 30.0f) {
            g_empty_storm_cnt++;
            g_stage2_rrc_storm_cnt++;
        } else {
            g_empty_storm_cnt = 0;
            g_stage2_rrc_storm_cnt = 0;
        }
        if (g_empty_storm_cnt >= 3) {
            printf(">>> [STAGE1-WARNING] RRC_STORM (UE churn) | empty_ind=%.0f/window "
                   "selama %d windows (%.0fms) — airplane toggle detected\n",
                   m->empty_ind_rate, g_empty_storm_cnt,
                   g_empty_storm_cnt * (float)g_period_ms);
            if (!stage1_hit) {
                stage1_hit = 1;
                alert_type = ALERT_RRC_STORM;
            }
            if (severity < 1) severity = 1;
        }
        if (g_stage2_rrc_storm_cnt >= g_cfg_rrc_storm_confirm_win) {
            printf(">>> [STAGE2-CRITICAL] RRC_STORM CONFIRMED | %d windows consecutive\n",
                   g_stage2_rrc_storm_cnt);
            if (severity < 2) severity = 2;
        }
    }

    /* ── Rule 4 (lama): Uplink Flood via RLC — tidak berubah ───────────────
     * Tetap dipertahankan karena menggunakan RLC rate (bukan hanya PRB).
     * RLC vol selalu 0 di srsRAN, tapi rule tetap ada untuk platform lain. */
    {
        float rlc_rate_ul_kbps = m->rlc_vol_ul * 1000.0f / (float)g_period_ms;
        float rlc_rate_dl_kbps = m->rlc_vol_dl * 1000.0f / (float)g_period_ms;
        float rlc_rate_ul_mbps = rlc_rate_ul_kbps / 1000.0f;
        float rlc_rate_dl_mbps = rlc_rate_dl_kbps / 1000.0f;
        static int ul_flood_cnt = 0;
        if (rlc_rate_ul_mbps > 15.0f && m->prb_used_ul > 80.0f && rlc_rate_dl_mbps < 0.5f) {
            ul_flood_cnt++;
        } else {
            ul_flood_cnt = 0;
        }
        if (ul_flood_cnt >= 3) {
            printf(">>> [STAGE1-WARNING] UPLINK_FLOOD (RLC) | RLC_UL=%.2fMbps "
                   "PRB_UL=%.0f%% selama %d windows (%.0fms)\n",
                   rlc_rate_ul_mbps, m->prb_used_ul, ul_flood_cnt,
                   ul_flood_cnt * (float)g_period_ms);
            if (!stage1_hit) {
                stage1_hit = 1;
                alert_type = ALERT_UL_SATURATION;
            }
            if (severity < 2) severity = 2;
        }
    }

    /* ── Rule 5 (lama): Downlink Flood via RLC — tidak berubah ─────────── */
    {
        float rlc_rate_dl_kbps = m->rlc_vol_dl * 1000.0f / (float)g_period_ms;
        float rlc_rate_ul_kbps = m->rlc_vol_ul * 1000.0f / (float)g_period_ms;
        float rlc_rate_dl_mbps = rlc_rate_dl_kbps / 1000.0f;
        float rlc_rate_ul_mbps = rlc_rate_ul_kbps / 1000.0f;
        static int dl_flood_cnt = 0;
        if (rlc_rate_dl_mbps > 50.0f && m->prb_used_dl > 80.0f && rlc_rate_ul_mbps < 0.5f) {
            dl_flood_cnt++;
        } else {
            dl_flood_cnt = 0;
        }
        if (dl_flood_cnt >= 3) {
            printf(">>> [STAGE1-WARNING] DOWNLINK_FLOOD (RLC) | RLC_DL=%.2fMbps "
                   "PRB_DL=%.0f%% selama %d windows (%.0fms)\n",
                   rlc_rate_dl_mbps, m->prb_used_dl, dl_flood_cnt,
                   dl_flood_cnt * (float)g_period_ms);
            if (!stage1_hit) {
                stage1_hit = 1;
                alert_type = ALERT_DL_SATURATION;
            }
            if (severity < 2) severity = 2;
        }
    }

    /* ── Rule 6 (lama): High UL Air Interface Delay — tidak berubah ─────── */
    if (m->air_delay_ul > 100.0f) {
        printf(">>> [STAGE1-WARNING] HIGH_UL_DELAY | AirIfDelayUl=%.0fms "
               "(threshold 100ms, possible jamming)\n",
               m->air_delay_ul);
        if (!stage1_hit) {
            stage1_hit = 1;
            alert_type = ALERT_RADIO_DEGRADATION_SUSPICION;
        }
        if (severity < 1) severity = 1;
    }

    /* ── Rule 7 (baru): Radio-Layer Degradation Suspicion via Sudden Resource Collapse ─
     * Deteksi: PRB total sebelumnya > 40% → tiba-tiba collapse < 5%
     * DAN air_delay_ul == 0 (scheduling inactivity).
     * CQI TIDAK digunakan karena srsRAN keep-last policy.
     * Framing: "suspicious radio-layer degradation" bukan "jammer detected". */
    {
        float prb_total_now = m->prb_used_dl + m->prb_used_ul;
        int rf_collapse = (g_prev_prb_total > 40.0f
                           && prb_total_now < 5.0f
                           && m->air_delay_ul < 1.0f);
        if (rf_collapse) {
            g_rf_susp_cnt++;
            g_stage2_rf_susp_cnt++;
        } else {
            g_rf_susp_cnt = 0;
            g_stage2_rf_susp_cnt = 0;
        }
        g_prev_prb_total = prb_total_now;

        if (g_rf_susp_cnt >= 2 && !stage1_hit) {
            printf(">>> [STAGE1-WARNING] RADIO_DEGRADATION_SUSPICION | PRB_prev=%.0f%%→now=%.0f%% "
                   "AirDelay=%.0fms selama %d windows — suspicious radio-layer degradation\n",
                   g_prev_prb_total, prb_total_now, m->air_delay_ul,
                   g_rf_susp_cnt);
            stage1_hit = 1;
            alert_type = ALERT_RADIO_DEGRADATION_SUSPICION;
            if (severity < 1) severity = 1;
        }
        if (g_stage2_rf_susp_cnt >= g_cfg_rf_susp_confirm_win) {
            printf(">>> [STAGE2-CRITICAL] RADIO_DEGRADATION_SUSPICION CONFIRMED | %d windows consecutive\n",
                   g_stage2_rf_susp_cnt);
            if (severity < 2) severity = 2;
        }
    }

    /* ── Rule 8 (baru): Periodic Saturation Pattern Detection ─────────────
     * prb_burst_index = log(1 + prb_total) / (rolling_mean + eps).
     * Stage 1: prb_burst_index > 2.0 di ≥2 window consecutive.
     * Stage 2: ≥3 ON→OFF→ON cycle dalam 60s window.               */
    {
        float prb_total = m->prb_used_dl + m->prb_used_ul;
        g_burst_rolling[g_burst_rolling_head % IDS_BURST_ROLLING] = prb_total;
        g_burst_rolling_head++;
        if (g_burst_rolling_count < IDS_BURST_ROLLING) g_burst_rolling_count++;
        float bsum = 0.0f;
        for (int k = 0; k < g_burst_rolling_count; k++) {
            int idx = ((g_burst_rolling_head - 1 - k) % IDS_BURST_ROLLING
                       + IDS_BURST_ROLLING) % IDS_BURST_ROLLING;
            bsum += g_burst_rolling[idx];
        }
        float rolling_mean   = bsum / (float)g_burst_rolling_count;
        float prb_burst_index = logf(1.0f + prb_total) / (rolling_mean + EPS);

        int burst_high = (prb_burst_index > 2.0f);
        if (burst_high) {
            g_burst_consec++;
        } else {
            if (g_burst_consec >= 2 && g_burst_was_high) {
                /* Satu ON→OFF cycle selesai */
                if (now_ms - g_stage2_burst_window_start_ms <= 60000) {
                    g_stage2_burst_cycle_count++;
                } else {
                    /* Reset window 60s */
                    g_stage2_burst_cycle_count = 1;
                    g_stage2_burst_window_start_ms = now_ms;
                }
            }
            g_burst_consec = 0;
        }
        g_burst_was_high = burst_high;

        if (g_burst_consec >= 2 && !stage1_hit) {
            printf(">>> [STAGE1-WARNING] PERIODIC_BURST_ANOMALY | prb_burst_index=%.2f "
                   "selama %d windows consecutive\n",
                   prb_burst_index, g_burst_consec);
            stage1_hit = 1;
            alert_type = ALERT_PERIODIC_BURST_ANOMALY;
            if (severity < 1) severity = 1;
        }
        if (g_stage2_burst_cycle_count >= g_cfg_burst_cycle_threshold) {
            printf(">>> [STAGE2-CRITICAL] PERIODIC_BURST_ANOMALY CONFIRMED | %d cycles dalam 60s\n",
                   g_stage2_burst_cycle_count);
            if (severity < 2) severity = 2;
        }
    }

    /* ── Stage 2: Saturation Persistence Validator ──────────────────────────
     * Berlaku untuk ALERT_UL_SATURATION dan ALERT_DL_SATURATION.
     * Threshold empiris: speedtest transient 15–40s vs flood sustained >120s.
     * Menggunakan durasi (ms), bukan window count — robust terhadap jitter. */
    {
        int sat_active = (alert_type == ALERT_UL_SATURATION
                          || alert_type == ALERT_DL_SATURATION);
        if (sat_active) {
            if (g_stage2_saturation_start_ms == 0) {
                g_stage2_saturation_start_ms = now_ms;
            }
            g_stage2_saturation_dur_ms = now_ms - g_stage2_saturation_start_ms;
            g_stage2_recovery_start_ms = 0; /* reset recovery timer */

            if (g_stage2_saturation_dur_ms >= g_cfg_saturation_confirm_ms) {
                printf(">>> [STAGE2-CRITICAL] SATURATION CONFIRMED | type=%s "
                       "duration=%.0fms (threshold=%.0fms)\n",
                       (alert_type == ALERT_UL_SATURATION) ? "ul_saturation" : "dl_saturation",
                       (float)g_stage2_saturation_dur_ms,
                       (float)g_cfg_saturation_confirm_ms);
                if (severity < 2) severity = 2;
            }
        } else {
            /* Recovery logic: reset Stage 2 setelah g_cfg_recovery_confirm_ms tenang */
            if (g_stage2_saturation_start_ms != 0) {
                if (g_stage2_recovery_start_ms == 0) {
                    g_stage2_recovery_start_ms = now_ms;
                }
                if (now_ms - g_stage2_recovery_start_ms >= g_cfg_recovery_confirm_ms) {
                    g_stage2_saturation_start_ms = 0;
                    g_stage2_saturation_dur_ms   = 0;
                    g_stage2_recovery_start_ms   = 0;
                }
            }
        }
    }

    /* ── Track Stage 1 event timing ────────────────────────────────────────── */
    if (stage1_hit) {
        if (!g_stage1_was_active) {
            g_stage1_event_start_ms = now_ms;
        }
        g_stage1_was_active = 1;
    } else {
        if (g_stage1_was_active) {
            /* Stage 1 baru saja berhenti — reset timing */
            g_stage1_event_start_ms = 0;
            g_stage1_was_active     = 0;
            g_stage2_confirm_ms     = 0;
            g_stage2_was_confirmed  = 0;
        }
    }

    /* Catat kapan Stage 2 pertama kali terkonfirmasi */
    if (severity == 2 && !g_stage2_was_confirmed) {
        g_stage2_confirm_ms    = now_ms;
        g_stage2_was_confirmed = 1;
    }
    if (severity < 2) {
        g_stage2_was_confirmed = 0;
        g_stage2_confirm_ms    = 0;
    }

    /* ── Update g_last_detection untuk ids_get_detection_state() ─────────── */
    g_last_detection.stage1_alert    = stage1_hit;
    g_last_detection.stage2_confirmed = (severity == 2) ? 1 : 0;
    g_last_detection.alert_type      = alert_type;
    g_last_detection.stage1_latency_ms =
        (stage1_hit && g_stage1_event_start_ms > 0)
        ? (now_ms - g_stage1_event_start_ms) : 0;
    g_last_detection.stage2_confirmation_time_ms =
        (severity == 2 && g_stage2_confirm_ms > 0 && g_stage1_event_start_ms > 0)
        ? (g_stage2_confirm_ms - g_stage1_event_start_ms) : 0;

    fflush(stdout);
    return severity;
}

ids_detection_state_t ids_get_detection_state(void)
{
    return g_last_detection;
}
```

- [ ] **Step 3: Tulis file sec_ids.c dengan menggabungkan Step 1 + Step 2**

Gabungkan semua kode dari Step 1 dan Step 2 menjadi satu file `/home/telmat/flexric/examples/xApp/c/monitor/sec_ids.c`. Header dulu (Step 1), lalu `rule_based_detect()` dan `ids_get_detection_state()` (Step 2).

- [ ] **Step 4: Build dan verifikasi tidak ada error**

```bash
cd /home/telmat/flexric/build && make -j$(nproc) xapp_sec_moni 2>&1 | grep -E "error:|error :" | head -20
```

Expected: Error dari `xapp_sec_moni.c` karena signature `rule_based_detect` mismatch (masih pakai 1 argumen). Ini BENAR — akan diperbaiki di Task 3.

---

## Task 3: Update xapp_sec_moni.c — Call Site + anomaly_score Global

**Files:**
- Modify: `/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c`

- [ ] **Step 1: Tambah global g_last_anomaly_score setelah deklarasi session**

Cari baris 80 (`static OrtSession* session = NULL;`). Setelah baris 82 (`static OrtMemoryInfo* memory_info = NULL;`), tambahkan:

```c
/* Anomaly score dari ONNX inference terakhir — digunakan csv_trainer_write() */
static float g_last_anomaly_score = 0.0f;
```

- [ ] **Step 2: Update run_inference() untuk menyimpan anomaly_score**

Cari baris 134:
```c
        printf(">>> [INFERENCE] RNTI %d Anomaly Score: %f\n", rnti, out_arr[0]);
```

Tambahkan satu baris setelah printf:
```c
        g_last_anomaly_score = out_arr[0];
```

Sehingga menjadi:
```c
        printf(">>> [INFERENCE] RNTI %d Anomaly Score: %f\n", rnti, out_arr[0]);
        g_last_anomaly_score = out_arr[0];
```

- [ ] **Step 3: Update call site rule_based_detect() di sm_cb_kpm() untuk pass now_ms**

Cari baris 1098:
```c
      int sev = rule_based_detect(&g_cell);
```

Ganti dengan:
```c
      int sev = rule_based_detect(&g_cell, (long long)kpm_now_ms);
```

- [ ] **Step 4: Build untuk verifikasi call site sudah benar**

```bash
cd /home/telmat/flexric/build && make -j$(nproc) xapp_sec_moni 2>&1 | grep -E "error:" | head -20
```

Expected: Error masih ada di `csv_trainer_open` (header lama) dan `test_csv_writer` (verifikasi header lama). Akan diperbaiki di Task 4.

---

## Task 4: Update xapp_sec_moni.c — CSV Header dan Writer

**Files:**
- Modify: `/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c`

- [ ] **Step 1: Update csv_trainer_open() — header string baru**

Cari baris 678–684 (fungsi `csv_trainer_open`, bagian `fprintf`):
```c
    fprintf(t->fp,
        "timestamp_ms,datetime,"
        "prb_usage_dl_ratio,prb_usage_ul_ratio,"
        "cqi,rach_preamble,air_delay_ul,"
        "prb_direction,prb_total,"
        "prb_dl_delta,prb_ul_delta,prb_burst_index,"
        "label\n");
```

Ganti dengan:
```c
    fprintf(t->fp,
        "timestamp_ms,datetime,"
        "prb_usage_dl_ratio,prb_usage_ul_ratio,"
        "cqi,rach_preamble,air_delay_ul,"
        "prb_direction,prb_total,"
        "prb_dl_delta,prb_ul_delta,prb_burst_index,"
        "label,"
        "stage1_alert,stage2_confirmed,alert_type,"
        "stage1_latency_ms,stage2_confirmation_time_ms,"
        "anomaly_score\n");
```

- [ ] **Step 2: Tambah alert_type_to_str() helper sebelum csv_trainer_write()**

Tambahkan fungsi helper kecil sebelum `csv_trainer_write()` (sekitar baris 689):

```c
static const char* alert_type_to_str(alert_type_t t) {
    switch (t) {
        case ALERT_UL_SATURATION:      return "ul_saturation";
        case ALERT_DL_SATURATION:      return "dl_saturation";
        case ALERT_RRC_STORM:          return "rrc_storm";
        case ALERT_RADIO_DEGRADATION_SUSPICION: return "radio_degradation_suspicion";
        case ALERT_PERIODIC_BURST_ANOMALY:return "periodic_burst_anomaly";
        default:                       return "none";
    }
}
```

- [ ] **Step 3: Update csv_trainer_write() — tambah kolom baru di akhir fprintf**

Cari baris 738–751 (bagian `fprintf` di `csv_trainer_write()`):
```c
    fprintf(t->fp,
        "%lld,%s,"
        "%.6f,%.6f,"
        "%.3f,%.3f,%.3f,"
        "%.6f,%.6f,"
        "%.6f,%.6f,%.6f,"
        "%d\n",
        ts_ms, datetime,
        prb_dl_ratio, prb_ul_ratio,
        m->cqi, m->rach_preamble, m->air_delay_ul,
        prb_direction, prb_total,
        prb_dl_delta, prb_ul_delta, prb_burst_index,
        g_label);
```

Ganti dengan:
```c
    ids_detection_state_t det = ids_get_detection_state();
    fprintf(t->fp,
        "%lld,%s,"
        "%.6f,%.6f,"
        "%.3f,%.3f,%.3f,"
        "%.6f,%.6f,"
        "%.6f,%.6f,%.6f,"
        "%d,"
        "%d,%d,%s,"
        "%lld,%lld,"
        "%.6f\n",
        ts_ms, datetime,
        prb_dl_ratio, prb_ul_ratio,
        m->cqi, m->rach_preamble, m->air_delay_ul,
        prb_direction, prb_total,
        prb_dl_delta, prb_ul_delta, prb_burst_index,
        g_label,
        det.stage1_alert, det.stage2_confirmed,
        alert_type_to_str(det.alert_type),
        det.stage1_latency_ms, det.stage2_confirmation_time_ms,
        g_last_anomaly_score);
```

- [ ] **Step 4: Update log format di sm_cb_kpm() untuk tampilkan stage info**

Cari baris 1120–1129 (bagian print status per detik):
```c
        const char *sev_str = (sev == 2) ? " \033[1;31m[CRITICAL]\033[0m"
                            : (sev == 1) ? " \033[1;33m[WARNING]\033[0m"
                            :              " [OK]";
        printf("[%s]%s PRB_DL=%.0f%% PRB_UL=%.0f%% RLC_DL=%.2fMbps RLC_UL=%.2fMbps"
               " RACH=%.0f CQI=%.0f\n",
```

Ganti dengan:
```c
        ids_detection_state_t _det = ids_get_detection_state();
        const char *sev_str = (sev == 2) ? " \033[1;31m[STAGE2-CRITICAL]\033[0m"
                            : (sev == 1) ? " \033[1;33m[STAGE1-WARNING]\033[0m"
                            :              " [OK]";
        printf("[%s]%s alert=%s PRB_DL=%.0f%% PRB_UL=%.0f%% RACH=%.0f CQI=%.0f anomaly=%.4f\n",
               _tbuf, sev_str, alert_type_to_str(_det.alert_type),
```

Dan update argumen printf untuk sesuai format string baru:
```c
               g_cell.prb_used_dl, g_cell.prb_used_ul,
               g_cell.rach_preamble, g_cell.cqi,
               g_last_anomaly_score);
```

- [ ] **Step 5: Build untuk verifikasi**

```bash
cd /home/telmat/flexric/build && make -j$(nproc) xapp_sec_moni 2>&1 | grep -E "error:" | head -20
```

Expected: Tidak ada error. Jika ada warning tentang unused variable, periksa apakah ada variabel lama yang tidak terpakai.

---

## Task 5: Update test_csv_writer() dan Tambah test_two_stage_detection()

**Files:**
- Modify: `/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c`

- [ ] **Step 1: Update test_csv_writer() untuk verifikasi kolom baru di header**

Cari baris 951–963 (bagian verifikasi header di `test_csv_writer()`):
```c
    if (!strstr(buf, "timestamp_ms")        ||
        !strstr(buf, "datetime")            ||
        !strstr(buf, "prb_usage_dl_ratio")  ||
        !strstr(buf, "prb_usage_ul_ratio")  ||
        !strstr(buf, "rach_preamble")       ||
        !strstr(buf, "prb_direction")       ||
        !strstr(buf, "prb_burst_index")     ||
        !strstr(buf, "label")) {
        fclose(f);
        printf("[FAIL] header missing required columns:\n  %s\n", buf);
        return 1;
    }
```

Ganti dengan:
```c
    if (!strstr(buf, "timestamp_ms")               ||
        !strstr(buf, "prb_usage_dl_ratio")         ||
        !strstr(buf, "prb_burst_index")            ||
        !strstr(buf, "label")                      ||
        !strstr(buf, "stage1_alert")               ||
        !strstr(buf, "stage2_confirmed")           ||
        !strstr(buf, "alert_type")                 ||
        !strstr(buf, "stage1_latency_ms")          ||
        !strstr(buf, "stage2_confirmation_time_ms")||
        !strstr(buf, "anomaly_score")) {
        fclose(f);
        printf("[FAIL] header missing required columns:\n  %s\n", buf);
        return 1;
    }
```

- [ ] **Step 2: Tambah test_two_stage_detection() sebelum main()**

Tambahkan fungsi baru setelah `test_csv_writer()` (sekitar baris 994):

```c
static int test_two_stage_detection(void)
{
    ids_reset();
    ids_init(100);

    cell_metrics_t m = {0};
    m.cqi = 15.0f;

    /* ── Test A: UL saturation Stage 1 fires after 3 windows ────────────── */
    m.prb_used_ul = 85.0f;  /* > 80% threshold */
    m.prb_used_dl = 5.0f;
    for (int i = 0; i < 3; i++) {
        int sev = rule_based_detect(&m, 1000LL + i * 120LL);
        if (i < 2 && sev != 0) {
            printf("[FAIL] test A: sev should be 0 before 3 windows, got %d at window %d\n", sev, i);
            return 1;
        }
    }
    int sev_a = rule_based_detect(&m, 1360LL);
    if (sev_a < 1) {
        printf("[FAIL] test A: UL_SATURATION should be WARNING after 3+ windows, got %d\n", sev_a);
        return 1;
    }
    ids_detection_state_t det_a = ids_get_detection_state();
    if (det_a.alert_type != ALERT_UL_SATURATION) {
        printf("[FAIL] test A: alert_type should be ALERT_UL_SATURATION, got %d\n", det_a.alert_type);
        return 1;
    }
    if (det_a.stage1_alert != 1) {
        printf("[FAIL] test A: stage1_alert should be 1, got %d\n", det_a.stage1_alert);
        return 1;
    }
    printf("[PASS] test A: UL_SATURATION Stage 1 WARNING after 3 windows\n");

    /* ── Test B: Stage 2 CRITICAL after 30s sustained ────────────────────── */
    /* Simulasi 300 windows @ 100ms = 30s. Stage 2 harus CRITICAL. */
    long long ts = 2000LL;
    int stage2_reached = 0;
    for (int i = 0; i < 310; i++) {
        int s = rule_based_detect(&m, ts);
        if (s == 2) { stage2_reached = 1; break; }
        ts += 100LL;
    }
    if (!stage2_reached) {
        printf("[FAIL] test B: Stage 2 CRITICAL not reached after 30s+ sustained\n");
        return 1;
    }
    ids_detection_state_t det_b = ids_get_detection_state();
    if (det_b.stage2_confirmed != 1) {
        printf("[FAIL] test B: stage2_confirmed should be 1\n");
        return 1;
    }
    printf("[PASS] test B: Stage 2 CRITICAL confirmed after 30s sustained\n");

    /* ── Test C: Speedtest-like (25s) does NOT reach Stage 2 CRITICAL ─────── */
    ids_reset();
    ids_init(100);
    m.prb_used_ul = 85.0f;
    ts = 10000LL;
    int stage2_fp = 0;
    for (int i = 0; i < 250; i++) {  /* 250 × 100ms = 25s */
        int s = rule_based_detect(&m, ts);
        if (s == 2) { stage2_fp = 1; break; }
        ts += 100LL;
    }
    if (stage2_fp) {
        printf("[FAIL] test C: Stage 2 CRITICAL triggered on 25s traffic (speedtest FP)\n");
        return 1;
    }
    printf("[PASS] test C: 25s saturation stays at WARNING (speedtest not escalated)\n");

    /* ── Test D: Normal traffic — no alert ─────────────────────────────────── */
    ids_reset();
    ids_init(100);
    m.prb_used_ul = 10.0f;
    m.prb_used_dl = 10.0f;
    int sev_d = rule_based_detect(&m, 20000LL);
    if (sev_d != 0) {
        printf("[FAIL] test D: normal traffic should return 0, got %d\n", sev_d);
        return 1;
    }
    ids_detection_state_t det_d = ids_get_detection_state();
    if (det_d.stage1_alert != 0 || det_d.stage2_confirmed != 0) {
        printf("[FAIL] test D: stage1_alert=%d stage2_confirmed=%d should both be 0\n",
               det_d.stage1_alert, det_d.stage2_confirmed);
        return 1;
    }
    printf("[PASS] test D: normal traffic returns severity=0, no alert\n");

    printf("[PASS] test_two_stage_detection: all tests passed\n");
    return 0;
}
```

- [ ] **Step 3: Tambahkan test_two_stage_detection() ke main() --test path**

Cari baris 1430–1435:
```c
  if (argc > 1 && strcmp(argv[1], "--test") == 0) {
      init_onnx();
      test_run_inference();
      int csv_rc = test_csv_writer();
      return csv_rc;
  }
```

Ganti dengan:
```c
  if (argc > 1 && strcmp(argv[1], "--test") == 0) {
      init_onnx();
      test_run_inference();
      int ids_rc = test_two_stage_detection();
      if (ids_rc != 0) return ids_rc;
      int csv_rc = test_csv_writer();
      return csv_rc;
  }
```

---

## Task 6: Build Final, Test, dan Verifikasi

**Files:**
- No file changes — hanya build dan test

- [ ] **Step 1: Build final**

```bash
cd /home/telmat/flexric/build && make -j$(nproc) xapp_sec_moni 2>&1
```

Expected: Build sukses tanpa error. Warning OK selama tidak ada error.

- [ ] **Step 2: Jalankan --test untuk verifikasi semua test pass**

```bash
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni --test
```

Expected output (urutan bisa berbeda):
```
ONNX Runtime initialized successfully
[TEST] Running inference test...
[TEST] Inference test completed.
[PASS] test A: UL_SATURATION Stage 1 WARNING after 3 windows
[PASS] test B: Stage 2 CRITICAL confirmed after 30s sustained
[PASS] test C: 25s saturation stays at WARNING (speedtest not escalated)
[PASS] test D: normal traffic returns severity=0, no alert
[PASS] test_two_stage_detection: all tests passed
[CSV] Recording to /tmp/test_sec_training.csv  (label=0)
[PASS] test_csv_writer
```

Exit code harus 0. Jika ada `[FAIL]`, baca pesan dan perbaiki kode sebelum lanjut.

- [ ] **Step 3: Verifikasi CSV output memiliki kolom baru**

Setelah test, periksa header CSV yang dihasilkan:

```bash
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni --test 2>/dev/null
head -1 /tmp/test_sec_training.csv
```

Expected: header berisi semua kolom baru:
```
timestamp_ms,datetime,prb_usage_dl_ratio,...,label,stage1_alert,stage2_confirmed,alert_type,stage1_latency_ms,stage2_confirmation_time_ms,anomaly_score
```

- [ ] **Step 4: Update copy-xapp (dokumentasi) agar sesuai dengan source terbaru**

```bash
cp /home/telmat/flexric/examples/xApp/c/monitor/sec_ids.h /home/telmat/sec-xapp/copy-xapp/sec_ids.h
cp /home/telmat/flexric/examples/xApp/c/monitor/sec_ids.c /home/telmat/sec-xapp/copy-xapp/sec_ids.c
cp /home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c /home/telmat/sec-xapp/copy-xapp/xapp_sec_moni.c
```

- [ ] **Step 5: Commit**

```bash
cd /home/telmat/sec-xapp
git add docs/superpowers/specs/2026-05-16-two-stage-hybrid-detection-design.md \
        docs/superpowers/plans/2026-05-16-two-stage-hybrid-detection.md
git commit -m "docs: add two-stage hybrid detection design spec and implementation plan

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

```bash
cd /home/telmat/flexric
git add examples/xApp/c/monitor/sec_ids.h \
        examples/xApp/c/monitor/sec_ids.c \
        examples/xApp/c/monitor/xapp_sec_moni.c
git commit -m "feat: two-stage hybrid detection — Stage 1 WARNING + Stage 2 persistence CRITICAL

- R1/R2: Update threshold 90%→80%, WARNING only (Stage 2 handles FP)
- R7 (new): RF burst suspicion via sudden PRB collapse + scheduling inactivity
- R8 (new): Periodic saturation pattern via prb_burst_index ON/OFF cycle counting
- Stage 2: duration-based persistence validator (default 30s, empiris dari speedtest 15-40s vs flood >120s)
- CSV: tambah stage1_alert, stage2_confirmed, alert_type, latency columns, anomaly_score
- Tests: test_two_stage_detection() — A/B/C/D scenarios termasuk speedtest FP guard

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Known Limitations (untuk Buku TA)

Dokumentasikan limitation berikut secara eksplisit di Buku TA:

- **Mitigation delay:** Stage 2 meningkatkan confidence tetapi menambah mitigation latency ~30s. Ini disengaja untuk mengurangi false positive.
- **Sustained benign uploads:** Trafik benign dengan durasi PRB saturation > 30s (large file upload berkepanjangan) dapat tetap mencapai Stage 2 CRITICAL.
- **Detection bersifat cell-level:** Tidak dapat mengidentifikasi UE yang bertanggung jawab — hanya anomali sel agregat.
- **Radio-layer degradation detection heuristik:** Berbasis sudden PRB collapse; tidak ada SINR histogram atau PHY-layer telemetry dari KPM cell-level.
- **Speedtest threshold empiris:** Batas 30s diturunkan dari observasi testbed kami (speedtest 15–40s), bukan dari standar umum — durasi speedtest di lingkungan lain dapat bervariasi.

---

## Notes untuk Implementasi

**Tentang Rule 4 dan Rule 5 (lama):** Di sec_ids.c baru, Rule 4 (UL Flood via RLC) dan Rule 5 (DL Flood via RLC) tetap menggunakan severity=2 langsung (tanpa Stage 2) karena RLC rate > 15 Mbps adalah sinyal yang jauh lebih kuat daripada PRB saja. Namun di srsRAN, RLC volume selalu 0 sehingga Rule 4 dan 5 tidak pernah aktif di lapangan.

**Tentang static counter di Rule 4 dan 5:** Kode yang ditulis menggunakan `static int ul_flood_cnt` dan `static int dl_flood_cnt` di dalam blok lokal `{}`. Ini valid di C99+ (GCC mengizinkan ini). Pastikan build menggunakan `-std=c99` atau lebih baru, yang sudah menjadi default di FlexRIC.

**Tentang Rule 7 dan g_prev_prb_total:** `g_prev_prb_total` diupdate di akhir Rule 7 (`g_prev_prb_total = prb_total_now`). Ini berarti Rule 7 melihat PRB dari window SEBELUMNYA vs window SEKARANG — ini sesuai dengan konsep "sudden collapse".

**Tentang Stage 2 saturation reset:** Recovery timer `g_stage2_recovery_start_ms` hanya aktif jika `g_stage2_saturation_start_ms != 0` (artinya sebelumnya ada saturation event). Jika traffic normal sejak awal, tidak ada reset yang perlu dilakukan.
