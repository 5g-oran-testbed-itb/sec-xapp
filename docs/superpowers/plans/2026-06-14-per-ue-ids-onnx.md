# Per-UE IDS + ONNX Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-UE rule-based + ML anomaly detection to xapp_sec_moni via a new `sec_ids_ue.c` module, a single runtime-selected ONNX session, and 5 selectable IDS modes (`--ids-mode`).

**Architecture:** New `sec_ids_ue.h/c` handles per-UE state (RNTI lookup table, rolling history, rule counters, ML window) and R1–R5 rule evaluation. `export_onnx_ue.py` exports GRU/LSTM per-UE models to ONNX with RobustScaler baked in and MSE as output. `xapp_sec_moni.c` gets a new `--ids-mode` flag, loads one ONNX session at startup, and calls IDS functions from the FORMAT_3 handler.

**Tech Stack:** C99 (FlexRIC), ONNX Runtime C API (`onnxruntime_c_api.h`), Python 3 (PyTorch, onnx, scikit-learn RobustScaler), GNU make.

---

## File Map

| File | Action |
|------|--------|
| `sec-xapp/export_onnx_ue.py` | Create — exports GRU or LSTM per-UE model to ONNX |
| `flexric/examples/xApp/c/monitor/sec_ids_ue.h` | Create — types, constants, public API |
| `flexric/examples/xApp/c/monitor/sec_ids_ue.c` | Create — `find_or_create_ue_state`, `ue_ids_update`, R1–R5 rules, decision engine |
| `flexric/examples/xApp/c/monitor/xapp_sec_moni.c` | Modify — add `--ids-mode` arg, per-UE ONNX session, FORMAT_3 IDS call |
| `sec-xapp/models/lstm_ue_v1.onnx` | Output — from export_onnx_ue.py |
| `sec-xapp/models/gru_ue_v1.onnx` | Output — from export_onnx_ue.py |

**Paths:**
- sec-xapp: `/home/telmat/sec-xapp`
- xApp source: `/home/telmat/flexric/examples/xApp/c/monitor/`
- xApp build: `cd /home/telmat/flexric/build && make -j$(nproc) xapp_sec_moni`

**Do NOT touch:** `sec_ids.h`, `sec_ids.c`, `security_model.onnx`, `train_gru_ue.py`, `train_lstm_ue.py`.

---

## Task 1: export_onnx_ue.py

Export GRU or LSTM per-UE autoencoder to ONNX. RobustScaler baked in. Output: scalar MSE per batch.

**Files:**
- Create: `/home/telmat/sec-xapp/export_onnx_ue.py`

**Context:**
- GRU model class: `src/detection/gru_autoencoder.py::GRUAutoencoder`
- LSTM model class: `src/detection/lstm_autoencoder.py::LSTMAutoencoder`
- Both use `.load(path, config)` class method
- Scaler files: `models/gru_ue_v1_scaler.pkl`, `models/lstm_ue_v1_scaler.pkl` (RobustScaler)
- Threshold files: `models/gru_ue_v1_threshold.json`, `models/lstm_ue_v1_threshold.json`
- Feature schema: `src/detection/feature_schema_ue.py` — 15 features
- Virtual env: `./venv/bin/python3`

- [ ] **Step 1: Write export_onnx_ue.py**

