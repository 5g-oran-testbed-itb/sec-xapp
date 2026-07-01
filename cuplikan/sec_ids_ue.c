#include "sec_ids_ue.h"
#include <string.h>
#include <stdio.h>
#include <math.h>

/* ── Global state table ───────────────────────────────────────────────────── */
ue_ids_state_t g_ue_ids_states[UE_IDS_MAX_SLOTS];

/* ── RNTI lookup / allocation ─────────────────────────────────────────────── */
int find_or_create_ue_state(uint16_t rnti) {
    int free_slot = -1;
    for (int i = 0; i < UE_IDS_MAX_SLOTS; i++) {
        if (g_ue_ids_states[i].active && g_ue_ids_states[i].rnti == rnti)
            return i;
        if (!g_ue_ids_states[i].active && free_slot < 0)
            free_slot = i;
    }
    if (free_slot < 0) {
        fprintf(stderr, "[UE-IDS] Table full (%d slots), cannot add RNTI 0x%04x\n",
                UE_IDS_MAX_SLOTS, rnti);
        return -1;
    }
    memset(&g_ue_ids_states[free_slot], 0, sizeof(ue_ids_state_t));
    g_ue_ids_states[free_slot].rnti   = rnti;
    g_ue_ids_states[free_slot].active = true;
    return free_slot;
}

void ue_ids_deactivate(uint16_t rnti) {
    for (int i = 0; i < UE_IDS_MAX_SLOTS; i++) {
        if (g_ue_ids_states[i].active && g_ue_ids_states[i].rnti == rnti) {
            g_ue_ids_states[i].active = false;
            return;
        }
    }
}

/* ── Feature computation + ML window push ────────────────────────────────── */
void ue_ids_update(int idx,
                   float prb_dl, float prb_ul, float thp_dl, float thp_ul,
                   float out_feat[ML_NUM_FEATURES])
{
    if (idx < 0 || idx >= UE_IDS_MAX_SLOTS) return;
    static const float EPS = 1e-6f;
    ue_ids_state_t *s = &g_ue_ids_states[idx];

    /* --- Derived features --- */
    float prb_total     = prb_dl + prb_ul;
    float prb_direction = (prb_ul - prb_dl) / (prb_total + EPS);
    float prb_ul_delta  = prb_ul - s->prev_prb_ul;
    float ul_eff        = (prb_ul > 0.005f) ? (thp_ul / prb_ul) : 0.0f;
    if (ul_eff > 50000.0f) ul_eff = 50000.0f;
    float thp_total     = thp_dl + thp_ul;
    float thp_ul_delta  = thp_ul - s->prev_thp_ul;
    float thp_dl_delta  = thp_dl - s->prev_thp_dl;
    float traffic_dir   = (thp_ul - thp_dl) / (thp_total + EPS);

    s->prev_prb_ul = prb_ul;
    s->prev_thp_ul = thp_ul;
    s->prev_thp_dl = thp_dl;

    /* --- Rolling PRB-UL stats (window=PRB_UL_ROLL_WIN=10, fixed, matches training) --- */
    int h = s->prb_ul_hist_head;
    s->prb_ul_hist[h] = prb_ul;
    s->prb_ul_hist_head = (h + 1) % PRB_UL_ROLL_WIN;
    if (s->prb_ul_hist_count < PRB_UL_ROLL_WIN) s->prb_ul_hist_count++;
    int n = s->prb_ul_hist_count;

    float sum_ul = 0.0f;
    int   persistent = 0;
    for (int k = 0; k < n; k++) {
        float v = s->prb_ul_hist[k];
        sum_ul += v;
        if (v > EPS) persistent++;
    }
    float roll_mean = sum_ul / (float)n;
    float ul_pers   = (float)persistent / (float)n;

    float var_sum = 0.0f;
    for (int k = 0; k < n; k++) {
        float d = s->prb_ul_hist[k] - roll_mean;
        var_sum += d * d;
    }
    float roll_std = sqrtf(var_sum / (float)n);

    /* --- Burst index rolling means (window=BURST_WIN=10) --- */
#define _PUSH_HIST(hist, head, cnt, val) do { \
    (hist)[(head)] = (val);                    \
    (head) = ((head) + 1) % BURST_WIN;        \
    if ((cnt) < BURST_WIN) (cnt)++;            \
} while(0)
#define _MEAN(hist, cnt) ({ \
    float _s = 0.0f;        \
    for (int _k = 0; _k < (cnt); _k++) _s += (hist)[_k]; \
    (cnt) > 0 ? _s / (float)(cnt) : 0.0f; \
})

    _PUSH_HIST(s->prb_dl_hist, s->prb_dl_hist_head, s->prb_dl_hist_count, prb_dl);
    _PUSH_HIST(s->thp_ul_hist, s->thp_ul_hist_head, s->thp_ul_hist_count, thp_ul);
    _PUSH_HIST(s->thp_dl_hist, s->thp_dl_hist_head, s->thp_dl_hist_count, thp_dl);

    float prb_dl_mean = _MEAN(s->prb_dl_hist, s->prb_dl_hist_count);
    float thp_ul_mean = _MEAN(s->thp_ul_hist, s->thp_ul_hist_count);
    float thp_dl_mean = _MEAN(s->thp_dl_hist, s->thp_dl_hist_count);

