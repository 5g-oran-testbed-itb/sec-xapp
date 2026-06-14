# Per-UE IDS + ONNX Integration — Design Spec

**Date:** 2026-06-14
**Status:** Freeze

---

## Goal

Add per-UE anomaly detection to `xapp_sec_moni` via a new modular IDS engine
(`sec_ids_ue.c`) and a single ONNX ML session. Support 5 runtime-selectable modes
(`--ids-mode`) so the LSTM TA and GRU TA can share one binary without interference.

---

## Background

The existing cell-level pipeline (`sec_ids.c` / `security_model.onnx`) operates on
aggregate KPM FORMAT_4/5 metrics and is **not modified by this work**.

The per-UE KPM FORMAT_3 handler in `xapp_sec_moni.c` already extracts 15 features
and writes them to a per-UE CSV. What it currently lacks:
- Per-UE rule-based anomaly detection
- Per-UE ML inference
- Alert logging

This spec adds all three.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `sec_ids_ue.h` | Create | State structs, enums, function declarations |
| `sec_ids_ue.c` | Create | R1-R5 rule engine + decision engine |
| `export_onnx_ue.py` | Create | Export GRU-UE v1 / LSTM-UE v1 to ONNX (RobustScaler baked in) |
| `xapp_sec_moni.c` | Modify | `--ids-mode` flag, single ONNX session, FORMAT_3 handler integration |
| `models/gru_ue_v1.onnx` | Output | Exported GRU ONNX model |
| `models/lstm_ue_v1.onnx` | Output | Exported LSTM ONNX model |

**Untouched:** `sec_ids.c`, `sec_ids.h`, `security_model.onnx`, `train_gru_ue.py`,
`train_lstm_ue.py`, `feature_schema_ue.py`.

---

## Data Types and Constants (sec_ids_ue.h)

```c
/* UE_IDS_MAX_SLOTS — avoid redefinition: xapp_sec_moni.c defines MAX_UE=10 */
#define UE_IDS_MAX_SLOTS  32
#define ML_SEQ_LEN        10
#define ML_NUM_FEATURES   15
#define ALERT_COOLDOWN_MS 30000

/* Bitmask — which rules fired */
#define RULE_MASK_R1  0x01u   /* UL Flood */
#define RULE_MASK_R2  0x02u   /* DL Flood */
#define RULE_MASK_R3  0x04u   /* Burst */
#define RULE_MASK_R4  0x08u   /* Persistence */
#define RULE_MASK_R5  0x10u   /* Efficiency / LDoS */

typedef enum {
    IDS_MODE_RULE_ONLY,
    IDS_MODE_LSTM_ONLY,
    IDS_MODE_LSTM_HYBRID,
    IDS_MODE_GRU_ONLY,
    IDS_MODE_GRU_HYBRID
} ids_mode_t;

/* ue_alert_type_t — avoid redefinition: sec_ids.h defines its own alert_type_t */
typedef enum {
    UE_ALERT_NONE,
    UE_ALERT_RULE,
    UE_ALERT_ML,
    UE_ALERT_HYBRID
} ue_alert_type_t;

typedef struct {
    int      severity;    /* 0=normal, 1=warning (R1-R3), 2=critical (R4-R5) */
    uint32_t rule_mask;   /* bitmask of fired rules */
} rule_result_t;

typedef struct {
    uint16_t rnti;
    bool     active;

    /* ML shift buffer — [0]=oldest, [ML_SEQ_LEN-1]=newest, always chronological.
     * New samples shift rows 0..N-2 up and write to [N-1]. Avoids reordering
     * for ONNX inference (C row-major [10][15] == ONNX [1,10,15]).            */
    float ml_window[ML_SEQ_LEN][ML_NUM_FEATURES];
    int   ml_window_count;  /* 0..ML_SEQ_LEN; stays at ML_SEQ_LEN when full    */

    /* Rolling PRB-UL buffer (mirrors prb_ul history for rolling stats).
     * Separate from ml_window to match csv_per_ue_write() computation order:
     * push prb_ul → compute stats (including current) → fill features.       */
    float prb_ul_hist[ML_SEQ_LEN];
    int   prb_ul_hist_head;   /* next write index (circular) */
    int   prb_ul_hist_count;  /* 0..ML_SEQ_LEN */

    /* Previous-sample values for delta features */
    float prev_prb_ul;
    float prev_thp_ul;
    float prev_thp_dl;

    /* Rule consecutive counters */
    int consecutive_r1;
    int consecutive_r2;
    int consecutive_r3;
    int consecutive_r4;
    int consecutive_r5;

    long long last_alert_ms;  /* cooldown reference */
} ue_ids_state_t;
```

---

