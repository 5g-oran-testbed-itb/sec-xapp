#ifndef SEC_IDS_UE_H
#define SEC_IDS_UE_H

#include <stdint.h>
#include <stdbool.h>

/* UE_IDS_MAX_SLOTS avoids conflict: xapp_sec_moni.c defines MAX_UE=10 */
#define UE_IDS_MAX_SLOTS  32
#define ML_SEQ_LEN        30   /* sliding window depth (30 × 1s = 30s context) */
#define ML_NUM_FEATURES   19   /* 15 base + 4 burst index features */
#define PRB_UL_ROLL_WIN   10   /* window for prb_ul rolling mean/std (fixed, matches training) */
#define BURST_WIN         10   /* window for burst index rolling means */
#define ALERT_COOLDOWN_MS 30000LL

/* Rule bitmask — which rules fired in a detection call */
#define RULE_MASK_R1  0x01u   /* UL Flood */
#define RULE_MASK_R2  0x02u   /* DL Flood */
#define RULE_MASK_R3  0x04u   /* Burst */
#define RULE_MASK_R4  0x08u   /* Persistence */
#define RULE_MASK_R5  0x10u   /* Efficiency / LDoS */

typedef enum {
    IDS_MODE_RULE_ONLY    = 0,
    IDS_MODE_LSTM_ONLY    = 1,
    IDS_MODE_LSTM_HYBRID  = 2,
    IDS_MODE_GRU_ONLY     = 3,
    IDS_MODE_GRU_HYBRID   = 4
} ids_mode_t;

/* ue_alert_type_t avoids conflict: sec_ids.h defines its own alert_type_t */
typedef enum {
    UE_ALERT_NONE    = 0,
    UE_ALERT_RULE    = 1,
    UE_ALERT_ML      = 2,
    UE_ALERT_HYBRID  = 3
} ue_alert_type_t;

typedef struct {
    int      severity;    /* 0=normal, 1=warning (R1-R3), 2=critical (R4-R5) */
    uint32_t rule_mask;   /* OR of RULE_MASK_Rx for each fired rule */
} rule_result_t;

typedef struct {
    uint16_t rnti;
    bool     active;

    /*
     * ML shift buffer: [0]=oldest row, [ML_SEQ_LEN-1]=newest row.
     * New sample: shift rows 0..N-2 up, write to [N-1].
     * Row-major layout [30][19] matches ONNX input [1,30,19] directly.
     */
    float ml_window[ML_SEQ_LEN][ML_NUM_FEATURES];
    int   ml_window_count;  /* 0..ML_SEQ_LEN; stays at ML_SEQ_LEN once full */

    /*
     * Rolling PRB-UL circular buffer (PRB_UL_ROLL_WIN=10) for computing
     * prb_ul_roll_mean, prb_ul_roll_std, ul_persistence.
     * Window fixed at 10 to match training data — independent of ML_SEQ_LEN.
     */
    float prb_ul_hist[PRB_UL_ROLL_WIN];
    int   prb_ul_hist_head;   /* next write index */
    int   prb_ul_hist_count;  /* 0..PRB_UL_ROLL_WIN */

    /* Burst index rolling means (BURST_WIN=10, matching feature_schema_ue.py) */
    float prb_dl_hist[BURST_WIN];
    int   prb_dl_hist_head;
    int   prb_dl_hist_count;
    float thp_ul_hist[BURST_WIN];
    int   thp_ul_hist_head;
    int   thp_ul_hist_count;
    float thp_dl_hist[BURST_WIN];
    int   thp_dl_hist_head;
    int   thp_dl_hist_count;

    /* Previous-sample values for delta features */
    float prev_prb_ul;
    float prev_thp_ul;
    float prev_thp_dl;

    /* Rule consecutive-window counters — reset to 0 when condition not met */
    int consecutive_r1;
    int consecutive_r2;
    int consecutive_r3;
    int consecutive_r4;
    int consecutive_r5;

    long long last_alert_ms;  /* epoch ms of last non-NONE alert (cooldown) */
} ue_ids_state_t;