#undef _PUSH_HIST
#undef _MEAN

    /* Burst indices: PRB uses log(1+x)/(mean+eps); THP uses x/(mean+1) */
#define _CLIP50(x) ((x) > 50.0f ? 50.0f : ((x) < 0.0f ? 0.0f : (x)))
    float prb_ul_bi = _CLIP50(log1pf(prb_ul) / (roll_mean + EPS));
    float prb_dl_bi = _CLIP50(log1pf(prb_dl) / (prb_dl_mean + EPS));
    float thp_ul_bi = _CLIP50(thp_ul / (thp_ul_mean + 1.0f));
    float thp_dl_bi = _CLIP50(thp_dl / (thp_dl_mean + 1.0f));
#undef _CLIP50

    /* --- Fill feature vector (feature_schema_ue.py order, 19 features) --- */
    out_feat[0]  = prb_dl;         /* prb_usage_dl_ratio */
    out_feat[1]  = prb_ul;         /* prb_usage_ul_ratio */
    out_feat[2]  = thp_dl;         /* thp_dl_kbps */
    out_feat[3]  = thp_ul;         /* thp_ul_kbps */
    out_feat[4]  = prb_direction;  /* prb_direction */
    out_feat[5]  = prb_total;      /* prb_total */
    out_feat[6]  = prb_ul_delta;   /* prb_ul_delta */
    out_feat[7]  = ul_eff;         /* ul_efficiency */
    out_feat[8]  = roll_mean;      /* prb_ul_roll_mean */
    out_feat[9]  = roll_std;       /* prb_ul_roll_std */
    out_feat[10] = ul_pers;        /* ul_persistence */
    out_feat[11] = thp_total;      /* thp_total_kbps */
    out_feat[12] = thp_ul_delta;   /* thp_ul_delta */
    out_feat[13] = thp_dl_delta;   /* thp_dl_delta */
    out_feat[14] = traffic_dir;    /* traffic_direction */
    out_feat[15] = prb_ul_bi;      /* prb_ul_burst_index */
    out_feat[16] = prb_dl_bi;      /* prb_dl_burst_index */
    out_feat[17] = thp_ul_bi;      /* thp_ul_burst_index */
    out_feat[18] = thp_dl_bi;      /* thp_dl_burst_index */

    /* --- Push to ML shift window --- */
    if (s->ml_window_count >= ML_SEQ_LEN) {
        /* shift rows 0..ML_SEQ_LEN-2 up by one */
        memmove(s->ml_window[0], s->ml_window[1],
                (ML_SEQ_LEN - 1) * ML_NUM_FEATURES * sizeof(float));
    }
    memcpy(s->ml_window[s->ml_window_count < ML_SEQ_LEN
                        ? s->ml_window_count
                        : ML_SEQ_LEN - 1],
           out_feat, ML_NUM_FEATURES * sizeof(float));
    if (s->ml_window_count < ML_SEQ_LEN)
        s->ml_window_count++;
}

/* ── Rule Engine ─────────────────────────────────────────────────────────── *
 *
 * Rule thresholds are compile-time constants derived from benign dataset
 * analysis (N=6,002 rows). Intentionally not runtime-configurable.
 *
 * Feature indices (feature_schema_ue.py):
 *   0=prb_usage_dl_ratio  1=prb_usage_ul_ratio  2=thp_dl_kbps  3=thp_ul_kbps
 *   4=prb_direction       5=prb_total            6=prb_ul_delta
 *   7=ul_efficiency       8=prb_ul_roll_mean     9=prb_ul_roll_std
 *  10=ul_persistence     11=thp_total_kbps      12=thp_ul_delta
 *  13=thp_dl_delta       14=traffic_direction
 */
#define R1_THP_UL_KBPS      15000.0f
#define R1_PRB_UL_RATIO     0.70f
#define R1_CONSECUTIVE      5

#define R2_THP_DL_KBPS      15000.0f
#define R2_PRB_DL_RATIO     0.85f
#define R2_CONSECUTIVE      5

#define R3_PRB_UL_ROLL_STD  0.12f
#define R3_PRB_UL_ROLL_MEAN 0.05f
#define R3_CONSECUTIVE      5

#define R4_UL_PERSISTENCE   0.90f
#define R4_PRB_UL_ROLL_MEAN 0.50f
#define R4_CONSECUTIVE      10

#define R5_PRB_UL_RATIO     0.30f
#define R5_UL_EFFICIENCY    5000.0f
#define R5_CONSECUTIVE      3