## Public Interface (sec_ids_ue.h)

```c
/* Returns slot index [0, UE_IDS_MAX_SLOTS), or -1 if table full */
int find_or_create_ue_state(uint16_t rnti);

/* Mark slot inactive when UE detaches */
void ue_ids_deactivate(uint16_t rnti);

/* Compute all 15 derived features from raw measurements, push to ML window,
   and return features in out_feat for use by rule_based_detect_ue().
   Maintains per-UE prb_ul rolling history + delta state internally.
   Must be called before rule_based_detect_ue() on the same sample.     */
void ue_ids_update(int idx,
                   float prb_dl, float prb_ul, float thp_dl, float thp_ul,
                   float out_feat[ML_NUM_FEATURES]);

/* Evaluate R1-R5 for this UE using current sample features.
   Must be called after ue_ids_push_features() for the same sample. */
rule_result_t rule_based_detect_ue(int idx,
                                   float const features[ML_NUM_FEATURES],
                                   long long now_ms);

/* Map --ids-mode string to enum. Returns -1 on unknown string. */
ids_mode_t ids_mode_parse(const char *s);

/* Decision engine — combines rule result + ML score.
   Returns UE_ALERT_NONE if cooldown active or no trigger. */
ue_alert_type_t decision_engine_ue(int idx, rule_result_t rule,
                                   float mse, float threshold,
                                   ids_mode_t mode, long long now_ms);
```

---

## Feature Order (ML Window Column Mapping)

The 15 columns written to `ml_window[pos]` must match the feature order used during
training in `feature_schema_ue.py`:

```
Index  Feature
0      prb_usage_dl_ratio
1      prb_usage_ul_ratio
2      thp_dl_kbps
3      thp_ul_kbps
4      prb_direction
5      prb_total
6      prb_ul_delta
7      ul_efficiency
8      prb_ul_roll_mean
9      prb_ul_roll_std
10     ul_persistence
11     thp_total_kbps
12     thp_ul_delta
13     thp_dl_delta
14     traffic_direction
```

Rolling features (indices 8-10) are re-computed from `ml_window` history by
`ue_ids_push_features()` before writing to the window; the caller passes raw
measurements only.

---

## Rule Engine (sec_ids_ue.c)

All thresholds are compile-time constants derived from benign dataset analysis
(N=6,002 rows). They are intentionally not configurable at runtime to avoid
accidental misconfiguration during dataset collection.

### R1 — UL Flood (Stage-1, WARNING)
```
condition : thp_ul_kbps > 15000 OR prb_usage_ul_ratio > 0.70
required  : 5 consecutive windows
mask bit  : RULE_MASK_R1
```
*Threshold source: benign thp_ul P99 = 15,300 kbps; consecutive requirement
reduces benign hit rate to negligible.*

### R2 — DL Flood (Stage-1, WARNING)
```
condition : thp_dl_kbps > 15000 OR prb_usage_dl_ratio > 0.85
required  : 5 consecutive windows
mask bit  : RULE_MASK_R2
```
*DL PRB is sporadic in srsRAN; throughput OR provides reliable fallback.*

### R3 — Burst (Stage-1, WARNING)
```
condition : prb_ul_roll_std > 0.12 AND prb_ul_roll_mean > 0.05
required  : 5 consecutive windows
mask bit  : RULE_MASK_R3
```
*Per-window benign hit 4.7%; consecutive requirement drops FPR to negligible.*

### R4 — Persistence (Stage-2, CRITICAL)
```
condition : ul_persistence >= 0.90 AND prb_ul_roll_mean > 0.50
required  : 10 consecutive windows
mask bit  : RULE_MASK_R4
```
*Benign hit 1.8%; double-gate (persistence + mean) necessary.*

### R5 — Efficiency / LDoS (Stage-2, CRITICAL)
```
condition : prb_usage_ul_ratio > 0.30 AND ul_efficiency < 5000
required  : 3 consecutive windows
mask bit  : RULE_MASK_R5
```
*Cleanest rule: only 0.3% benign hit. Active-UL median efficiency = 25,485.*

### Severity Mapping
- R1, R2, R3 → `severity = 1` (WARNING)
- R4, R5 → `severity = 2` (CRITICAL)
- Multiple rules in one call → severity = max, mask = OR of all fired masks

---

## ONNX Export (export_onnx_ue.py)

Two separate ONNX files; export them independently:

```bash
# LSTM
./venv/bin/python3 export_onnx_ue.py \
    --model  models/lstm_ue_v1.pt \
    --scaler models/lstm_ue_v1_scaler.pkl \
    --arch   lstm \
    --out    models/lstm_ue_v1.onnx

# GRU
./venv/bin/python3 export_onnx_ue.py \
    --model  models/gru_ue_v1.pt \
    --scaler models/gru_ue_v1_scaler.pkl \
    --arch   gru \
    --out    models/gru_ue_v1.onnx
```

