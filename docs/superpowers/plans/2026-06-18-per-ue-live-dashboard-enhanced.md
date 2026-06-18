# Per-UE Live Dashboard Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tambah 3 stat panel baru di header dan 1 tabel UE aktif ke `per_ue_live.json`, geser semua panel existing ke bawah.

**Architecture:** Pure JSON edit pada satu file Grafana dashboard provisioning. Tidak ada perubahan Python/exporter. Semua query menggunakan gauge `xapp_ue_*` yang sudah ada di Prometheus.

**Tech Stack:** Grafana 10.4 dashboard JSON, Prometheus PromQL instant queries.

**Spec:** `docs/superpowers/specs/2026-06-18-per-ue-live-dashboard-enhanced.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `grafana/provisioning/dashboards/per_ue_live.json` | +3 stat panels, +1 table panel, shift existing panels |

---

## Task 1: Resize panel 3 dan tambah 3 stat panel baru di header

**Files:**
- Modify: `grafana/provisioning/dashboards/per_ue_live.json`

Panel 3 saat ini `w=8` di `x=8`. Harus diperkecil ke `w=4` agar muat bersama 3 panel baru di row yang sama (total 6 × w=4 = 24).

- [ ] **Step 1: Resize panel 3 dari w=8 ke w=4**

Cari dan ganti di `grafana/provisioning/dashboards/per_ue_live.json`:

```json
"id": 3, "type": "stat", "title": "Selected UE Alert Status",
      "gridPos": { "x": 8, "y": 0, "w": 8, "h": 4 },
```

Ganti dengan:

```json
"id": 3, "type": "stat", "title": "Selected UE Alert Status",
      "gridPos": { "x": 8, "y": 0, "w": 4, "h": 4 },