rule_result_t rule_based_detect_ue(int idx,
                                   float const features[ML_NUM_FEATURES],
                                   long long now_ms)
{
    rule_result_t invalid = {0, 0u};
    if (idx < 0 || idx >= UE_IDS_MAX_SLOTS) return invalid;
    (void)now_ms;   /* reserved for future time-based rules */
    ue_ids_state_t *s = &g_ue_ids_states[idx];
    rule_result_t result = {0, 0u};

    /* R1 — UL Flood */
    if (features[3] > R1_THP_UL_KBPS || features[1] > R1_PRB_UL_RATIO)
        s->consecutive_r1++;
    else
        s->consecutive_r1 = 0;
    if (s->consecutive_r1 >= R1_CONSECUTIVE) {
        result.rule_mask |= RULE_MASK_R1;
        result.severity = 1;
    }

    /* R2 — DL Flood */
    if (features[2] > R2_THP_DL_KBPS || features[0] > R2_PRB_DL_RATIO)
        s->consecutive_r2++;
    else
        s->consecutive_r2 = 0;
    if (s->consecutive_r2 >= R2_CONSECUTIVE) {
        result.rule_mask |= RULE_MASK_R2;
        result.severity = 1;
    }

    /* R3 — Burst (rolling std AND mean) */
    if (features[9] > R3_PRB_UL_ROLL_STD && features[8] > R3_PRB_UL_ROLL_MEAN)
        s->consecutive_r3++;
    else
        s->consecutive_r3 = 0;
    if (s->consecutive_r3 >= R3_CONSECUTIVE) {
        result.rule_mask |= RULE_MASK_R3;
        result.severity = 1;
    }

    /* R4 — Persistence (ul_persistence AND roll_mean) */
    if (features[10] >= R4_UL_PERSISTENCE && features[8] > R4_PRB_UL_ROLL_MEAN)
        s->consecutive_r4++;
    else
        s->consecutive_r4 = 0;
    if (s->consecutive_r4 >= R4_CONSECUTIVE) {
        result.rule_mask |= RULE_MASK_R4;
        if (result.severity < 2) result.severity = 2;
    }

    /* R5 — Efficiency / LDoS */
    if (features[1] > R5_PRB_UL_RATIO && features[7] < R5_UL_EFFICIENCY)
        s->consecutive_r5++;
    else
        s->consecutive_r5 = 0;
    if (s->consecutive_r5 >= R5_CONSECUTIVE) {
        result.rule_mask |= RULE_MASK_R5;
        if (result.severity < 2) result.severity = 2;
    }

    return result;
}

/* ── IDS mode parsing ─────────────────────────────────────────────────────── */
int ids_mode_parse(const char *s) {
    if (!s) return -1;
    if (strcmp(s, "rule-only")    == 0) return IDS_MODE_RULE_ONLY;
    if (strcmp(s, "lstm-only")    == 0) return IDS_MODE_LSTM_ONLY;
    if (strcmp(s, "lstm-hybrid")  == 0) return IDS_MODE_LSTM_HYBRID;
    if (strcmp(s, "gru-only")     == 0) return IDS_MODE_GRU_ONLY;
    if (strcmp(s, "gru-hybrid")   == 0) return IDS_MODE_GRU_HYBRID;
    return -1;
}

/* ── Decision Engine ─────────────────────────────────────────────────────── */
ue_alert_type_t decision_engine_ue(int idx,
                                   rule_result_t rule,
                                   float mse, float threshold,
                                   ids_mode_t mode, long long now_ms)
{
    if (idx < 0 || idx >= UE_IDS_MAX_SLOTS) return UE_ALERT_NONE;
    ue_ids_state_t *s = &g_ue_ids_states[idx];

    /* Cooldown: last_alert_ms==0 means "no prior alert" (zero-initialized).
     * Caller must ensure now_ms > 0 (valid CLOCK_REALTIME epoch). */
    if (s->last_alert_ms > 0 && (now_ms - s->last_alert_ms) < ALERT_COOLDOWN_MS)
        return UE_ALERT_NONE;

    int rule_hit = (rule.severity >= 1);
    int ml_hit   = (mse > 0.0f && mse > threshold);

    ue_alert_type_t alert = UE_ALERT_NONE;

    switch (mode) {
        case IDS_MODE_RULE_ONLY:
            if (rule_hit) alert = UE_ALERT_RULE;
            break;

        case IDS_MODE_LSTM_ONLY:
        case IDS_MODE_GRU_ONLY:
            if (ml_hit) alert = UE_ALERT_ML;
            break;

        case IDS_MODE_LSTM_HYBRID:
        case IDS_MODE_GRU_HYBRID:
            if (rule_hit && ml_hit)  alert = UE_ALERT_HYBRID;
            else if (rule_hit)       alert = UE_ALERT_RULE;
            else if (ml_hit)         alert = UE_ALERT_ML;
            break;
    }

    if (alert != UE_ALERT_NONE)
        s->last_alert_ms = now_ms;

    return alert;
}
