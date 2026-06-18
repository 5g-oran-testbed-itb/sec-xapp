# Per-UE Live Dashboard Enhancement — Design Spec
**Date:** 2026-06-18  
**Scope:** `grafana/provisioning/dashboards/per_ue_live.json` only — no exporter changes

---

## 1. Goal

Tambah 3 stat panel baru di header dan 1 tabel UE aktif ke dashboard `xapp-ue-live`, menggunakan gauge Prometheus yang sudah ada. Tidak ada perubahan pada `csv_exporter.py`.

---

## 2. Changes Summary

| Perubahan | Detail |
|-----------|--------|
| Header row | Dari 3 stat (w=4+4+8) → 6 stat (w=4 masing-masing) |
| Panel baru | Tabel UE Aktif (id=14, w=24, h=6) di y=4 |
| Existing panels | Semua digeser +10 baris ke bawah (y: 4→14, 10→20, 16→26) |
| Iframe panel | Digeser ke y=32 |

---

## 3. Panel Spec

### 3.1 Header Stats (y=0, h=4)

Semua 6 panel pakai `type: stat`, `instant: true`, `reduceOptions.calcs: ["lastNotNull"]`.

| id | x | w | Title | Query | Color logic |
|----|---|---|-------|-------|-------------|
| 1 | 0 | 4 | Active RNTIs | `count(xapp_ue_thp_ul_kbps > 0) or vector(0)` | green→blue saat ≥1 |
| 2 | 4 | 4 | Alerted RNTIs | `count(xapp_ue_alert_type > 0) or vector(0)` | green→red saat ≥1 |
| 3 | 8 | 4 | Selected UE Alert Status | `xapp_ue_alert_type{rnti=~"$rnti"}` | value mapping 0–4 |
| 11 | 12 | 4 | Total Alerts Aktif | `count(xapp_ue_alert_type > 0) or vector(0)` | green→orange saat ≥1 |
| 12 | 16 | 4 | Avg MSE (on alert) | `avg(xapp_ue_mse > 0) or vector(0)` | green→red, decimals=4 |
| 13 | 20 | 4 | Max Stage Aktif | `max(xapp_ue_stage) or vector(0)` | 0=green, 1=orange, 2=red |

**Catatan panel 11:** Karena `xapp_ue_alert_type` adalah Gauge (bukan Counter), kita tidak bisa `increase()`. Gunakan `count(xapp_ue_alert_type > 0)` — ini menghitung UE yang *saat ini* dalam keadaan alert, lebih akurat untuk live monitoring.

**Catatan panel 12:** `avg(xapp_ue_mse > 0)` hanya menghitung UE yang memiliki MSE > 0 (yaitu yang pernah trigger alert). UE normal yang MSE=0 tidak masuk rata-rata.

**Panel 3 value mappings** (sudah ada, tidak berubah):
```
0 → NORMAL (green), 1 → UL FLOOD (orange), 2 → DL FLOOD (orange), 3 → BURST (yellow), 4 → RoQ (red)
```

**Panel 13 thresholds:**
```json
{ "mode": "absolute", "steps": [
  { "value": null, "color": "green" },
  { "value": 1,    "color": "orange" },
  { "value": 2,    "color": "red" }
]}
```

---

### 3.2 Tabel UE Aktif (id=14)

```
type:      table
title:     UE Aktif — Status Real-Time
gridPos:   { x: 0, y: 4, w: 24, h: 6 }
```

**Targets** (semua `instant: true`, `legendFormat: "{{rnti}}"`):
```
A: xapp_ue_thp_ul_kbps    → legendFormat: "THP UL"
B: xapp_ue_thp_dl_kbps    → legendFormat: "THP DL"
C: xapp_ue_prb_ul          → legendFormat: "PRB UL"
D: xapp_ue_prb_dl          → legendFormat: "PRB DL"
E: xapp_ue_alert_type      → legendFormat: "Alert"
F: xapp_ue_stage           → legendFormat: "Stage"
```

**Transform:** `merge` (join semua series by label `rnti`) + `organize` untuk rename kolom dan hide Time/Value.

**Field overrides:**
- `Alert` → value mapping sama dengan panel 3 (0=NORMAL green, 1=UL FLOOD orange, 2=DL FLOOD orange, 3=BURST yellow, 4=RoQ red)
- `Stage` → thresholds: 0=green, 1=orange, 2=red
- `THP UL`, `THP DL` → unit: `kbps`, decimals: 0
- `PRB UL`, `PRB DL` → unit: `percentunit`, decimals: 2

**Options:**
```json
{
  "sortBy": [{ "displayName": "Stage", "desc": true }],
  "footer": { "show": false }
}
```

---

### 3.3 Existing Panels — Grid Position Shifts

Panel existing digeser agar tabel baru (h=6 di y=4) muat:

| id | Title | y lama | y baru |
|----|-------|--------|--------|
| 5 | Detection Stage per UE | 4 | 14 |
| 6 | MSE Score per UE | 4 | 14 |
| 7 | Throughput UL/DL per UE | 10 | 20 |
| 8 | PRB Utilization per UE | 10 | 20 |
| 9 | PRB Direction & UL Efficiency | 16 | 26 |
| 10 | Attack Detection Evaluation (iframe) | 22 | 32 |

---

## 4. Files Changed

| File | Perubahan |
|------|-----------|
| `grafana/provisioning/dashboards/per_ue_live.json` | +2 stat panels (id 11, 12, 13 merge ke header), +1 tabel (id 14), shift y semua existing |

**Tidak ada perubahan:** `csv_exporter.py`, `docker-compose.yml`, test files.

---

## 5. Validation

```bash
python3 -c "import json; json.load(open('grafana/provisioning/dashboards/per_ue_live.json')); print('JSON valid')"
docker restart xapp-grafana
```

Dashboard akan reload otomatis. Verifikasi di http://localhost:3000 → Per-UE Live.
