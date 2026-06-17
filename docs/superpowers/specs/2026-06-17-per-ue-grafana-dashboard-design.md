# Per-UE Grafana Dashboard Design
**Date:** 2026-06-17  
**Approach:** Opsi A — extend `csv_exporter.py`, dua dashboard JSON baru  
**Consistent with:** existing `main.json` (live) + `eval.json` (evaluation) pattern

---

## 1. Scope

Tambah monitoring per-UE IDS v4 (GRU-UE v4, seq_len=30, 19 fitur, threshold P97=0.025969) ke Grafana stack yang sudah ada. Dua dashboard terpisah:

| File | UID | Fungsi |
|------|-----|--------|
| `grafana/provisioning/dashboards/per_ue_live.json` | `xapp-ue-live` | Live monitoring per-RNTI saat xApp running |
| `grafana/provisioning/dashboards/per_ue_eval.json` | `xapp-ue-eval` | Offline evaluation results per-UE v4 (5 configs) |

---

## 2. Exporter Extension (`exporter/csv_exporter.py`)

### 2.1 New Prometheus Gauges

Semua gauge per-UE pakai label `rnti` (hex string, e.g. `"0x1a2b"`).

**Alert gauges** (dari `ue_alerts_*.csv`, event-driven):
```python
xapp_ue_mse{rnti}          # MSE reconstruction error saat alert fire
xapp_ue_alert_type{rnti}   # 0=none 1=ul_flood 2=dl_flood 3=burst 4=roq
xapp_ue_stage{rnti}        # rule_stage: 0/1/2
```

**Feature gauges** (dari per-UE feature CSV, continuous ~800ms):
```python
xapp_ue_thp_ul_kbps{rnti}
xapp_ue_thp_dl_kbps{rnti}
xapp_ue_prb_ul{rnti}
xapp_ue_prb_dl{rnti}
xapp_ue_prb_direction{rnti}
xapp_ue_ul_efficiency{rnti}
```

> **Catatan:** Per-UE feature CSV (`per_ue_training_*.csv`) menggunakan schema 15-fitur (training collector). Burst indices (fitur 16–19) dihitung di C oleh `ue_ids_update()` tapi tidak ditulis ke CSV ini — sehingga tidak tersedia untuk exporter. Panel burst index dihapus dari live dashboard.

**Eval gauges** (static, loaded at startup dari `KNOWN_EVAL_UE`):
```python
xapp_ue_eval_recall_v4{config, attack}   # attack: all/ul_flood/dl_flood/burst/roq
xapp_ue_eval_f1_v4{config, attack}
xapp_ue_eval_fpr_v4{config}
xapp_ue_eval_det_lat_v4{config}          # detection latency seconds
xapp_ue_eval_mit_lat_v4{config}          # mitigation latency seconds
```

### 2.2 New Background Threads

**`ue_alert_tail_loop()`**
- Pattern: `glob(CSV_DIR + "/ue_alerts_*.csv")` → newest file
- Same tail-switching logic as `csv_tail_loop()`
- CSV columns: `timestamp_ms, rnti, rule_mask, rule_stage, mse, threshold, alert_type`
- On each row: update `xapp_ue_mse`, `xapp_ue_alert_type`, `xapp_ue_stage` for that rnti
- Poll interval: same `POLL_INTERVAL` (100ms)

**`ue_feature_tail_loop()`**
- Pattern: `glob(CSV_DIR + "/per_ue_training_*.csv")` → newest file
- CSV columns include: `rnti, thp_dl_kbps, thp_ul_kbps, prb_usage_dl_ratio, prb_usage_ul_ratio, thp_ul_burst_index, prb_ul_burst_index`
- On each row: update feature gauges for that rnti
- Poll interval: 500ms (feature CSV updates ~800ms, no need for 100ms)

### 2.3 Static Eval Data (`KNOWN_EVAL_UE`)

Values dari `docs/STATUS_DAN_RENCANA_EVALUASI.md` §1.6c:

