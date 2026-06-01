# Grafana PRB + Mitigation Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tambahkan panel PRB + mitigation shading dan 3 stat latency ke dashboard `main` dan `testing` di Grafana, dengan metric latency baru di exporter.

**Architecture:** `csv_exporter.py` ditambah 3 Gauge baru + fungsi `_track_stage_latency()` yang meng-update timestamp saat stage berubah. `main.json` dan `testing.json` masing-masing ditambah 4 panel baru via script Python (safer dari edit manual JSON). Panel PRB+shading menggunakan `xapp_detection_stage / 2` sebagai fill series dengan threshold coloring.

**Tech Stack:** Python 3, prometheus_client, Grafana 10.4 JSON dashboard provisioning, Docker Compose.

---

## File Structure

| File | Perubahan |
|------|-----------|
| `exporter/csv_exporter.py` | Tambah 3 Gauge + `_stage_ts` dict + `_track_stage_latency()` + panggil di `csv_tail_loop()` |
| `exporter/test_csv_exporter.py` | Tambah 3 test untuk `_track_stage_latency()` |
| `grafana/provisioning/dashboards/main.json` | Tambah 4 panel (via script), shift panel lama ke bawah |
| `grafana/provisioning/dashboards/testing.json` | Tambah 4 panel (via script), shift iframe ke bawah |

---

## Task 1: Latency Metrics di csv_exporter.py

**Files:**
- Modify: `exporter/csv_exporter.py:32-38` (tambah Gauge), `:97` (tambah dict+fungsi), `:240-242` (panggil fungsi)
- Test: `exporter/test_csv_exporter.py`

- [ ] **Step 1: Tulis failing tests**

Tambahkan ke akhir `exporter/test_csv_exporter.py`:

```python
# ── Latency tracking tests ───────────────────────────────────────────────────

def test_track_latency_detect_on_stage0_to_1(monkeypatch):
    """Stage 0→1 setelah 3.1s → g_latency_detect = 3100 ms."""
    import csv_exporter
    csv_exporter._stage_ts.update({"t0": None, "t1": None, "t2": None})
    t = [0.0]
    monkeypatch.setattr(csv_exporter.time, "monotonic", lambda: t[0])

    csv_exporter._track_stage_latency(0, -1)   # record t0
    t[0] = 3.1
    csv_exporter._track_stage_latency(1, 0)    # detect: 3100ms

    assert csv_exporter.g_latency_detect._value.get() == pytest.approx(3100.0, rel=1e-3)


def test_track_latency_confirm_and_total_on_stage1_to_2(monkeypatch):
    """Stage 0→1→2: confirm = 5000ms, total = 8000ms."""
    import csv_exporter
    csv_exporter._stage_ts.update({"t0": None, "t1": None, "t2": None})
    t = [0.0]
    monkeypatch.setattr(csv_exporter.time, "monotonic", lambda: t[0])

    csv_exporter._track_stage_latency(0, -1)   # t0=0
    t[0] = 3.0
    csv_exporter._track_stage_latency(1, 0)    # t1=3.0
    t[0] = 8.0
    csv_exporter._track_stage_latency(2, 1)    # t2=8.0

    assert csv_exporter.g_latency_confirm._value.get() == pytest.approx(5000.0, rel=1e-3)
    assert csv_exporter.g_latency_total._value.get() == pytest.approx(8000.0, rel=1e-3)


def test_track_latency_noop_when_stage_unchanged(monkeypatch):
    """Memanggil dengan stage sama → gauge tidak berubah."""
    import csv_exporter
    csv_exporter._stage_ts.update({"t0": None, "t1": None, "t2": None})
    t = [0.0]
    monkeypatch.setattr(csv_exporter.time, "monotonic", lambda: t[0])

    before = csv_exporter.g_latency_detect._value.get()
    csv_exporter._track_stage_latency(0, 0)    # no-op
    assert csv_exporter.g_latency_detect._value.get() == before
```

- [ ] **Step 2: Jalankan test — verifikasi FAIL**

```bash
cd /home/telmat/sec-xapp/exporter
/home/telmat/xapp/venv/bin/python3 -m pytest test_csv_exporter.py::test_track_latency_detect_on_stage0_to_1 -v
```

Expected: `FAILED` dengan `AttributeError: module 'csv_exporter' has no attribute '_track_stage_latency'`