```python
"""
Export per-UE GRU or LSTM autoencoder to ONNX.

Pipeline baked into ONNX:
  raw features → RobustScaler → autoencoder → MSE scalar

Input ONNX  : raw (unscaled) features, float32[1, 10, 15]
Output ONNX : MSE scalar, float32[1]  (compare > threshold in C)

Usage:
  ./venv/bin/python3 export_onnx_ue.py \\
      --arch lstm \\
      --model  models/lstm_ue_v1.pt \\
      --scaler models/lstm_ue_v1_scaler.pkl \\
      --out    models/lstm_ue_v1.onnx

  ./venv/bin/python3 export_onnx_ue.py \\
      --arch gru \\
      --model  models/gru_ue_v1.pt \\
      --scaler models/gru_ue_v1_scaler.pkl \\
      --out    models/gru_ue_v1.onnx
"""

import argparse
import os
import pickle
import sys

import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.lstm_autoencoder import LSTMAutoencoder
from src.detection.feature_schema_ue import NUM_FEATURES

SEQ_LEN = 10


class ONNXPerUEWrapper(nn.Module):
    """Wraps autoencoder with RobustScaler pre-processing and MSE output.

    RobustScaler: x_scaled = (x - center_) / scale_
    Output: mean((x_scaled - reconstruction)^2) over all timesteps and features.
    """

    def __init__(self, model: nn.Module, scaler):
        super().__init__()
        self.model = model
        center = torch.tensor(scaler.center_, dtype=torch.float32)
        scale = torch.tensor(scaler.scale_, dtype=torch.float32)
        scale = torch.clamp(scale, min=1e-8)
        self.center = nn.Parameter(center, requires_grad=False)
        self.scale = nn.Parameter(scale, requires_grad=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x_scaled = (x - self.center) / self.scale
        reconstructed = self.model(x_scaled)
        mse = torch.mean((x_scaled - reconstructed) ** 2, dim=(1, 2))
        return mse


def _gru_config():
    return {
        "gru_model": {
            "input_features": NUM_FEATURES,
            "encoder_hidden": [64, 32],
            "decoder_hidden": [32, 64],
            "latent_dim": 32,
            "bidirectional": True,
        },
        "detection": {"sequence_length": SEQ_LEN},
    }


def _lstm_config():
    return {
        "lstm_model": {
            "input_features": NUM_FEATURES,
            "encoder_hidden": [64, 32],
            "decoder_hidden": [32, 64],
            "latent_dim": 32,
            "bidirectional": False,
        },
        "detection": {"sequence_length": SEQ_LEN},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch",   required=True, choices=["gru", "lstm"])
    parser.add_argument("--model",  required=True)
    parser.add_argument("--scaler", required=True)
    parser.add_argument("--out",    required=True)
    args = parser.parse_args()

    for p in [args.model, args.scaler]:
        if not os.path.exists(p):
            print(f"Error: {p} not found")
            sys.exit(1)

    print(f"[1/4] Loading {args.arch.upper()} model from {args.model} ...")
    if args.arch == "gru":
        base_model = GRUAutoencoder.load(args.model, _gru_config())
    else:
        base_model = LSTMAutoencoder.load(args.model, _lstm_config())
    base_model.eval()

    print(f"[2/4] Loading RobustScaler from {args.scaler} ...")
    with open(args.scaler, "rb") as f:
        scaler = pickle.load(f)
    print(f"      {len(scaler.center_)} features  "
          f"center[0]={scaler.center_[0]:.4f}  scale[0]={scaler.scale_[0]:.4f}")

    print("[3/4] Wrapping model ...")
    wrapped = ONNXPerUEWrapper(base_model, scaler)
    wrapped.eval()

    dummy = torch.zeros(1, SEQ_LEN, NUM_FEATURES, dtype=torch.float32)
    with torch.no_grad():
        dummy_mse = wrapped(dummy)
    print(f"      Dummy forward OK — mse={dummy_mse.item():.6f}")

    print(f"[4/4] Exporting to {args.out} ...")
    torch.onnx.export(
        wrapped,
        dummy,
        args.out,
        export_params=True,
        opset_version=14,
        do_constant_folding=True,
        input_names=["input"],
        output_names=["mse"],
        dynamic_axes={"input": {0: "batch_size"}, "mse": {0: "batch_size"}},
    )
    size_kb = os.path.getsize(args.out) / 1024
    print(f"[OK] {args.out}  ({size_kb:.1f} KB)")
    print(f"     Input : float32[1, {SEQ_LEN}, {NUM_FEATURES}] — raw features")
    print(f"     Output: float32[1] — MSE (compare > threshold in C)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Export LSTM model**

```bash
cd /home/telmat/sec-xapp
./venv/bin/python3 export_onnx_ue.py \
    --arch   lstm \
    --model  models/lstm_ue_v1.pt \
    --scaler models/lstm_ue_v1_scaler.pkl \
    --out    models/lstm_ue_v1.onnx
