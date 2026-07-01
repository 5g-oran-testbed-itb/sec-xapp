#include "sec_ids.h"

#include <stdio.h>
#include <string.h>

/* ── Internal state ─────────────────────────────────────────────────────── */

#define RACH_HIST 10
#define IDS_BURST_ON_SLOTS 16  /* circular buffer: max simultaneous OFF→ON timestamps */
#define UL_VAR_ROLLING    10   /* rolling window untuk UL PRB variance (fast-path R1b) */
#define DL_VAR_ROLLING    10   /* rolling window untuk DL PRB variance (fast-path R2b) */

static uint64_t g_period_ms = 100;

/* ─── Stage 1 consecutive-window counters ─────────────────────────────── */
static int g_ul_sat_cnt      = 0;   /* ul_saturation consecutive windows */
static int g_dl_sat_cnt      = 0;   /* dl_saturation consecutive windows */
static int g_sig_storm_cnt   = 0;   /* signaling storm (Rule 2) */
static int g_empty_storm_cnt = 0;   /* RRC storm via empty indications (Rule 3b) */
static int g_rf_susp_cnt     = 0;   /* radio_degradation_suspicion (R7) consecutive */
/* ─── UL PRB rolling window untuk variance fast-path (R1b) ───────────────── */
static float g_ul_var_buf[UL_VAR_ROLLING] = {0};
static int   g_ul_var_head  = 0;
static int   g_ul_var_count = 0;

/* ─── DL PRB rolling window untuk variance fast-path (R2b) ───────────────── */
static float g_dl_var_buf[DL_VAR_ROLLING] = {0};
static int   g_dl_var_head  = 0;
static int   g_dl_var_count = 0;

/* ─── Stage 2 persistence state ─────────────────────────────────────────── */
static long long g_stage2_saturation_start_ms = 0;  /* epoch ms saat saturation pertama */
static long long g_stage2_saturation_dur_ms   = 0;  /* durasi kumulatif saturation */
static int       g_rrc_burst_cnt          = 0;  /* distinct empty-ind bursts dalam decay window */
static int       g_rrc_since_last_burst   = 0;  /* windows sejak burst terakhir selesai */
static int       g_rrc_was_in_burst       = 0;  /* flag: burst aktif di window sebelumnya */
static int       g_stage2_rf_susp_cnt         = 0;  /* consecutive windows RF suspicion (Stage 2) */
static long long g_stage2_recovery_start_ms   = 0;  /* epoch ms saat recovery mulai */

/* ─── Stage 2 configuration (empiris dari testbed) ───────────────────── */
static long long g_cfg_saturation_confirm_ms  = 30000; /* speedtest ≤40s, flood >120s */
static int       g_cfg_burst_on_threshold     = 4;  /* minimum ON transitions for Stage 2 */
#define RRC_BURST_DECAY_WIN  300   /* 30s @ 100ms: window decay burst counter */
#define RRC_STORM_BURST_CNT    3   /* 3 burst terpisah dalam 30s = storm */
static int       g_cfg_rf_susp_confirm_win    = 5;
static long long g_cfg_recovery_confirm_ms    = 5000;

/* ─── Stage 1 event tracking (untuk latency reporting) ───────────────── */
static long long g_stage1_event_start_ms = 0;   /* epoch ms Stage 1 pertama kali aktif */
static int       g_stage1_was_active     = 0;   /* flag: Stage 1 aktif di window sebelumnya */
static long long g_stage2_confirm_ms     = 0;   /* epoch ms Stage 2 terkonfirmasi */
static int       g_stage2_was_confirmed  = 0;

/* ─── R7: Previous PRB total untuk sudden-collapse detection ─────────── */
static float g_prev_prb_total = 0.0f;  /* prb_used_dl + prb_used_ul window sebelumnya */