- [ ] **Step 3: Implementasi — tambah Gauge definitions**

Di `exporter/csv_exporter.py`, setelah baris 38 (baris `g_stage = Gauge(...)`), tambahkan:

```python
g_latency_detect  = Gauge("xapp_latency_detect_ms",  "Stage 0→1 detection latency ms (last event)")
g_latency_confirm = Gauge("xapp_latency_confirm_ms", "Stage 1→2 confirmation latency ms (last event)")
g_latency_total   = Gauge("xapp_latency_total_ms",   "Total Stage 0→2 mitigation latency ms (last event)")
```

- [ ] **Step 4: Implementasi — tambah state dict + fungsi**

Di `exporter/csv_exporter.py`, setelah baris `WINDOW_SIZE = 10` (sekitar baris 60), tambahkan:

```python
_stage_ts: dict = {"t0": None, "t1": None, "t2": None}


def _track_stage_latency(stage: int, prev_stage: int) -> None:
    """Update latency gauges on stage transitions. No-op if stage unchanged."""
    if stage == prev_stage:
        return
    now = time.monotonic()
    if stage == 0:
        _stage_ts["t0"] = now
    elif stage == 1 and prev_stage == 0:
        _stage_ts["t1"] = now
        if _stage_ts["t0"] is not None:
            g_latency_detect.set((now - _stage_ts["t0"]) * 1000)
    elif stage == 2 and prev_stage == 1:
        _stage_ts["t2"] = now
        if _stage_ts["t1"] is not None:
            g_latency_confirm.set((now - _stage_ts["t1"]) * 1000)
        if _stage_ts["t0"] is not None:
            g_latency_total.set((now - _stage_ts["t0"]) * 1000)
```

- [ ] **Step 5: Implementasi — panggil di csv_tail_loop**

Di `exporter/csv_exporter.py`, cari blok ini (sekitar baris 240):

```python
                if stage != prev_stage:
                    push_grafana_annotation(stage, prev_stage)
                    prev_stage = stage
```

Ganti dengan:

```python
                if stage != prev_stage:
                    push_grafana_annotation(stage, prev_stage)
                    _track_stage_latency(stage, prev_stage)
                    prev_stage = stage
```

- [ ] **Step 6: Jalankan semua tests — verifikasi PASS**

```bash
cd /home/telmat/sec-xapp/exporter
/home/telmat/xapp/venv/bin/python3 -m pytest test_csv_exporter.py -v
```

Expected: semua test `PASSED` (termasuk 3 test baru + test lama yang sudah ada)

- [ ] **Step 7: Commit**

```bash
git add exporter/csv_exporter.py exporter/test_csv_exporter.py
git commit -m "feat: add detection/mitigation latency metrics to exporter"
```

---

## Task 2: Update main.json — Tambah 4 Panel Baru

**Files:**
- Modify: `grafana/provisioning/dashboards/main.json`

Layout final setelah perubahan:

| Y  | Panel | w | h |
|----|-------|---|---|
| 0  | Detection Status (existing) | 24 | 4 |
| 3  | PRB DL, PRB UL, CQI, UL Air Delay (existing) | 6 each | 4 |
| 7  | **DETECT LATENCY** (id=8) | 4 | 4 |
| 7  | **CONFIRM LATENCY** (id=9) | 4 | 4 |
| 7  | **TOTAL TO MITIGATE** (id=10) | 4 | 4 |
| 11 | **PRB + Mitigation Shading** (id=11) | 24 | 8 |
| 19 | PRB Utilization 5min (existing, shifted) | 15 | 8 |
| 19 | LSTM Anomaly Score (existing, shifted) | 9 | 8 |

- [ ] **Step 1: Jalankan script untuk update main.json**

