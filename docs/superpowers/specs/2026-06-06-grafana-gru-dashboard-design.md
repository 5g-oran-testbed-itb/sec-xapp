# Grafana GRU Dashboard — Design Spec
**Tanggal:** 2026-06-06  
**Scope:** Tambah GRU live inference + dua dashboard terpisah (Live Monitoring & Evaluation Results)

---

## 1. Ringkasan

Sistem saat ini hanya menampilkan LSTM anomaly score di Grafana. Spec ini menambahkan:
1. **GRU live inference** (thread dalam `csv_exporter.py`) dengan model GRU-A + GRU-B tuned
2. **Dashboard 1** — Live Monitoring: network metrics, LSTM score, GRU scores, alert status
3. **Dashboard 2** — Evaluation Results: Rule vs LSTM vs GRU Tuned vs Hybrid, per-attack recall, FPR, F1

---

## 2. Arsitektur

```
CSV (C xApp live)
    │
    ├── csv_tail_loop() [100ms]         → LSTM metrics → Prometheus :8000
    │       reads: prb_*, anomaly_score, stage1_alert, stage2_confirmed, alert_type
    │
    └── gru_inference_loop() [1000ms]   → GRU metrics → Prometheus :8000
            reads: same CSV (latest row)
            runs: GRU-A (seq_len=10) + GRU-B (seq_len=30) inference via PyTorch
            thresholds: A=0.002881, B=0.003363

Prometheus scrape :8000 → Grafana
    ├── Dashboard 1: xapp_grafana/main.json      (live monitoring)
    └── Dashboard 2: xapp_grafana/eval.json      (evaluation results)
```

---

## 3. Perubahan `csv_exporter.py`

### 3.1 Prometheus Gauges Baru

```python
# GRU scores
g_gru_a       = Gauge("xapp_gru_score_a",    "GRU-A reconstruction error (raw)")
g_gru_b       = Gauge("xapp_gru_score_b",    "GRU-B reconstruction error (raw)")
g_gru_stage   = Gauge("xapp_gru_stage",      "GRU stage: 0=normal 1=warn 2=crit")

# Alert type (dari kolom alert_type di CSV)
g_alert_type  = Gauge("xapp_alert_type",     "Alert type: 0=none 1=ul_flood 2=dl_flood 3=burst 4=rrc_storm")

# Fitur penting yang sudah dibaca tapi belum di-expose
g_empty_ind   = Gauge("xapp_empty_ind_rate", "RRC empty indication rate (per window)")
g_burst_idx   = Gauge("xapp_prb_burst_index","PRB burst index (log ratio)")
```

> `xapp_rach_preamble` sudah ada, tinggal pastikan di-expose.

### 3.2 Konstanta GRU

```python
GRU_MODEL_A    = os.getenv("GRU_MODEL_A", "/data/models/gru_autoencoder_A_v1.pt")
GRU_MODEL_B    = os.getenv("GRU_MODEL_B", "/data/models/gru_autoencoder_B_v1.pt")
GRU_SCALER     = os.getenv("GRU_SCALER",  "/data/models/scaler_gru.pkl")
GRU_THRESH_A   = 0.002881
GRU_THRESH_B   = 0.003363
GRU_SEQ_A      = 10   # seq_len GRU-A
GRU_SEQ_B      = 30   # seq_len GRU-B
GRU_FEATURES   = 16   # fitur GRU (subset dari 25 LSTM features)
GRU_POLL_SEC   = 1.0  # polling interval (lebih lambat dari LSTM 100ms)
```

### 3.3 GRU Inference Thread

```
gru_inference_loop():
    1. Load GRU-A, GRU-B, scaler_gru.pkl saat startup
       → Jika file tidak ada: log warning, skip (LSTM loop tidak terpengaruh)
    2. Maintain deque_a (maxlen=10) dan deque_b (maxlen=30)
    3. Tiap 1s:
       a. Baca latest_row dari g_latest_row (shared state dengan csv_tail_loop)
       b. Extract 16 GRU features, transform via scaler
       c. Append ke deque_a dan deque_b
       d. Jika len(deque_a) == 10: run GRU-A inference → g_gru_a.set(score_a)
       e. Jika len(deque_b) == 30: run GRU-B inference → g_gru_b.set(score_b)
       f. gru_stage = 2 jika score_a > GRU_THRESH_A OR score_b > GRU_THRESH_B
          gru_stage = 1 jika score_a > GRU_THRESH_A*0.5 OR score_b > GRU_THRESH_B*0.5 (warn zone)
          gru_stage = 0 otherwise
       g. g_gru_stage.set(gru_stage)
```