/* ─── R8: OFF→ON transition counter untuk periodic burst detection ───── */
static long long g_burst_on_times[IDS_BURST_ON_SLOTS]; /* circ buf: OFF→ON timestamps */
static int       g_burst_on_head   = 0;  /* next write slot */
static int       g_burst_on_count  = 0;  /* valid entries (≤ IDS_BURST_ON_SLOTS) */
static int       g_burst_was_on    = 0;  /* 1 jika window sebelumnya ON */
static long long g_burst_active_until = 0; /* alert live sampai timestamp ini */

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
    g_ul_var_head      = 0;
    g_ul_var_count     = 0;
    memset(g_ul_var_buf, 0, sizeof(g_ul_var_buf));
    g_dl_var_head      = 0;
    g_dl_var_count     = 0;
    memset(g_dl_var_buf, 0, sizeof(g_dl_var_buf));

    g_stage2_saturation_start_ms = 0;
    g_stage2_saturation_dur_ms   = 0;
    g_rrc_burst_cnt        = 0;
    g_rrc_since_last_burst = 0;
    g_rrc_was_in_burst     = 0;
    g_stage2_rf_susp_cnt         = 0;
    g_stage2_recovery_start_ms   = 0;

    g_stage1_event_start_ms = 0;
    g_stage1_was_active     = 0;
    g_stage2_confirm_ms     = 0;
    g_stage2_was_confirmed  = 0;

    g_prev_prb_total      = 0.0f;
    g_burst_on_head       = 0;
    g_burst_on_count      = 0;
    g_burst_was_on        = 0;
    g_burst_active_until  = 0;

    g_rach_hist_idx = 0;
    memset(g_rach_hist,      0, sizeof(g_rach_hist));
    memset(g_burst_on_times, 0, sizeof(g_burst_on_times));
    memset(&g_last_detection, 0, sizeof(g_last_detection));
}