/* Global state table — declared in sec_ids_ue.c, extern here so
 * xapp_sec_moni.c can pass state->ml_window directly to ONNX.     */
extern ue_ids_state_t g_ue_ids_states[UE_IDS_MAX_SLOTS];

/*
 * find_or_create_ue_state() — look up RNTI in state table.
 * Returns slot index [0, UE_IDS_MAX_SLOTS), or -1 if table full.
 * Creates a zeroed slot on first encounter.
 */
int find_or_create_ue_state(uint16_t rnti);

/*
 * ue_ids_deactivate() — mark slot inactive when UE detaches.
 * The slot can be reused by a new RNTI afterwards.
 */
void ue_ids_deactivate(uint16_t rnti);

/*
 * ue_ids_update() — compute all 15 derived features from raw KPM measurements
 * and push them into the ML shift window.
 *
 * Computation matches csv_per_ue_write() exactly:
 *   - prb_ul pushed into prb_ul_hist (circular), then rolling stats computed
 *     including current sample.
 *   - Deltas computed from prev_prb_ul / prev_thp_* fields.
 *   - ul_efficiency = thp_ul / prb_ul, clamped [0, 50000].
 *
 * out_feat[ML_NUM_FEATURES] is filled with the 15 computed features in
 * feature_schema_ue.py order (index 0=prb_usage_dl_ratio ... 14=traffic_direction).
 * Pass out_feat to rule_based_detect_ue() for rule evaluation.
 *
 * MUST be called before rule_based_detect_ue() on the same sample.
 */
void ue_ids_update(int idx,
                   float prb_dl, float prb_ul, float thp_dl, float thp_ul,
                   float out_feat[ML_NUM_FEATURES]);

/*
 * rule_based_detect_ue() — evaluate R1-R5 against current-sample features.
 * Updates consecutive counters in state.
 * Returns {severity, rule_mask}: severity 0=normal, 1=warning, 2=critical.
 * now_ms: epoch milliseconds (from clock_gettime CLOCK_REALTIME).
 */
rule_result_t rule_based_detect_ue(int idx,
                                   float const features[ML_NUM_FEATURES],
                                   long long now_ms);

/*
 * ids_mode_parse() — map "--ids-mode" string to enum.
 * Returns -1 on unknown string or NULL input.
 * Valid strings: "rule-only", "lstm-only", "lstm-hybrid", "gru-only", "gru-hybrid"
 */
int ids_mode_parse(const char *s);

/*
 * decision_engine_ue() — combine rule result + ML MSE score.
 * Applies ALERT_COOLDOWN_MS; updates last_alert_ms on non-NONE return.
 * mse=0.0f treated as "no ML result" (window not yet full or RULE_ONLY mode).
 *
 * Logic:
 *   RULE_ONLY:                rule.severity>=1  → UE_ALERT_RULE
 *   LSTM_ONLY / GRU_ONLY:     mse>threshold     → UE_ALERT_ML
 *   LSTM_HYBRID / GRU_HYBRID: rule && !ml       → UE_ALERT_RULE
 *                             !rule && ml        → UE_ALERT_ML
 *                             rule && ml         → UE_ALERT_HYBRID
 */
ue_alert_type_t decision_engine_ue(int idx,
                                   rule_result_t rule,
                                   float mse, float threshold,
                                   ids_mode_t mode, long long now_ms);

/* String representation for CSV/log output */
static inline const char *ue_alert_type_str(ue_alert_type_t t) {
    switch (t) {
        case UE_ALERT_RULE:   return "RULE";
        case UE_ALERT_ML:     return "ML";
        case UE_ALERT_HYBRID: return "HYBRID";
        default:              return "NONE";
    }
}

#endif /* SEC_IDS_UE_H */
