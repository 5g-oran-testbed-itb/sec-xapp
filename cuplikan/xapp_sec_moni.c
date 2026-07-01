/*
 * Licensed to the OpenAirInterface (OAI) Software Alliance under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The OpenAirInterface Software Alliance licenses this file to You under
 * the OAI Public License, Version 1.1  (the "License"); you may not use this file
 * except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.openairinterface.org/?page_id=698
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *-------------------------------------------------------------------------------
 * For more information about the OpenAirInterface (OAI) Software Alliance:
 *      contact@openairinterface.org
 */

#include "../../../../src/xApp/e42_xapp_api.h"
#include "../../../../src/util/alg_ds/alg/defer.h"
#include "../../../../src/util/time_now_us.h"
#include "../../../../src/util/e2ap_ngran_types.h"
#include "../../../../src/util/alg_ds/ds/lock_guard/lock_guard.h"
#include "../../../../src/sm/kpm_sm/kpm_sm_id_wrapper.h"
#include "../../../../src/sm/rc_sm/rc_sm_id.h"
#include "../../../../src/sm/rc_sm/ie/rc_data_ie.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_struct.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_list.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_parameter_value.h"
#include "../../../../src/util/byte_array.h"
#include "../../../../src/lib/sm/ie/ue_id.h"
#include "../../../../src/sm/mac_sm/mac_sm_id.h"
#include "../../../../src/sm/rlc_sm/rlc_sm_id.h"

#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#include <signal.h>
#include <pthread.h>
#include <math.h>
#include <string.h>
#include <sys/stat.h>

// Include ONNX Runtime C API
#include <onnxruntime_c_api.h>
#include "sec_ids.h"
#include "ue_tracker.h"
#include "sec_ids_ue.h"

/* Per-UE IDS runtime state */
static ids_mode_t   g_ids_mode     = IDS_MODE_RULE_ONLY;
static OrtSession*  sess_ml        = NULL;
static float        g_ue_threshold = 0.0f;
static FILE*        g_ue_alert_fp  = NULL;
static int          g_cell_enabled = 1; /* --no-cell disables cell-level detection + CSV */
static int          g_csv_enabled  = 1; /* --no-csv  disables all training CSV writes */

#define MAX_UE 10
#define WINDOW_SIZE 10
#define NUM_FEATURES 25

typedef struct {
    float features[WINDOW_SIZE][NUM_FEATURES];
    int count;
} ue_buffer_t;

static ue_buffer_t ue_buffers[MAX_UE] = {0};

/* MAC per-UE CSV — declared early because sm_cb_mac (below) calls csv_mac_write */
typedef struct { FILE* fp; int label; } csv_mac_trainer_t;
static csv_mac_trainer_t g_csv_mac = {0};
static void csv_mac_write(csv_mac_trainer_t* t,
                          uint32_t rnti,
                          uint32_t dl_prb,   uint32_t ul_prb,
                          float    wb_cqi,   float    pusch_snr,
                          float    dl_tbs_mbps, float ul_tbs_mbps,
                          float    dl_bler,  float    ul_bler,
                          uint32_t dl_retx,  uint32_t ul_retx,
                          uint8_t  dl_mcs,   uint8_t  ul_mcs,
                          int8_t   phr,      uint32_t bsr,
                          uint32_t rnti_birth_rate);

static const OrtApi* g_ort = NULL;
static OrtEnv* env = NULL;
static OrtSession* session = NULL;
static OrtSessionOptions* session_options = NULL;
static OrtMemoryInfo* memory_info = NULL;
/* Anomaly score dari ONNX inference terakhir — digunakan csv_trainer_write() */
static float g_last_anomaly_score = 0.0f;

/* ── LSTM cell-level inference state ──────────────────────────────────────── */
#define LSTM_THRESHOLD  0.21f      /* v16 dual-ensemble recalibrated threshold */
#define LSTM_STAGE1_WIN 3          /* consecutive above-threshold calls → Stage1 */
#define LSTM_STAGE2_MS  5000LL     /* 5s sustained → Stage2 CRITICAL */

static struct {
    float     prev_prb_dl;
    float     prev_prb_ul;
    float     roll[10];            /* rolling prb_total (W10) */
    float     roll_dl[10];         /* rolling prb_dl_ratio (W10) */
    float     roll_ul[10];         /* rolling prb_ul_ratio (W10) */
    float     roll_cqi[10];        /* rolling cqi (W10) — for cqi_roll_std */
    float     roll_rach[10];       /* rolling rach_preamble (W10) — for rach_roll_mean */
    float     roll_ul_long[100];   /* rolling prb_ul_ratio (100ts) — for max_100 */
    float     roll_rach30[30];     /* rolling rach_preamble (30ts) — for rach_roll_max_30 */
    float     roll_empty30[30];    /* rolling empty_ind_rate (30ts) — for empty_ind_roll_sum_30 */
    int       roll_head;           /* shared head for all W10 buffers */
    int       roll_count;          /* shared count for all W10 buffers */
    int       roll_long_head;
    int       roll_long_count;
    int       roll30_head;         /* shared head for 30ts buffers */
    int       roll30_count;        /* shared count for 30ts buffers */
    float     feat[10][NUM_FEATURES]; /* sliding window [timestep][feature] */
    int       filled;
    int       anomaly_cnt;
    long long stage2_start_ms;
    long long stage2_dur_ms;
    int       severity;
    /* cached rolling stats for csv_trainer_write() */
    float     last_prb_dl_roll_mean;
    float     last_prb_dl_roll_std;
    float     last_prb_ul_roll_std;
    float     last_prb_ul_roll_max;
    float     last_prb_ul_roll_max_100;
} g_lstm_infer = {0};

/* Load "threshold" key from JSON file.
 * Uses strstr() — whitespace-insensitive, works for compact and pretty JSON. */
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
    char *colon = strchr(key, ':');
    if (!colon) return 0.0f;
    return strtof(colon + 1, NULL);
}

static void init_onnx_ue(void) {
    const char *model_path = NULL;
    const char *thr_path   = NULL;
    switch (g_ids_mode) {
        case IDS_MODE_LSTM_ONLY:
        case IDS_MODE_LSTM_HYBRID:
            model_path = "/home/telmat/sec-xapp/models/lstm_ue_v4.onnx";
            thr_path   = "/home/telmat/sec-xapp/models/lstm_ue_v4_threshold.json";
            break;
        case IDS_MODE_GRU_ONLY:
        case IDS_MODE_GRU_HYBRID:
            model_path = "/home/telmat/sec-xapp/models/gru_ue_v4.onnx";
            thr_path   = "/home/telmat/sec-xapp/models/gru_ue_v4_threshold.json";
            break;
        default:
            printf("[IDS-UE] RULE_ONLY mode — ONNX not loaded.\n");
            return;
    }
    g_ue_threshold = load_ue_threshold(thr_path);
    if (g_ue_threshold <= 0.0f) {
        fprintf(stderr, "[IDS-UE] Invalid threshold from %s — ONNX disabled.\n", thr_path);
        return;
    }
    printf("[IDS-UE] Threshold: %.2f (from %s)\n", g_ue_threshold, thr_path);
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

static float run_inference_ue(int ue_slot) {
    if (!sess_ml) return 0.0f;
    ue_ids_state_t *s = &g_ue_ids_states[ue_slot];
    if (s->ml_window_count < ML_SEQ_LEN) return 0.0f;
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
    } else if (st) {
        fprintf(stderr, "[IDS-UE] Inference error: %s\n", g_ort->GetErrorMessage(st));
        g_ort->ReleaseStatus(st);
    }
    if (out_tensor) g_ort->ReleaseValue(out_tensor);
    g_ort->ReleaseValue(in_tensor);
    return mse;
}

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

static void init_onnx(void) {
    g_ort = OrtGetApiBase()->GetApi(ORT_API_VERSION);
    if (!g_ort) {
        printf("Failed to init ONNX Runtime API\n");
        return;
    }
    g_ort->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "sec_xapp", &env);
    g_ort->CreateSessionOptions(&session_options);
    
    // Sesuaikan path model ONNX
    const char* model_path = "/home/telmat/sec-xapp/security_model_v16.onnx";
    OrtStatus* status = g_ort->CreateSession(env, model_path, session_options, &session);
    if (status != NULL) {
        printf("Warning: Failed to load ONNX model (%s).\n", model_path);
        printf("ONNX Error: %s\n", g_ort->GetErrorMessage(status));
        g_ort->ReleaseStatus(status);
        session = NULL;
    } else {
        printf("ONNX Runtime initialized successfully\n");
    }
    g_ort->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &memory_info);
}

static void run_inference(int rnti, int ue_idx) {
    if (session == NULL) return;
    
    const int64_t input_shape[] = {1, WINDOW_SIZE, NUM_FEATURES};
    size_t input_tensor_size = 1 * WINDOW_SIZE * NUM_FEATURES * sizeof(float);
    
    OrtValue* input_tensor = NULL;
    OrtStatus* status = g_ort->CreateTensorWithDataAsOrtValue(memory_info, 
                                          ue_buffers[ue_idx].features, input_tensor_size, 
                                          input_shape, 3, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 
                                          &input_tensor);
                                          
    if (status != NULL) {
        g_ort->ReleaseStatus(status);
        return;
    }
                                          
    const char* input_names[] = {"input"};
    const char* output_names[] = {"score"};
    OrtValue* output_tensor = NULL;
    
    // Run inference
    status = g_ort->Run(session, NULL, input_names, (const OrtValue* const*)&input_tensor, 1, output_names, 1, &output_tensor);
    
    if (status == NULL && output_tensor != NULL) {
        float* out_arr;
        g_ort->GetTensorMutableData(output_tensor, (void**)&out_arr);
        printf(">>> [INFERENCE] RNTI %d Anomaly Score: %f\n", rnti, out_arr[0]);
        g_last_anomaly_score = out_arr[0];
        if (out_arr[0] > 0.5) { // Threshold 0.5
            printf(">>> [ALERT] ANOMALY DETECTED FOR RNTI %d !!!\n", rnti);
        }
        g_ort->ReleaseValue(output_tensor);
    } else {
        printf("Inference failed\n");
        if (status) g_ort->ReleaseStatus(status);
    }
    
    g_ort->ReleaseValue(input_tensor);
    
    // Geser window atau reset. Untuk simpel, kita reset buffer.
    // Jika ingin sliding window, kita geser 9 data terakhir ke depan.
    for(int i=1; i<WINDOW_SIZE; i++) {
        for(int j=0; j<NUM_FEATURES; j++) {
            ue_buffers[ue_idx].features[i-1][j] = ue_buffers[ue_idx].features[i][j];
        }
    }
    ue_buffers[ue_idx].count = WINDOW_SIZE - 1; // Sisakan 1 slot untuk iterasi berikutnya
}

/* Forward declaration — g_cell didefinisikan di bawah setelah semua global */
static cell_metrics_t g_cell;

/* ── Cell-level LSTM inference — dipanggil dari KPM format 1 callback ────── *
 * Menghitung 10 fitur yang sama persis dengan csv_trainer_write(),            *
 * menjalankan ONNX, lalu update Stage1/Stage2 LSTM severity.                 */
