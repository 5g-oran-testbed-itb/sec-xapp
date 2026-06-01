# Grafana PRB + Mitigation Overlay Design

**Tanggal:** 2026-06-01  
**Status:** Approved

---

## Goal

Tambahkan visualisasi ke dua Grafana dashboard (`main` dan `testing`) yang menampilkan:
1. Grafik PRB DL/UL dengan **background shading** saat deteksi aktif (kuning = Warning, merah = Critical/Throttle)
2. Tiga stat panel **latency event terakhir**: Detect Latency, Confirm Latency, Total to Mitigate

---

## Architecture

Dua file yang diubah:

1. **`exporter/csv_exporter.py`** — tambah 3 Prometheus Gauge baru + tracking timestamp transisi stage
2. **`grafana/provisioning/dashboards/main.json`** — tambah 4 panel baru
3. **`grafana/provisioning/dashboards/testing.json`** — tambah 4 panel baru yang sama (di atas iframe)

Tidak ada perubahan pada C xApp, Prometheus config, atau docker-compose.

---

## Metric Baru (csv_exporter.py)

### Gauges

```python
g_latency_detect  = Gauge("xapp_latency_detect_ms",  "Stage 0→1 latency ms (last event)")
g_latency_confirm = Gauge("xapp_latency_confirm_ms", "Stage 1→2 latency ms (last event)")
g_latency_total   = Gauge("xapp_latency_total_ms",   "Stage 0→2 total latency ms (last event)")
```

### State Tracking

Tambahkan state dict di thread exporter:

```python
_stage_ts = {
    "t0": None,   # timestamp (time.monotonic()) saat stage terakhir kali masuk 0
    "t1": None,   # timestamp saat stage masuk 1
    "t2": None,   # timestamp saat stage masuk 2
    "prev_stage": 0,
}
```

### Logic Transisi

Di dalam loop `_update_metrics(row)`, setelah membaca `stage = int(row.get("stage2_confirmed", 0)) + int(row.get("stage1_alert", 0))` (atau gunakan `xapp_detection_stage` yang sudah ada):

```python
def _track_stage_latency(stage: int):
    prev = _stage_ts["prev_stage"]
    now = time.monotonic()
    if stage == prev:
        return
    if stage == 0:
        _stage_ts["t0"] = now
    elif stage == 1 and prev == 0:
        _stage_ts["t1"] = now
        if _stage_ts["t0"] is not None:
            g_latency_detect.set((now - _stage_ts["t0"]) * 1000)
    elif stage == 2 and prev == 1:
        _stage_ts["t2"] = now
        if _stage_ts["t1"] is not None:
            g_latency_confirm.set((now - _stage_ts["t1"]) * 1000)
        if _stage_ts["t0"] is not None:
            g_latency_total.set((now - _stage_ts["t0"]) * 1000)
    _stage_ts["prev_stage"] = stage
```

Panggil `_track_stage_latency(stage)` setiap kali row baru diproses.

**Catatan:** Gauge tidak di-reset saat kembali ke stage 0 — nilai tetap dari event terakhir (sticky), cocok untuk screenshot thesis.

---

## Panel Baru: Stat Latency (3 panel)

Sama persis di `main.json` dan `testing.json`.

| Panel | Metric | Unit | Thresholds |
|-------|--------|------|------------|
| DETECT LATENCY | `xapp_latency_detect_ms` | ms→s | <5000=green, <10000=yellow, ≥10000=red |
| CONFIRM LATENCY | `xapp_latency_confirm_ms` | ms→s | ≤5000=green, <15000=yellow, ≥15000=red |
| TOTAL TO MITIGATE | `xapp_latency_total_ms` | ms→s | <10000=green, <20000=yellow, ≥20000=red |

- **Type:** `stat`
- **Unit:** `durationMs` (Grafana unit — auto-format ke "3.1s")
- **Value mappings:** `0` → "—" (belum ada event)
- **Grid:** `w:4, h:4` masing-masing, berjajar horizontal
- **Slot ke-4:** panel spacer kosong `w:12` (atau diisi panel lain kelak)

---

## Panel Baru: PRB + Mitigation Overlay (1 panel)

### Queries

| Ref | Query | Legend |
|-----|-------|--------|
| A | `xapp_prb_dl_ratio` | PRB DL |
| B | `xapp_prb_ul_ratio` | PRB UL |
| C | `xapp_detection_stage / 2` | Stage (hidden) |

### Field Overrides untuk Series C (`xapp_detection_stage / 2`)

```json
{
  "matcher": {"id": "byName", "options": "Stage (hidden)"},
  "properties": [
    {"id": "custom.lineWidth",    "value": 0},
    {"id": "custom.fillOpacity",  "value": 20},
    {"id": "custom.hideFrom",     "value": {"legend": true, "tooltip": false, "viz": false}},
    {"id": "color.mode",          "value": "thresholds"},
    {"id": "thresholds",          "value": {
      "mode": "absolute",
      "steps": [
        {"color": "transparent", "value": null},
        {"color": "yellow",      "value": 0.45},
        {"color": "red",         "value": 0.9}
      ]
    }}
  ]
}
```

### Panel Config

- **Type:** `timeseries`
- **Default time range:** `now-10m` to `now`
- **Title:** `PRB DL / UL + Mitigation Stage — Last 10 min`
- **Grid:** `w:24, h:8`
- **Y axis:** 0–1.1 (untuk series A dan B; series C max=1.0)
- **Legend:** PRB DL, PRB UL saja (Stage hidden dari legend)

---

## Posisi Panel di main.json

Panel baru disisipkan setelah stat row yang ada:

| Y | Panel | w | h |
|---|-------|---|---|
| 0 | (existing) Detection Status, PRB DL, PRB UL, CQI, UL Air Delay | — | 4 |
| 4 | **DETECT LATENCY** | 4 | 4 |
| 4 | **CONFIRM LATENCY** | 4 | 4 |
| 4 | **TOTAL TO MITIGATE** | 4 | 4 |
| 4 | (spacer) | 12 | 4 |
| 8 | (existing) PRB Utilization — Last 5 min | 24 | 8 |
| 16 | **PRB DL/UL + Mitigation Stage — Last 10 min** | 24 | 8 |
| 24 | (existing) LSTM Anomaly Score | 24 | 8 |

---

## Posisi Panel di testing.json

Panel ditambahkan **di atas** iframe yang sudah ada:

| Y | Panel | w | h |
|---|-------|---|---|
| 0 | **DETECT LATENCY** | 4 | 4 |
| 0 | **CONFIRM LATENCY** | 4 | 4 |
| 0 | **TOTAL TO MITIGATE** | 4 | 4 |
| 0 | (spacer) | 12 | 4 |
| 4 | **PRB DL/UL + Mitigation Stage — Last 10 min** | 24 | 8 |
| 12 | (existing) Testing App iframe | 24 | 36 |

Iframe panel `y` digeser dari 0 → 12.

---

## Tidak Perlu

- Metric `xapp_throttle_active` — `xapp_detection_stage / 2` sudah cukup sebagai proxy
- Perubahan Prometheus config — scrape interval sudah 1s
- Perubahan docker-compose — volume mount tidak berubah
- Restart RIC/gNB/xApp — hanya exporter container yang perlu rebuild
