# Grafana Dashboard Design — sec-xapp Security Monitor

**Date:** 2026-05-20  
**Status:** Approved

---

## Overview

Two Grafana dashboards for the sec-xapp hybrid IDS (rule-based + LSTM autoencoder):

1. **Main Page** — Real-time KPM metrics from the live C xApp
2. **Testing Page** — Offline evaluation results (accuracy, recall, F1, latency, per-stage)

---

## Architecture

```
xapp_sec_moni (C binary)
    └─ writes → csv/training_YYYYMMDD_HHMMSS.csv  (live, ~120ms/row)

csv_exporter.py  (Python sidecar, Docker container)
    ├─ tails latest CSV → expose /metrics (Prometheus format, port 8000)
    └─ reads results/eval_results.json → expose testing metrics as Gauges

evaluate_detection.py --output results/eval_results.json  ← manual trigger

Prometheus  (scrape interval: 2s)
    └─ scrapes csv_exporter:8000/metrics

Grafana  (port 3000)
    ├─ Dashboard: Main Page
    └─ Dashboard: Testing Page
```

### Docker Compose Components

| Service | Image | Port | Purpose |
|---|---|---|---|
| `csv-exporter` | custom Python | 8000 | Tails CSV + serves Prometheus metrics |
| `prometheus` | prom/prometheus | 9090 | Scrapes exporter, stores time-series |
| `grafana` | grafana/grafana | 3000 | Visualizes dashboards |

**Volumes:**
- `./csv/` → mounted read-only into `csv-exporter` (live data)
- `./results/` → mounted read-only into `csv-exporter` (eval results)
- `./grafana/provisioning/` → mounted into Grafana (auto-provision datasource + dashboards)

---

## Prometheus Metrics Schema

### Main Page Metrics (updated ~120ms, from live CSV tail)

| Metric | Type | Description |
|---|---|---|
| `xapp_prb_dl_ratio` | Gauge | PRB DL utilization (0.0–1.0) |
| `xapp_prb_ul_ratio` | Gauge | PRB UL utilization (0.0–1.0) |
| `xapp_cqi` | Gauge | Channel Quality Indicator (0–15) |
| `xapp_rach_preamble` | Gauge | RACH preamble count per window |
| `xapp_air_delay_ul_ms` | Gauge | UL air interface delay (ms) |
| `xapp_anomaly_score` | Gauge | LSTM reconstruction error |
| `xapp_detection_stage` | Gauge | 0=Normal, 1=Warning, 2=Critical |
| `xapp_detection_rule` | Gauge | Last triggered rule (label: `rule`) |

### Testing Page Metrics (updated when eval_results.json changes)

| Metric | Labels | Description |
|---|---|---|
| `xapp_eval_accuracy` | `stage` | Accuracy (stage1/stage2/hybrid) |
| `xapp_eval_recall` | `stage`, `attack` | Recall per stage per attack |
| `xapp_eval_precision` | `stage`, `attack` | Precision per stage per attack |
| `xapp_eval_f1` | `stage`, `attack` | F1-Score per stage per attack |
| `xapp_eval_latency_ms` | `stage`, `attack` | Avg detection latency (ms) |
| `xapp_eval_fpr` | `stage` | False Positive Rate |

**Attack label values:** `ul_flood`, `dl_flood`, `burst`, `rrc_storm`, `rf_jammer`

---

## Dashboard 1 — Main Page

**Grafana theme:** Dark  
**Refresh interval:** 2s  
**Default time range:** Last 5 minutes

### Panel Layout

**Row 1 — Detection Status Banner** (full width)
- Type: Stat panel
- Metric: `xapp_detection_stage`
- Value mapping: 0→"NORMAL", 1→"WARNING", 2→"CRITICAL"
- Color thresholds: green (0), yellow (1), red (2)
- Shows: current stage + last triggered rule label

**Row 2 — Current Metrics** (4 panels, equal width)
- PRB DL: `xapp_prb_dl_ratio * 100` — Gauge + sparkline, red if >80%
- PRB UL: `xapp_prb_ul_ratio * 100` — Gauge + sparkline, red if >80%
- CQI: `xapp_cqi` — Stat panel, static (srsRAN always 15 in normal conditions)
- UL Air Delay: `xapp_air_delay_ul_ms` — Stat panel, red if >100ms

**Row 3 — Time-Series** (3/5 + 2/5 split)
- Left (PRB DL/UL): dual line chart, threshold annotation at 80%
- Right (LSTM Score): single line, red dashed threshold line at 0.5