```python
KNOWN_EVAL_UE = {
    "rule_only":   {"recall": 0.858, "f1": 0.913, "fpr": 0.0293, "det_lat": 4.67, "mit_lat": 4.79,
                    "ul_flood": 0.979, "dl_flood": 0.968, "burst": 0.988, "roq": 0.922},
    "lstm_only":   {"recall": 0.910, "f1": 0.928, "fpr": 0.0305, "det_lat": 12.21, "mit_lat": 12.33, ...},
    "gru_only":    {"recall": 0.896, "f1": 0.921, "fpr": 0.0305, "det_lat": 10.46, "mit_lat": 10.58, ...},
    "lstm_hybrid": {"recall": 0.950, "f1": 0.948, "fpr": 0.0497, "det_lat": 4.67,  "mit_lat": 4.79,  ...},
    "gru_hybrid":  {"recall": 0.961, "f1": 0.954, "fpr": 0.0514, "det_lat": 4.04,  "mit_lat": 4.16,  ...},
}
```

Per-attack recall (GRU Hybrid only, dari §1.6c): UL Flood 97.9%, DL Flood 96.8%, Burst 98.8%, RoQ 92.2%.  
Config lain (rule_only, lstm_only, gru_only, lstm_hybrid) hanya punya overall recall di STATUS doc — per-attack breakdown tidak tersedia, gauge `attack!="all"` tidak di-populate untuk config tersebut.

New function `_populate_eval_ue_v4()` dipanggil di `main()` setelah `_populate_eval_v2()`.

### 2.4 Implementation Notes

- Tidak ada perubahan `docker-compose.yml` atau `prometheus.yml`
- RNTI di-expose sebagai hex string label: `format(rnti_int, "#06x")`
- Gauge per-RNTI persist sampai xApp restart — acceptable karena RNTI bisa recycle tapi dalam satu sesi konsisten
- `ue_alert_tail_loop` dan `ue_feature_tail_loop` dimulai sebagai daemon thread di `main()`
- Per-UE feature CSV actual column names: `prb_usage_dl_ratio, prb_usage_ul_ratio, thp_dl_kbps, thp_ul_kbps, prb_direction, prb_total, prb_ul_delta, ul_efficiency, prb_ul_roll_mean, prb_ul_roll_std, ul_persistence, thp_total_kbps, thp_ul_delta, thp_dl_delta, traffic_direction, label`

---

## 3. Dashboard: Per-UE Live (`per_ue_live.json`)

**UID:** `xapp-ue-live` | **Refresh:** 5s | **Window:** now-10m  
**Tags:** `["xapp", "security", "per-ue", "live"]`

### Layout (24-column grid)

| ID | Type | Title | Query | GridPos |
|----|------|--------|-------|---------|
| 1 | stat | Active RNTIs | `count(xapp_ue_thp_ul_kbps > 0)` | x=0 y=0 w=4 h=4 |
| 2 | stat | Alerted RNTIs | `count(xapp_ue_alert_type > 0)` | x=4 y=0 w=4 h=4 |
| 3 | stat | Selected UE Alert Status | `xapp_ue_alert_type{rnti=~"$rnti"}` + value mapping | x=8 y=0 w=8 h=4 |
| 5 | timeseries | Detection Stage per UE | `xapp_ue_stage` (all rnti) | x=0 y=4 w=12 h=6 |
| 6 | timeseries | MSE Score per UE (on alert) | `xapp_ue_mse` + thresh line 0.025969 | x=12 y=4 w=12 h=6 |
| 7 | timeseries | Throughput UL/DL per UE | `xapp_ue_thp_ul_kbps`, `xapp_ue_thp_dl_kbps` | x=0 y=10 w=12 h=6 |
| 8 | timeseries | PRB Utilization per UE | `xapp_ue_prb_ul`, `xapp_ue_prb_dl` | x=12 y=10 w=12 h=6 |
| 9 | timeseries | PRB Direction & Efficiency per UE | `xapp_ue_prb_direction`, `xapp_ue_ul_efficiency` (dari per-UE feature CSV) | x=0 y=16 w=24 h=6 |