**RobustScaler is baked into the ONNX graph** as a `Mul` + `Add` pre-processing
node. The C xApp feeds raw feature values; no external scaler step needed.

ONNX input/output shapes:
- Input name: `"input"`, shape `float32[1, 10, 15]` (batch=1, seq=10, features=15)
- Output name: `"mse"`, shape `float32[1]` — MSE computed inside the ONNX graph
  (`mean((x_scaled - reconstructed)^2)` over all 150 elements)

The ONNX wrapper computes MSE internally so C only does: `mse > g_ue_threshold`.
This is consistent with the existing cell-level export pattern.

---

## Threshold Runtime Loading (xapp_sec_moni.c)

Threshold is **not hardcoded**. At startup, `xapp_sec_moni.c` reads the JSON file
corresponding to the selected mode:

```
IDS_MODE_LSTM_ONLY / LSTM_HYBRID → models/lstm_ue_v1_threshold.json
IDS_MODE_GRU_ONLY  / GRU_HYBRID  → models/gru_ue_v1_threshold.json
IDS_MODE_RULE_ONLY                → no JSON loaded
```

JSON format (already produced by `train_gru_ue.py` / `train_lstm_ue.py`):
```json
{
  "threshold": 2793671.0,
  "percentile": 99.0,
  "fpr_pct": 1.004,
  "source": "validation_set"
}
```

Only the `"threshold"` key is read. The loaded value is stored in a `float g_ue_threshold`.

---

## Decision Engine

```
IDS_MODE_RULE_ONLY:
  rule.severity >= 1  → ALERT_RULE

IDS_MODE_LSTM_ONLY / GRU_ONLY:
  mse > g_ue_threshold  → ALERT_ML

IDS_MODE_LSTM_HYBRID / GRU_HYBRID:
  rule_triggered  = (rule.severity >= 1)
  ml_triggered    = (mse > g_ue_threshold)

  rule_triggered  && !ml_triggered  → ALERT_RULE
  !rule_triggered &&  ml_triggered  → ALERT_ML
  rule_triggered  &&  ml_triggered  → ALERT_HYBRID
```

Cooldown: if `now_ms - state->last_alert_ms < ALERT_COOLDOWN_MS`, return
`ALERT_NONE` regardless of triggers. `last_alert_ms` is updated on every
non-NONE return.

ML inference is **skipped** (mse = 0) when `ml_window_filled == 0` — i.e. fewer
than 10 rows have been accumulated for this UE since attach.

---

## Alert Output

Every non-NONE alert is written to:
1. stdout: `[UE-IDS] ts=<ms> rnti=0x<hex> mask=0x<hex> stage=<0-2> mse=<f> thr=<f> type=<str>`
2. CSV: `ue_alerts_YYYYMMDD_HHmmss.csv` (one file per xApp session)

CSV columns:
```
timestamp_ms, rnti, rule_mask, rule_stage, mse, threshold, alert_type
```

Example:
```
1718368800000,0x4601,0x09,2,3141592.0,2793671.0,HYBRID
```
`rule_mask=0x09` = R1 (0x01) | R4 (0x08) both active.

---

## Startup Examples

```bash
# TA LSTM — hybrid mode (default for evaluation)
./xapp_sec_moni -c my_xapp_kpm.conf --ids-mode=lstm-hybrid

# TA GRU — hybrid mode
./xapp_sec_moni -c my_xapp_kpm.conf --ids-mode=gru-hybrid

# Rule only — for dataset collection / baseline (no ONNX loaded)
./xapp_sec_moni -c my_xapp_kpm.conf --ids-mode=rule-only

# ML only — ablation study
./xapp_sec_moni -c my_xapp_kpm.conf --ids-mode=lstm-only
```

Default when `--ids-mode` is absent: `rule-only`. Backward compatible — existing
`start_xapp_c.sh` works without modification.

---

## Evaluation Table (Planned)

After attack dataset collection, results in TA LSTM:

| Mode | Recall | FPR | Notes |
|------|--------|-----|-------|
| Rule Only | TBD | TBD | Baseline |
| LSTM Only | TBD | TBD | ML ablation |
| LSTM Hybrid | TBD | TBD | Full system |

---

## What Is NOT Included

- Per-UE E2SM-RC mitigation (out of scope; cell-level mitigation via `--mitigate` unchanged)
- UE detach detection (future work; `ue_ids_deactivate()` is provided but caller
  must invoke it from the correct E2 indication handler)
- Multi-model ensemble (deliberately excluded — each TA owns one model)