```

Expected output ends with:
```
[OK] models/lstm_ue_v1.onnx  (XXX.X KB)
     Input : float32[1, 10, 15] — raw features
     Output: float32[1] — MSE (compare > threshold in C)
```

- [ ] **Step 3: Export GRU model**

```bash
./venv/bin/python3 export_onnx_ue.py \
    --arch   gru \
    --model  models/gru_ue_v1.pt \
    --scaler models/gru_ue_v1_scaler.pkl \
    --out    models/gru_ue_v1.onnx
```

Expected: same format, file `models/gru_ue_v1.onnx` exists.

- [ ] **Step 4: Verify ONNX files exist**

```bash
ls -lh /home/telmat/sec-xapp/models/lstm_ue_v1.onnx \
        /home/telmat/sec-xapp/models/gru_ue_v1.onnx
```

Expected: both files present, size > 100 KB.

- [ ] **Step 5: Commit**

```bash
cd /home/telmat/sec-xapp
git add export_onnx_ue.py models/lstm_ue_v1.onnx models/gru_ue_v1.onnx
git commit -m "feat: add export_onnx_ue.py; export lstm_ue_v1 and gru_ue_v1 to ONNX"
```

---

## Task 2: sec_ids_ue.h

Header file with all types, constants, and public API for per-UE IDS module.

**Files:**
- Create: `/home/telmat/flexric/examples/xApp/c/monitor/sec_ids_ue.h`

**Critical naming:** `sec_ids.h` already defines `alert_type_t`. This file uses `ue_alert_type_t`. `xapp_sec_moni.c` already defines `MAX_UE 10`. This file uses `UE_IDS_MAX_SLOTS`.

- [ ] **Step 1: Create sec_ids_ue.h**

```c
#ifndef SEC_IDS_UE_H
#define SEC_IDS_UE_H

#include <stdint.h>
#include <stdbool.h>
#include <math.h>

/* UE_IDS_MAX_SLOTS avoids conflict: xapp_sec_moni.c defines MAX_UE=10 */
#define UE_IDS_MAX_SLOTS  32
#define ML_SEQ_LEN        10
#define ML_NUM_FEATURES   15
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
     * Row-major layout [10][15] matches ONNX input [1,10,15] directly.
     */
    float ml_window[ML_SEQ_LEN][ML_NUM_FEATURES];
    int   ml_window_count;  /* 0..ML_SEQ_LEN; stays at ML_SEQ_LEN once full */

    /*
     * Rolling PRB-UL circular buffer for computing prb_ul_roll_mean,
     * prb_ul_roll_std, ul_persistence. Matches csv_per_ue_write() order:
     * push prb_ul first, then compute stats including current sample.
     */
    float prb_ul_hist[ML_SEQ_LEN];
    int   prb_ul_hist_head;   /* next write index */
    int   prb_ul_hist_count;  /* 0..ML_SEQ_LEN */

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
 * Returns -1 on unknown string.
 * Valid strings: "rule-only", "lstm-only", "lstm-hybrid", "gru-only", "gru-hybrid"
 */
ids_mode_t ids_mode_parse(const char *s);

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
```

- [ ] **Step 2: Verify header compiles standalone**

```bash
cd /home/telmat/flexric/examples/xApp/c/monitor
gcc -fsyntax-only -std=c99 -Wall -I. sec_ids_ue.h 2>&1
```

Expected: no errors. (Warnings about external decls are OK; errors are not.)

---

## Task 3: sec_ids_ue.c

Rule engine implementation: RNTI table, `ue_ids_update()`, R1–R5 rules, `decision_engine_ue()`.

**Files:**
- Create: `/home/telmat/flexric/examples/xApp/c/monitor/sec_ids_ue.c`

- [ ] **Step 1: Create sec_ids_ue.c**

```c
#include "sec_ids_ue.h"
#include <string.h>
#include <stdio.h>
#include <math.h>