```

- [ ] **Step 2: Tambah 3 stat panel baru setelah panel 3 (sebelum panel 5)**

Cari baris `},` yang menutup panel 3 (setelah `"targets": [{ "expr": "xapp_ue_alert_type...`), lalu sisipkan 3 panel berikut:

```json
    },
    {
      "id": 11, "type": "stat", "title": "Total Alerts Aktif",
      "gridPos": { "x": 12, "y": 0, "w": 4, "h": 4 },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "thresholds" },
          "thresholds": { "mode": "absolute", "steps": [
            { "value": null, "color": "green" },
            { "value": 1,    "color": "orange" }
          ]}
        }
      },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [{ "expr": "count(xapp_ue_alert_type > 0) or vector(0)", "instant": true, "legendFormat": "" }]
    },
    {
      "id": 12, "type": "stat", "title": "Avg MSE (on alert)",
      "gridPos": { "x": 16, "y": 0, "w": 4, "h": 4 },
      "fieldConfig": {
        "defaults": {
          "decimals": 4,
          "color": { "mode": "thresholds" },
          "thresholds": { "mode": "absolute", "steps": [
            { "value": null, "color": "green" },
            { "value": 0.025969, "color": "orange" },
            { "value": 0.05,     "color": "red" }
          ]}
        }
      },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [{ "expr": "avg(xapp_ue_mse > 0) or vector(0)", "instant": true, "legendFormat": "" }]
    },
    {
      "id": 13, "type": "stat", "title": "Max Stage Aktif",
      "gridPos": { "x": 20, "y": 0, "w": 4, "h": 4 },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "thresholds" },
          "thresholds": { "mode": "absolute", "steps": [
            { "value": null, "color": "green" },
            { "value": 1,    "color": "orange" },
            { "value": 2,    "color": "red" }
          ]}
        }
      },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [{ "expr": "max(xapp_ue_stage) or vector(0)", "instant": true, "legendFormat": "" }]
    },
```

- [ ] **Step 3: Validasi JSON**

```bash
python3 -c "import json; json.load(open('grafana/provisioning/dashboards/per_ue_live.json')); print('JSON valid')"
```

Expected: `JSON valid`

- [ ] **Step 4: Commit**

```bash
git add grafana/provisioning/dashboards/per_ue_live.json
git commit -m "feat: add 3 stat panels to per-UE live dashboard header (Total Alerts, Avg MSE, Max Stage)"
```

---

## Task 2: Tambah tabel UE aktif dan geser semua panel existing ke bawah

**Files:**
- Modify: `grafana/provisioning/dashboards/per_ue_live.json`

Tabel UE aktif ditempatkan di `y=4` (tepat di bawah header). Semua panel existing (id 5,6,7,8,9,10) harus digeser +10 ke bawah agar tidak bertabrakan.

- [ ] **Step 1: Tambah panel tabel UE aktif sebelum panel 5**

Cari baris yang membuka panel 5:

```json
    {
      "id": 5, "type": "timeseries", "title": "Detection Stage per UE",
```

Sisipkan panel tabel berikut **tepat sebelum** baris tersebut:

```json
    {
      "id": 14, "type": "table", "title": "UE Aktif — Status Real-Time",
      "gridPos": { "x": 0, "y": 4, "w": 24, "h": 6 },
      "fieldConfig": {
        "defaults": { "custom": { "align": "center" } },
        "overrides": [
          { "matcher": { "id": "byName", "options": "rnti" },
            "properties": [
              { "id": "displayName", "value": "RNTI" },
              { "id": "custom.width", "value": 100 }
            ]
          },
          { "matcher": { "id": "byName", "options": "Value #A" },
            "properties": [
              { "id": "displayName", "value": "THP UL (kbps)" },
              { "id": "unit", "value": "none" },
              { "id": "decimals", "value": 0 }
            ]
          },
          { "matcher": { "id": "byName", "options": "Value #B" },
            "properties": [
              { "id": "displayName", "value": "THP DL (kbps)" },
              { "id": "unit", "value": "none" },
              { "id": "decimals", "value": 0 }
            ]
          },
          { "matcher": { "id": "byName", "options": "Value #C" },
            "properties": [
              { "id": "displayName", "value": "PRB UL" },
              { "id": "unit", "value": "percentunit" },
              { "id": "decimals", "value": 2 }
            ]
          },
          { "matcher": { "id": "byName", "options": "Value #D" },
            "properties": [
              { "id": "displayName", "value": "PRB DL" },
              { "id": "unit", "value": "percentunit" },
              { "id": "decimals", "value": 2 }
            ]
          },
          { "matcher": { "id": "byName", "options": "Value #E" },
            "properties": [
              { "id": "displayName", "value": "Alert" },
              { "id": "mappings", "value": [
                { "type": "value", "options": { "0": { "text": "NORMAL",   "color": "green"  } } },
                { "type": "value", "options": { "1": { "text": "UL FLOOD", "color": "orange" } } },
                { "type": "value", "options": { "2": { "text": "DL FLOOD", "color": "orange" } } },
                { "type": "value", "options": { "3": { "text": "BURST",    "color": "yellow" } } },
                { "type": "value", "options": { "4": { "text": "RoQ",      "color": "red"    } } }
              ]},
              { "id": "custom.displayMode", "value": "color-background" }
            ]
          },
          { "matcher": { "id": "byName", "options": "Value #F" },
            "properties": [
              { "id": "displayName", "value": "Stage" },
              { "id": "thresholds", "value": { "mode": "absolute", "steps": [
                { "value": null, "color": "green" },
                { "value": 1,    "color": "orange" },
                { "value": 2,    "color": "red" }
              ]}},
              { "id": "custom.displayMode", "value": "color-background" }
            ]
          }
        ]
      },
      "options": {
        "sortBy": [{ "displayName": "Stage", "desc": true }],
        "footer": { "show": false }
      },
      "transformations": [
        { "id": "merge", "options": {} },
        { "id": "organize", "options": {
          "excludeByName": { "Time": true },
          "indexByName": { "rnti": 0, "Value #A": 1, "Value #B": 2, "Value #C": 3, "Value #D": 4, "Value #E": 5, "Value #F": 6 }
        }}
      ],
      "targets": [
        { "expr": "xapp_ue_thp_ul_kbps", "instant": true, "legendFormat": "{{rnti}}", "refId": "A" },
        { "expr": "xapp_ue_thp_dl_kbps", "instant": true, "legendFormat": "{{rnti}}", "refId": "B" },
        { "expr": "xapp_ue_prb_ul",       "instant": true, "legendFormat": "{{rnti}}", "refId": "C" },
        { "expr": "xapp_ue_prb_dl",       "instant": true, "legendFormat": "{{rnti}}", "refId": "D" },
        { "expr": "xapp_ue_alert_type",   "instant": true, "legendFormat": "{{rnti}}", "refId": "E" },
        { "expr": "xapp_ue_stage",        "instant": true, "legendFormat": "{{rnti}}", "refId": "F" }
      ]
    },
```

- [ ] **Step 2: Geser panel 5 dan 6 dari y=4 ke y=14**

Cari dan ganti:

```json
"id": 5, "type": "timeseries", "title": "Detection Stage per UE",
      "gridPos": { "x": 0, "y": 4, "w": 12, "h": 6 },
```

Ganti `"y": 4` → `"y": 14`:

```json
"id": 5, "type": "timeseries", "title": "Detection Stage per UE",
      "gridPos": { "x": 0, "y": 14, "w": 12, "h": 6 },
```

Cari dan ganti panel 6:

```json
"id": 6, "type": "timeseries", "title": "MSE Score per UE (on alert events)",
      "gridPos": { "x": 12, "y": 4, "w": 12, "h": 6 },
```

Ganti `"y": 4` → `"y": 14`:

```json
"id": 6, "type": "timeseries", "title": "MSE Score per UE (on alert events)",
      "gridPos": { "x": 12, "y": 14, "w": 12, "h": 6 },
```

- [ ] **Step 3: Geser panel 7 dan 8 dari y=10 ke y=20**

Panel 7:
```json
"id": 7, "type": "timeseries", "title": "Throughput UL / DL per UE",
      "gridPos": { "x": 0, "y": 10, "w": 12, "h": 6 },
```
→ `"y": 10` ganti jadi `"y": 20`

Panel 8:
```json
"id": 8, "type": "timeseries", "title": "PRB Utilization per UE",
      "gridPos": { "x": 12, "y": 10, "w": 12, "h": 6 },
```
→ `"y": 10` ganti jadi `"y": 20`

- [ ] **Step 4: Geser panel 9 dari y=16 ke y=26**

```json
"id": 9, "type": "timeseries", "title": "PRB Direction & UL Efficiency per UE",
      "gridPos": { "x": 0, "y": 16, "w": 24, "h": 6 },
```
→ `"y": 16` ganti jadi `"y": 26`

- [ ] **Step 5: Geser panel 10 (iframe) dari y=22 ke y=32**

```json
"id": 10, "type": "text", "title": "Attack Detection Evaluation",
      "gridPos": { "x": 0, "y": 22, "w": 24, "h": 24 },
```
→ `"y": 22` ganti jadi `"y": 32`

- [ ] **Step 6: Validasi JSON dan reload Grafana**

```bash
python3 -c "import json; json.load(open('grafana/provisioning/dashboards/per_ue_live.json')); print('JSON valid')"
docker restart xapp-grafana
```

Expected: `JSON valid` lalu Grafana restart.

- [ ] **Step 7: Verifikasi di browser**

Buka http://localhost:3000 → dashboard **xApp Security Monitor — Per-UE Live**.

Pastikan:
- Row pertama ada 6 stat panel (Active RNTIs, Alerted RNTIs, Alert Status, Total Alerts Aktif, Avg MSE, Max Stage)
- Row kedua: tabel UE Aktif dengan kolom RNTI, THP UL, THP DL, PRB UL, PRB DL, Alert, Stage
- Panel timeseries masih ada di bawah tabel

- [ ] **Step 8: Commit**

```bash
git add grafana/provisioning/dashboards/per_ue_live.json
git commit -m "feat: add UE active table and shift existing panels in per_ue_live dashboard"
```

---

## Verification After All Tasks

```bash
# Validasi JSON
python3 -c "import json; d=json.load(open('grafana/provisioning/dashboards/per_ue_live.json')); ids=[p['id'] for p in d['panels']]; print('Panel IDs:', sorted(ids)); ys=[(p['id'],p['gridPos']['y']) for p in d['panels']]; print('gridPos y:', sorted(ys))"
```

Expected output:
```
Panel IDs: [1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
gridPos y: [(1, 0), (2, 0), (3, 0), (11, 0), (12, 0), (13, 0), (14, 4), (5, 14), (6, 14), (7, 20), (8, 20), (9, 26), (10, 32)]
```