```bash
cd /home/telmat/sec-xapp
/home/telmat/xapp/venv/bin/python3 - << 'PYEOF'
import json

DS = {"type": "prometheus", "uid": "prometheus"}

def stat_panel(pid, title, expr, x, thresholds):
    return {
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "unit": "ms",
                "thresholds": {"mode": "absolute", "steps": thresholds},
                "mappings": [{"type": "value", "options": {"0": {"text": "—", "index": 0}}}]
            },
            "overrides": []
        },
        "gridPos": {"h": 4, "w": 4, "x": x, "y": 7},
        "id": pid,
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto"
        },
        "title": title,
        "type": "stat",
        "targets": [{"datasource": DS, "expr": expr, "legendFormat": "", "refId": "A"}]
    }

prb_shading_panel = {
    "datasource": DS,
    "fieldConfig": {
        "defaults": {
            "color": {"mode": "palette-classic"},
            "unit": "percentunit",
            "custom": {"lineWidth": 2, "fillOpacity": 10}
        },
        "overrides": [
            {
                "matcher": {"id": "byName", "options": "PRB DL"},
                "properties": [{"id": "color", "value": {"fixedColor": "red", "mode": "fixed"}}]
            },
            {
                "matcher": {"id": "byName", "options": "PRB UL"},
                "properties": [{"id": "color", "value": {"fixedColor": "blue", "mode": "fixed"}}]
            },
            {
                "matcher": {"id": "byName", "options": "Stage"},
                "properties": [
                    {"id": "custom.lineWidth", "value": 0},
                    {"id": "custom.fillOpacity", "value": 20},
                    {"id": "custom.axisPlacement", "value": "hidden"},
                    {"id": "custom.hideFrom", "value": {"legend": True, "tooltip": False, "viz": False}},
                    {"id": "color.mode", "value": "thresholds"},
                    {"id": "thresholds", "value": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "transparent", "value": None},
                            {"color": "yellow", "value": 0.4},
                            {"color": "red", "value": 0.9}
                        ]
                    }}
                ]
            }
        ]
    },
    "gridPos": {"h": 8, "w": 24, "x": 0, "y": 11},
    "id": 11,
    "options": {
        "legend": {"calcs": ["last", "max"], "displayMode": "list", "placement": "bottom"},
        "tooltip": {"mode": "multi", "sort": "none"}
    },
    "timeFrom": "10m",
    "title": "PRB DL / UL + Mitigation Stage — Last 10 min",
    "type": "timeseries",
    "targets": [
        {"datasource": DS, "expr": "xapp_prb_dl_ratio",       "legendFormat": "PRB DL", "refId": "A"},
        {"datasource": DS, "expr": "xapp_prb_ul_ratio",       "legendFormat": "PRB UL", "refId": "B"},
        {"datasource": DS, "expr": "xapp_detection_stage / 2","legendFormat": "Stage",   "refId": "C"}
    ]
}

new_panels = [
    stat_panel(8,  "DETECT LATENCY",    "xapp_latency_detect_ms",  0,
               [{"color": "green", "value": None}, {"color": "yellow", "value": 5000}, {"color": "red", "value": 10000}]),
    stat_panel(9,  "CONFIRM LATENCY",   "xapp_latency_confirm_ms", 4,
               [{"color": "green", "value": None}, {"color": "yellow", "value": 5000}, {"color": "red", "value": 15000}]),
    stat_panel(10, "TOTAL TO MITIGATE", "xapp_latency_total_ms",   8,
               [{"color": "green", "value": None}, {"color": "yellow", "value": 10000}, {"color": "red", "value": 20000}]),
    prb_shading_panel,
]

with open("grafana/provisioning/dashboards/main.json") as f:
    d = json.load(f)

# Shift existing panels at y >= 7 down by 12
for p in d["panels"]:
    if p["gridPos"]["y"] >= 7:
        p["gridPos"]["y"] += 12

d["panels"].extend(new_panels)

with open("grafana/provisioning/dashboards/main.json", "w") as f:
    json.dump(d, f, indent=2)

print("main.json updated OK")
PYEOF
```

Expected output: `main.json updated OK`

- [ ] **Step 2: Verifikasi panel count dan posisi**

```bash
cat grafana/provisioning/dashboards/main.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
for p in d['panels']:
    print(f\"id={p['id']:2d} y={p['gridPos']['y']:2d} title='{p['title'][:40]}'\")
"
```

Expected output (urutan Y):
```
id= 1 y= 0 title='Detection Status'
id= 2 y= 3 title='PRB DL'
id= 3 y= 3 title='PRB UL'
id= 4 y= 3 title='CQI'
id= 5 y= 3 title='UL Air Delay'
id= 8 y= 7 title='DETECT LATENCY'
id= 9 y= 7 title='CONFIRM LATENCY'
id=10 y= 7 title='TOTAL TO MITIGATE'
id=11 y=11 title='PRB DL / UL + Mitigation Stage — Last 10 min'
id= 6 y=19 title='PRB Utilization DL / UL — Last 5 min'
id= 7 y=19 title='LSTM Anomaly Score (threshold = 0.5)'
```