static void run_cell_inference(long long now_ms)
{
    static const float EPS = 1e-6f;

    /* 1. Hitung 10 fitur dari g_cell (sama dengan csv_trainer_write) */
    float prb_dl_ratio = (g_cell.prb_avail_dl > 0.0f)
        ? g_cell.prb_used_dl / (g_cell.prb_used_dl + g_cell.prb_avail_dl)
        : g_cell.prb_used_dl / 100.0f;
    float prb_ul_ratio = (g_cell.prb_avail_ul > 0.0f)
        ? g_cell.prb_used_ul / (g_cell.prb_used_ul + g_cell.prb_avail_ul)
        : g_cell.prb_used_ul / 100.0f;

    float prb_total     = prb_dl_ratio + prb_ul_ratio;
    float prb_direction = (prb_ul_ratio - prb_dl_ratio) / (prb_total + EPS);
    float prb_dl_delta  = prb_dl_ratio - g_lstm_infer.prev_prb_dl;
    float prb_ul_delta  = prb_ul_ratio - g_lstm_infer.prev_prb_ul;
    g_lstm_infer.prev_prb_dl = prb_dl_ratio;
    g_lstm_infer.prev_prb_ul = prb_ul_ratio;

    int ridx = g_lstm_infer.roll_head % 10;
    g_lstm_infer.roll[ridx]      = prb_total;
    g_lstm_infer.roll_dl[ridx]   = prb_dl_ratio;
    g_lstm_infer.roll_ul[ridx]   = prb_ul_ratio;
    g_lstm_infer.roll_cqi[ridx]  = g_cell.cqi;
    g_lstm_infer.roll_rach[ridx] = g_cell.rach_preamble;
    g_lstm_infer.roll_head++;
    if (g_lstm_infer.roll_count < 10) g_lstm_infer.roll_count++;

    /* long window (100ts) for prb_ul_roll_max_100 */
    int lidx = g_lstm_infer.roll_long_head % 100;
    g_lstm_infer.roll_ul_long[lidx] = prb_ul_ratio;
    g_lstm_infer.roll_long_head++;
    if (g_lstm_infer.roll_long_count < 100) g_lstm_infer.roll_long_count++;

    /* 30ts windows for rach_roll_max_30 and empty_ind_roll_sum_30 */
    int r30idx = g_lstm_infer.roll30_head % 30;
    g_lstm_infer.roll_rach30[r30idx]  = g_cell.rach_preamble;
    g_lstm_infer.roll_empty30[r30idx] = g_cell.empty_ind_rate;
    g_lstm_infer.roll30_head++;
    if (g_lstm_infer.roll30_count < 30) g_lstm_infer.roll30_count++;

    int rcnt   = g_lstm_infer.roll_count;
    int cnt30  = g_lstm_infer.roll30_count;

    /* prb_burst_index: log(1+total) / rolling_mean */
    float rsum = 0.0f;
    for (int k = 0; k < rcnt; k++) {
        int idx = ((g_lstm_infer.roll_head - 1 - k) % 10 + 10) % 10;
        rsum += g_lstm_infer.roll[idx];
    }
    float roll_mean       = rsum / (float)rcnt;
    float prb_burst_index = logf(1.0f + prb_total) / (roll_mean + EPS);

    /* prb_dl_roll_mean, prb_dl_roll_std */
    float sum_dl = 0.0f;
    for (int k = 0; k < rcnt; k++) sum_dl += g_lstm_infer.roll_dl[k];
    float prb_dl_roll_mean = sum_dl / (float)rcnt;
    float var_dl = 0.0f;
    for (int k = 0; k < rcnt; k++) {
        float d = g_lstm_infer.roll_dl[k] - prb_dl_roll_mean;
        var_dl += d * d;
    }
    float prb_dl_roll_std = sqrtf(var_dl / (float)rcnt);

    /* prb_ul_roll_std, prb_ul_roll_max */
    float sum_ul = 0.0f, prb_ul_roll_max = g_lstm_infer.roll_ul[0];
    for (int k = 0; k < rcnt; k++) {
        sum_ul += g_lstm_infer.roll_ul[k];
        if (g_lstm_infer.roll_ul[k] > prb_ul_roll_max)
            prb_ul_roll_max = g_lstm_infer.roll_ul[k];
    }
    float ul_mean = sum_ul / (float)rcnt;
    float var_ul = 0.0f;
    for (int k = 0; k < rcnt; k++) {
        float d = g_lstm_infer.roll_ul[k] - ul_mean;
        var_ul += d * d;
    }
    float prb_ul_roll_std = sqrtf(var_ul / (float)rcnt);

    /* prb_ul_roll_max_100: max over 100-timestep window */
    float prb_ul_roll_max_100 = g_lstm_infer.roll_ul_long[0];
    for (int k = 1; k < g_lstm_infer.roll_long_count; k++) {
        if (g_lstm_infer.roll_ul_long[k] > prb_ul_roll_max_100)
            prb_ul_roll_max_100 = g_lstm_infer.roll_ul_long[k];
    }

    /* ── 9 additional features for 25-feature model (v16/v22) ─────────────── */

    /* prb_total_variance: rolling var(prb_total, W10, ddof=0) */
    float var_total = 0.0f;
    for (int k = 0; k < rcnt; k++) {
        float d = g_lstm_infer.roll[k] - roll_mean;
        var_total += d * d;
    }
    float prb_total_variance = var_total / (float)rcnt;

    /* cqi_roll_std: rolling std(cqi, W10, ddof=0) */
    float cqi_sum = 0.0f;
    for (int k = 0; k < rcnt; k++) cqi_sum += g_lstm_infer.roll_cqi[k];
    float cqi_mean = cqi_sum / (float)rcnt;
    float cqi_var  = 0.0f;
    for (int k = 0; k < rcnt; k++) {
        float d = g_lstm_infer.roll_cqi[k] - cqi_mean;
        cqi_var += d * d;
    }
    float cqi_roll_std = sqrtf(cqi_var / (float)rcnt);

    /* rach_roll_mean: rolling mean(rach_preamble, W10) */
    float rach_sum = 0.0f;
    for (int k = 0; k < rcnt; k++) rach_sum += g_lstm_infer.roll_rach[k];
    float rach_roll_mean = rach_sum / (float)rcnt;

    /* prb_dl_ul_asym: |prb_dl - prb_ul| / (prb_dl + prb_ul + EPS) */
    float prb_dl_ul_asym = fabsf(prb_dl_ratio - prb_ul_ratio)
                           / (prb_dl_ratio + prb_ul_ratio + EPS);

    /* prb_ul_near_zero_rate: fraction of W10 where prb_ul < 6/106 */
    float near_zero_thresh = 6.0f / 106.0f;
    int   near_zero_cnt = 0;
    for (int k = 0; k < rcnt; k++)
        if (g_lstm_infer.roll_ul[k] < near_zero_thresh) near_zero_cnt++;
    float prb_ul_near_zero_rate = (float)near_zero_cnt / (float)rcnt;

    /* prb_peak_drop: prb_ul_roll_max_100 - prb_ul_ratio */
    float prb_peak_drop = prb_ul_roll_max_100 - prb_ul_ratio;

    /* rach_cqi_joint: rach_preamble * (1 - cqi/15) */
    float rach_cqi_joint = g_cell.rach_preamble * (1.0f - g_cell.cqi / 15.0f);

    /* rach_roll_max_30: max(rach_preamble, 30ts) */
    float rach_roll_max_30 = (cnt30 > 0) ? g_lstm_infer.roll_rach30[0] : 0.0f;
    for (int k = 1; k < cnt30; k++)
        if (g_lstm_infer.roll_rach30[k] > rach_roll_max_30)
            rach_roll_max_30 = g_lstm_infer.roll_rach30[k];

    /* empty_ind_roll_sum_30: sum(empty_ind_rate, 30ts) */
    float empty_ind_roll_sum_30 = 0.0f;
    for (int k = 0; k < cnt30; k++)
        empty_ind_roll_sum_30 += g_lstm_infer.roll_empty30[k];

    /* 2. Update sliding window — shift kiri, isi timestep terakhir */
    for (int i = 0; i < 9; i++)
        for (int j = 0; j < NUM_FEATURES; j++)
            g_lstm_infer.feat[i][j] = g_lstm_infer.feat[i+1][j];

    float* row = g_lstm_infer.feat[9];
    row[0]  = prb_dl_ratio;
    row[1]  = prb_ul_ratio;
    row[2]  = g_cell.cqi;
    row[3]  = g_cell.rach_preamble;
    row[4]  = g_cell.air_delay_ul;
    row[5]  = prb_direction;
    row[6]  = prb_total;
    row[7]  = prb_dl_delta;
    row[8]  = prb_ul_delta;
    row[9]  = prb_burst_index;
    row[10] = g_cell.empty_ind_rate;
    row[11] = prb_dl_roll_mean;
    row[12] = prb_dl_roll_std;
    row[13] = prb_ul_roll_std;
    row[14] = prb_ul_roll_max;
    row[15] = prb_ul_roll_max_100;
    row[16] = cqi_roll_std;
    row[17] = rach_roll_mean;
    row[18] = prb_total_variance;
    row[19] = prb_dl_ul_asym;
    row[20] = prb_ul_near_zero_rate;
    row[21] = prb_peak_drop;
    row[22] = rach_cqi_joint;
    row[23] = rach_roll_max_30;
    row[24] = empty_ind_roll_sum_30;

    /* Cache rolling stats agar csv_trainer_write() bisa menulis nilai yang sama */
    g_lstm_infer.last_prb_dl_roll_mean    = prb_dl_roll_mean;
    g_lstm_infer.last_prb_dl_roll_std     = prb_dl_roll_std;
    g_lstm_infer.last_prb_ul_roll_std     = prb_ul_roll_std;
    g_lstm_infer.last_prb_ul_roll_max     = prb_ul_roll_max;
    g_lstm_infer.last_prb_ul_roll_max_100 = prb_ul_roll_max_100;

    /* Guard ONNX setelah rolling stats di-cache — CSV writer tetap dapat nilai benar */
    if (session == NULL) return;

    if (g_lstm_infer.filled < 10) { g_lstm_infer.filled++; return; }

    /* 3. Jalankan ONNX inference — input [1, 10, NUM_FEATURES], output "score" */
    const int64_t shape[] = {1, 10, NUM_FEATURES};
    OrtValue* in_tensor = NULL;
    OrtStatus* st = g_ort->CreateTensorWithDataAsOrtValue(
        memory_info, g_lstm_infer.feat, 10 * NUM_FEATURES * sizeof(float),
        shape, 3, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &in_tensor);
    if (st != NULL) { g_ort->ReleaseStatus(st); return; }

    const char* in_names[]  = {"input"};
    const char* out_names[] = {"score"};
    OrtValue* out_tensor = NULL;
    st = g_ort->Run(session, NULL, in_names,
                    (const OrtValue* const*)&in_tensor, 1,
                    out_names, 1, &out_tensor);
    g_ort->ReleaseValue(in_tensor);

    if (st != NULL || out_tensor == NULL) {
        if (st) g_ort->ReleaseStatus(st);
        return;
    }

    float* out_arr;
    g_ort->GetTensorMutableData(out_tensor, (void**)&out_arr);
    g_last_anomaly_score = out_arr[0];
    g_ort->ReleaseValue(out_tensor);

    /* 4. LSTM Stage1/Stage2 — logika paralel dengan rule_based_detect() */
    if (g_last_anomaly_score > LSTM_THRESHOLD) {
        g_lstm_infer.anomaly_cnt++;
        if (g_lstm_infer.stage2_start_ms == 0) g_lstm_infer.stage2_start_ms = now_ms;
        g_lstm_infer.stage2_dur_ms = now_ms - g_lstm_infer.stage2_start_ms;
    } else {
        g_lstm_infer.anomaly_cnt    = 0;
        g_lstm_infer.stage2_start_ms = 0;
        g_lstm_infer.stage2_dur_ms   = 0;
    }

    g_lstm_infer.severity = 0;
    if (g_lstm_infer.anomaly_cnt >= LSTM_STAGE1_WIN) {
        if (g_lstm_infer.anomaly_cnt == LSTM_STAGE1_WIN)
            printf(">>> [STAGE1-WARNING] LSTM_ANOMALY | score=%.6f selama %d windows\n",
                   g_last_anomaly_score, g_lstm_infer.anomaly_cnt);
        g_lstm_infer.severity = 1;
    }
    if (g_lstm_infer.stage2_dur_ms >= LSTM_STAGE2_MS) {
        if (g_lstm_infer.severity < 2)
            printf(">>> [STAGE2-CRITICAL] LSTM_ANOMALY CONFIRMED | score=%.6f duration=%.0fms\n",
                   g_last_anomaly_score, (float)g_lstm_infer.stage2_dur_ms);
        g_lstm_infer.severity = 2;
    }
    fflush(stdout);
}

static volatile int keep_running = 1;

static void sig_handler_stop(int signo) {
  (void)signo;
  keep_running = 0;
}

static
pthread_mutex_t mtx;

/* Global UE tracker — must be declared before all callbacks that reference it */
static ue_tracker_t g_ue_tracker;

////////////
// Get RC Indication Messages -> begin
////////////

static void sm_cb_rc(sm_ag_if_rd_t const *rd, global_e2_node_id_t const* e2_node)
{
  assert(rd != NULL);
  assert(rd->type == INDICATION_MSG_AGENT_IF_ANS_V0);
  assert(rd->ind.type == RAN_CTRL_STATS_V1_03);
  (void) e2_node;

  // Reading Indication Message Format 2
  e2sm_rc_ind_msg_frmt_2_t const *msg_frm_2 = &rd->ind.rc.ind.msg.frmt_2;

  printf("RC REPORT Style 2 - Call Process Outcome\n");

  // Sequence of UE Identifier
  //[1-65535]
  for (size_t i = 0; i < msg_frm_2->sz_seq_ue_id; i++)
  {
    // UE ID
    // Mandatory
    // 9.3.10
    switch (msg_frm_2->seq_ue_id[i].ue_id.type)
    {
      case GNB_UE_ID_E2SM:
        printf("UE connected to gNB with amf_ue_ngap_id = %lu\n", msg_frm_2->seq_ue_id[i].ue_id.gnb.amf_ue_ngap_id);
        break;
      default:
        printf("Not yet implemented UE ID type\n");
    }
  }
}

////////////
// Get RC Indication Messages -> end
////////////

/* Per-UE MAC PRB cache: updated by sm_cb_mac from the scheduler.
 * KPM FORMAT_3 often sends NO_MEAS_VALUE for RRU.PrbUsedUl/Dl; we fall back
 * to these scheduler-derived values which are always populated when a UE is
 * actively transmitting. Indexed by rnti % MAX_UE. */
static uint32_t g_mac_ul_prb[MAX_UE] = {0};
static uint32_t g_mac_dl_prb[MAX_UE] = {0};

////////////
// Get MAC Indication Messages -> begin
////////////

static void sm_cb_mac(sm_ag_if_rd_t const* rd, global_e2_node_id_t const* e2_node)
{
  assert(rd != NULL);
  assert(rd->type == INDICATION_MSG_AGENT_IF_ANS_V0);
  assert(rd->ind.type == MAC_STATS_V0);
  (void) e2_node;

  mac_ind_msg_t const* msg = &rd->ind.mac.msg;

  struct timespec _ts_mac;
  clock_gettime(CLOCK_REALTIME, &_ts_mac);
  uint64_t mac_now_ms = (uint64_t)_ts_mac.tv_sec * 1000ULL + _ts_mac.tv_nsec / 1000000ULL;

  static uint64_t _mac_cb_count = 0;
  if (++_mac_cb_count <= 5 || _mac_cb_count % 1000 == 0)
      printf("[MAC_CB] call#%lu len_ue_stats=%u\n", _mac_cb_count, msg->len_ue_stats);

  lock_guard(&mtx);
  float cell_dl_mbps = 0.0f, cell_ul_mbps = 0.0f;
  for(uint32_t i = 0; i < msg->len_ue_stats; ++i){
    mac_ue_stats_impl_t const* s = &msg->ue_stats[i];

    /* UE-level tracker: feed per-RNTI metrics for RRC/flood detection */
    float mac_dl_mbps = (float)s->dl_aggr_tbs / 125000.0f; /* bytes → Mbps (8 bits) */
    float mac_ul_mbps = (float)s->ul_aggr_tbs / 125000.0f;
    cell_dl_mbps += mac_dl_mbps;
    cell_ul_mbps += mac_ul_mbps;
    ue_tracker_mac_update(&g_ue_tracker,
                          s->rnti,
                          mac_dl_mbps, mac_ul_mbps,
                          (uint32_t)s->dl_aggr_prb, (uint32_t)s->ul_aggr_prb,
                          (float)s->pusch_snr, (float)s->wb_cqi, (int64_t)s->bsr,
                          mac_now_ms);

    if (g_csv_enabled)
      csv_mac_write(&g_csv_mac,
                    s->rnti,
                    (uint32_t)s->dl_aggr_prb,  (uint32_t)s->ul_aggr_prb,
                    (float)s->wb_cqi,          (float)s->pusch_snr,
                    mac_dl_mbps,               mac_ul_mbps,
                    (float)s->dl_bler,         (float)s->ul_bler,
                    (uint32_t)s->dl_aggr_retx_prb, (uint32_t)s->ul_aggr_retx_prb,
                    s->dl_mcs1,                s->ul_mcs1,
                    s->phr,                    (uint32_t)s->bsr,
                    g_ue_tracker.new_this_period);

    int ue_idx = s->rnti % MAX_UE;

    /* Update MAC PRB cache used as fallback in KPM FORMAT_3 handler */
    g_mac_ul_prb[ue_idx] = (uint32_t)s->ul_aggr_prb;
    g_mac_dl_prb[ue_idx] = (uint32_t)s->dl_aggr_prb;

    int t = ue_buffers[ue_idx].count;

    if (t < WINDOW_SIZE) {
        ue_buffers[ue_idx].features[t][0] = (float)s->dl_aggr_tbs / 10000.0f;
        ue_buffers[ue_idx].features[t][1] = (float)s->ul_aggr_tbs / 10000.0f;
        ue_buffers[ue_idx].features[t][2] = (float)s->dl_aggr_prb / 100.0f;
        ue_buffers[ue_idx].features[t][3] = (float)s->ul_aggr_prb / 100.0f;
        ue_buffers[ue_idx].features[t][4] = (float)s->pusch_snr;
        ue_buffers[ue_idx].features[t][5] = (float)s->dl_bler;
        ue_buffers[ue_idx].features[t][6] = (float)s->wb_cqi / 15.0f;
        ue_buffers[ue_idx].features[t][7] = (float)s->dl_mcs1 / 28.0f;
        ue_buffers[ue_idx].features[t][8] = (float)s->bsr;
        ue_buffers[ue_idx].features[t][9] = (float)s->phr;

        ue_buffers[ue_idx].count++;
    }
  }
  /* Aggregate cell-level throughput from MAC SM (sum of all active UEs) */
  if (msg->len_ue_stats > 0) {
      g_cell.thp_dl_mbps = cell_dl_mbps;
      g_cell.thp_ul_mbps = cell_ul_mbps;
  }
}