/* ── Global state table ───────────────────────────────────────────────────── */
ue_ids_state_t g_ue_ids_states[UE_IDS_MAX_SLOTS];

static int g_ue_ids_init_done = 0;

static void ensure_init(void) {
    if (!g_ue_ids_init_done) {
        memset(g_ue_ids_states, 0, sizeof(g_ue_ids_states));
        g_ue_ids_init_done = 1;
    }
}

/* ── RNTI lookup / allocation ─────────────────────────────────────────────── */
int find_or_create_ue_state(uint16_t rnti) {
    ensure_init();
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

    /* --- Rolling PRB-UL stats (push current first, then compute) --- */
    int h = s->prb_ul_hist_head;
    s->prb_ul_hist[h] = prb_ul;
    s->prb_ul_hist_head = (h + 1) % ML_SEQ_LEN;
    if (s->prb_ul_hist_count < ML_SEQ_LEN) s->prb_ul_hist_count++;
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

    /* --- Fill feature vector (feature_schema_ue.py order) --- */
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
ids_mode_t ids_mode_parse(const char *s) {
    if (strcmp(s, "rule-only")    == 0) return IDS_MODE_RULE_ONLY;
    if (strcmp(s, "lstm-only")    == 0) return IDS_MODE_LSTM_ONLY;
    if (strcmp(s, "lstm-hybrid")  == 0) return IDS_MODE_LSTM_HYBRID;
    if (strcmp(s, "gru-only")     == 0) return IDS_MODE_GRU_ONLY;
    if (strcmp(s, "gru-hybrid")   == 0) return IDS_MODE_GRU_HYBRID;
    return (ids_mode_t)(-1);
}

/* ── Decision Engine ─────────────────────────────────────────────────────── */
ue_alert_type_t decision_engine_ue(int idx,
                                   rule_result_t rule,
                                   float mse, float threshold,
                                   ids_mode_t mode, long long now_ms)
{
    ue_ids_state_t *s = &g_ue_ids_states[idx];

    /* Cooldown check */
    if (s->last_alert_ms > 0 && (now_ms - s->last_alert_ms) < ALERT_COOLDOWN_MS)
        return UE_ALERT_NONE;

    int rule_hit = (rule.severity >= 1);
    int ml_hit   = (mse > threshold && mse > 0.0f);

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
```

- [ ] **Step 2: Verify sec_ids_ue.c compiles**

```bash
cd /home/telmat/flexric/examples/xApp/c/monitor
gcc -c -std=c99 -Wall -Wextra -Wno-unused-parameter -I. \
    sec_ids_ue.c -o /tmp/sec_ids_ue.o 2>&1
```

Expected: exit 0, no errors. (Warnings about `(void)now_ms` or unused fields are OK.)

- [ ] **Step 3: Commit**

```bash
cd /home/telmat/flexric/examples/xApp/c/monitor
git add sec_ids_ue.h sec_ids_ue.c
git commit -m "feat: add sec_ids_ue — per-UE IDS with R1-R5 rules and decision engine"
```

---

## Task 4: xapp_sec_moni.c — IDS Mode + ONNX + FORMAT_3 Integration

Four surgical changes to the existing 2400-line file:
1. Add `#include "sec_ids_ue.h"` and per-UE globals (near top)
2. Add `--ids-mode` parsing in `main()` (alongside existing `--mode`)
3. Add `init_onnx_ue()` and `run_inference_ue()` functions
4. Call IDS pipeline from the FORMAT_3 handler

**Files:**
- Modify: `/home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c`

**Read these line ranges before editing:**
- Lines 48-55: existing includes and `#define MAX_UE`
- Lines 78-84: ONNX globals
- Lines 1569-1655: FORMAT_3 handler
- Lines 2050-2082: `--mode` / `--label` / `--mitigate` argument parsing in main()
- Lines 2123-2128: `init_onnx()` and `ids_init()` calls in main()

### Change 1: Add include + per-UE globals

Find the block after `#include "sec_ids.h"` (around line 50):

```c
#include <onnxruntime_c_api.h>
#include "sec_ids.h"
#include "ue_tracker.h"
```

Add after this block:

```c
#include "sec_ids_ue.h"

/* Per-UE IDS runtime state */
static ids_mode_t   g_ids_mode     = IDS_MODE_RULE_ONLY;
static OrtSession*  sess_ml        = NULL;   /* per-UE ONNX session (NULL in RULE_ONLY) */
static float        g_ue_threshold = 0.0f;  /* loaded from JSON at startup */
static FILE*        g_ue_alert_fp  = NULL;  /* ue_alerts_*.csv */
```

- [ ] **Step 1: Apply Change 1**

Find the exact lines in the file:
```bash
grep -n '#include "sec_ids.h"' \
    /home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c
```

Then add the block immediately after those three include lines.

### Change 2: Add --ids-mode argument parsing

In `main()`, inside the arg-stripping loop (around lines 2057-2081), after the existing `--mode` block, add:

```c
      } else if (strcmp(argv[a], "--ids-mode") == 0 && a + 1 < argc) {
          a++;
          ids_mode_t m = ids_mode_parse(argv[a]);
          if ((int)m < 0) {
              fprintf(stderr, "[WARN] Unknown --ids-mode '%s', using rule-only.\n",
                      argv[a]);
          } else {
              g_ids_mode = m;
          }
          printf("[IDS-UE] mode=%s\n", argv[a]);
```

- [ ] **Step 2: Apply Change 2**

Find the end of the `--mode` block:
```bash
grep -n '"hybrid"\|using hybrid\|fargv\[fargc' \
    /home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c | head -10
```

Insert after the closing brace of the `--mode` block, before the `} else {` that falls through to `fargv[fargc++]`.

### Change 3: Add init_onnx_ue() and run_inference_ue()

Add these two functions directly before the existing `init_onnx(void)` function (around line 122).

First, add a helper to load threshold from JSON:

```c
/* Load "threshold" key from a threshold JSON file.
 * Uses strstr() search — whitespace-insensitive, handles compact and pretty JSON.
 * Returns 0.0f on failure (caller should check g_ue_threshold > 0). */
static float load_ue_threshold(const char *json_path) {
    FILE *f = fopen(json_path, "r");
    if (!f) {
        fprintf(stderr, "[IDS-UE] Cannot open threshold file: %s\n", json_path);
        return 0.0f;
    }
    char buf[1024] = {0};
    fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);

    char *key = strstr(buf, "\"threshold\"");
    if (!key) {
        fprintf(stderr, "[IDS-UE] Key 'threshold' not found in %s\n", json_path);
        return 0.0f;
    }
    /* Advance past the key and the colon */
    char *colon = strchr(key, ':');
    if (!colon) return 0.0f;
    float thr = strtof(colon + 1, NULL);
    return thr;
}

static void init_onnx_ue(void) {
    /* Determine which ONNX file and threshold JSON to load */
    const char *model_path = NULL;
    const char *thr_path   = NULL;
    switch (g_ids_mode) {
        case IDS_MODE_LSTM_ONLY:
        case IDS_MODE_LSTM_HYBRID:
            model_path = "/home/telmat/sec-xapp/models/lstm_ue_v1.onnx";
            thr_path   = "/home/telmat/sec-xapp/models/lstm_ue_v1_threshold.json";
            break;
        case IDS_MODE_GRU_ONLY:
        case IDS_MODE_GRU_HYBRID:
            model_path = "/home/telmat/sec-xapp/models/gru_ue_v1.onnx";
            thr_path   = "/home/telmat/sec-xapp/models/gru_ue_v1_threshold.json";
            break;
        default:
            /* RULE_ONLY — no ONNX needed */
            printf("[IDS-UE] RULE_ONLY mode — ONNX not loaded.\n");
            return;
    }

    g_ue_threshold = load_ue_threshold(thr_path);
    if (g_ue_threshold <= 0.0f) {
        fprintf(stderr, "[IDS-UE] Invalid threshold from %s — ONNX disabled.\n", thr_path);
        return;
    }
    printf("[IDS-UE] Threshold: %.2f (from %s)\n", g_ue_threshold, thr_path);

    /* Reuse existing g_ort env created by init_onnx() */
    if (!g_ort || !env || !session_options) {
        fprintf(stderr, "[IDS-UE] ONNX Runtime not initialized — call init_onnx() first.\n");
        return;
    }
    OrtStatus *st = g_ort->CreateSession(env, model_path, session_options, &sess_ml);
    if (st) {
        fprintf(stderr, "[IDS-UE] Failed to load %s: %s\n",
                model_path, g_ort->GetErrorMessage(st));
        g_ort->ReleaseStatus(st);
        sess_ml = NULL;
    } else {
        printf("[IDS-UE] Per-UE ONNX loaded: %s\n", model_path);
    }
}

/* Run per-UE ONNX inference on state->ml_window.
 * Returns MSE (raw float). Returns 0.0f if not ready. */
static float run_inference_ue(int ue_slot) {
    if (!sess_ml) return 0.0f;
    ue_ids_state_t *s = &g_ue_ids_states[ue_slot];
    if (s->ml_window_count < ML_SEQ_LEN) return 0.0f;

    /* ml_window is [ML_SEQ_LEN][ML_NUM_FEATURES] in row-major — same as ONNX [1,10,15] */
    const int64_t shape[] = {1, ML_SEQ_LEN, ML_NUM_FEATURES};
    size_t nbytes = (size_t)(ML_SEQ_LEN * ML_NUM_FEATURES) * sizeof(float);
    OrtValue *in_tensor = NULL;
    OrtStatus *st = g_ort->CreateTensorWithDataAsOrtValue(
        memory_info, s->ml_window, nbytes, shape, 3,
        ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &in_tensor);
    if (st) { g_ort->ReleaseStatus(st); return 0.0f; }

    const char *in_names[]  = {"input"};
    const char *out_names[] = {"mse"};
    OrtValue *out_tensor = NULL;
    st = g_ort->Run(sess_ml, NULL, in_names,
                    (const OrtValue *const *)&in_tensor, 1,
                    out_names, 1, &out_tensor);
    float mse = 0.0f;
    if (!st && out_tensor) {
        float *p;
        g_ort->GetTensorMutableData(out_tensor, (void **)&p);
        mse = p[0];
        g_ort->ReleaseValue(out_tensor);
    } else if (st) {
        fprintf(stderr, "[IDS-UE] Inference error: %s\n", g_ort->GetErrorMessage(st));
        g_ort->ReleaseStatus(st);
    }
    g_ort->ReleaseValue(in_tensor);
    return mse;
}

/* Log per-UE alert to stdout and ue_alerts CSV */
static void alert_log_ue(uint32_t rnti, rule_result_t rule, float mse,
                         float threshold, ue_alert_type_t alert_type,
                         long long now_ms)
{
    printf("[UE-IDS] ts=%lld rnti=0x%04x mask=0x%02x stage=%d "
           "mse=%.2f thr=%.2f type=%s\n",
           (long long)now_ms, rnti, rule.rule_mask, rule.severity,
           mse, threshold, ue_alert_type_str(alert_type));
    fflush(stdout);
    if (g_ue_alert_fp) {
        fprintf(g_ue_alert_fp, "%lld,0x%04x,0x%02x,%d,%.6f,%.6f,%s\n",
                (long long)now_ms, rnti, rule.rule_mask, rule.severity,
                mse, threshold, ue_alert_type_str(alert_type));
        fflush(g_ue_alert_fp);
    }
}
```

- [ ] **Step 3: Apply Change 3**

Locate line number of `static void init_onnx(void)`:
```bash
grep -n "^static void init_onnx(" \
    /home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c
```

Insert the entire `load_ue_threshold`, `init_onnx_ue`, `run_inference_ue`, and `alert_log_ue` block immediately before that line.

### Change 4: Open alert CSV + call init_onnx_ue() in main()

After the existing `init_onnx(); ids_init(kpm_period_ms);` lines in `main()` (around line 2124), add:

```c
  /* Open per-UE alert CSV */
  {
      char alert_path[256];
      time_t now_a = time(NULL);
      struct tm *tm_a = localtime(&now_a);
      strftime(alert_path, sizeof(alert_path),
               "/home/telmat/sec-xapp/csv/ue_alerts_%Y%m%d_%H%M%S.csv",
               tm_a);
      g_ue_alert_fp = fopen(alert_path, "w");
      if (g_ue_alert_fp)
          fprintf(g_ue_alert_fp,
                  "timestamp_ms,rnti,rule_mask,rule_stage,mse,threshold,alert_type\n");
  }
  defer({ if (g_ue_alert_fp) fclose(g_ue_alert_fp); });

  /* Per-UE ONNX session — must be after init_onnx() */
  init_onnx_ue();
```

- [ ] **Step 4: Apply Change 4**

Locate `ids_init(kpm_period_ms)`:
```bash
grep -n "ids_init(" /home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c
```

Insert the block immediately after that line.

### Change 5: IDS pipeline in FORMAT_3 handler

In the FORMAT_3 handler, after the existing `csv_per_ue_write(...)` call (around line 1653), add the IDS pipeline:

```c
      /* Per-UE IDS pipeline */
      {
          int ue_slot = find_or_create_ue_state((uint16_t)rnti);
          if (ue_slot >= 0) {
              struct timespec _ts_ids; clock_gettime(CLOCK_REALTIME, &_ts_ids);
              long long ids_now_ms = (long long)_ts_ids.tv_sec * 1000LL
                                   + (long long)_ts_ids.tv_nsec / 1000000LL;

              float features[ML_NUM_FEATURES];
              ue_ids_update(ue_slot, prb_dl, prb_ul, thp_dl, thp_ul, features);

              rule_result_t rule = rule_based_detect_ue(ue_slot, features, ids_now_ms);

              float mse = run_inference_ue(ue_slot);

              ue_alert_type_t alert = decision_engine_ue(
                  ue_slot, rule, mse, g_ue_threshold, g_ids_mode, ids_now_ms);

              if (alert != UE_ALERT_NONE)
                  alert_log_ue(rnti, rule, mse, g_ue_threshold, alert, ids_now_ms);
          }
      }
```

- [ ] **Step 5: Apply Change 5**

Locate `csv_per_ue_write(&g_csv_per_ue` in FORMAT_3 handler:
```bash
grep -n "csv_per_ue_write" \
    /home/telmat/flexric/examples/xApp/c/monitor/xapp_sec_moni.c
```

Insert the IDS block immediately after the `csv_per_ue_write(...)` call (after the semicolon, same indentation level as the if-block).

- [ ] **Step 6: Build**

```bash
cd /home/telmat/flexric/build
make -j$(nproc) xapp_sec_moni 2>&1 | tail -20
```

Expected: `[100%] Linking C executable xapp_sec_moni` with no errors. Warnings about unused variables are OK.

- [ ] **Step 7: Commit**

```bash
cd /home/telmat/flexric/examples/xApp/c/monitor
git add xapp_sec_moni.c
git commit -m "feat: integrate per-UE IDS into xapp_sec_moni — --ids-mode, ONNX session, FORMAT_3 pipeline"
```

---

## Task 5: Smoke Test

Verify the binary works in rule-only mode (no RIC needed) using the existing `--test` path.

**Files:**
- No file changes — test only

- [ ] **Step 1: Verify --help / startup**

```bash
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni --test 2>&1 | head -30
```

Expected: exits cleanly (exit 0), no segfault, ONNX init lines appear.

- [ ] **Step 2: Verify --ids-mode=rule-only starts**

```bash
timeout 5 /home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni \
    -c /home/telmat/xapp/security-xapp/my_xapp_kpm.conf \
    --ids-mode=rule-only 2>&1 | head -20
```

Expected output includes:
```
[IDS-UE] mode=rule-only
[IDS-UE] RULE_ONLY mode — ONNX not loaded.
```
(Then it waits for E2 nodes — `timeout 5` exits it.)

- [ ] **Step 3: Verify --ids-mode=lstm-hybrid loads ONNX**

```bash
timeout 5 /home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni \
    -c /home/telmat/xapp/security-xapp/my_xapp_kpm.conf \
    --ids-mode=lstm-hybrid 2>&1 | head -20
```

Expected output includes:
```
[IDS-UE] mode=lstm-hybrid
[IDS-UE] Threshold: XXXXXXXX.xx (from .../lstm_ue_v1_threshold.json)
[IDS-UE] Per-UE ONNX loaded: .../lstm_ue_v1.onnx
ONNX Runtime initialized successfully
```

- [ ] **Step 4: Verify ue_alerts CSV is created**

```bash
ls -lh /home/telmat/sec-xapp/csv/ue_alerts_*.csv 2>/dev/null | tail -3
```

Expected: at least one file exists from the smoke test above (even with 0 data rows — just header).

- [ ] **Step 5: Final commit (sec-xapp side)**

```bash
cd /home/telmat/sec-xapp
git add docs/superpowers/specs/2026-06-14-per-ue-ids-onnx.md \
        docs/superpowers/plans/2026-06-14-per-ue-ids-onnx.md
git commit -m "docs: add frozen spec and implementation plan for per-UE IDS + ONNX"
```

---

## Build Reference

```bash
# Full rebuild:
cd /home/telmat/flexric/build && make -j$(nproc) xapp_sec_moni

# Binary location:
/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_moni

# Run in hybrid mode (for evaluation):
./xapp_sec_moni -c /home/telmat/xapp/security-xapp/my_xapp_kpm.conf \
    --ids-mode=lstm-hybrid

# Run rule-only (for dataset collection without ML overhead):
./xapp_sec_moni -c /home/telmat/xapp/security-xapp/my_xapp_kpm.conf \
    --ids-mode=rule-only
```

---

## Self-Review Checklist

- [x] Task 1: export_onnx_ue.py — exports both GRU and LSTM, verifies file exists
- [x] Task 2: sec_ids_ue.h — naming conflicts resolved (`ue_alert_type_t`, `UE_IDS_MAX_SLOTS`)
- [x] Task 3: sec_ids_ue.c — R1-R5 all implemented, `ue_ids_update()` matches csv_per_ue_write order
- [x] Task 4: xapp_sec_moni.c — 5 surgical changes, `init_onnx_ue()` after `init_onnx()`, alert CSV
- [x] Task 5: smoke test verifies rule-only AND lstm-hybrid modes before live run
- [x] No placeholders remaining
- [x] Type consistency: `ue_alert_type_t` / `rule_result_t` / `ids_mode_t` consistent across all tasks
- [x] `init_onnx_ue()` reuses `g_ort`, `env`, `session_options` from existing `init_onnx()` — no double-init
- [x] `ALERT_COOLDOWN_MS` is `long long` to avoid comparison warning with `long long now_ms`