- [ ] **Step 3: Commit**

```bash
git add grafana/provisioning/dashboards/main.json
git commit -m "feat: add latency stats and PRB mitigation shading panel to main dashboard"
```

---

## Task 3: Update testing.json — Tambah 4 Panel Baru

**Files:**
- Modify: `grafana/provisioning/dashboards/testing.json`

Layout final:

| Y  | Panel | w | h |
|----|-------|---|---|
| 0  | **DETECT LATENCY** (id=2) | 4 | 4 |
| 0  | **CONFIRM LATENCY** (id=3) | 4 | 4 |
| 0  | **TOTAL TO MITIGATE** (id=4) | 4 | 4 |
| 4  | **PRB + Mitigation Shading** (id=5) | 24 | 8 |
| 12 | Testing App iframe (existing, shifted) | 24 | 36 |

- [ ] **Step 1: Jalankan script untuk update testing.json**

```bash
cd /home/telmat/sec-xapp
/home/telmat/xapp/venv/bin/python3 - << 'PYEOF'
import json

DS = {"type": "prometheus", "uid": "prometheus"}

def stat_panel(pid, title, expr, x, thresholds):
    return {
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "unit": "ms",
                "thresholds": {"mode": "absolute", "steps": thresholds},
                "mappings": [{"type": "value", "options": {"0": {"text": "—", "index": 0}}}]
            },
            "overrides": []
        },
        "gridPos": {"h": 4, "w": 4, "x": x, "y": 0},
        "id": pid,
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto"
        },
        "title": title,
        "type": "stat",
        "targets": [{"datasource": DS, "expr": expr, "legendFormat": "", "refId": "A"}]
    }

prb_shading_panel = {
    "datasource": DS,
    "fieldConfig": {
        "defaults": {
            "color": {"mode": "palette-classic"},
            "unit": "percentunit",
            "custom": {"lineWidth": 2, "fillOpacity": 10}
        },
        "overrides": [
            {
                "matcher": {"id": "byName", "options": "PRB DL"},
                "properties": [{"id": "color", "value": {"fixedColor": "red", "mode": "fixed"}}]
            },
            {
                "matcher": {"id": "byName", "options": "PRB UL"},
                "properties": [{"id": "color", "value": {"fixedColor": "blue", "mode": "fixed"}}]
            },
            {
                "matcher": {"id": "byName", "options": "Stage"},
                "properties": [
                    {"id": "custom.lineWidth", "value": 0},
                    {"id": "custom.fillOpacity", "value": 20},
                    {"id": "custom.axisPlacement", "value": "hidden"},
                    {"id": "custom.hideFrom", "value": {"legend": True, "tooltip": False, "viz": False}},
                    {"id": "color.mode", "value": "thresholds"},
                    {"id": "thresholds", "value": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "transparent", "value": None},
                            {"color": "yellow", "value": 0.4},
                            {"color": "red", "value": 0.9}
                        ]
                    }}
                ]
            }
        ]
    },
    "gridPos": {"h": 8, "w": 24, "x": 0, "y": 4},
    "id": 5,
    "options": {
        "legend": {"calcs": ["last", "max"], "displayMode": "list", "placement": "bottom"},
        "tooltip": {"mode": "multi", "sort": "none"}
    },
    "timeFrom": "10m",
    "title": "PRB DL / UL + Mitigation Stage — Last 10 min",
    "type": "timeseries",
    "targets": [
        {"datasource": DS, "expr": "xapp_prb_dl_ratio",       "legendFormat": "PRB DL", "refId": "A"},
        {"datasource": DS, "expr": "xapp_prb_ul_ratio",       "legendFormat": "PRB UL", "refId": "B"},
        {"datasource": DS, "expr": "xapp_detection_stage / 2","legendFormat": "Stage",   "refId": "C"}
    ]
}

new_panels = [
    stat_panel(2, "DETECT LATENCY",    "xapp_latency_detect_ms",  0,
               [{"color": "green", "value": None}, {"color": "yellow", "value": 5000}, {"color": "red", "value": 10000}]),
    stat_panel(3, "CONFIRM LATENCY",   "xapp_latency_confirm_ms", 4,
               [{"color": "green", "value": None}, {"color": "yellow", "value": 5000}, {"color": "red", "value": 15000}]),
    stat_panel(4, "TOTAL TO MITIGATE", "xapp_latency_total_ms",   8,
               [{"color": "green", "value": None}, {"color": "yellow", "value": 10000}, {"color": "red", "value": 20000}]),
    prb_shading_panel,
]

with open("grafana/provisioning/dashboards/testing.json") as f:
    d = json.load(f)

# Shift existing iframe panel down by 12 rows
for p in d["panels"]:
    p["gridPos"]["y"] += 12

d["panels"].extend(new_panels)

with open("grafana/provisioning/dashboards/testing.json", "w") as f:
    json.dump(d, f, indent=2)

print("testing.json updated OK")
PYEOF
```