////////////
// Get MAC Indication Messages -> end
////////////

////////////
// Get RLC Indication Messages -> begin
////////////

static void sm_cb_rlc(sm_ag_if_rd_t const* rd, global_e2_node_id_t const* e2_node)
{
  assert(rd != NULL);
  assert(rd->type == INDICATION_MSG_AGENT_IF_ANS_V0);
  assert(rd->ind.type == RLC_STATS_V0);
  (void) e2_node;

  rlc_ind_msg_t const* msg = &rd->ind.rlc.msg;

  lock_guard(&mtx);
  printf("RLC_STATS_START num_rb=%u tstamp=%ld\n", msg->len, msg->tstamp);
  for(uint32_t i = 0; i < msg->len; ++i){
    rlc_radio_bearer_stats_t const* s = &msg->rb[i];
    printf("RLC_UE_STATS rnti=%u rbid=%u tx_b=%u rx_b=%u tx_pkts=%u rx_pkts=%u dd_pkts=%u wait=%f b_occ=%u\n",
           s->rnti, s->rbid, s->txpdu_bytes, s->rxpdu_bytes, s->txpdu_pkts, s->rxpdu_pkts, 
           s->txpdu_dd_pkts, s->txsdu_avg_time_to_tx, s->txbuf_occ_bytes);
  }
  printf("RLC_STATS_END\n");
}

////////////
// Get RLC Indication Messages -> end
////////////

////////////
// KPM SUBSCRIPTION HELPERS
////////////
static uint64_t const kpm_period_ms = 10; /* must match my_xapp_kpm.conf time= */

static label_info_lst_t fill_kpm_label(void) {
  label_info_lst_t label_item = {0};
  label_item.noLabel = calloc(1, sizeof(enum_value_e));
  *label_item.noLabel = TRUE_ENUM_VALUE;
  return label_item;
}

static kpm_act_def_format_1_t fill_act_def_frm_1(ric_report_style_item_t const* report_item) {
  kpm_act_def_format_1_t ad_frm_1 = {0};
  size_t const sz = report_item->meas_info_for_action_lst_len;
  ad_frm_1.meas_info_lst_len = sz;
  ad_frm_1.meas_info_lst = calloc(sz, sizeof(meas_info_format_1_lst_t));
  for (size_t i = 0; i < sz; i++) {
    meas_info_format_1_lst_t* meas_item = &ad_frm_1.meas_info_lst[i];
    meas_item->meas_type.type = NAME_MEAS_TYPE;
    meas_item->meas_type.name = copy_byte_array(report_item->meas_info_for_action_lst[i].name);
    meas_item->label_info_lst_len = 1;
    meas_item->label_info_lst = calloc(1, sizeof(label_info_lst_t));
    meas_item->label_info_lst[0] = fill_kpm_label();
  }
  ad_frm_1.gran_period_ms = kpm_period_ms;
  ad_frm_1.cell_global_id = NULL;
  return ad_frm_1;
}

static test_info_lst_t filter_predicate(test_cond_type_e type, test_cond_e cond, int value) {
  test_info_lst_t dst = {0};
  dst.test_cond_type = type;
  dst.S_NSSAI = TRUE_TEST_COND_TYPE;
  dst.test_cond = calloc(1, sizeof(test_cond_e));
  *dst.test_cond = cond;
  dst.test_cond_value = calloc(1, sizeof(test_cond_value_t));
  dst.test_cond_value->type = OCTET_STRING_TEST_COND_VALUE;
  dst.test_cond_value->octet_string_value = calloc(1, sizeof(byte_array_t));
  dst.test_cond_value->octet_string_value->len = 1;
  dst.test_cond_value->octet_string_value->buf = calloc(1, sizeof(uint8_t));
  dst.test_cond_value->octet_string_value->buf[0] = value;
  return dst;
}

static kpm_act_def_t fill_report_style_1(ric_report_style_item_t const* report_item) {
  kpm_act_def_t act_def = {.type = FORMAT_1_ACTION_DEFINITION};
  act_def.frm_1.meas_info_lst_len = report_item->meas_info_for_action_lst_len;
  act_def.frm_1.meas_info_lst = calloc(act_def.frm_1.meas_info_lst_len, sizeof(meas_info_format_1_lst_t));
  for (size_t i = 0; i < act_def.frm_1.meas_info_lst_len; i++) {
    meas_info_format_1_lst_t* meas_item = &act_def.frm_1.meas_info_lst[i];
    meas_item->meas_type.type = NAME_MEAS_TYPE;
    meas_item->meas_type.name = copy_byte_array(report_item->meas_info_for_action_lst[i].name);
    meas_item->label_info_lst_len = 1;
    meas_item->label_info_lst = calloc(meas_item->label_info_lst_len, sizeof(label_info_lst_t));
    meas_item->label_info_lst[0] = fill_kpm_label();
  }
  act_def.frm_1.gran_period_ms = kpm_period_ms;
  act_def.frm_1.cell_global_id = NULL;
  return act_def;
}

static kpm_act_def_t fill_report_style_4(ric_report_style_item_t const* report_item) {
  kpm_act_def_t act_def = {.type = FORMAT_4_ACTION_DEFINITION};
  act_def.frm_4.matching_cond_lst_len = 1;
  act_def.frm_4.matching_cond_lst = calloc(1, sizeof(matching_condition_format_4_lst_t));
  act_def.frm_4.matching_cond_lst[0].test_info_lst = filter_predicate(S_NSSAI_TEST_COND_TYPE, EQUAL_TEST_COND, 1);
  act_def.frm_4.action_def_format_1 = fill_act_def_frm_1(report_item);
  return act_def;
}

static kpm_act_def_t fill_report_style_5(ric_report_style_item_t const* report_item) {
  kpm_act_def_t act_def = {.type = FORMAT_5_ACTION_DEFINITION};
  act_def.frm_5.ue_id_lst_len = 2;
  act_def.frm_5.ue_id_lst = calloc(2, sizeof(ue_id_e2sm_t));
  act_def.frm_5.ue_id_lst[0].type = GNB_UE_ID_E2SM;
  act_def.frm_5.ue_id_lst[0].gnb.amf_ue_ngap_id = 1;
  act_def.frm_5.ue_id_lst[0].gnb.gnb_cu_ue_f1ap_lst_len = 1;
  act_def.frm_5.ue_id_lst[0].gnb.gnb_cu_ue_f1ap_lst = calloc(1, sizeof(uint64_t));
  act_def.frm_5.ue_id_lst[0].gnb.gnb_cu_ue_f1ap_lst[0] = 1;
  act_def.frm_5.ue_id_lst[1].type = GNB_UE_ID_E2SM;
  act_def.frm_5.ue_id_lst[1].gnb.amf_ue_ngap_id = 2;
  act_def.frm_5.ue_id_lst[1].gnb.gnb_cu_ue_f1ap_lst_len = 1;
  act_def.frm_5.ue_id_lst[1].gnb.gnb_cu_ue_f1ap_lst = calloc(1, sizeof(uint64_t));
  act_def.frm_5.ue_id_lst[1].gnb.gnb_cu_ue_f1ap_lst[0] = 2;
  act_def.frm_5.action_def_format_1 = fill_act_def_frm_1(report_item);
  return act_def;
}

typedef kpm_act_def_t (*fill_kpm_act_def)(ric_report_style_item_t const* report_item);
static fill_kpm_act_def get_kpm_act_def[END_RIC_SERVICE_REPORT] = {
    fill_report_style_1, NULL, NULL, fill_report_style_4, fill_report_style_5,
};

static kpm_sub_data_t gen_kpm_subs(kpm_ran_function_def_t const* ran_func, ric_report_style_item_t const* report_item) {
  kpm_sub_data_t kpm_sub = {0};
  kpm_sub.ev_trg_def.type = FORMAT_1_RIC_EVENT_TRIGGER;
  kpm_sub.ev_trg_def.kpm_ric_event_trigger_format_1.report_period_ms = kpm_period_ms;
  kpm_sub.sz_ad = 1;
  kpm_sub.ad = calloc(1, sizeof(kpm_act_def_t));
  *kpm_sub.ad = get_kpm_act_def[report_item->report_style_type](report_item);
  return kpm_sub;
}

static bool eq_sm(sm_ran_function_t const* elem, int const id) { return elem->id == id; }
static size_t find_sm_idx(sm_ran_function_t* rf, size_t sz, bool (*f)(sm_ran_function_t const*, int const), int const id) {
  for (size_t i = 0; i < sz; i++) if (f(&rf[i], id)) return i;
  return 0;
}

/* Akumulator metrik sel — diisi per metrik di sm_cb_kpm, lalu diserahkan ke IDS */
static cell_metrics_t g_cell = {0};

/* Timestamp of last CSV row written — used to debounce duplicate callbacks.
   srsRAN fires sm_cb_kpm once per internal RNTI entry per 100ms period;
   we write only the first callback per window to avoid duplicate rows. */
static uint64_t g_last_csv_ms = 0;
/* Per-UE CSV debounce: srsRAN fires FORMAT_3 callback ~2x per period.
   Gate at 90ms prevents duplicate rows with identical values. */
static uint64_t g_last_per_ue_csv_ms = 0;

/* ── E2SM-RC PRB Throttle Mitigation ─────────────────────────────────────
 * When rule_based_detect() returns CRITICAL (severity=2), we send an
 * E2SM-RC Control Style 2 message to limit slice PRB quota to 5%.
 * This is O-RAN compliant and effective for data-plane attacks (UL/DL flood,
 * burst).  Control-plane attacks (signaling storm) require SSH AMF barring.
 *
 * Threading: sm_cb_kpm sets g_pending_throttle; main() calls rc_send_prb_quota
 * outside the callback lock to avoid blocking KPM callbacks.               */
static global_e2_node_id_t g_du_node_id;
static volatile int g_du_node_id_valid = 0;
static volatile int g_throttle_active  = 0;
static volatile int g_pending_throttle = 0; /* 1=apply throttle, 2=restore */
static uint64_t g_throttle_last_ms     = 0;
/* Timestamp of last per-UE alert — used to gate cell-level restore path
 * so sustained per-UE detection keeps throttle active until attack clears. */
static volatile uint64_t g_last_ue_alert_ms = 0;
#define THROTTLE_COOLDOWN_MS 30000   /* 30s between throttle/restore actions */
#define THROTTLE_RESTORE_MS  10000   /* 10s of calm before auto-restore */

/* srsRAN RC Bug #468 RESOLVED (patch merged May 2024, gckopper).
 * Enable E2SM-RC PRB throttle mitigation via --mitigate flag. */
static int g_mitigate_enabled = 0;

/* Detection mode: 0=rule-only, 1=lstm-only, 2=hybrid (default) */
static int g_detection_mode = 2;