**Shared state:** `csv_tail_loop` menyimpan `g_latest_row` (dict) yang dibaca `gru_inference_loop`. Diproteksi dengan `threading.Lock`.

**Graceful degradation:** Jika PyTorch tidak tersedia atau model corrupt, thread GRU log error dan exit — semua gauge GRU tetap di 0, tidak raise ke main thread.

### 3.4 Perubahan `csv_tail_loop`

Tambahkan ke baris parse:
```python
g_empty_ind.set(row.get("empty_ind_rate", 0.0))
g_burst_idx.set(row.get("prb_burst_index", 0.0))

alert_map = {"none": 0, "ul_flood": 1, "dl_flood": 2, "burst": 3, "rrc_storm": 4}
g_alert_type.set(alert_map.get(row.get("alert_type", "none"), 0))
```

### 3.5 Eval Metrics Baru

Load dari dua JSON: `eval_results_attack_mei.json` + `eval_results_gru_ensemble_v1.json`

```python
# Label values untuk model:
# "rule", "lstm", "gru_tuned", "hybrid_lstm", "hybrid_gru"

g_eval_recall_v2    = Gauge("xapp_eval_recall_v2",    "Eval recall",    ["model", "attack"])
g_eval_fpr_v2       = Gauge("xapp_eval_fpr_v2",       "Eval FPR",       ["model", "stage"])
g_eval_f1_v2        = Gauge("xapp_eval_f1_v2",        "Eval F1",        ["model", "attack"])
g_eval_precision_v2 = Gauge("xapp_eval_precision_v2", "Eval precision", ["model"])
```

> Suffix `_v2` untuk menghindari konflik dengan gauge lama.

---

## 4. `docker-compose.yml` — Perubahan

Tambah mount di service `csv-exporter`:
```yaml
volumes:
  - ./models:/data/models:ro          # GRU models + scaler_gru.pkl
  - ./results:/data/results:ro        # eval JSONs (sudah ada)
environment:
  - GRU_MODEL_A=/data/models/gru_autoencoder_A_v1.pt
  - GRU_MODEL_B=/data/models/gru_autoencoder_B_v1.pt
  - GRU_SCALER=/data/models/scaler_gru.pkl
  - EVAL_JSON_GRU=/data/results/eval_results_gru_ensemble_v1.json
  - EVAL_JSON_LSTM=/data/results/eval_results_attack_mei.json
```

Tambah ke `requirements.txt`:
```
torch>=2.0.0
scikit-learn>=1.3.0   # untuk pickle scaler_gru.pkl
```

---

## 5. Dashboard 1 — Live Monitoring (`main.json`)

**Refresh:** 5s auto-refresh. **Time range default:** last 10 min.

### Row 1 — Status Bar (height: 4, stat panels)
| Panel | Query | Thresholds |
|-------|-------|------------|
| Detection Stage (LSTM) | `xapp_detection_stage` | 0=green, 1=yellow, 2=red |
| Detection Stage (GRU) | `xapp_gru_stage` | 0=green, 1=yellow, 2=red |
| Alert Type | `xapp_alert_type` | value map: 0=None, 1=UL Flood, 2=DL Flood, 3=Burst, 4=RRC Storm |
| Detect Latency | `xapp_latency_detect_ms` | — |
| Confirm Latency | `xapp_latency_confirm_ms` | — |
| Total to Mitigate | `xapp_latency_total_ms` | — |

### Row 2 — Network Metrics (timeseries, last 10 min)
| Panel | Queries | Keterangan |
|-------|---------|------------|
| PRB Utilization | `xapp_prb_dl_ratio`, `xapp_prb_ul_ratio` | Threshold line: 70% |
| Signaling & Burst | `xapp_rach_preamble`, `xapp_empty_ind_rate`, `xapp_prb_burst_index` | Y-axis: 0–10 |