**Template variable:** `$rnti` — multi-select dropdown dari `label_values(xapp_ue_thp_ul_kbps, rnti)`. Panel 3 dan 4 masing-masing bisa filter ke RNTI tertentu dari dropdown yang sama.

**Value mappings for alert_type:** 0=Normal (green), 1=UL Flood (orange), 2=DL Flood (orange), 3=Burst (yellow), 4=RoQ (red)

---

## 4. Dashboard: Per-UE Evaluation (`per_ue_eval.json`)

**UID:** `xapp-ue-eval` | **Refresh:** 1m | **Window:** now-5m  
**Tags:** `["xapp", "security", "per-ue", "evaluation"]`

### Layout

| ID | Type | Title | Query | GridPos |
|----|------|--------|-------|---------|
| 1 | text | Banner | Dataset + model info markdown | x=0 y=0 w=24 h=3 |
| 10 | stat | rule_only | recall+f1+fpr | x=0 y=3 w=4 h=5 |
| 11 | stat | lstm_only | recall+f1+fpr | x=4 y=3 w=4 h=5 |
| 12 | stat | gru_only | recall+f1+fpr | x=8 y=3 w=4 h=5 |
| 13 | stat | lstm_hybrid | recall+f1+fpr | x=12 y=3 w=4 h=5 |
| 14 | stat | **gru_hybrid** ✅ | recall+f1+fpr | x=16 y=3 w=8 h=5 |
| 20 | barchart | Per-Attack Recall — GRU Hybrid | `xapp_ue_eval_recall_v4{config="gru_hybrid",attack!="all"}` — 4 attack types | x=0 y=8 w=12 h=7 |
| 21 | barchart | Overall Recall — All 5 Configs | `xapp_ue_eval_recall_v4{attack="all"}` | x=12 y=8 w=12 h=7 |
| 30 | barchart | Detection Latency per Config | `xapp_ue_eval_det_lat_v4` | x=0 y=15 w=12 h=5 |
| 31 | barchart | Mitigation Latency per Config | `xapp_ue_eval_mit_lat_v4` | x=12 y=15 w=12 h=5 |

**Banner content:**
```
## Per-UE IDS v4 — Evaluation Results
Dataset: `csv/dataset_attack_ue_juni.csv` · 4 attack classes · interval 1s/sample  
Model aktif: **GRU-UE v4** (BiGRU [64,32], seq_len=30, 19 fitur, Weighted MSE Scheme A, threshold=0.025969)  
Konfigurasi terbaik: **gru_hybrid** — Recall 96.1%, F1 95.4%, FPR 5.14%, Det.Lat 4.04s
```

**gru_hybrid stat panel** lebih lebar (w=8) dan warnanya hijau untuk highlight sebagai best config, konsisten dengan cara `eval.json` highlight Hybrid R+LSTM.

---

## 5. Files Changed

| File | Perubahan |
|------|-----------|
| `exporter/csv_exporter.py` | +2 threads, +12 gauges, +`KNOWN_EVAL_UE` dict, +`_populate_eval_ue_v4()` |
| `grafana/provisioning/dashboards/per_ue_live.json` | **Baru** — 9 panels |
| `grafana/provisioning/dashboards/per_ue_eval.json` | **Baru** — 8 panels |

Tidak ada perubahan: `docker-compose.yml`, `prometheus.yml`, `dashboards.yml`, C xApp source.

---

## 6. Out of Scope

- Per-UE MSE continuous (bukan hanya saat alert) — butuh C xApp modification (Opsi C)
- Auto-discovery RNTI tanpa template variable — Prometheus label cardinality concern
- Historical per-UE data retention — bergantung pada Prometheus retention setting yang sudah ada