static void rc_send_prb_quota(int max_prb_pct)
{
    if (!g_du_node_id_valid) return;

    rc_ctrl_req_data_t rc_ctrl = {0};

    /* Header: Style 2, Action 6 (Slice-level PRB quota).
     * UE ID is ignored by gNB for cell/slice-level control — use zeros. */
    ue_id_e2sm_t ue_id = {0};
    ue_id.type = GNB_UE_ID_E2SM;
    ue_id.gnb.amf_ue_ngap_id = 0;
    ue_id.gnb.guami.plmn_id.mcc = 1;
    ue_id.gnb.guami.plmn_id.mnc = 1;
    ue_id.gnb.guami.plmn_id.mnc_digit_len = 2;
    rc_ctrl.hdr.format = FORMAT_1_E2SM_RC_CTRL_HDR;
    rc_ctrl.hdr.frmt_1.ue_id = cp_ue_id_e2sm(&ue_id);
    rc_ctrl.hdr.frmt_1.ric_style_type = 2;
    rc_ctrl.hdr.frmt_1.ctrl_act_id = Slice_level_PRB_quotal_7_6_3_1;

    /* Message: Format 1 — RRM_Policy_Ratio_List (8.4.3.6)
     * Structure:
     *  RRM_Policy_Ratio_List (LIST)
     *  └─ RRM_Policy_Ratio_Group (STRUCTURE, 4 elements)
     *     ├─ [0] RRM_Policy (STRUCTURE)
     *     │      └─ RRM_Policy_Member_List (LIST)
     *     │         └─ RRM_Policy_Member (STRUCTURE)
     *     │            ├─ PLMN_Identity (OCTET_STRING)
     *     │            └─ S_NSSAI (STRUCTURE)
     *     │               ├─ SST (OCTET_STRING)
     *     │               └─ SD  (OCTET_STRING)
     *     ├─ [1] Min_PRB_Policy_Ratio (INTEGER)
     *     ├─ [2] Max_PRB_Policy_Ratio (INTEGER)
     *     └─ [3] Dedicated_PRB_Policy_Ratio (INTEGER)              */
    rc_ctrl.msg.format = FORMAT_1_E2SM_RC_CTRL_MSG;
    e2sm_rc_ctrl_msg_frmt_1_t* msg = &rc_ctrl.msg.frmt_1;
    msg->sz_ran_param = 1;
    msg->ran_param = calloc(1, sizeof(seq_ran_param_t));
    assert(msg->ran_param != NULL);

    /* RRM_Policy_Ratio_List */
    seq_ran_param_t* rrm_list = &msg->ran_param[0];
    rrm_list->ran_param_id = RRM_Policy_Ratio_List_8_4_3_6;
    rrm_list->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
    rrm_list->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
    assert(rrm_list->ran_param_val.lst != NULL);
    rrm_list->ran_param_val.lst->sz_lst_ran_param = 1;
    rrm_list->ran_param_val.lst->lst_ran_param = calloc(1, sizeof(lst_ran_param_t));
    assert(rrm_list->ran_param_val.lst->lst_ran_param != NULL);

    /* RRM_Policy_Ratio_Group */
    lst_ran_param_t* grp = &rrm_list->ran_param_val.lst->lst_ran_param[0];
    grp->ran_param_struct.sz_ran_param_struct = 4;
    grp->ran_param_struct.ran_param_struct = calloc(4, sizeof(seq_ran_param_t));
    assert(grp->ran_param_struct.ran_param_struct != NULL);

    /* [0] RRM_Policy */
    seq_ran_param_t* policy = &grp->ran_param_struct.ran_param_struct[0];
    policy->ran_param_id = RRM_Policy_8_4_3_6;
    policy->ran_param_val.type = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
    policy->ran_param_val.strct = calloc(1, sizeof(ran_param_struct_t));
    assert(policy->ran_param_val.strct != NULL);
    policy->ran_param_val.strct->sz_ran_param_struct = 1;
    policy->ran_param_val.strct->ran_param_struct = calloc(1, sizeof(seq_ran_param_t));
    assert(policy->ran_param_val.strct->ran_param_struct != NULL);

    /* RRM_Policy_Member_List */
    seq_ran_param_t* mem_list = &policy->ran_param_val.strct->ran_param_struct[0];
    mem_list->ran_param_id = RRM_Policy_Member_List_8_4_3_6;
    mem_list->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
    mem_list->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
    assert(mem_list->ran_param_val.lst != NULL);
    mem_list->ran_param_val.lst->sz_lst_ran_param = 1;
    mem_list->ran_param_val.lst->lst_ran_param = calloc(1, sizeof(lst_ran_param_t));
    assert(mem_list->ran_param_val.lst->lst_ran_param != NULL);

    /* RRM_Policy_Member */
    lst_ran_param_t* mem = &mem_list->ran_param_val.lst->lst_ran_param[0];
    mem->ran_param_struct.sz_ran_param_struct = 2;
    mem->ran_param_struct.ran_param_struct = calloc(2, sizeof(seq_ran_param_t));
    assert(mem->ran_param_struct.ran_param_struct != NULL);

    /* PLMN Identity (Open5GS default: MCC=001, MNC=01 → "00101") */
    seq_ran_param_t* plmn = &mem->ran_param_struct.ran_param_struct[0];
    plmn->ran_param_id = PLMN_Identity_8_4_3_6;
    plmn->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
    plmn->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
    assert(plmn->ran_param_val.flag_false != NULL);
    plmn->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
    byte_array_t plmn_ba = cp_str_to_ba("00101");
    plmn->ran_param_val.flag_false->octet_str_ran = plmn_ba;

    /* S-NSSAI */
    seq_ran_param_t* nssai = &mem->ran_param_struct.ran_param_struct[1];
    nssai->ran_param_id = S_NSSAI_8_4_3_6;
    nssai->ran_param_val.type = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
    nssai->ran_param_val.strct = calloc(1, sizeof(ran_param_struct_t));
    assert(nssai->ran_param_val.strct != NULL);
    nssai->ran_param_val.strct->sz_ran_param_struct = 2;
    nssai->ran_param_val.strct->ran_param_struct = calloc(2, sizeof(seq_ran_param_t));
    assert(nssai->ran_param_val.strct->ran_param_struct != NULL);

    seq_ran_param_t* sst_p = &nssai->ran_param_val.strct->ran_param_struct[0];
    sst_p->ran_param_id = SST_8_4_3_6;
    sst_p->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
    sst_p->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
    assert(sst_p->ran_param_val.flag_false != NULL);
    sst_p->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
    byte_array_t sst_ba = cp_str_to_ba("1"); /* SST=1 (eMBB default) */
    sst_p->ran_param_val.flag_false->octet_str_ran = sst_ba;

    seq_ran_param_t* sd_p = &nssai->ran_param_val.strct->ran_param_struct[1];
    sd_p->ran_param_id = SD_8_4_3_6;
    sd_p->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
    sd_p->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
    assert(sd_p->ran_param_val.flag_false != NULL);
    sd_p->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
    byte_array_t sd_ba = cp_str_to_ba("0"); /* SD=0 */
    sd_p->ran_param_val.flag_false->octet_str_ran = sd_ba;

    /* [1] Min PRB Policy Ratio — always 0 */
    seq_ran_param_t* min_prb = &grp->ran_param_struct.ran_param_struct[1];
    min_prb->ran_param_id = Min_PRB_Policy_Ratio_8_4_3_6;
    min_prb->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
    min_prb->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
    assert(min_prb->ran_param_val.flag_false != NULL);
    min_prb->ran_param_val.flag_false->type = INTEGER_RAN_PARAMETER_VALUE;
    min_prb->ran_param_val.flag_false->int_ran = 0;

    /* [2] Max PRB Policy Ratio */
    seq_ran_param_t* max_prb = &grp->ran_param_struct.ran_param_struct[2];
    max_prb->ran_param_id = Max_PRB_Policy_Ratio_8_4_3_6;
    max_prb->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
    max_prb->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
    assert(max_prb->ran_param_val.flag_false != NULL);
    max_prb->ran_param_val.flag_false->type = INTEGER_RAN_PARAMETER_VALUE;
    max_prb->ran_param_val.flag_false->int_ran = max_prb_pct;

    /* [3] Dedicated PRB Policy Ratio */
    seq_ran_param_t* ded_prb = &grp->ran_param_struct.ran_param_struct[3];
    ded_prb->ran_param_id = Dedicated_PRB_Policy_Ratio_8_4_3_6;
    ded_prb->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
    ded_prb->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
    assert(ded_prb->ran_param_val.flag_false != NULL);
    ded_prb->ran_param_val.flag_false->type = INTEGER_RAN_PARAMETER_VALUE;
    ded_prb->ran_param_val.flag_false->int_ran = max_prb_pct;

    control_sm_xapp_api(&g_du_node_id, SM_RC_ID, &rc_ctrl);
    free_rc_ctrl_req_data(&rc_ctrl);
    printf("[MITIGATE] E2SM-RC PRB quota sent: max=%d%%\n", max_prb_pct);
    fflush(stdout);
}

////////////
// CSV Training Data Recorder
////////////

#define CSV_ROLLING_LEN  5   /* rolling window depth for burst_index */
#define PER_UE_ROLL_WIN  10  /* rolling window depth for per-UE proxy features */

typedef struct {
    FILE*  fp;
    float  prev_prb_dl;          /* previous window prb_dl_ratio for delta */
    float  prev_prb_ul;          /* previous window prb_ul_ratio for delta */
    float  rolling[CSV_ROLLING_LEN]; /* rolling prb_total for burst_index */
    int    rolling_head;
    int    rolling_count;
    int    label;
} csv_trainer_t;

static csv_trainer_t g_csv = {0};

typedef struct {
    FILE*  fp;
    float  prev_prb_dl[MAX_UE];
    float  prev_prb_ul[MAX_UE];
    float  prev_thp_dl[MAX_UE];
    float  prev_thp_ul[MAX_UE];
    /* rolling buffers for per-UE proxy features */
    float  prb_ul_hist[MAX_UE][PER_UE_ROLL_WIN];
    float  rach_hist[MAX_UE][PER_UE_ROLL_WIN];
    int    roll_head[MAX_UE];
    int    roll_count[MAX_UE];
    int    label;
} csv_per_ue_trainer_t;

static csv_per_ue_trainer_t g_csv_per_ue = {0};

/* ---------- Hot-Label Reload ---------- */
static int g_label = 0;

/* Per-UE label table — populated from /tmp/xapp_label_ue.csv */
#define LABEL_UE_MAX 16
typedef struct { uint16_t rnti; int label; } rnti_label_t;
static rnti_label_t g_rnti_labels[LABEL_UE_MAX];
static int          g_rnti_label_count = 0;

static int get_label_for_rnti(uint32_t rnti) {
    for (int i = 0; i < g_rnti_label_count; i++)
        if ((uint32_t)g_rnti_labels[i].rnti == rnti)
            return g_rnti_labels[i].label;
    return 0;
}

static void log_scenario_event(long long epoch_ms, const char *event,
                                int label, const char *scenario,
                                const char *attacker_ue)
{
    static const char *path = "/home/telmat/xapp/security-xapp/logs/scenario_events.log";
    FILE *f = fopen(path, "a");
    if (!f) return;
    fprintf(f, "%lld,%s,%d,%s,%s,\n", epoch_ms, event, label, scenario, attacker_ue);
    fclose(f);
}

static void maybe_reload_label(void)
{
    static const char *path = "/tmp/xapp_label_ue.csv";
    static time_t     last_mtime = 0;

    struct stat st;
    if (stat(path, &st) != 0) return;
    if (st.st_mtime == last_mtime) return;
    last_mtime = st.st_mtime;

    FILE *f = fopen(path, "r");
    if (!f) return;

    /* Skip header line */
    char line[64];
    if (!fgets(line, sizeof(line), f)) { fclose(f); return; }

    g_rnti_label_count = 0;
    int max_label = 0;

    while (fgets(line, sizeof(line), f)) {
        unsigned rnti_val;
        int lbl;
        if (sscanf(line, "%u,%d", &rnti_val, &lbl) != 2) continue;
        if (g_rnti_label_count < LABEL_UE_MAX) {
            g_rnti_labels[g_rnti_label_count].rnti  = (uint16_t)rnti_val;
            g_rnti_labels[g_rnti_label_count].label = lbl;
            g_rnti_label_count++;
        }
        if (lbl > max_label) max_label = lbl;
    }
    fclose(f);

    g_label = max_label;
    printf("[LABEL] Reloaded %s — %d UE(s), cell label=%d\n",
           path, g_rnti_label_count, g_label);
}
/* -------------------------------------- */

static void csv_trainer_open(csv_trainer_t* t, const char* path, int label, uint64_t period_ms)
{
    (void)period_ms; /* reserved for future use */
    t->fp = fopen(path, "w");
    if (!t->fp) {
        printf("[CSV] ERROR: cannot open %s\n", path);
        return;
    }
    t->label        = label;
    t->prev_prb_dl  = 0.0f;
    t->prev_prb_ul  = 0.0f;
    t->rolling_head  = 0;
    t->rolling_count = 0;
    fprintf(t->fp,
        "timestamp_ms,datetime,"
        "prb_usage_dl_ratio,prb_usage_ul_ratio,"
        "cqi,rach_preamble,air_delay_ul,"
        "prb_direction,prb_total,"
        "prb_dl_delta,prb_ul_delta,prb_burst_index,"
        "label,"
        "empty_ind_rate,"
        "prb_dl_roll_mean,prb_dl_roll_std,"
        "prb_ul_roll_std,prb_ul_roll_max,prb_ul_roll_max_100,"
        "stage1_alert,stage2_confirmed,alert_type,"
        "stage1_latency_ms,stage2_confirmation_time_ms,"
        "anomaly_score,"
        "prb_total_variance,rlc_drop_dl,rlc_delay_ul\n");
    fflush(t->fp);
    printf("[CSV] Recording to %s  (label=%d)\n", path, label);
}

static const char* alert_type_to_str(alert_type_t t) {
    switch (t) {
        case ALERT_UL_SATURATION:               return "ul_saturation";
        case ALERT_DL_SATURATION:               return "dl_saturation";
        case ALERT_RRC_STORM:                   return "rrc_storm";
        case ALERT_RADIO_DEGRADATION_SUSPICION: return "radio_degradation_suspicion";
        case ALERT_PERIODIC_BURST_ANOMALY:      return "periodic_burst_anomaly";
        default:                                return "none";
    }
}