### Row 3 — Detection Scores (timeseries, last 10 min)
| Panel | Queries | Keterangan |
|-------|---------|------------|
| LSTM Anomaly Score | `xapp_anomaly_score` | Threshold line: 0.21 (v16), label fix dari "0.5" |
| GRU Score A & B | `xapp_gru_score_a`, `xapp_gru_score_b` | Threshold lines: A=0.002881, B=0.003363 |

### Row 4 — Stage Timeline (timeseries, last 30 min)
- Satu panel lebar penuh
- Queries: `xapp_prb_dl_ratio`, `xapp_prb_ul_ratio`, `xapp_detection_stage/2`, `xapp_gru_stage/2`
- Grafana annotations: stage change events (sudah ada via `push_grafana_annotation`)

---

## 6. Dashboard 2 — Evaluation Results (`eval.json`)

**Refresh:** 1 min (data jarang berubah). **No time range** (stat panels dari nilai terkini).

### Row 1 — Overall Summary (stat panels, 5 kolom)
Setiap kolom = satu model, tiga baris stat: Recall / F1 / FPR Stage1

| Model | Recall | F1 | FPR Stage1 |
|-------|--------|----|------------|
| Rule-Based | 97.7% | 0.981 | 1.40% |
| LSTM Ensemble | 79.2% | 0.855 | 6.27% |
| GRU Tuned | 93.2% | 0.930 | 5.30% |
| Hybrid Rule+LSTM | 98.3% | 0.962 | 1.37%* |
| Hybrid Rule+GRU | 98.2% | 0.977 | 2.87% |

*FPR Stage2

### Row 2 — Per-Attack Recall (bar chart, grouped)
- Type: `bar chart`, orientation: horizontal
- X-axis: nilai recall (0–1)
- Y-axis: UL Flood · DL Flood · Burst ON/OFF · RRC Storm
- Series (5): Rule / LSTM / GRU Tuned / Hybrid R+LSTM / Hybrid R+GRU
- Query: `xapp_eval_recall_v2{model=~".*", attack=~".*"}`

### Row 3 — FPR & F1 Side-by-Side (dua panel)
| Panel | Query | Keterangan |
|-------|-------|------------|
| FPR Stage1 (bar) | `xapp_eval_fpr_v2{stage="stage1"}` | Lower is better · threshold line: 2% |
| F1 Score (bar) | `xapp_eval_f1_v2` | Higher is better |

---

## 7. File yang Dimodifikasi / Dibuat

| File | Aksi |
|------|------|
| `exporter/csv_exporter.py` | Modifikasi — GRU thread, gauge baru, eval metrics v2 |
| `exporter/requirements.txt` | Modifikasi — tambah torch, scikit-learn |
| `docker-compose.yml` | Modifikasi — mount models/, env vars GRU |
| `grafana/provisioning/dashboards/main.json` | Modifikasi — Row 1–4 baru |
| `grafana/provisioning/dashboards/eval.json` | Buat baru — Dashboard 2 |
| `grafana/provisioning/dashboards/dashboards.yml` | Modifikasi — daftarkan eval.json |

---

## 8. GRU Features (16 fitur untuk scaler_gru.pkl)

Urutan harus sama persis dengan saat scaler di-fit:
```
prb_usage_dl_ratio, prb_usage_ul_ratio, cqi, rach_preamble,
air_delay_ul, prb_direction, prb_total, prb_dl_delta, prb_ul_delta,
prb_burst_index, prb_dl_roll_mean, prb_dl_roll_std,
prb_ul_roll_std, prb_ul_roll_max, prb_ul_roll_max_100, empty_ind_rate
```

> Verifikasi urutan ini terhadap `models/scaler_gru.pkl` saat implementasi.

---

## 9. Out of Scope

- GRU Stage 2 confirmation (5s consecutive) — tidak diimplementasi di sidecar, hanya raw stage
- Mitigation trigger dari GRU — tetap hanya dari LSTM/Rule di C xApp
- Unknown attack evaluation panels — belum ada data (E1–E5 belum dijalankan)
- Testing dashboard (`testing.json`) — tidak diubah