Expected output: `testing.json updated OK`

- [ ] **Step 2: Verifikasi panel count dan posisi**

```bash
cat grafana/provisioning/dashboards/testing.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
for p in sorted(d['panels'], key=lambda p: p['gridPos']['y']):
    print(f\"id={p['id']:2d} y={p['gridPos']['y']:2d} type={p['type']} title='{str(p.get('title',''))[:30]}'\")
"
```

Expected output:
```
id= 2 y= 0 type=stat title='DETECT LATENCY'
id= 3 y= 0 type=stat title='CONFIRM LATENCY'
id= 4 y= 0 type=stat title='TOTAL TO MITIGATE'
id= 5 y= 4 type=timeseries title='PRB DL / UL + Mitigation Stage'
id= 1 y=12 type=text title=''
```

- [ ] **Step 3: Commit**

```bash
git add grafana/provisioning/dashboards/testing.json
git commit -m "feat: add latency stats and PRB mitigation shading panel to testing dashboard"
```

---

## Task 4: Deploy dan Verifikasi

**Files:** (tidak ada perubahan kode, hanya deployment)

- [ ] **Step 1: Rebuild exporter container**

```bash
cd /home/telmat/sec-xapp
docker compose build csv-exporter
```

Expected: `Successfully built ...` atau `Successfully tagged xapp-exporter:latest`

- [ ] **Step 2: Restart exporter**

```bash
docker compose up -d csv-exporter
```

Expected: `Container xapp-exporter  Started`

- [ ] **Step 3: Verifikasi metric baru muncul di /metrics**

```bash
curl -s http://localhost:8000/metrics | grep xapp_latency
```

Expected (nilai 0.0 karena belum ada event):
```
# HELP xapp_latency_detect_ms Stage 0→1 detection latency ms (last event)
# TYPE xapp_latency_detect_ms gauge
xapp_latency_detect_ms 0.0
# HELP xapp_latency_confirm_ms Stage 1→2 confirmation latency ms (last event)
# TYPE xapp_latency_confirm_ms gauge
xapp_latency_confirm_ms 0.0
# HELP xapp_latency_total_ms Total Stage 0→2 mitigation latency ms (last event)
# TYPE xapp_latency_total_ms gauge
xapp_latency_total_ms 0.0
```

- [ ] **Step 4: Reload Grafana dashboard**

Grafana provisioning dashboard reload secara otomatis saat file JSON berubah jika `updateIntervalSeconds` di `dashboards.yml` dikonfigurasi. Jika tidak auto-reload:

```bash
# Restart grafana container untuk force reload provisioning
docker compose restart grafana
```

Buka http://localhost:3000 → Security xApp Monitor → verifikasi:
1. Row baru "DETECT LATENCY / CONFIRM LATENCY / TOTAL TO MITIGATE" muncul (semua "—" karena belum ada event)
2. Panel "PRB DL / UL + Mitigation Stage — Last 10 min" muncul di bawah stat row

- [ ] **Step 5: Verifikasi di testing dashboard**

Buka http://localhost:3000 → xApp Security Monitor — Testing → verifikasi:
1. Tiga stat latency muncul di atas
2. PRB+shading panel muncul di bawah stat, di atas iframe

- [ ] **Step 6: Test end-to-end dengan simulasi stage change**

Jika xapp sedang jalan dan ada CSV data, tunggu sampai ada detection event. Atau simulasikan dengan mengedit CSV terakhir:

```bash
# Cek CSV terbaru
ls -lt /home/telmat/sec-xapp/csv/*.csv | head -3
```

Saat ada Stage 1 atau Stage 2 event, panel latency akan menampilkan nilai dalam ms (bukan "—").