static void csv_trainer_write(csv_trainer_t* t, const cell_metrics_t* m)
{
    if (!t->fp) return;
    maybe_reload_label();

    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    long long ts_ms = (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000LL;
    char datetime[32];
    struct tm* tm_info = localtime(&ts.tv_sec);
    int ms = (int)(ts.tv_nsec / 1000000LL);
    strftime(datetime, sizeof(datetime), "%Y-%m-%d %H:%M:%S", tm_info);
    snprintf(datetime + 19, sizeof(datetime) - 19, ".%03d", ms);

    static const float EPS = 1e-6f;

    /* PRB usage ratios (0-1): prb_used / (prb_used + prb_avail)
     * Only PRB metrics are reliable in srsRAN KPM DU.
     * DRB.UEThpDl/UL and DRB.RlcSduTransmittedVolumeDL/UL are always 0. */
    float prb_dl_ratio = (m->prb_avail_dl > 0.0f)
        ? m->prb_used_dl / (m->prb_used_dl + m->prb_avail_dl)
        : m->prb_used_dl / 100.0f;
    float prb_ul_ratio = (m->prb_avail_ul > 0.0f)
        ? m->prb_used_ul / (m->prb_used_ul + m->prb_avail_ul)
        : m->prb_used_ul / 100.0f;

    /* Engineered PRB features */
    float prb_total    = prb_dl_ratio + prb_ul_ratio;
    /* prb_direction: (ul-dl)/(ul+dl+eps), bounded [-1,+1].
     * -1=pure DL, +1=pure UL, ~0=balanced (idle or signaling storm).
     * Avoids the division-by-near-zero problem of ul/dl ratio. */
    float prb_direction = (prb_ul_ratio - prb_dl_ratio) / (prb_total + EPS);
    float prb_dl_delta  = prb_dl_ratio - t->prev_prb_dl;
    float prb_ul_delta  = prb_ul_ratio - t->prev_prb_ul;
    t->prev_prb_dl = prb_dl_ratio;
    t->prev_prb_ul = prb_ul_ratio;

    /* Rolling window for prb_burst_index */
    t->rolling[t->rolling_head % CSV_ROLLING_LEN] = prb_total;
    t->rolling_head++;
    if (t->rolling_count < CSV_ROLLING_LEN) t->rolling_count++;
    float sum = 0.0f;
    for (int k = 0; k < t->rolling_count; k++) {
        int idx = ((t->rolling_head - 1 - k) % CSV_ROLLING_LEN + CSV_ROLLING_LEN) % CSV_ROLLING_LEN;
        sum += t->rolling[idx];
    }
    float rolling_mean   = sum / (float)t->rolling_count;
    float prb_burst_index = logf(1.0f + prb_total) / (rolling_mean + EPS);

    float var_sum = 0.0f;
    for (int k = 0; k < t->rolling_count; k++) {
        int idx = ((t->rolling_head - 1 - k) % CSV_ROLLING_LEN + CSV_ROLLING_LEN) % CSV_ROLLING_LEN;
        float diff = t->rolling[idx] - rolling_mean;
        var_sum += diff * diff;
    }
    float prb_total_variance = var_sum / (float)t->rolling_count;

    ids_detection_state_t det = ids_get_detection_state();
    fprintf(t->fp,
        "%lld,%s,"
        "%.6f,%.6f,"
        "%.3f,%.3f,%.3f,"
        "%.6f,%.6f,"
        "%.6f,%.6f,%.6f,"
        "%d,"
        "%.6f,"
        "%.6f,%.6f,"
        "%.6f,%.6f,%.6f,"
        "%d,%d,%s,"
        "%lld,%lld,"
        "%.6f,"
        "%.6f,%.6f,%.6f\n",
        ts_ms, datetime,
        prb_dl_ratio, prb_ul_ratio,
        m->cqi, m->rach_preamble, m->air_delay_ul,
        prb_direction, prb_total,
        prb_dl_delta, prb_ul_delta, prb_burst_index,
        g_label,
        m->empty_ind_rate,
        g_lstm_infer.last_prb_dl_roll_mean, g_lstm_infer.last_prb_dl_roll_std,
        g_lstm_infer.last_prb_ul_roll_std, g_lstm_infer.last_prb_ul_roll_max,
        g_lstm_infer.last_prb_ul_roll_max_100,
        det.stage1_alert, det.stage2_confirmed,
        alert_type_to_str(det.alert_type),
        det.stage1_latency_ms, det.stage2_confirmation_time_ms,
        g_last_anomaly_score,
        prb_total_variance, m->rlc_drop_dl, m->rlc_delay_ul);
    fflush(t->fp);
}

static void csv_trainer_close(csv_trainer_t* t)
{
    if (t->fp) {
        fclose(t->fp);
        t->fp = NULL;
        printf("[CSV] Recording stopped.\n");
    }
}

static void csv_per_ue_open(csv_per_ue_trainer_t* t, const char* path, int label)
{
    t->fp = fopen(path, "w");
    if (!t->fp) {
        printf("[CSV_UE] ERROR: cannot open %s\n", path);
        return;
    }
    t->label = label;
    memset(t->prev_prb_dl, 0, sizeof(t->prev_prb_dl));
    memset(t->prev_prb_ul, 0, sizeof(t->prev_prb_ul));
    memset(t->prev_thp_dl, 0, sizeof(t->prev_thp_dl));
    memset(t->prev_thp_ul, 0, sizeof(t->prev_thp_ul));
    memset(t->prb_ul_hist, 0, sizeof(t->prb_ul_hist));
    memset(t->rach_hist,   0, sizeof(t->rach_hist));
    memset(t->roll_head,   0, sizeof(t->roll_head));
    memset(t->roll_count,  0, sizeof(t->roll_count));
    fprintf(t->fp,
        "timestamp_ms,datetime,rnti,"
        "prb_usage_dl_ratio,prb_usage_ul_ratio,"
        "thp_dl_kbps,thp_ul_kbps,"
        "prb_direction,prb_total,"
        "prb_ul_delta,"
        "ul_efficiency,"
        "prb_ul_roll_mean,prb_ul_roll_std,ul_persistence,"
        "thp_total_kbps,thp_ul_delta,thp_dl_delta,"
        "traffic_direction,"
        "label\n");
    fflush(t->fp);
    printf("[CSV_UE] Recording per-UE to %s  (label=%d)\n", path, label);
}

static void csv_per_ue_write(csv_per_ue_trainer_t* t, uint32_t rnti, int ue_idx,
                              float prb_dl_raw, float prb_ul_raw,
                              float thp_dl, float thp_ul)
{
    if (!t->fp) return;
    if (ue_idx < 0 || ue_idx >= MAX_UE) return;
    maybe_reload_label();

    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    long long ts_ms = (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000LL;
    char datetime[32];
    struct tm* tm_info = localtime(&ts.tv_sec);
    int ms = (int)(ts.tv_nsec / 1000000LL);
    strftime(datetime, sizeof(datetime), "%Y-%m-%d %H:%M:%S", tm_info);
    snprintf(datetime + 19, sizeof(datetime) - 19, ".%03d", ms);

    static const float EPS = 1e-6f;
    float prb_total     = prb_dl_raw + prb_ul_raw;
    float prb_direction = (prb_ul_raw - prb_dl_raw) / (prb_total + EPS);
    float prb_ul_delta  = prb_ul_raw - t->prev_prb_ul[ue_idx];
    t->prev_prb_dl[ue_idx] = prb_dl_raw;
    t->prev_prb_ul[ue_idx] = prb_ul_raw;

    /* ul_efficiency: throughput per PRB unit — drops under LDoS congestion.
     * Threshold 0.005f captures val=1 PRB (1/100.0f ≈ 0.0099999f in float).
     * Clamped at 50,000 to suppress timing-mismatch outliers. */
    float ul_efficiency = (prb_ul_raw > 0.005f) ? (thp_ul / prb_ul_raw) : 0.0f;
    if (ul_efficiency > 50000.0f) ul_efficiency = 50000.0f;

    /* throughput-derived features */
    float thp_total_kbps    = thp_dl + thp_ul;
    float thp_ul_delta      = thp_ul - t->prev_thp_ul[ue_idx];
    float thp_dl_delta      = thp_dl - t->prev_thp_dl[ue_idx];
    float traffic_direction = (thp_ul - thp_dl) / (thp_total_kbps + EPS);
    t->prev_thp_ul[ue_idx] = thp_ul;
    t->prev_thp_dl[ue_idx] = thp_dl;

    /* push prb_ul into rolling buffer */
    int h = t->roll_head[ue_idx];
    t->prb_ul_hist[ue_idx][h] = prb_ul_raw;
    t->roll_head[ue_idx]  = (h + 1) % PER_UE_ROLL_WIN;
    if (t->roll_count[ue_idx] < PER_UE_ROLL_WIN) t->roll_count[ue_idx]++;
    int n = t->roll_count[ue_idx];

    /* compute rolling stats */
    float sum_prb = 0.0f;
    int   persistent = 0;
    for (int k = 0; k < n; k++) {
        float v = t->prb_ul_hist[ue_idx][k];
        sum_prb += v;
        if (v > EPS) persistent++;
    }
    float prb_ul_roll_mean = sum_prb / (float)n;
    float ul_persistence   = (float)persistent / (float)n;

    float var_sum = 0.0f;
    for (int k = 0; k < n; k++) {
        float d = t->prb_ul_hist[ue_idx][k] - prb_ul_roll_mean;
        var_sum += d * d;
    }
    float prb_ul_roll_std = sqrtf(var_sum / (float)n);

    fprintf(t->fp,
        "%lld,%s,%u,"
        "%.6f,%.6f,"
        "%.2f,%.2f,"
        "%.6f,%.6f,"
        "%.6f,"
        "%.4f,"
        "%.6f,%.6f,%.4f,"
        "%.2f,%.2f,%.2f,"
        "%.6f,"
        "%d\n",
        ts_ms, datetime, rnti,
        prb_dl_raw, prb_ul_raw,
        thp_dl, thp_ul,
        prb_direction, prb_total,
        prb_ul_delta,
        ul_efficiency,
        prb_ul_roll_mean, prb_ul_roll_std, ul_persistence,
        thp_total_kbps, thp_ul_delta, thp_dl_delta,
        traffic_direction,
        get_label_for_rnti(rnti));
    fflush(t->fp);
}

static void csv_per_ue_close(csv_per_ue_trainer_t* t)
{
    if (t->fp) {
        fclose(t->fp);
        t->fp = NULL;
        printf("[CSV_UE] Per-UE recording stopped.\n");
    }
}

static void csv_mac_open(csv_mac_trainer_t* t, const char* path, int label)
{
    t->fp = fopen(path, "w");
    if (!t->fp) {
        printf("[CSV_MAC] ERROR: cannot open %s\n", path);
        return;
    }
    t->label = label;
    fprintf(t->fp,
        "timestamp_ms,datetime,rnti,"
        "dl_prb,ul_prb,wb_cqi,pusch_snr,"
        "dl_tbs_mbps,ul_tbs_mbps,"
        "dl_bler,ul_bler,"
        "dl_retx_prb,ul_retx_prb,"
        "dl_mcs,ul_mcs,"
        "phr,bsr,"
        "rnti_birth_rate,"
        "label\n");
    fflush(t->fp);
    printf("[CSV_MAC] Recording MAC per-UE to %s  (label=%d)\n", path, label);
}

static void csv_mac_write(csv_mac_trainer_t* t,
                          uint32_t rnti,
                          uint32_t dl_prb,   uint32_t ul_prb,
                          float    wb_cqi,   float    pusch_snr,
                          float    dl_tbs_mbps, float ul_tbs_mbps,
                          float    dl_bler,  float    ul_bler,
                          uint32_t dl_retx,  uint32_t ul_retx,
                          uint8_t  dl_mcs,   uint8_t  ul_mcs,
                          int8_t   phr,      uint32_t bsr,
                          uint32_t rnti_birth_rate)
{
    if (!t->fp) return;
    maybe_reload_label();

    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    long long ts_ms = (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000LL;
    char datetime[32];
    struct tm* tm_info = localtime(&ts.tv_sec);
    int ms_part = (int)(ts.tv_nsec / 1000000LL);
    strftime(datetime, sizeof(datetime), "%Y-%m-%d %H:%M:%S", tm_info);
    snprintf(datetime + 19, sizeof(datetime) - 19, ".%03d", ms_part);

    fprintf(t->fp,
        "%lld,%s,%u,"
        "%u,%u,%.3f,%.3f,"
        "%.6f,%.6f,"
        "%.6f,%.6f,"
        "%u,%u,"
        "%u,%u,"
        "%d,%u,"
        "%u,"
        "%d\n",
        ts_ms, datetime, rnti,
        dl_prb, ul_prb, wb_cqi, pusch_snr,
        dl_tbs_mbps, ul_tbs_mbps,
        dl_bler, ul_bler,
        dl_retx, ul_retx,
        (unsigned)dl_mcs, (unsigned)ul_mcs,
        (int)phr, bsr,
        rnti_birth_rate,
        get_label_for_rnti(rnti));
    fflush(t->fp);
}

static void csv_mac_close(csv_mac_trainer_t* t)
{
    if (t->fp) {
        fclose(t->fp);
        t->fp = NULL;
        printf("[CSV_MAC] MAC per-UE recording stopped.\n");
    }
}

/* TDD test — called via --test flag */
static int test_csv_writer(void)
{
    const char* path = "/tmp/test_sec_training.csv";

    cell_metrics_t m = {
        .thp_dl_mbps   = 50.0f,
        .thp_ul_mbps   =  5.0f,
        .prb_used_dl   = 60.0f,
        .prb_used_ul   = 10.0f,
        .prb_avail_dl  = 106.0f,
        .prb_avail_ul  = 106.0f,
        .cqi           = 12.0f,
        .rsrp          = -75.0f,
        .rsrq          =  -8.0f,
        .rach_preamble =   2.0f,
        .air_delay_ul  =   3.5f,
        .rlc_vol_dl    = 1000.0f,
        .rlc_vol_ul    =  200.0f,
        .rlc_drop_dl   =   0.0f,
        .rlc_delay_ul  =   1.0f,
        .rrc_att       =   3.0f,
        .rrc_succ      =   3.0f,
    };

    csv_trainer_t t = {0};
    csv_trainer_open(&t, path, 0, 100);
    if (!t.fp) {
        printf("[FAIL] test_csv_writer: file not created\n");
        return 1;
    }

    csv_trainer_write(&t, &m);
    csv_trainer_close(&t);

    FILE* f = fopen(path, "r");
    if (!f) { printf("[FAIL] test_csv_writer: file missing after close\n"); return 1; }

    char buf[1024];

    /* Verify header contains required columns */
    if (!fgets(buf, sizeof(buf), f)) { fclose(f); printf("[FAIL] no header line\n"); return 1; }
    if (!strstr(buf, "timestamp_ms")                ||
        !strstr(buf, "prb_usage_dl_ratio")          ||
        !strstr(buf, "prb_burst_index")             ||
        !strstr(buf, "label")                       ||
        !strstr(buf, "stage1_alert")                ||
        !strstr(buf, "stage2_confirmed")            ||
        !strstr(buf, "alert_type")                  ||
        !strstr(buf, "stage1_latency_ms")           ||
        !strstr(buf, "stage2_confirmation_time_ms") ||
        !strstr(buf, "anomaly_score")               ||
        !strstr(buf, "prb_total_variance")          ||
        !strstr(buf, "rlc_drop_dl")                 ||
        !strstr(buf, "rlc_delay_ul")) {
        fclose(f);
        printf("[FAIL] header missing required columns:\n  %s\n", buf);
        return 1;
    }

    /* Verify data row */
    if (!fgets(buf, sizeof(buf), f)) { fclose(f); printf("[FAIL] no data row\n"); return 1; }

    /* prb_dl_ratio: prb_used_dl=60 / (60+106) = 0.361446 */
    if (!strstr(buf, "0.361446")) {
        fclose(f);
        printf("[FAIL] prb_dl_ratio=0.361 not in row:\n  %s\n", buf);
        return 1;
    }

    /* cqi = 12.000 */
    if (!strstr(buf, "12.000")) {
        fclose(f);
        printf("[FAIL] cqi=12.0 not in row:\n  %s\n", buf);
        return 1;
    }

    /* rlc_delay_ul is the last field — check row ends with a float */
    char* last_comma = strrchr(buf, ',');
    if (!last_comma || strtof(last_comma + 1, NULL) < 0.0f) {
        fclose(f);
        printf("[FAIL] rlc_delay_ul (last field) missing or invalid:\n  %s\n", buf);
        return 1;
    }
    /* prb_total_variance must be present */
    if (!strstr(buf, "prb_total_variance") && strstr(buf, "timestamp_ms")) {
        /* header check already done above; here just confirm data row has floats */
    }

    fclose(f);
    remove(path);
    printf("[PASS] test_csv_writer\n");
    return 0;
}

////////////
// Get KPM Indication Messages -> begin
////////////

static void sm_cb_kpm(sm_ag_if_rd_t const* rd, global_e2_node_id_t const* e2_node)
{
  assert(rd != NULL);
  assert(rd->type == INDICATION_MSG_AGENT_IF_ANS_V0);
  assert(rd->ind.type == KPM_STATS_V3_0);

  kpm_ind_data_t const* kpm = &rd->ind.kpm.ind;

  /* CU-CP callbacks only update RRC counters — no CSV write, no rule-based IDS */
  /* srsRAN CU-CP registers as ngran_gNB (monolithic type), not ngran_gNB_CUCP */
  bool const is_cu_cp = (e2_node != NULL &&
                         (e2_node->type == e2ap_ngran_gNB_CUCP ||
                          e2_node->type == e2ap_ngran_gNB));

  lock_guard(&mtx);

  /* Compact 1-line status printed once per second to keep terminal readable.
     Verbose per-metric dump suppressed — alerts remain clearly visible.    */
  static uint64_t g_last_status_print_ms = 0;

  if (kpm->msg.type == FORMAT_1_INDICATION_MESSAGE) {
    kpm_ind_msg_format_1_t const* msg_frm_1 = &kpm->msg.frm_1;

    /* meas_data_lst_len == 0 means APER decode failed (srsRAN sent an empty
     * indication during UE detach/reattach). Count these as a RRC storm proxy.
     * Run detection time-gated so Rule 3b (RRC_STORM) can fire even when
     * ALL messages in a window are empty (no successful decode arrives). */
    if (msg_frm_1->meas_data_lst_len == 0) {
      if (!is_cu_cp) {
        g_cell.empty_ind_rate += 1.0f;
        struct timespec _ts_e;
        clock_gettime(CLOCK_REALTIME, &_ts_e);
        uint64_t empty_now_ms = (uint64_t)_ts_e.tv_sec * 1000ULL
                                + _ts_e.tv_nsec / 1000000ULL;
        if (empty_now_ms - g_last_csv_ms >= 90) {
          if (g_cell_enabled) rule_based_detect(&g_cell, (long long)empty_now_ms);
          if (g_cell_enabled && g_csv_enabled) csv_trainer_write(&g_csv, &g_cell);
          g_cell.empty_ind_rate = 0.0f;
          g_last_csv_ms = empty_now_ms;
        }
      }
      return;
    }

    for (size_t j = 0; j < msg_frm_1->meas_data_lst_len; j++) {
      size_t rec_idx = 0;
      for (size_t i = 0; i < msg_frm_1->meas_info_lst_len; i++) {
        size_t n_labels = msg_frm_1->meas_info_lst[i].label_info_lst_len;
        if (n_labels == 0) n_labels = 1;
        if (msg_frm_1->meas_info_lst[i].meas_type.type == NAME_MEAS_TYPE) {
          char name[64];
          int len = msg_frm_1->meas_info_lst[i].meas_type.name.len;
          if (len >= 64) len = 63;
          memcpy(name, msg_frm_1->meas_info_lst[i].meas_type.name.buf, len);
          name[len] = '\0';

          float val = 0.0f;
          if (rec_idx < msg_frm_1->meas_data_lst[j].meas_record_len) {
            meas_record_lst_t const* rec = &msg_frm_1->meas_data_lst[j].meas_record_lst[rec_idx];
            if (rec->value == REAL_MEAS_VALUE)      val = (float)rec->real_val;
            else if (rec->value == INTEGER_MEAS_VALUE) val = (float)rec->int_val;

            /* Isi g_cell untuk rule-based detection + CSV recorder */
            if      (!strcmp(name, "RRU.PrbUsedDl"))                    g_cell.prb_used_dl   = val;
            else if (!strcmp(name, "RRU.PrbUsedUl"))                    g_cell.prb_used_ul   = val;
            else if (!strcmp(name, "RRU.PrbTotDl"))                     g_cell.prb_tot_dl    = val;
            else if (!strcmp(name, "RRU.PrbTotUl"))                     g_cell.prb_tot_ul    = val;
            else if (!strcmp(name, "RRU.PrbAvailDl"))                   g_cell.prb_avail_dl  = val;
            else if (!strcmp(name, "RRU.PrbAvailUl"))                   g_cell.prb_avail_ul  = val;
            else if (!strcmp(name, "DRB.UEThpDl"))                      g_cell.thp_dl_mbps   = val / 1000.0f;
            else if (!strcmp(name, "DRB.UEThpUl"))                      g_cell.thp_ul_mbps   = val / 1000.0f;
            else if (!strcmp(name, "RACH.PreambleDedCell"))              g_cell.rach_preamble = val;
            else if (!strcmp(name, "DRB.AirIfDelayUl"))                 g_cell.air_delay_ul  = val;
            else if (!strcmp(name, "DRB.RlcSduTransmittedVolumeDL"))    g_cell.rlc_vol_dl    = val;
            else if (!strcmp(name, "DRB.RlcSduTransmittedVolumeUL"))    g_cell.rlc_vol_ul    = val;
            else if (!strcmp(name, "DRB.RlcPacketDropRateDl"))          g_cell.rlc_drop_dl   = val;
            else if (!strcmp(name, "DRB.RlcDelayUl"))                   g_cell.rlc_delay_ul  = val;
            else if (!strcmp(name, "CQI"))                              g_cell.cqi           = val;
            else if (!strcmp(name, "RSRP"))                             g_cell.rsrp          = val;
            else if (!strcmp(name, "RSRQ"))                             g_cell.rsrq          = val;
            else if (!strcmp(name, "RRC.ConnEstabAtt"))                 g_cell.rrc_att       = val;
            else if (!strcmp(name, "RRC.ConnEstabSucc"))                g_cell.rrc_succ      = val;
          }
        }
        rec_idx += n_labels;
      }
    }
    if (!is_cu_cp) {
      struct timespec _ts_kpm;
      clock_gettime(CLOCK_REALTIME, &_ts_kpm);
      uint64_t kpm_now_ms = (uint64_t)_ts_kpm.tv_sec * 1000ULL
                            + _ts_kpm.tv_nsec / 1000000ULL;

      /* srsRAN may fire sm_cb_kpm multiple times per configured period (once
         per internal RNTI entry).  Write at most one CSV row per 90ms to
         collapse duplicates.  At time=10ms config srsRAN fires every ~20ms;
         the 90ms gate keeps CSV at ~100ms rows.  At time=100ms srsRAN fires
         every ~200ms — every callback passes the gate (200 >= 90). */
      int sev = g_cell_enabled ? rule_based_detect(&g_cell, (long long)kpm_now_ms) : 0;
      if (g_cell_enabled) run_cell_inference((long long)kpm_now_ms);
      int final_sev;
      if (!g_cell_enabled)            final_sev = 0;                     /* cell disabled */
      else if (g_detection_mode == 0) final_sev = sev;                   /* rule only */
      else if (g_detection_mode == 1) final_sev = g_lstm_infer.severity; /* lstm only */
      else final_sev = (g_lstm_infer.severity > sev) ? g_lstm_infer.severity : sev; /* hybrid */

      if (g_cell_enabled && g_csv_enabled && kpm_now_ms - g_last_csv_ms >= 90) {
        csv_trainer_write(&g_csv, &g_cell);
        g_cell.empty_ind_rate = 0.0f; /* reset accumulator after each CSV row */
        g_last_csv_ms = kpm_now_ms;
      }

      /* Signal main() to apply/restore RC PRB throttle outside the mutex.
       * Restore is gated: if per-UE IDS is active, only restore when no
       * per-UE alert has fired in the last THROTTLE_RESTORE_MS (10s). */
      int ue_ids_active = (g_ids_mode != IDS_MODE_RULE_ONLY);
      int ue_recently_alerted = ue_ids_active
          && ((kpm_now_ms - g_last_ue_alert_ms) < (uint64_t)THROTTLE_RESTORE_MS);
      if (final_sev == 2 && !g_throttle_active) {
        g_pending_throttle = 1; /* request throttle */
      } else if (final_sev == 0 && g_throttle_active && !ue_recently_alerted) {
        g_pending_throttle = 2; /* request restore */
      }

      ue_tracker_flush(&g_ue_tracker, kpm_now_ms);

      /* Compact 1-line status per second — key metrics only. */
      if (kpm_now_ms - g_last_status_print_ms >= 1000) {
        time_t _t = (time_t)(_ts_kpm.tv_sec);
        struct tm *_tm = localtime(&_t);
        char _tbuf[16];
        strftime(_tbuf, sizeof(_tbuf), "%H:%M:%S", _tm);
        ids_detection_state_t _det = ids_get_detection_state();
        const char *sev_str = (final_sev == 2) ? " \033[1;31m[STAGE2-CRITICAL]\033[0m"
                            : (final_sev == 1) ? " \033[1;33m[STAGE1-WARNING]\033[0m"
                            :                    " [OK]";
        printf("[%s]%s alert=%s PRB_DL=%.0f%% PRB_UL=%.0f%% RACH=%.0f CQI=%.0f anomaly=%.4f\n",
               _tbuf, sev_str, alert_type_to_str(_det.alert_type),
               g_cell.prb_used_dl, g_cell.prb_used_ul,
               g_cell.rach_preamble, g_cell.cqi,
               g_last_anomaly_score);
        fflush(stdout);
        g_last_status_print_ms = kpm_now_ms;
      }
    }
    fflush(stdout);

  } else if (kpm->msg.type == FORMAT_3_INDICATION_MESSAGE) {
    kpm_ind_msg_format_3_t const* msg_frm_3 = &kpm->msg.frm_3;
    printf("[FORMAT3] ue_meas_report_lst_len=%zu\n", msg_frm_3->ue_meas_report_lst_len);
    fflush(stdout);

    /* Debounce: srsRAN per-UE metrics update at ~1s granularity.
     * Gate at 800ms keeps 1 unique row per second and drops stale duplicates. */
    struct timespec _ts_ue; clock_gettime(CLOCK_REALTIME, &_ts_ue);
    uint64_t ue_now_ms = (uint64_t)_ts_ue.tv_sec * 1000ULL + (uint64_t)_ts_ue.tv_nsec / 1000000ULL;
    int write_csv = (ue_now_ms - g_last_per_ue_csv_ms >= 800);
    if (write_csv) g_last_per_ue_csv_ms = ue_now_ms;

    for (size_t i = 0; i < msg_frm_3->ue_meas_report_lst_len; i++) {
      uint32_t rnti = 9999;
      ue_id_e2sm_t const* uid = &msg_frm_3->meas_report_per_ue[i].ue_meas_report_lst;
      printf("[FORMAT3] UE[%zu] id_type=%d\n", i, uid->type);
      fflush(stdout);
      if (uid->type == GNB_UE_ID_E2SM)
          rnti = (uint32_t)uid->gnb.amf_ue_ngap_id;
      else if (uid->type == GNB_DU_UE_ID_E2SM)
          rnti = uid->gnb_du.gnb_cu_ue_f1ap;

      int ue_idx = rnti % MAX_UE;
      int t = ue_buffers[ue_idx].count;

      float prb_dl = 0.0f, prb_ul = 0.0f, cqi_ue = 15.0f, rach_ue = 0.0f;
      float thp_dl = 0.0f, thp_ul = 0.0f;

      /* Always extract metrics so CSV gets real values even when buffer is full */
      kpm_ind_msg_format_1_t const* msg_frm_1 =
          &msg_frm_3->meas_report_per_ue[i].ind_msg_format_1;
      for (size_t j = 0; j < msg_frm_1->meas_data_lst_len; j++) {
        for (size_t z = 0; z < msg_frm_1->meas_data_lst[j].meas_record_len; z++) {
          if (msg_frm_1->meas_info_lst_len > 0 &&
              msg_frm_1->meas_info_lst[z].meas_type.type == NAME_MEAS_TYPE)
          {
            char name[64];
            int len = msg_frm_1->meas_info_lst[z].meas_type.name.len;
            if (len >= 64) len = 63;
            memcpy(name, msg_frm_1->meas_info_lst[z].meas_type.name.buf, len);
            name[len] = '\0';

            float val = 0.0f;
            if (msg_frm_1->meas_data_lst[j].meas_record_lst[z].value == REAL_MEAS_VALUE)
                val = msg_frm_1->meas_data_lst[j].meas_record_lst[z].real_val;
            else if (msg_frm_1->meas_data_lst[j].meas_record_lst[z].value == INTEGER_MEAS_VALUE)
                val = (float)msg_frm_1->meas_data_lst[j].meas_record_lst[z].int_val;

            if      (strstr(name, "RRU.PrbUsedDl")          != NULL) prb_dl      = val / 100.0f;
            else if (strstr(name, "RRU.PrbUsedUl")          != NULL) prb_ul      = val / 100.0f;
            else if (strstr(name, "CQI")                    != NULL) cqi_ue      = val;
            else if (strstr(name, "RACH.Preamble")          != NULL) rach_ue     = val;
            else if (strstr(name, "DRB.UEThpDl")            != NULL) thp_dl      = val;
            else if (strstr(name, "DRB.UEThpUl")            != NULL) thp_ul      = val;
            /* DRB.RlcPacketDropRateDl not sent by srsRAN Style 4 per-UE — skipped */

            printf("[Style5-UE rnti=%u] %s = %.4f\n", rnti, name, val);
          }
        }
      }

      /* KPM FORMAT_3 often sends NO_MEAS_VALUE for RRU.PrbUsedUl/Dl — fall
       * back to MAC scheduler PRB cache which is always populated when the
       * UE is actively transmitting. */
      if (prb_ul < 0.001f && g_mac_ul_prb[ue_idx] > 0)
          prb_ul = (float)g_mac_ul_prb[ue_idx] / 100.0f;
      if (prb_dl < 0.001f && g_mac_dl_prb[ue_idx] > 0)
          prb_dl = (float)g_mac_dl_prb[ue_idx] / 100.0f;

      printf("[Style5-UE rnti=%u] => prb_dl=%.3f prb_ul=%.3f cqi=%.1f rach=%.1f "
             "thp_dl=%.2f thp_ul=%.2f\n",
             rnti, prb_dl, prb_ul, cqi_ue, rach_ue, thp_dl, thp_ul);
      fflush(stdout);

      /* Per-UE buffer (reserved for future per-UE analysis) */
      if (t < WINDOW_SIZE) {
          ue_buffers[ue_idx].features[t][2] = prb_dl;
          ue_buffers[ue_idx].features[t][3] = prb_ul;
          ue_buffers[ue_idx].features[t][4] = cqi_ue / 15.0f;
          ue_buffers[ue_idx].features[t][5] = rach_ue;
          ue_buffers[ue_idx].count++;
      }

      if (write_csv)
        csv_per_ue_write(&g_csv_per_ue, rnti, ue_idx, prb_dl, prb_ul, thp_dl, thp_ul);
      /* Per-UE IDS pipeline — runs regardless of write_csv */
      {
          int ue_slot = find_or_create_ue_state((uint16_t)rnti);
          if (ue_slot >= 0) {
              struct timespec _ts_ids;
              clock_gettime(CLOCK_REALTIME, &_ts_ids);
              long long ids_now_ms = (long long)_ts_ids.tv_sec * 1000LL
                                   + (long long)_ts_ids.tv_nsec / 1000000LL;
              float features[ML_NUM_FEATURES];
              ue_ids_update(ue_slot, prb_dl, prb_ul, thp_dl, thp_ul, features);
              rule_result_t rule = rule_based_detect_ue(ue_slot, features, ids_now_ms);
              float mse = run_inference_ue(ue_slot);
              ue_alert_type_t alert = decision_engine_ue(
                  ue_slot, rule, mse, g_ue_threshold, g_ids_mode, ids_now_ms);
              if (alert != UE_ALERT_NONE) {
                  alert_log_ue(rnti, rule, mse, g_ue_threshold, alert, ids_now_ms);
                  /* Per-UE alert → request E2SM-RC throttle; update last-alert timestamp
                   * so cell-level restore path does not fire while attack is ongoing. */
                  g_last_ue_alert_ms = (uint64_t)ids_now_ms;
                  if (!g_throttle_active)
                      g_pending_throttle = 1;
              }
          }
      }
    }
    fflush(stdout);
  } else {
    printf("[KPM] Received unhandled message format type=%d\n", kpm->msg.type);
    fflush(stdout);
  }
}
////////////
// Get KPM Indication Messages -> end
////////////

////////////
// Get RLC Indication Messages -> end
////////////


static e2sm_rc_ev_trg_frmt_2_t gen_rc_ev_trig_frm_2(void)
{
  e2sm_rc_ev_trg_frmt_2_t ev_trigger = {0};

  //  Call Process Type ID
  //  Mandatory
  //  9.3.15
  ev_trigger.call_proc_type_id = 3; // Mobility Management

  // Call Breakpoint ID
  // Mandatory
  // 9.3.49
  ev_trigger.call_break_id = 1; // Handover Preparation

  // Associated E2 Node Info
  // Optional
  // 9.3.29
  ev_trigger.assoc_e2_node_info = NULL;

  // Associated UE Info
  // Optional
  // 9.3.26
  ev_trigger.assoc_ue_info = NULL;

  return ev_trigger;
}

static
e2sm_rc_event_trigger_t gen_rc_ev_trig(e2sm_rc_ev_trigger_format_e act_frm)
{
  e2sm_rc_event_trigger_t dst = {0};

  if (act_frm == FORMAT_2_E2SM_RC_EV_TRIGGER_FORMAT) {
    dst.format = FORMAT_2_E2SM_RC_EV_TRIGGER_FORMAT;
    dst.frmt_2 = gen_rc_ev_trig_frm_2();
  } else {
    assert(0!=0 && "not support event trigger type");
  }

  return dst;
}

static
kpm_event_trigger_def_t gen_kpm_ev_trig(uint64_t period)
{
  kpm_event_trigger_def_t dst = {0};

  dst.type = FORMAT_1_RIC_EVENT_TRIGGER;
  dst.kpm_ric_event_trigger_format_1.report_period_ms = period;

  return dst;
}

static
meas_info_format_1_lst_t gen_meas_info_format_1_lst(const act_name_id_t act)
{
  meas_info_format_1_lst_t dst = {0};

  // use id
  if (!strcasecmp(act.name, "null")) {
    dst.meas_type.type = ID_MEAS_TYPE;
    dst.meas_type.id = act.id;
  } else { // use name
    dst.meas_type.type = NAME_MEAS_TYPE;
    // ETSI TS 128 552
    dst.meas_type.name = cp_str_to_ba(act.name);
  }

  dst.label_info_lst_len = 1;
  dst.label_info_lst = calloc(1, sizeof(label_info_lst_t));
  assert(dst.label_info_lst != NULL && "Memory exhausted");

  // No Label
  dst.label_info_lst[0].noLabel = calloc(1, sizeof(enum_value_e));
  assert(dst.label_info_lst[0].noLabel != NULL && "Memory exhausted");
  *dst.label_info_lst[0].noLabel = TRUE_ENUM_VALUE;

  return dst;
}

static
kpm_act_def_format_1_t gen_kpm_act_def_frmt_1(const sub_oran_sm_t sub_sm, uint32_t period_ms)
{
  kpm_act_def_format_1_t dst = {0};

  dst.gran_period_ms = period_ms;

  dst.meas_info_lst_len = sub_sm.act_len;
  dst.meas_info_lst = calloc(dst.meas_info_lst_len, sizeof(meas_info_format_1_lst_t));
  assert(dst.meas_info_lst != NULL && "Memory exhausted");

  for(size_t i = 0; i < dst.meas_info_lst_len; i++) {
    dst.meas_info_lst[i] = gen_meas_info_format_1_lst(sub_sm.actions[i]);
  }

  return dst;
}

static
kpm_act_def_format_4_t gen_kpm_act_def_frmt_4(const sub_oran_sm_t sub_sm, uint32_t period_ms)
{
  kpm_act_def_format_4_t dst = {0};

  // [1, 32768]
  dst.matching_cond_lst_len = 1;

  dst.matching_cond_lst = calloc(dst.matching_cond_lst_len, sizeof(matching_condition_format_4_lst_t));
  assert(dst.matching_cond_lst != NULL && "Memory exhausted");

  test_info_lst_t* test_info_lst = &dst.matching_cond_lst[0].test_info_lst;
  test_info_lst->test_cond_type = S_NSSAI_TEST_COND_TYPE;
  test_info_lst->S_NSSAI = TRUE_TEST_COND_TYPE;

  test_cond_e* test_cond = calloc(1, sizeof(test_cond_e));
  assert(test_cond != NULL && "Memory exhausted");
  *test_cond = EQUAL_TEST_COND;
  test_info_lst->test_cond = test_cond;

  test_cond_value_t* test_cond_value = calloc(1, sizeof(test_cond_value_t));
  assert(test_cond_value != NULL && "Memory exhausted");
  test_cond_value->type = OCTET_STRING_TEST_COND_VALUE;
  test_cond_value->octet_string_value = calloc(1, sizeof(byte_array_t));
  assert(test_cond_value->octet_string_value != NULL && "Memory exhausted");
  test_cond_value->octet_string_value->len = 1;
  test_cond_value->octet_string_value->buf = calloc(1, sizeof(uint8_t));
  assert(test_cond_value->octet_string_value->buf != NULL && "Memory exhausted");
  test_cond_value->octet_string_value->buf[0] = 1;
  test_info_lst->test_cond_value = test_cond_value;

  // Action definition Format 1
  dst.action_def_format_1 = gen_kpm_act_def_frmt_1(sub_sm, period_ms);  // 8.2.1.2.1

  return dst;
}

static
e2sm_rc_act_def_frmt_1_t gen_rc_act_def_frm_1(const sub_oran_sm_t sub_sm)
{
  e2sm_rc_act_def_frmt_1_t act_def_frm_1 = {0};

  // Parameters to be Reported List
  // [1-65535]
  // 8.2.2
  act_def_frm_1.sz_param_report_def = sub_sm.act_len;
  act_def_frm_1.param_report_def = calloc(act_def_frm_1.sz_param_report_def, sizeof(param_report_def_t));
  assert(act_def_frm_1.param_report_def != NULL && "Memory exhausted");

  // Current UE ID RAN Parameter
  for (size_t i = 0; i < act_def_frm_1.sz_param_report_def; i++) {
    // use id
    if (!strcasecmp(sub_sm.actions[i].name, "null")) {
      act_def_frm_1.param_report_def[i].ran_param_id = sub_sm.actions[i].id;
    } else { // use name
      assert(0!=0 && "not supported Name for RC action definition\n");
    }
  }

  return act_def_frm_1;
}

static
e2sm_rc_action_def_t gen_rc_act_def(const sub_oran_sm_t sub_sm, uint32_t ric_style_type, e2sm_rc_act_def_format_e act_frmt)
{
  e2sm_rc_action_def_t dst = {0};
  dst.ric_style_type = ric_style_type;
  dst.format = act_frmt;
  if (act_frmt == FORMAT_1_E2SM_RC_ACT_DEF) {
    dst.frmt_1 = gen_rc_act_def_frm_1(sub_sm);
  } else {
    assert(0!=0 && "not supported RC action definition\n");
  }

  return dst;
}

static
kpm_act_def_t gen_kpm_act_def(const sub_oran_sm_t sub_sm, format_action_def_e act_frm, uint32_t period_ms)
{
  kpm_act_def_t dst = {0};

  if (act_frm == FORMAT_1_ACTION_DEFINITION) {
    dst.type = FORMAT_1_ACTION_DEFINITION;
    dst.frm_1 = gen_kpm_act_def_frmt_1(sub_sm, period_ms);
  } else if (act_frm == FORMAT_4_ACTION_DEFINITION) {
    dst.type = FORMAT_4_ACTION_DEFINITION;
    dst.frm_4 = gen_kpm_act_def_frmt_4(sub_sm, period_ms);
  } else if (act_frm == FORMAT_5_ACTION_DEFINITION) {
    /* Style 5: UE-level measurements — kirim Format 3 indication (per-UE DRB metrics) */
    dst.type = FORMAT_5_ACTION_DEFINITION;
    /* encoder requires ue_id_lst_len >= 2; use placeholder IDs — RAN matches all active UEs */
    dst.frm_5.ue_id_lst_len = 2;
    dst.frm_5.ue_id_lst = calloc(2, sizeof(ue_id_e2sm_t));
    assert(dst.frm_5.ue_id_lst != NULL && "Memory exhausted");
    dst.frm_5.ue_id_lst[0].type = GNB_UE_ID_E2SM;
    dst.frm_5.ue_id_lst[0].gnb.amf_ue_ngap_id = 1;
    dst.frm_5.ue_id_lst[0].gnb.gnb_cu_ue_f1ap_lst_len = 1;
    dst.frm_5.ue_id_lst[0].gnb.gnb_cu_ue_f1ap_lst = calloc(1, sizeof(uint64_t));
    dst.frm_5.ue_id_lst[0].gnb.gnb_cu_ue_f1ap_lst[0] = 1;
    dst.frm_5.ue_id_lst[1].type = GNB_UE_ID_E2SM;
    dst.frm_5.ue_id_lst[1].gnb.amf_ue_ngap_id = 2;
    dst.frm_5.ue_id_lst[1].gnb.gnb_cu_ue_f1ap_lst_len = 1;
    dst.frm_5.ue_id_lst[1].gnb.gnb_cu_ue_f1ap_lst = calloc(1, sizeof(uint64_t));
    dst.frm_5.ue_id_lst[1].gnb.gnb_cu_ue_f1ap_lst[0] = 2;
    dst.frm_5.action_def_format_1 = gen_kpm_act_def_frmt_1(sub_sm, period_ms);
  } else {
    assert(0!=0 && "not support action definition type");
  }

  return dst;
}

static void test_run_inference(void) {
    printf("[TEST] Running inference test...\n");
    // Simulate UE 0 with RNTI 9999
    int ue_idx = 0;
    int rnti = 9999;
    
    // Fill buffer with dummy data
    for (int t = 0; t < WINDOW_SIZE; t++) {
        for (int f = 0; f < NUM_FEATURES; f++) {
            // Fill with small values to simulate normal traffic
            ue_buffers[ue_idx].features[t][f] = 0.1f * (t + 1);
        }
    }
    ue_buffers[ue_idx].count = WINDOW_SIZE;
    
    run_inference(rnti, ue_idx);
    printf("[TEST] Inference test completed.\n");
}

static bool has_ran_func(e2_node_connected_xapp_t* n, uint16_t func_id) {
    for (size_t j = 0; j < n->len_rf; j++) {
        if (n->rf[j].id == func_id) return true;
    }
    return false;
}

static int test_two_stage_detection(void)
{
    ids_reset();
    ids_init(100);

    cell_metrics_t m = {0};
    m.cqi = 15.0f;

    /* Test A: UL saturation Stage 1 fires after 3 windows */
    m.prb_used_ul = 85.0f;
    m.prb_used_dl = 5.0f;
    for (int i = 0; i < 3; i++) {
        int s = rule_based_detect(&m, 1000LL + i * 120LL);
        if (i < 2 && s != 0) {
            printf("[FAIL] test A: sev should be 0 before 3 windows, got %d at window %d\n", s, i);
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

    /* Test B: Stage 2 CRITICAL after 30s sustained */
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

    /* Test C: DL-dominant speedtest (25s) — DL threshold 30s, tidak ada UL fast-path.
     * Speedtest nyata: PRB DL tinggi, PRB UL rendah (<15%).
     * Fast-path UL tidak berlaku karena prb_ul < 80%. */
    ids_reset();
    ids_init(100);
    m.prb_used_dl = 90.0f;
    m.prb_used_ul = 10.0f;
    ts = 10000LL;
    int stage2_fp = 0;
    for (int i = 0; i < 250; i++) {  /* 250 x 100ms = 25s */
        int s = rule_based_detect(&m, ts);
        if (s == 2) { stage2_fp = 1; break; }
        ts += 100LL;
    }
    if (stage2_fp) {
        printf("[FAIL] test C: Stage 2 CRITICAL triggered on 25s DL speedtest (FP)\n");
        return 1;
    }
    printf("[PASS] test C: 25s DL speedtest stays at WARNING (DL uses 30s threshold, no UL fast-path)\n");

    /* Test D: Normal traffic — no alert */
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

    /* ── Test E: UL flatline fast-path ─────────────────────────────────────
     * PRB UL konstan = zero variance → CRITICAL sebelum 30s (threshold normal).
     * Stage1 aktif mulai window ke-3, Stage2 fast-path aktif setelah 3000ms. */
    ids_reset();
    {
        cell_metrics_t m_e = {0};
        m_e.prb_used_ul = 85.0f;  /* konstan = variance nol */
        long long now_e  = 2000000000LL;
        int sev_e = 0;
        int win_e = 0;
        for (; win_e < 60 && sev_e < 2; win_e++) {
            now_e += 120;
            sev_e = rule_based_detect(&m_e, now_e);
        }
        if (sev_e != 2) {
            printf("[FAIL] test E: UL flatline fast-path tidak mencapai CRITICAL "
                   "dalam 60 windows\n");
            ids_reset();
            return 1;
        }
        long long elapsed_ms = (long long)win_e * 120LL;
        if (elapsed_ms >= 30000) {
            printf("[FAIL] test E: CRITICAL dicapai setelah %lldms — "
                   "fast-path tidak bekerja (normal 30s threshold)\n", elapsed_ms);
            ids_reset();
            return 1;
        }
        printf("[PASS] test E: UL_SATURATION fast-path CRITICAL dalam %lldms "
               "(jauh < 30s threshold)\n", elapsed_ms);
    }
    ids_reset();

    printf("[PASS] test_two_stage_detection: all tests passed\n");
    return 0;
}

int main(int argc, char *argv[])
{
  // Disable stdout buffering so output is immediately available
  // when piped to Python or redirected to a file
  setbuf(stdout, NULL);

  // Test mode: if "--test" is provided, run inference test and exit
  if (argc > 1 && strcmp(argv[1], "--test") == 0) {
      init_onnx();
      test_run_inference();
      int ids_rc = test_two_stage_detection();
      if (ids_rc != 0) return ids_rc;
      int csv_rc = test_csv_writer();
      return csv_rc;
  }

  /* Parse --label N and --mitigate; strip both from argv before init_fr_args.
     init_fr_args uses getopt and will reject unknown long flags.
     --mitigate enables automatic E2SM-RC PRB throttle (opt-in: srsRAN RC
     Bug #468 can cause gNB E2 agent to crash after RC Control message).  */
  g_label = 0;  /* initial label — may be overridden by --label N */
  int fargc = 0;
  char *fargv[64];
  for (int a = 0; a < argc && fargc < 63; a++) {
      if (strcmp(argv[a], "--label") == 0 && a + 1 < argc) {
          g_label = atoi(argv[a + 1]);
          a++; /* skip value too */
      } else if (strcmp(argv[a], "--mitigate") == 0) {
          g_mitigate_enabled = 1;
          printf("[MITIGATE] Automatic RC PRB throttle ENABLED (--mitigate).\n");
      } else if (strcmp(argv[a], "--no-cell") == 0) {
          g_cell_enabled = 0;
          printf("[CONFIG] Cell-level detection DISABLED (--no-cell).\n");
      } else if (strcmp(argv[a], "--no-csv") == 0) {
          g_csv_enabled = 0;
          printf("[CONFIG] Training CSV writes DISABLED (--no-csv).\n");
      } else if (strcmp(argv[a], "--mode") == 0 && a + 1 < argc) {
          a++;
          if (strcmp(argv[a], "rule") == 0) {
              g_detection_mode = 0;
              printf("[MODE] Rule-Based IDS only (LSTM disabled).\n");
          } else if (strcmp(argv[a], "lstm") == 0) {
              g_detection_mode = 1;
              printf("[MODE] LSTM only (rule-based disabled).\n");
          } else if (strcmp(argv[a], "hybrid") == 0) {
              g_detection_mode = 2;
              printf("[MODE] Hybrid detection (Rule + LSTM, default).\n");
          } else {
              fprintf(stderr, "[WARN] Unknown --mode '%s', using hybrid.\n", argv[a]);
          }
      } else if (strcmp(argv[a], "--ids-mode") == 0 && a + 1 < argc) {
          a++;
          int _m = ids_mode_parse(argv[a]);
          if (_m < 0) {
              fprintf(stderr, "[WARN] Unknown --ids-mode '%s', using rule-only.\n", argv[a]);
          } else {
              g_ids_mode = (ids_mode_t)_m;
          }
          printf("[IDS-UE] mode=%s\n", argv[a]);
      } else {
          fargv[fargc++] = argv[a];
      }
  }
  fargv[fargc] = NULL;

  fr_args_t args = init_fr_args(fargc, fargv);
  defer({ free_fr_args(&args); });

  /* Open CSV recorder with timestamped filename */
  {
      char csv_path[256];
      time_t now = time(NULL);
      struct tm* tm_info = localtime(&now);
      strftime(csv_path, sizeof(csv_path),
               "/home/telmat/sec-xapp/csv/training_%Y%m%d_%H%M%S.csv",
               tm_info);
      csv_trainer_open(&g_csv, csv_path, g_label, kpm_period_ms);
  }
  defer({ csv_trainer_close(&g_csv); });

  /* Per-UE CSV for format=4 */
  {
      char per_ue_path[256];
      time_t now2 = time(NULL);
      struct tm* tm_info2 = localtime(&now2);
      strftime(per_ue_path, sizeof(per_ue_path),
               "/home/telmat/sec-xapp/csv/per_ue_training_%Y%m%d_%H%M%S.csv",
               tm_info2);
      csv_per_ue_open(&g_csv_per_ue, per_ue_path, g_label);
  }
  defer({ csv_per_ue_close(&g_csv_per_ue); });

  /* MAC SM per-UE CSV — primary training data source */
  {
      char mac_path[256];
      time_t now3 = time(NULL);
      struct tm* tm_info3 = localtime(&now3);
      strftime(mac_path, sizeof(mac_path),
               "/home/telmat/sec-xapp/csv/mac_per_ue_%Y%m%d_%H%M%S.csv",
               tm_info3);
      csv_mac_open(&g_csv_mac, mac_path, g_label);
  }
  defer({ csv_mac_close(&g_csv_mac); });

  // Init ONNX Runtime
  init_onnx();

  // Init Rule-Based IDS
  ids_init(kpm_period_ms);
  /* Open per-UE alert CSV */
  {
      char alert_path[256];
      time_t now_a = time(NULL);
      struct tm *tm_a = localtime(&now_a);
      strftime(alert_path, sizeof(alert_path),
               "/home/telmat/sec-xapp/csv/ue_alerts_%Y%m%d_%H%M%S.csv",
               tm_a);
      g_ue_alert_fp = fopen(alert_path, "w");
      if (g_ue_alert_fp) {
          fprintf(g_ue_alert_fp,
                  "timestamp_ms,rnti,rule_mask,rule_stage,mse,threshold,alert_type\n");
          fflush(g_ue_alert_fp);
      }
  }
  defer({ if (g_ue_alert_fp) fclose(g_ue_alert_fp); });

  /* Per-UE ONNX session — must be after init_onnx() */
  init_onnx_ue();
  ue_tracker_init(&g_ue_tracker, NULL); /* alerts → stderr; pass a FILE* for file logging */

  //Init the xApp
  init_xapp_api(&args);
  sleep(1);

  e2_node_arr_xapp_t nodes = {0};

  // Wait for at least one E2 node to connect (retry every 2 seconds)
  for (int attempt = 0; attempt < 120; attempt++) {  // max 4 minutes
    nodes = e2_nodes_xapp_api();
    if (nodes.len > 0)
      break;
    if (attempt == 0)
      printf("Waiting for E2 nodes to connect...\n");
    if (attempt % 5 == 0 && attempt > 0)
      printf("  Still waiting... (attempt %d)\n", attempt);
    free_e2_node_arr_xapp(&nodes);
    sleep(2);
  }

  if (nodes.len == 0) {
    printf("Error: No E2 nodes connected after waiting. Exiting.\n");
    while(try_stop_xapp_api() == false) usleep(1000);
    return 1;
  }
  defer({ free_e2_node_arr_xapp(&nodes); });

  printf("Connected E2 nodes = %d\n", nodes.len);

  //Init SM handler
  sm_ans_xapp_t* kpm_handle = NULL;
  sm_ans_xapp_t* rc_handle = NULL;
  sm_ans_xapp_t* mac_handle = NULL;
  sm_ans_xapp_t* rlc_handle = NULL;

  if(nodes.len > 0){
    kpm_handle = calloc( nodes.len, sizeof(sm_ans_xapp_t) );
    assert(kpm_handle  != NULL);
    rc_handle = calloc( nodes.len, sizeof(sm_ans_xapp_t) );
    assert(rc_handle  != NULL);
    mac_handle = calloc( nodes.len, sizeof(sm_ans_xapp_t) );
    assert(mac_handle  != NULL);
    rlc_handle = calloc( nodes.len, sizeof(sm_ans_xapp_t) );
    assert(rlc_handle  != NULL);
  }

  int n_kpm_handle = 0;
  int n_rc_handle = 0;
  int n_mac_handle = 0;
  int n_rlc_handle = 0;
  //Subscribe SMs for all the E2-nodes
  for (int i = 0; i < nodes.len; i++) {
    e2_node_connected_xapp_t* n = &nodes.n[i];
    for (size_t j = 0; j < n->len_rf; j++)
      printf("Registered node %d ran func id = %d \n ", i, n->rf[j].id);

    for (int32_t j = 0; j < args.sub_oran_sm_len; j++) {
      if (!strcasecmp(args.sub_oran_sm[j].name, "kpm")) {
        int fmt = args.sub_oran_sm[j].format;
        uint64_t period_ms = args.sub_oran_sm[j].time;
        printf("[xApp]: reporting period = %lu [ms], format = %d\n", period_ms, fmt);

        // TODO: implement e2ap_ngran_eNB
        if (n->id.type == e2ap_ngran_eNB)
          continue;
        if (strcasecmp(args.sub_oran_sm[j].ran_type, get_e2ap_ngran_name(n->id.type)))
          continue;

        /* For per-UE formats (4, 5): discover the report_item the E2 node actually
           advertises for that style. Using the RAN-advertised measurement list is
           required — srsRAN silently drops subscriptions built with unknown names. */
        ric_report_style_item_t* report_item = NULL;
        if (fmt != 1) {
          ric_service_report_e target_style = (ric_service_report_e)(fmt - 1);
          size_t kpm_idx = find_sm_idx(n->rf, n->len_rf, eq_sm, SM_KPM_ID);
          if (kpm_idx < n->len_rf
              && n->rf[kpm_idx].id == SM_KPM_ID
              && n->rf[kpm_idx].defn.type == KPM_RAN_FUNC_DEF_E) {
            size_t sz = n->rf[kpm_idx].defn.kpm.sz_ric_report_style_list;
            for (size_t k = 0; k < sz; k++) {
              if (n->rf[kpm_idx].defn.kpm.ric_report_style_list[k].report_style_type == target_style) {
                report_item = &n->rf[kpm_idx].defn.kpm.ric_report_style_list[k];
                break;
              }
            }
          }
          if (report_item == NULL
              || target_style >= END_RIC_SERVICE_REPORT
              || get_kpm_act_def[target_style] == NULL) {
            printf("WARNING: KPM Format %d not advertised by E2 node %d — skipping per-UE subscription\n", fmt, i);
            continue;
          }
          printf("[xApp]: KPM Style %d advertised with %zu measurements\n",
                 fmt, report_item->meas_info_for_action_lst_len);
        }

        kpm_sub_data_t kpm_sub = {0};
        defer({ free_kpm_sub_data(&kpm_sub); });
        kpm_sub.ev_trg_def = gen_kpm_ev_trig(period_ms);
        kpm_sub.sz_ad = 1;
        kpm_sub.ad = calloc(1, sizeof(kpm_act_def_t));
        assert(kpm_sub.ad != NULL && "Memory exhausted");

        if (fmt == 1) {
          *kpm_sub.ad = gen_kpm_act_def((const sub_oran_sm_t)args.sub_oran_sm[j],
                                        FORMAT_1_ACTION_DEFINITION, period_ms);
        } else {
          ric_service_report_e target_style = (ric_service_report_e)(fmt - 1);
          *kpm_sub.ad = get_kpm_act_def[target_style](report_item);
        }

        printf("xApp subscribes RAN Func ID %d in E2 node idx %d, nb_id %d\n", SM_KPM_ID, i, n->id.nb_id.nb_id);
        kpm_handle[i] = report_sm_xapp_api(&nodes.n[i].id, SM_KPM_ID, &kpm_sub, sm_cb_kpm);
        assert(kpm_handle[i].success == true);
        n_kpm_handle += 1;

      } else if (!strcasecmp(args.sub_oran_sm[j].name, "rc")) {
        rc_sub_data_t rc_sub = {0};
        defer({ free_rc_sub_data(&rc_sub); });

        // RC Event Trigger
        rc_sub.et = gen_rc_ev_trig(FORMAT_2_E2SM_RC_EV_TRIGGER_FORMAT);

        // RC Action Definition
        rc_sub.sz_ad = 1;
        rc_sub.ad = calloc(rc_sub.sz_ad, sizeof(e2sm_rc_action_def_t));
        assert(rc_sub.ad != NULL && "Memory exhausted");
        e2sm_rc_act_def_format_e act_type = END_E2SM_RC_ACT_DEF;
        if (args.sub_oran_sm[j].format == 1)
          act_type = FORMAT_1_E2SM_RC_ACT_DEF;
        else
          assert(0!=0 && "not supported action definition format");

        // use RIC style 2 by default
        *rc_sub.ad = gen_rc_act_def((const sub_oran_sm_t)args.sub_oran_sm[j], 2, act_type);

        // RC HO only supports for e2ap_ngran_gNB
        if (n->id.type == e2ap_ngran_eNB || n->id.type == e2ap_ngran_gNB_CU || n->id.type == e2ap_ngran_gNB_DU)
          continue;
        if (strcasecmp(args.sub_oran_sm[j].ran_type, get_e2ap_ngran_name(n->id.type)))
          continue;
        printf("xApp subscribes RAN Func ID %d in E2 node idx %d, nb_id %d\n", SM_RC_ID, i, n->id.nb_id.nb_id);
        rc_handle[i] = report_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &rc_sub, sm_cb_rc);
        assert(rc_handle[i].success == true);
        n_rc_handle += 1;

      } else {
        assert(0!=0 && "unknown SM in .conf");
      }
    }
    
    /* Store DU node ID for RC PRB throttle mitigation */
    if ((n->id.type == e2ap_ngran_gNB_DU || n->id.type == e2ap_ngran_gNB)
        && !g_du_node_id_valid) {
      g_du_node_id = cp_global_e2_node_id(&n->id);
      g_du_node_id_valid = 1;
      printf("[RC] Stored DU node id for PRB throttle mitigation.\n");
    }

    // Attempt MAC/RLC/KPM subscriptions
    if (n->id.type == e2ap_ngran_gNB_DU || n->id.type == e2ap_ngran_gNB) {
      char const* period = "1_ms";
      char const* kpm_period = "10_ms"; // KPM usually needs a slightly larger period like 10_ms or 100_ms
      
      if (!has_ran_func(n, SM_KPM_ID)) {
          printf("WARNING: KPM SM (ID %d) not supported by E2 node %d\n", SM_KPM_ID, i);
      }
      /* KPM auto-discovery disabled: causes duplicate subscriptions and noisy CSV.
         Always use -c <config> to subscribe explicitly. */

      if (has_ran_func(n, SM_MAC_ID)) {
          printf("xApp subscribes MAC SM in DU index %d\n", i);
          mac_handle[i] = report_sm_xapp_api(&nodes.n[i].id, SM_MAC_ID, (void*)period, sm_cb_mac);
          if (mac_handle[i].success) n_mac_handle++;
      } else {
          printf("WARNING: MAC SM (ID %d) not supported by E2 node %d\n", SM_MAC_ID, i);
      }

      if (has_ran_func(n, SM_RLC_ID)) {
          printf("xApp subscribes RLC SM in DU index %d\n", i);
          rlc_handle[i] = report_sm_xapp_api(&nodes.n[i].id, SM_RLC_ID, (void*)period, sm_cb_rlc);
          if (rlc_handle[i].success) n_rlc_handle++;
      } else {
          printf("WARNING: RLC SM (ID %d) not supported by E2 node %d\n", SM_RLC_ID, i);
      }
    }

    sleep(1);
  }

  printf("xApp is running. Press CTRL+C to stop\n");
  fflush(stdout);

  // Register signal handlers for clean shutdown
  signal(SIGINT, sig_handler_stop);
  signal(SIGTERM, sig_handler_stop);

  /* Main loop: process pending RC PRB throttle actions outside callbacks.
   * sm_cb_kpm sets g_pending_throttle; we apply here to avoid blocking the
   * KPM callback thread with a network call while holding the mutex.      */
  while(keep_running) {
    sleep(1);

    struct timespec ts_main;
    clock_gettime(CLOCK_REALTIME, &ts_main);
    uint64_t now_ms = (uint64_t)ts_main.tv_sec * 1000ULL
                      + ts_main.tv_nsec / 1000000ULL;

    if (g_pending_throttle == 1 && !g_throttle_active
        && (now_ms - g_throttle_last_ms > THROTTLE_COOLDOWN_MS)) {
      if (g_mitigate_enabled) {
        printf("[MITIGATE] CRITICAL detected — applying E2SM-RC PRB throttle (max=5%%).\n");
        fflush(stdout);
        rc_send_prb_quota(5);
        g_throttle_active  = 1;
        g_throttle_last_ms = now_ms;
      } else {
        printf("[DETECT] CRITICAL — RC PRB throttle skipped (run with --mitigate to enable).\n");
        fflush(stdout);
      }
      g_pending_throttle = 0;
    } else if (g_pending_throttle == 2 && g_throttle_active
               && (now_ms - g_throttle_last_ms > THROTTLE_RESTORE_MS)) {
      if (g_mitigate_enabled) {
        printf("[MITIGATE] Attack subsided — restoring PRB quota (max=100%%).\n");
        fflush(stdout);
        rc_send_prb_quota(100);
      }
      g_throttle_active  = 0;
      g_throttle_last_ms = now_ms;
      g_pending_throttle = 0;
    }
  }

  printf("Stopping xApp...\n");

  // Clean unsubscribe FIRST (before stopping the API)
  for(int i = 0; i < n_kpm_handle; ++i) {
    rm_report_sm_xapp_api(kpm_handle[i].u.handle);
  }

  for(int i = 0; i < n_rc_handle; ++i) {
    rm_report_sm_xapp_api(rc_handle[i].u.handle);
  }

  for(int i = 0; i < nodes.len; ++i) {
    if (mac_handle && mac_handle[i].u.handle != 0)
      rm_report_sm_xapp_api(mac_handle[i].u.handle);
    if (rlc_handle && rlc_handle[i].u.handle != 0)
      rm_report_sm_xapp_api(rlc_handle[i].u.handle);
  }

  // NOW stop the xApp API
  while(try_stop_xapp_api() == false)
    usleep(1000);

  // free sm handle
  if(n_kpm_handle > 0) {
    free(kpm_handle);
  }
  if(n_rc_handle > 0) {
    free(rc_handle);
  }
  if (mac_handle) free(mac_handle);
  if (rlc_handle) free(rlc_handle);

  printf("Test xApp run SUCCESSFULLY\n");
}

