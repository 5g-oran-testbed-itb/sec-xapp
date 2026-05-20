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
static int g_rlc_ul_flood_cnt = 0;
static int g_rlc_dl_flood_cnt = 0;

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
    g_rlc_ul_flood_cnt = 0;
    g_rlc_dl_flood_cnt = 0;

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

int rule_based_detect(cell_metrics_t const* m, long long now_ms)
{
    int severity    = 0;
    int stage1_hit  = 0;
    alert_type_t alert_type = ALERT_NONE;

    static const float EPS = 1e-6f;

    /* ── Rule 1 (update): UL Saturation — Stage 1 WARNING ─────────────────
     * prb_used_ul > 80% → ALERT_UL_SATURATION WARNING.
     * Threshold diperluas dari >90% ke >80% agar lebih sensitif.
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
     * Tetap tidak berubah — dipertahankan untuk backward compat. */
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

    /* ── Rule 3: RRC Flood via RACH Spike ────────────────────────────────── */
    {
        float rach_sum = 0.0f;
        for (int i = 0; i < RACH_HIST; i++) rach_sum += g_rach_hist[i];
        float rach_mean = rach_sum / (float)RACH_HIST;
        g_rach_hist[g_rach_hist_idx] = m->rach_preamble;
        g_rach_hist_idx = (g_rach_hist_idx + 1) % RACH_HIST;
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

    /* ── Rule 4 (lama): Uplink Flood via RLC — tidak berubah ─────────────── */
    {
        float rlc_rate_ul_kbps = m->rlc_vol_ul * 1000.0f / (float)g_period_ms;
        float rlc_rate_dl_kbps = m->rlc_vol_dl * 1000.0f / (float)g_period_ms;
        float rlc_rate_ul_mbps = rlc_rate_ul_kbps / 1000.0f;
        float rlc_rate_dl_mbps = rlc_rate_dl_kbps / 1000.0f;
        if (rlc_rate_ul_mbps > 15.0f && m->prb_used_ul > 80.0f && rlc_rate_dl_mbps < 0.5f) {
            g_rlc_ul_flood_cnt++;
        } else {
            g_rlc_ul_flood_cnt = 0;
        }
        if (g_rlc_ul_flood_cnt >= 3) {
            printf(">>> [STAGE1-WARNING] UPLINK_FLOOD (RLC) | RLC_UL=%.2fMbps "
                   "PRB_UL=%.0f%% selama %d windows (%.0fms)\n",
                   rlc_rate_ul_mbps, m->prb_used_ul, g_rlc_ul_flood_cnt,
                   g_rlc_ul_flood_cnt * (float)g_period_ms);
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
        if (rlc_rate_dl_mbps > 50.0f && m->prb_used_dl > 80.0f && rlc_rate_ul_mbps < 0.5f) {
            g_rlc_dl_flood_cnt++;
        } else {
            g_rlc_dl_flood_cnt = 0;
        }
        if (g_rlc_dl_flood_cnt >= 3) {
            printf(">>> [STAGE1-WARNING] DOWNLINK_FLOOD (RLC) | RLC_DL=%.2fMbps "
                   "PRB_DL=%.0f%% selama %d windows (%.0fms)\n",
                   rlc_rate_dl_mbps, m->prb_used_dl, g_rlc_dl_flood_cnt,
                   g_rlc_dl_flood_cnt * (float)g_period_ms);
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
        float prb_total_prev_snapshot = g_prev_prb_total;
        g_prev_prb_total = prb_total_now;

        if (g_rf_susp_cnt >= 2 && !stage1_hit) {
            printf(">>> [STAGE1-WARNING] RADIO_DEGRADATION_SUSPICION | PRB_prev=%.0f%%→now=%.0f%% "
                   "AirDelay=%.0fms selama %d windows — suspicious radio-layer degradation\n",
                   prb_total_prev_snapshot, prb_total_now, m->air_delay_ul,
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
        g_burst_rolling[g_burst_rolling_head] = prb_total;
        g_burst_rolling_head = (g_burst_rolling_head + 1) % IDS_BURST_ROLLING;
        if (g_burst_rolling_count < IDS_BURST_ROLLING) g_burst_rolling_count++;
        float bsum = 0.0f;
        for (int k = 0; k < g_burst_rolling_count; k++) {
            int idx = ((g_burst_rolling_head - 1 - k) % IDS_BURST_ROLLING
                       + IDS_BURST_ROLLING) % IDS_BURST_ROLLING;
            bsum += g_burst_rolling[idx];
        }
        float rolling_mean    = bsum / (float)g_burst_rolling_count;
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