**Row 4 — Event Timeline** (full width)
- Type: Grafana Annotations
- Source: annotation written by exporter each time `xapp_detection_stage` changes
- Markers: yellow=WARNING, red=CRITICAL, green=NORMAL (recovery)

---

## Dashboard 2 — Testing Page

**Grafana theme:** Dark  
**Refresh interval:** 30s (static data, only changes on manual trigger)

### Panel Layout

**Row 1 — Overall Hybrid IDS Stats** (5 panels, equal width)

Overall metrics come from `per_stage.hybrid.*` in the JSON (not per-attack). Exporter exposes these without an `attack` label:
- Accuracy: `xapp_eval_accuracy{stage="hybrid"}`
- Recall: `xapp_eval_recall{stage="hybrid"}`
- Precision: `xapp_eval_precision{stage="hybrid"}`
- F1-Score: `xapp_eval_f1{stage="hybrid"}`
- Avg Latency: `xapp_eval_latency_ms{stage="hybrid"}`

**Row 2 — Per-Attack Breakdown** (3/5 + 2/5 split)
- Left: Table panel — columns: Attack Type, Recall, Precision, F1, Latency, Detecting Stage
  - Rows: UL Flood, DL Flood, Burst ON/OFF, RRC Storm, RF Jammer
  - Color coding: F1 ≥95% green, 80–95% yellow, <80% red
- Right: Bar chart — F1-Score per attack, horizontal bars, color per attack type

**Row 3 — Per-Stage Comparison** (3 equal columns)
- Stage 1 (Rule-Based): Accuracy, Recall, F1, Avg Latency
- Stage 2 (LSTM): Accuracy, Recall, F1, Avg Latency
- Hybrid ★: Accuracy, Recall, F1, Avg Latency (highlighted with green border)

---

## File Structure

```
sec-xapp/
├── docker-compose.yml
├── results/
│   └── eval_results.json          ← output dari evaluate_detection.py --output
├── exporter/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── csv_exporter.py            ← Python Prometheus exporter
├── prometheus/
│   └── prometheus.yml             ← scrape config
└── grafana/
    └── provisioning/
        ├── datasources/
        │   └── prometheus.yml
        └── dashboards/
            ├── dashboards.yml
            ├── main.json          ← Main page dashboard
            └── testing.json       ← Testing page dashboard
```

---

## evaluate_detection.py Changes

Add `--output <path>` argument:
- Writes `eval_results.json` with structure:
  ```json
  {
    "timestamp": "2026-05-20T10:00:00",
    "dataset": "dataset_testing_v2_with_empty.csv",
    "per_stage": {
      "stage1": {"accuracy": 0.912, "recall": 0.941, "precision": 0.915, "f1": 0.928, "fpr": 0.008, "latency_ms": 300},
      "stage2": {"accuracy": 0.875, "recall": 0.853, "precision": 0.870, "f1": 0.861, "fpr": 0.005, "latency_ms": 520},
      "hybrid": {"accuracy": 0.941, "recall": 0.962, "precision": 0.958, "f1": 0.960, "fpr": 0.005, "latency_ms": 320}
    },
    "per_attack": {
      "ul_flood":   {"stage1": {...}, "stage2": {...}, "hybrid": {"recall": 0.991, "precision": 0.985, "f1": 0.988, "latency_ms": 280}},
      "dl_flood":   {"hybrid": {"recall": 0.983, "precision": 0.975, "f1": 0.979, "latency_ms": 310}},
      "burst":      {"hybrid": {"recall": 0.799, "precision": 0.831, "f1": 0.812, "latency_ms": 520}},
      "rrc_storm":  {"hybrid": {"recall": 0.974, "precision": 0.962, "f1": 0.968, "latency_ms": 290}},
      "rf_jammer":  {"hybrid": {"recall": 0.623, "precision": 0.684, "f1": 0.651, "latency_ms": 480}}
    }
  }
  ```

---

## Constraints & Notes

- CSV file is written by C binary to `csv/training_*.csv` — exporter must detect and tail the **newest** file dynamically (filename changes on each xApp restart)
- Scrape interval 2s is safe; CSV rows arrive ~120ms apart, so exporter exposes latest row values
- CQI is always 15 in srsRAN under normal conditions — this is expected, not a bug
- DRB (RLC) metrics are always 0 in srsRAN — do NOT include throughput panels
- Annotations for detection events: the exporter pushes to Grafana HTTP Annotations API (`POST /api/annotations`) each time `xapp_detection_stage` changes. Requires Grafana API key (service account token) passed as env var `GRAFANA_TOKEN` to the exporter container
- ONNX IR version warning on startup is non-functional — ignore in dashboard