int rule_based_detect(cell_metrics_t const* m, long long now_ms)
{
    int severity    = 0;
    int stage1_hit  = 0;
    alert_type_t alert_type = ALERT_NONE;


    /* ── UL PRB variance (rolling 10 windows, ~1.2s) — untuk fast-path R1b ──
     * Variance near-zero = flatline = ciri khas UDP flood tanpa congestion control.
     * Speedtest tidak menyebabkan UL saturation sehingga tidak ada ambiguitas DL/UL.
     * prb_used_ul dalam satuan % (0–100), variance dalam unit %^2. */
    g_ul_var_buf[g_ul_var_head] = m->prb_used_ul;
    g_ul_var_head = (g_ul_var_head + 1) % UL_VAR_ROLLING;
    if (g_ul_var_count < UL_VAR_ROLLING) g_ul_var_count++;
    float prb_ul_variance = 0.0f;
    {
        float s = 0.0f;
        for (int k = 0; k < g_ul_var_count; k++) {
            int idx = ((g_ul_var_head - 1 - k) % UL_VAR_ROLLING + UL_VAR_ROLLING) % UL_VAR_ROLLING;
            s += g_ul_var_buf[idx];
        }
        float ul_mean = s / (float)g_ul_var_count;
        float sq = 0.0f;
        for (int k = 0; k < g_ul_var_count; k++) {
            int idx = ((g_ul_var_head - 1 - k) % UL_VAR_ROLLING + UL_VAR_ROLLING) % UL_VAR_ROLLING;
            float d = g_ul_var_buf[idx] - ul_mean;
            sq += d * d;
        }
        prb_ul_variance = sq / (float)g_ul_var_count;
    }

    /* ── DL PRB variance (rolling 10 windows, ~1.2s) — untuk fast-path R2b ──
     * Mirrors R1b: DL Flood dari server menghasilkan DL PRB flatline near 100%. */
    g_dl_var_buf[g_dl_var_head] = m->prb_used_dl;
    g_dl_var_head = (g_dl_var_head + 1) % DL_VAR_ROLLING;
    if (g_dl_var_count < DL_VAR_ROLLING) g_dl_var_count++;
    float prb_dl_variance = 0.0f;
    {
        float s = 0.0f;
        for (int k = 0; k < g_dl_var_count; k++) {
            int idx = ((g_dl_var_head - 1 - k) % DL_VAR_ROLLING + DL_VAR_ROLLING) % DL_VAR_ROLLING;
            s += g_dl_var_buf[idx];
        }
        float dl_mean = s / (float)g_dl_var_count;
        float sq = 0.0f;
        for (int k = 0; k < g_dl_var_count; k++) {
            int idx = ((g_dl_var_head - 1 - k) % DL_VAR_ROLLING + DL_VAR_ROLLING) % DL_VAR_ROLLING;
            float d = g_dl_var_buf[idx] - dl_mean;
            sq += d * d;
        }
        prb_dl_variance = sq / (float)g_dl_var_count;
    }

    /* ── Rule 1: UL Saturation — Stage 1 WARNING ──────────────────────────
     * Kondisi: prb_ul > 80% AND prb_dl < 15% selama ≥5 window consecutive.
     * Guard prb_dl < 15% membedakan UL flood (DL≈0%) dari TCP upload/speedtest
     * (DL>5% karena ACK). Data empiris: UL flood selalu prb_dl=0%;
     * normal UL>80% dengan prb_dl>5% = 46.6% dari kasus (TCP bidirectional).
     * Stage 2: sustained ≥ 30s → CRITICAL. */
    {
        int ul_sat = (m->prb_used_ul > 80.0f && m->prb_used_dl < 15.0f);
        if (ul_sat) {
            g_ul_sat_cnt++;
        } else {
            g_ul_sat_cnt = 0;
        }
        if (g_ul_sat_cnt >= 5 && !stage1_hit) {
            printf(">>> [STAGE1-WARNING] UL_SATURATION | PRB_UL=%.0f%% PRB_DL=%.0f%% "
                   "selama %d windows (%.0fms)\n",
                   m->prb_used_ul, m->prb_used_dl, g_ul_sat_cnt,
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
     * Stage 1 (WARNING): 3 window berturut-turut — single airplane/reconnect boleh trigger.
     * Stage 2 (CRITICAL): RRC_STORM_BURST_CNT burst TERPISAH dalam RRC_BURST_DECAY_WIN.
     * Dengan ini, 1x reconnect = WARNING saja; repeated churn = CRITICAL. */
    {
        int was_in_burst = g_rrc_was_in_burst;

        if (m->empty_ind_rate >= 2.0f && m->prb_used_ul < 30.0f && m->prb_used_dl < 30.0f) {
            g_empty_storm_cnt++;
        } else {
            if (was_in_burst && g_empty_storm_cnt >= 3)
                g_rrc_since_last_burst = 0;  /* catat burst baru saja selesai */
            g_empty_storm_cnt = 0;
        }

        int in_burst_now = (g_empty_storm_cnt >= 3);

        /* Stage 1: WARNING — single reconnect sudah cukup untuk ini */
        if (in_burst_now) {
            printf(">>> [STAGE1-WARNING] RRC_STORM (UE churn) | empty_ind=%.0f/window "
                   "selama %d windows (%.0fms)\n",
                   m->empty_ind_rate, g_empty_storm_cnt,
                   g_empty_storm_cnt * (float)g_period_ms);
            if (!stage1_hit) {
                stage1_hit = 1;
                alert_type = ALERT_RRC_STORM;
            }
            if (severity < 1) severity = 1;
        }

        /* Burst transition tracking untuk Stage 2 */
        if (in_burst_now && !was_in_burst) {
            /* Burst baru dimulai: reset counter jika sudah decay */
            if (g_rrc_since_last_burst > RRC_BURST_DECAY_WIN)
                g_rrc_burst_cnt = 0;
            g_rrc_burst_cnt++;
            g_rrc_since_last_burst = 0;
        }
        if (!in_burst_now)
            g_rrc_since_last_burst++;
        g_rrc_was_in_burst = in_burst_now;

        /* Stage 2: hanya setelah burst berulang — bukan single reconnect */
        if (g_rrc_burst_cnt >= RRC_STORM_BURST_CNT) {
            printf(">>> [STAGE2-CRITICAL] RRC_STORM CONFIRMED | %d burst terpisah "
                   "dalam %.0fs\n",
                   g_rrc_burst_cnt,
                   (float)(RRC_BURST_DECAY_WIN * g_period_ms) / 1000.0f);
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

    /* ── Rule 8: Periodic Burst Detection (OFF→ON transition counter) ────
     * Detects burst ON/OFF attack by counting OFF→ON transitions and maintaining
     * alert persistence across OFF phases.
     * Stage 1: ≥2 ON transitions within 90s AND alert window active (15s past last ON).
     * Stage 2: ≥4 ON transitions within 90s AND alert window active.
     *
     * burst_is_on: UL >70% AND DL <20% (UL-only high-traffic = burst or UL flood).
     * Rule 1 fires first for sustained UL flood; burst adds cycle-counting on top.
     * Alert window (15s) bridges OFF phases (max observed 5.8s) without excessive
     * post-attack lingering.                                                       */
    {
        int burst_is_on = (m->prb_used_ul > 70.0f && m->prb_used_dl < 20.0f);

        if (burst_is_on && !g_burst_was_on) {
            /* OFF→ON transition: record timestamp in circular buffer */
            g_burst_on_times[g_burst_on_head] = now_ms;
            g_burst_on_head = (g_burst_on_head + 1) % IDS_BURST_ON_SLOTS;
            if (g_burst_on_count < IDS_BURST_ON_SLOTS) g_burst_on_count++;
        }
        g_burst_was_on = burst_is_on;

        /* Evict transitions older than 90s; count valid ones */
        int valid = 0;
        long long latest_on = 0;
        for (int k = 0; k < g_burst_on_count; k++) {
            int slot = (g_burst_on_head - 1 - k + IDS_BURST_ON_SLOTS) % IDS_BURST_ON_SLOTS;
            if (now_ms - g_burst_on_times[slot] <= 90000LL) {
                valid++;
                if (g_burst_on_times[slot] > latest_on)
                    latest_on = g_burst_on_times[slot];
            }
        }
        g_burst_on_count = valid;

        /* Extend active window 15s past most recent ON transition once 2+ seen */
        if (valid >= 2) {
            long long candidate = latest_on + 15000LL;
            if (candidate > g_burst_active_until)
                g_burst_active_until = candidate;
        }

        int burst_alert = (g_burst_active_until > 0 && now_ms <= g_burst_active_until);

        if (burst_alert && !stage1_hit) {
            printf(">>> [STAGE1-WARNING] PERIODIC_BURST_ANOMALY | %d ON-cycles dalam 90s, "
                   "active_window=%llds\n",
                   valid, (long long)(g_burst_active_until - now_ms) / 1000LL);
            stage1_hit = 1;
            alert_type = ALERT_PERIODIC_BURST_ANOMALY;
            if (severity < 1) severity = 1;
        }
        if (burst_alert && valid >= g_cfg_burst_on_threshold) {
            printf(">>> [STAGE2-CRITICAL] PERIODIC_BURST_ANOMALY CONFIRMED | %d ON-cycles "
                   "dalam 90s\n", valid);
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

        /* DEBUG: print every 50 calls to diagnose DL Stage2 stall */
        static int _dbg_call = 0;
        if (++_dbg_call % 50 == 0)
            printf("[S2-DBG] alert=%d sat=%d start=%lld dur=%lldms\n",
                   alert_type, sat_active,
                   g_stage2_saturation_start_ms, g_stage2_saturation_dur_ms);

        if (sat_active) {
            /* Reset start time if: (a) first-ever saturation, OR (b) resuming after a
             * gap — recovery was in progress but hasn't completed. Case (b) prevents
             * a prior DL/UL saturation's stale start_ms from being inherited when a
             * different saturation type begins within the 5s recovery window. */
            if (g_stage2_saturation_start_ms == 0 || g_stage2_recovery_start_ms != 0) {
                g_stage2_saturation_start_ms = now_ms;
                g_stage2_saturation_dur_ms   = 0;
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

            /* ── Fast-path R1b: UL flatline variance — UDP flood tanpa CC ─────────
             * Kondisi: UL saturasi ≥15s DAN variance ≈0 (PRB flat).
             * Guard 15s mencegah FP dari iperf3 -u pendek: UDP iperf3 identik dengan
             * attack traffic (no DL ACKs, no CC, flat PRB) sehingga Rule1 guard
             * prb_dl<15% tidak memblokir UDP iperf3. 15s masih 2× lebih cepat dari
             * normal 30s path namun aman untuk iperf3 test < 15s.
             * Threshold variance 0.0001 (%^2) — dari data: flood=0.000003, jauh di bawah. */
            if (alert_type == ALERT_UL_SATURATION
                    && g_stage2_saturation_dur_ms >= 15000
                    && prb_ul_variance < 0.0001f
                    && m->prb_used_ul > 80.0f) {
                printf(">>> [STAGE2-CRITICAL] UL_SATURATION FAST-PATH | "
                       "variance=%.6f duration=%.0fms (UDP flood flatline)\n",
                       prb_ul_variance, (float)g_stage2_saturation_dur_ms);
                if (severity < 2) severity = 2;
            }

            /* ── Fast-path R2b: DL flatline variance — DL flood dari server ─────
             * Mirror R1b: DL Flood menghasilkan DL PRB ≈100% flat selama serangan.
             * Durasi 15s sama dengan R1b — mencegah FP dari iperf3 -u -R pendek. */
            if (alert_type == ALERT_DL_SATURATION
                    && g_stage2_saturation_dur_ms >= 15000
                    && prb_dl_variance < 0.0001f
                    && m->prb_used_dl > 80.0f) {
                printf(">>> [STAGE2-CRITICAL] DL_SATURATION FAST-PATH | "
                       "variance=%.6f duration=%.0fms (DL flood flatline)\n",
                       prb_dl_variance, (float)g_stage2_saturation_dur_ms);
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
