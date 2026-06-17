# Per-UE Grafana Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two Grafana dashboards and extend the Prometheus exporter to expose per-UE (per-RNTI) IDS v4 metrics — live monitoring and offline evaluation results.

**Architecture:** Extend `exporter/csv_exporter.py` with two new background threads: `ue_alert_tail_loop()` tails `csv/ue_alerts_*.csv` (alert events per RNTI), `ue_feature_tail_loop()` tails `csv/per_ue_training_*.csv` (continuous per-UE features). Static per-UE v4 eval results are pushed to Prometheus at startup via `_populate_eval_ue_v4()`. Two new Grafana JSON dashboards consume these metrics.

**Tech Stack:** Python 3, prometheus_client, Grafana 10 JSON provisioning, Prometheus instant/range queries with `rnti` label.

**Spec:** `docs/superpowers/specs/2026-06-17-per-ue-grafana-dashboard-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `exporter/csv_exporter.py` | +2 threads, +14 gauges, +KNOWN_EVAL_UE, +2 helpers, +populate fn |
| Modify | `exporter/test_csv_exporter.py` | +10 tests for new pure functions |
| Create | `grafana/provisioning/dashboards/per_ue_live.json` | Live per-RNTI monitoring dashboard |
| Create | `grafana/provisioning/dashboards/per_ue_eval.json` | Per-UE v4 evaluation results dashboard |

---

## Task 1: Extend `find_newest_csv` to accept a glob pattern

**Files:**
- Modify: `exporter/csv_exporter.py:165-173`
- Modify: `exporter/test_csv_exporter.py`

- [ ] **Step 1: Write the failing test**

Add to `exporter/test_csv_exporter.py`:

```python
def test_find_newest_csv_filters_by_pattern(tmp_path):
    """find_newest_csv with pattern 'ue_alerts_*.csv' ignores other CSV files."""
    from csv_exporter import find_newest_csv
    (tmp_path / "training_20260617.csv").write_text("cell-level")
    time.sleep(0.05)
    alert = tmp_path / "ue_alerts_20260617.csv"
    alert.write_text("per-ue")
    assert find_newest_csv(str(tmp_path), "ue_alerts_*.csv") == str(alert)


def test_find_newest_csv_default_pattern_unchanged(tmp_path):
    """Default pattern '*.csv' still returns newest csv (backward-compatible)."""
    from csv_exporter import find_newest_csv
    (tmp_path / "a.csv").write_text("a")
    time.sleep(0.05)
    b = tmp_path / "b.csv"
    b.write_text("b")
    assert find_newest_csv(str(tmp_path)) == str(b)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/telmat/sec-xapp/exporter
../venv/bin/python -m pytest test_csv_exporter.py::test_find_newest_csv_filters_by_pattern test_csv_exporter.py::test_find_newest_csv_default_pattern_unchanged -v
```

Expected: `TypeError` — `find_newest_csv() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Update `find_newest_csv` signature in `csv_exporter.py`**

Replace the existing `find_newest_csv` function (lines 165–173):

```python
def find_newest_csv(csv_dir: str, pattern: str = "*.csv"):
    """Return path to the most recently modified file matching pattern in csv_dir, or None."""
    files = glob.glob(os.path.join(csv_dir, pattern))
    if not files:
        return None
    try:
        return max(files, key=os.path.getmtime)
    except FileNotFoundError:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/telmat/sec-xapp/exporter
../venv/bin/python -m pytest test_csv_exporter.py -v
```

Expected: All existing tests plus the 2 new tests PASS.

- [ ] **Step 5: Commit**

```bash
git add exporter/csv_exporter.py exporter/test_csv_exporter.py
git commit -m "feat: extend find_newest_csv to accept glob pattern for per-UE CSV targeting"
```

---

## Task 2: Add per-UE Prometheus gauges + eval data + populate function

**Files:**
- Modify: `exporter/csv_exporter.py` (after line 62, after `g_eval_precision_v2`)
- Modify: `exporter/test_csv_exporter.py`

- [ ] **Step 1: Write the failing tests**

Add to `exporter/test_csv_exporter.py`:

```python
def test_populate_eval_ue_v4_sets_gru_hybrid_overall_recall():
    import csv_exporter
    csv_exporter._populate_eval_ue_v4()
    val = csv_exporter.g_ue_eval_recall_v4.labels(config="gru_hybrid", attack="all")._value.get()
    assert abs(val - 0.961) < 0.001


def test_populate_eval_ue_v4_sets_gru_hybrid_roq_recall():
    import csv_exporter
    csv_exporter._populate_eval_ue_v4()
    val = csv_exporter.g_ue_eval_recall_v4.labels(config="gru_hybrid", attack="roq")._value.get()
    assert abs(val - 0.922) < 0.001


def test_populate_eval_ue_v4_sets_gru_hybrid_det_latency():
    import csv_exporter
    csv_exporter._populate_eval_ue_v4()
    val = csv_exporter.g_ue_eval_det_lat_v4.labels(config="gru_hybrid")._value.get()
    assert abs(val - 4.04) < 0.01


def test_populate_eval_ue_v4_sets_rule_only_fpr():
    import csv_exporter
    csv_exporter._populate_eval_ue_v4()
    val = csv_exporter.g_ue_eval_fpr_v4.labels(config="rule_only")._value.get()
    assert abs(val - 0.0293) < 0.0001
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/telmat/sec-xapp/exporter
../venv/bin/python -m pytest test_csv_exporter.py::test_populate_eval_ue_v4_sets_gru_hybrid_overall_recall -v
```

Expected: `AttributeError: module 'csv_exporter' has no attribute '_populate_eval_ue_v4'`

- [ ] **Step 3: Add gauges, KNOWN_EVAL_UE, and populate function to `csv_exporter.py`**

After the `g_eval_precision_v2` line (after line 62), add:

```python
# ── Per-UE IDS v4 live metrics (per-RNTI label) ──────────────────────────────
g_ue_mse        = Gauge("xapp_ue_mse",        "Per-UE MSE reconstruction error (on alert)", ["rnti"])
g_ue_alert_type = Gauge("xapp_ue_alert_type", "Per-UE alert type: 0=none 1=ul_flood 2=dl_flood 3=burst 4=roq", ["rnti"])
g_ue_stage      = Gauge("xapp_ue_stage",      "Per-UE IDS stage: 0/1/2", ["rnti"])

g_ue_thp_ul_kbps    = Gauge("xapp_ue_thp_ul_kbps",    "Per-UE UL throughput (kbps)",  ["rnti"])
g_ue_thp_dl_kbps    = Gauge("xapp_ue_thp_dl_kbps",    "Per-UE DL throughput (kbps)",  ["rnti"])
g_ue_prb_ul         = Gauge("xapp_ue_prb_ul",         "Per-UE PRB UL utilization (0-1)", ["rnti"])
g_ue_prb_dl         = Gauge("xapp_ue_prb_dl",         "Per-UE PRB DL utilization (0-1)", ["rnti"])
g_ue_prb_direction  = Gauge("xapp_ue_prb_direction",  "Per-UE PRB direction [-1,+1]", ["rnti"])
g_ue_ul_efficiency  = Gauge("xapp_ue_ul_efficiency",  "Per-UE UL efficiency (thp_ul/prb_ul)", ["rnti"])

# ── Per-UE v4 eval metrics (config label, optional attack label) ──────────────
g_ue_eval_recall_v4  = Gauge("xapp_ue_eval_recall_v4",  "Per-UE v4 eval recall",  ["config", "attack"])
g_ue_eval_f1_v4      = Gauge("xapp_ue_eval_f1_v4",      "Per-UE v4 eval F1",      ["config", "attack"])
g_ue_eval_fpr_v4     = Gauge("xapp_ue_eval_fpr_v4",     "Per-UE v4 eval FPR",     ["config"])
g_ue_eval_det_lat_v4 = Gauge("xapp_ue_eval_det_lat_v4", "Per-UE v4 detection latency (s)", ["config"])
g_ue_eval_mit_lat_v4 = Gauge("xapp_ue_eval_mit_lat_v4", "Per-UE v4 mitigation latency (s)", ["config"])

# ── Static per-UE v4 eval results (from STATUS_DAN_RENCANA_EVALUASI.md §1.6c) ─
KNOWN_EVAL_UE = {
    "rule_only":   {"recall": 0.858, "f1": 0.913, "fpr": 0.0293, "det_lat": 4.67,  "mit_lat": 4.79},
    "lstm_only":   {"recall": 0.910, "f1": 0.928, "fpr": 0.0305, "det_lat": 12.21, "mit_lat": 12.33},
    "gru_only":    {"recall": 0.896, "f1": 0.921, "fpr": 0.0305, "det_lat": 10.46, "mit_lat": 10.58},
    "lstm_hybrid": {"recall": 0.950, "f1": 0.948, "fpr": 0.0497, "det_lat": 4.67,  "mit_lat": 4.79},
    "gru_hybrid":  {"recall": 0.961, "f1": 0.954, "fpr": 0.0514, "det_lat": 4.04,  "mit_lat": 4.16,
                    "ul_flood": 0.979, "dl_flood": 0.968, "burst": 0.988, "roq": 0.922},
}
```

Then, after the `_populate_eval_v2` function definition, add:

```python
def _populate_eval_ue_v4():
    """Push KNOWN_EVAL_UE into Prometheus gauges at startup."""
    per_attack = ["ul_flood", "dl_flood", "burst", "roq"]
    for config, data in KNOWN_EVAL_UE.items():
        g_ue_eval_recall_v4.labels(config=config, attack="all").set(data["recall"])
        g_ue_eval_f1_v4.labels(config=config, attack="all").set(data["f1"])
        g_ue_eval_fpr_v4.labels(config=config).set(data["fpr"])
        g_ue_eval_det_lat_v4.labels(config=config).set(data["det_lat"])
        g_ue_eval_mit_lat_v4.labels(config=config).set(data["mit_lat"])
        for atk in per_attack:
            if atk in data:
                g_ue_eval_recall_v4.labels(config=config, attack=atk).set(data[atk])
    log.info("UE eval v4 metrics populated for %d configs", len(KNOWN_EVAL_UE))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/telmat/sec-xapp/exporter
../venv/bin/python -m pytest test_csv_exporter.py -v
```

Expected: All tests PASS (including the 4 new ones).

- [ ] **Step 5: Commit**

```bash
git add exporter/csv_exporter.py exporter/test_csv_exporter.py
git commit -m "feat: add per-UE Prometheus gauges and eval v4 static metrics"
```

---

## Task 3: Add `parse_ue_alert_row()` helper + tests

**Files:**
- Modify: `exporter/csv_exporter.py` (after `ALERT_TYPE_MAP`)
- Modify: `exporter/test_csv_exporter.py`

ue_alerts CSV columns (written by C xApp):
`timestamp_ms, rnti, rule_mask, rule_stage, mse, threshold, alert_type`

- [ ] **Step 1: Write the failing tests**

Add to `exporter/test_csv_exporter.py`:

```python
def test_parse_ue_alert_row_extracts_fields():
    from csv_exporter import parse_ue_alert_row
    raw = {"timestamp_ms": "1750000000000", "rnti": "0x1a2b",
           "rule_mask": "0x01", "rule_stage": "2",
           "mse": "0.031500", "threshold": "0.025969", "alert_type": "ul_flood"}
    result = parse_ue_alert_row(raw)
    assert result["rnti"] == "0x1a2b"
    assert result["rule_stage"] == 2
    assert abs(result["mse"] - 0.0315) < 1e-6
    assert result["alert_type"] == 1  # ul_flood → 1


def test_parse_ue_alert_row_roq_maps_to_4():
    from csv_exporter import parse_ue_alert_row
    raw = {"rnti": "0x0001", "rule_stage": "1", "mse": "0.028", "alert_type": "roq", "threshold": "0.025969"}
    result = parse_ue_alert_row(raw)
    assert result["alert_type"] == 4


def test_parse_ue_alert_row_unknown_alert_type_defaults_zero():
    from csv_exporter import parse_ue_alert_row
    raw = {"rnti": "0x0002", "rule_stage": "0", "mse": "0.0", "alert_type": "unknown_type"}
    result = parse_ue_alert_row(raw)
    assert result["alert_type"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/telmat/sec-xapp/exporter
../venv/bin/python -m pytest test_csv_exporter.py::test_parse_ue_alert_row_extracts_fields -v
```

Expected: `ImportError` — `cannot import name 'parse_ue_alert_row'`

- [ ] **Step 3: Add `UE_ALERT_TYPE_MAP` and `parse_ue_alert_row` to `csv_exporter.py`**

Add after the existing `ALERT_TYPE_MAP` dict (after line 82):

```python
UE_ALERT_TYPE_MAP = {"none": 0, "ul_flood": 1, "dl_flood": 2, "burst": 3, "roq": 4}


def parse_ue_alert_row(raw: dict) -> dict:
    """Parse one row from ue_alerts_*.csv. Returns typed dict."""
    try:
        stage = int(float(raw.get("rule_stage", 0)))
    except (ValueError, TypeError):
        stage = 0
    try:
        mse = float(raw.get("mse", 0.0))
    except (ValueError, TypeError):
        mse = 0.0
    return {
        "rnti":       raw.get("rnti", "0x0000"),
        "rule_stage": stage,
        "mse":        mse,
        "alert_type": UE_ALERT_TYPE_MAP.get(raw.get("alert_type", "none"), 0),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/telmat/sec-xapp/exporter
../venv/bin/python -m pytest test_csv_exporter.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add exporter/csv_exporter.py exporter/test_csv_exporter.py
git commit -m "feat: add parse_ue_alert_row helper for ue_alerts CSV parsing"
```

---

## Task 4: Add `parse_ue_feature_row()` helper + tests

**Files:**
- Modify: `exporter/csv_exporter.py` (after `parse_ue_alert_row`)
- Modify: `exporter/test_csv_exporter.py`

per_ue_training CSV columns (written by C xApp at ~800ms interval):
`timestamp_ms, datetime, rnti, prb_usage_dl_ratio, prb_usage_ul_ratio, thp_dl_kbps, thp_ul_kbps, prb_direction, prb_total, prb_ul_delta, ul_efficiency, prb_ul_roll_mean, prb_ul_roll_std, ul_persistence, thp_total_kbps, thp_ul_delta, thp_dl_delta, traffic_direction, label`

- [ ] **Step 1: Write the failing tests**

Add to `exporter/test_csv_exporter.py`:

```python
def test_parse_ue_feature_row_extracts_fields():
    from csv_exporter import parse_ue_feature_row
    raw = {
        "timestamp_ms": "1750000000000", "datetime": "2026-06-17 14:00:00",
        "rnti": "0x2345",
        "prb_usage_ul_ratio": "0.75", "prb_usage_dl_ratio": "0.30",
        "thp_ul_kbps": "5000.0", "thp_dl_kbps": "1200.0",
        "prb_direction": "0.43", "ul_efficiency": "6666.7", "label": "1",
    }
    result = parse_ue_feature_row(raw)
    assert result["rnti"] == "0x2345"
    assert abs(result["prb_usage_ul_ratio"] - 0.75) < 1e-6
    assert abs(result["thp_ul_kbps"] - 5000.0) < 1e-6
    assert abs(result["prb_direction"] - 0.43) < 1e-6


def test_parse_ue_feature_row_missing_col_defaults_zero():
    from csv_exporter import parse_ue_feature_row
    result = parse_ue_feature_row({"rnti": "0x0001"})
    assert result["thp_ul_kbps"] == 0.0
    assert result["prb_usage_dl_ratio"] == 0.0
    assert result["ul_efficiency"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/telmat/sec-xapp/exporter
../venv/bin/python -m pytest test_csv_exporter.py::test_parse_ue_feature_row_extracts_fields -v
```

Expected: `ImportError` — `cannot import name 'parse_ue_feature_row'`

- [ ] **Step 3: Add `UE_FEATURE_FLOAT_COLS` and `parse_ue_feature_row` to `csv_exporter.py`**

Add after `parse_ue_alert_row`:

```python
UE_FEATURE_FLOAT_COLS = [
    "prb_usage_dl_ratio", "prb_usage_ul_ratio",
    "thp_dl_kbps", "thp_ul_kbps",
    "prb_direction", "ul_efficiency",
]


def parse_ue_feature_row(raw: dict) -> dict:
    """Parse one row from per_ue_training_*.csv. Returns typed dict."""
    out = {"rnti": raw.get("rnti", "0x0000")}
    for col in UE_FEATURE_FLOAT_COLS:
        v = raw.get(col, "")
        try:
            out[col] = float(v)
        except (ValueError, TypeError):
            out[col] = 0.0
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/telmat/sec-xapp/exporter
../venv/bin/python -m pytest test_csv_exporter.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add exporter/csv_exporter.py exporter/test_csv_exporter.py
git commit -m "feat: add parse_ue_feature_row helper for per-UE training CSV parsing"
```

---

## Task 5: Add `ue_alert_tail_loop()` and `ue_feature_tail_loop()`, wire into `main()`

**Files:**
- Modify: `exporter/csv_exporter.py` (after `gru_inference_loop`, before `main`)

These are I/O threads — tested by smoke-running the exporter, not unit tests.

- [ ] **Step 1: Add `ue_alert_tail_loop()` to `csv_exporter.py`**

Add after `gru_inference_loop`:

```python
def ue_alert_tail_loop():
    """Tail newest ue_alerts_*.csv and update per-RNTI alert gauges."""
    current_file = None
    file_handle  = None
    reader       = None

    while True:
        newest = find_newest_csv(CSV_DIR, "ue_alerts_*.csv")

        if newest != current_file:
            if file_handle:
                file_handle.close()
                file_handle = None
                reader = None
            if newest:
                log.info("Tailing new UE alert CSV: %s", newest)
                file_handle = open(newest, newline="")
                reader = csv.DictReader(file_handle)
                for _ in reader:
                    pass  # skip existing rows on first open
            current_file = newest

        if reader:
            for raw in reader:
                row = parse_ue_alert_row(raw)
                rnti = row["rnti"]
                g_ue_mse.labels(rnti=rnti).set(row["mse"])
                g_ue_alert_type.labels(rnti=rnti).set(row["alert_type"])
                g_ue_stage.labels(rnti=rnti).set(row["rule_stage"])

        time.sleep(POLL_INTERVAL)


def ue_feature_tail_loop():
    """Tail newest per_ue_training_*.csv and update per-RNTI feature gauges."""
    current_file = None
    file_handle  = None
    reader       = None

    while True:
        newest = find_newest_csv(CSV_DIR, "per_ue_training_*.csv")

        if newest != current_file:
            if file_handle:
                file_handle.close()
                file_handle = None
                reader = None
            if newest:
                log.info("Tailing new UE feature CSV: %s", newest)
                file_handle = open(newest, newline="")
                reader = csv.DictReader(file_handle)
                for _ in reader:
                    pass  # skip existing rows on first open
            current_file = newest

        if reader:
            for raw in reader:
                row = parse_ue_feature_row(raw)
                rnti = row["rnti"]
                g_ue_prb_ul.labels(rnti=rnti).set(row["prb_usage_ul_ratio"])
                g_ue_prb_dl.labels(rnti=rnti).set(row["prb_usage_dl_ratio"])
                g_ue_thp_ul_kbps.labels(rnti=rnti).set(row["thp_ul_kbps"])
                g_ue_thp_dl_kbps.labels(rnti=rnti).set(row["thp_dl_kbps"])
                g_ue_prb_direction.labels(rnti=rnti).set(row["prb_direction"])
                g_ue_ul_efficiency.labels(rnti=rnti).set(row["ul_efficiency"])

        time.sleep(0.5)
```

- [ ] **Step 2: Wire threads + `_populate_eval_ue_v4()` into `main()`**

In the `main()` function, after `_populate_eval_v2()` add `_populate_eval_ue_v4()`.
After `t3.start()` add:

```python
    _populate_eval_ue_v4()

    t1 = threading.Thread(target=csv_tail_loop,      daemon=True, name="csv-tail")
    t2 = threading.Thread(target=eval_watch_loop,    daemon=True, name="eval-watch")
    t3 = threading.Thread(target=gru_inference_loop, daemon=True, name="gru-infer")
    t4 = threading.Thread(target=ue_alert_tail_loop,   daemon=True, name="ue-alert-tail")
    t5 = threading.Thread(target=ue_feature_tail_loop, daemon=True, name="ue-feature-tail")
    t1.start(); t2.start(); t3.start(); t4.start(); t5.start()
```

The full updated `main()`:

```python
def main():
    log.info("Starting xapp Prometheus exporter on :8000")
    start_http_server(8000)
    _populate_eval_v2()
    _populate_eval_ue_v4()

    t1 = threading.Thread(target=csv_tail_loop,        daemon=True, name="csv-tail")
    t2 = threading.Thread(target=eval_watch_loop,      daemon=True, name="eval-watch")
    t3 = threading.Thread(target=gru_inference_loop,   daemon=True, name="gru-infer")
    t4 = threading.Thread(target=ue_alert_tail_loop,   daemon=True, name="ue-alert-tail")
    t5 = threading.Thread(target=ue_feature_tail_loop, daemon=True, name="ue-feature-tail")
    t1.start()
    t2.start()
    t3.start()
    t4.start()
    t5.start()

    log.info("Exporter running. Metrics at http://0.0.0.0:8000/metrics")
    while True:
        time.sleep(60)
```

- [ ] **Step 3: Smoke test — verify exporter starts and exposes new metrics**

```bash
cd /home/telmat/sec-xapp/exporter
CSV_DIR=/tmp ../venv/bin/python csv_exporter.py &
sleep 2
curl -s http://localhost:8000/metrics | grep xapp_ue_eval_recall_v4 | head -5
kill %1
```

Expected output includes lines like:
```
xapp_ue_eval_recall_v4{attack="all",config="gru_hybrid"} 0.961
xapp_ue_eval_recall_v4{attack="roq",config="gru_hybrid"} 0.922
```

- [ ] **Step 4: Run full test suite**

```bash
cd /home/telmat/sec-xapp/exporter
../venv/bin/python -m pytest test_csv_exporter.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add exporter/csv_exporter.py
git commit -m "feat: add ue_alert_tail_loop and ue_feature_tail_loop threads to exporter"
```

---

## Task 6: Create `per_ue_live.json` Grafana dashboard

**Files:**
- Create: `grafana/provisioning/dashboards/per_ue_live.json`

- [ ] **Step 1: Create the dashboard file**

```bash
cat > /home/telmat/sec-xapp/grafana/provisioning/dashboards/per_ue_live.json << 'ENDJSON'
```

Write the following content to `grafana/provisioning/dashboards/per_ue_live.json`:

```json
{
  "uid": "xapp-ue-live",
  "title": "xApp Security Monitor — Per-UE Live",
  "tags": ["xapp", "security", "per-ue", "live"],
  "timezone": "browser",
  "refresh": "5s",
  "time": { "from": "now-10m", "to": "now" },
  "schemaVersion": 38,
  "templating": {
    "list": [
      {
        "name": "rnti",
        "label": "RNTI",
        "type": "query",
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "query": { "query": "label_values(xapp_ue_thp_ul_kbps, rnti)", "refId": "StandardVariableQuery" },
        "multi": true,
        "includeAll": true,
        "current": {},
        "refresh": 2,
        "sort": 1
      }
    ]
  },
  "panels": [
    {
      "id": 1, "type": "stat", "title": "Active RNTIs",
      "gridPos": { "x": 0, "y": 0, "w": 4, "h": 4 },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "thresholds" },
          "thresholds": { "mode": "absolute", "steps": [
            { "value": null, "color": "green" },
            { "value": 1,    "color": "blue" }
          ]}
        }
      },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [{ "expr": "count(xapp_ue_thp_ul_kbps > 0) or vector(0)", "instant": true, "legendFormat": "" }]
    },
    {
      "id": 2, "type": "stat", "title": "Alerted RNTIs",
      "gridPos": { "x": 4, "y": 0, "w": 4, "h": 4 },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "thresholds" },
          "thresholds": { "mode": "absolute", "steps": [
            { "value": null, "color": "green" },
            { "value": 1,    "color": "red" }
          ]}
        }
      },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [{ "expr": "count(xapp_ue_alert_type > 0) or vector(0)", "instant": true, "legendFormat": "" }]
    },
    {
      "id": 3, "type": "stat", "title": "Selected UE Alert Status",
      "gridPos": { "x": 8, "y": 0, "w": 8, "h": 4 },
      "fieldConfig": {
        "defaults": {
          "mappings": [
            { "type": "value", "options": { "0": { "text": "NORMAL",   "color": "green"  } } },
            { "type": "value", "options": { "1": { "text": "UL FLOOD", "color": "orange" } } },
            { "type": "value", "options": { "2": { "text": "DL FLOOD", "color": "orange" } } },
            { "type": "value", "options": { "3": { "text": "BURST",    "color": "yellow" } } },
            { "type": "value", "options": { "4": { "text": "RoQ",      "color": "red"    } } }
          ],
          "thresholds": { "mode": "absolute", "steps": [
            { "value": null, "color": "green" },
            { "value": 1,    "color": "red"   }
          ]}
        }
      },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "orientation": "vertical" },
      "targets": [{ "expr": "xapp_ue_alert_type{rnti=~\"$rnti\"}", "instant": true, "legendFormat": "{{rnti}}" }]
    },
    {
      "id": 5, "type": "timeseries", "title": "Detection Stage per UE",
      "gridPos": { "x": 0, "y": 4, "w": 12, "h": 6 },
      "fieldConfig": {
        "defaults": { "min": 0, "max": 2,
          "mappings": [
            { "type": "value", "options": { "0": { "text": "Normal"   } } },
            { "type": "value", "options": { "1": { "text": "Warning"  } } },
            { "type": "value", "options": { "2": { "text": "Critical" } } }
          ]
        }
      },
      "options": { "tooltip": { "mode": "multi" } },
      "targets": [{ "expr": "xapp_ue_stage", "legendFormat": "Stage {{rnti}}" }]
    },
    {
      "id": 6, "type": "timeseries", "title": "MSE Score per UE (on alert events)",
      "gridPos": { "x": 12, "y": 4, "w": 12, "h": 6 },
      "fieldConfig": {
        "defaults": { "min": 0 },
        "overrides": [
          { "matcher": { "id": "byName", "options": "Threshold GRU-UE v4" },
            "properties": [
              { "id": "custom.lineStyle", "value": { "fill": "dash" } },
              { "id": "color", "value": { "mode": "fixed", "fixedColor": "red" } }
            ]
          }
        ]
      },
      "options": { "tooltip": { "mode": "multi" } },
      "targets": [
        { "expr": "xapp_ue_mse", "legendFormat": "MSE {{rnti}}" },
        { "expr": "0.025969 + 0 * xapp_ue_mse", "legendFormat": "Threshold GRU-UE v4" }
      ]
    },
    {
      "id": 7, "type": "timeseries", "title": "Throughput UL / DL per UE",
      "gridPos": { "x": 0, "y": 10, "w": 12, "h": 6 },
      "fieldConfig": { "defaults": { "unit": "kbps", "min": 0 } },
      "options": { "tooltip": { "mode": "multi" } },
      "targets": [
        { "expr": "xapp_ue_thp_ul_kbps", "legendFormat": "UL {{rnti}}" },
        { "expr": "xapp_ue_thp_dl_kbps", "legendFormat": "DL {{rnti}}" }
      ]
    },
    {
      "id": 8, "type": "timeseries", "title": "PRB Utilization per UE",
      "gridPos": { "x": 12, "y": 10, "w": 12, "h": 6 },
      "fieldConfig": { "defaults": { "unit": "percentunit", "min": 0, "max": 1 } },
      "options": { "tooltip": { "mode": "multi" } },
      "targets": [
        { "expr": "xapp_ue_prb_ul", "legendFormat": "PRB UL {{rnti}}" },
        { "expr": "xapp_ue_prb_dl", "legendFormat": "PRB DL {{rnti}}" }
      ]
    },
    {
      "id": 9, "type": "timeseries", "title": "PRB Direction & UL Efficiency per UE",
      "gridPos": { "x": 0, "y": 16, "w": 24, "h": 6 },
      "fieldConfig": { "defaults": { "min": -1 } },
      "options": { "tooltip": { "mode": "multi" } },
      "targets": [
        { "expr": "xapp_ue_prb_direction",  "legendFormat": "PRB Dir {{rnti}}" },
        { "expr": "xapp_ue_ul_efficiency",  "legendFormat": "UL Eff {{rnti}}" }
      ]
    }
  ]
}
```

- [ ] **Step 2: Validate JSON**

```bash
python3 -c "import json; json.load(open('grafana/provisioning/dashboards/per_ue_live.json')); print('JSON valid')"
```

Expected: `JSON valid`

- [ ] **Step 3: Commit**

```bash
git add grafana/provisioning/dashboards/per_ue_live.json
git commit -m "feat: add per-UE live Grafana dashboard (per_ue_live.json)"
```

---

## Task 7: Create `per_ue_eval.json` Grafana dashboard

**Files:**
- Create: `grafana/provisioning/dashboards/per_ue_eval.json`

- [ ] **Step 1: Create the dashboard file**

Write the following content to `grafana/provisioning/dashboards/per_ue_eval.json`:

```json
{
  "uid": "xapp-ue-eval",
  "title": "xApp Security Monitor — Per-UE Evaluation (v4)",
  "tags": ["xapp", "security", "per-ue", "evaluation"],
  "timezone": "browser",
  "refresh": "1m",
  "time": { "from": "now-5m", "to": "now" },
  "schemaVersion": 38,
  "panels": [
    {
      "id": 1, "type": "text", "title": "",
      "gridPos": { "x": 0, "y": 0, "w": 24, "h": 3 },
      "options": { "mode": "markdown", "content": "## Per-UE IDS v4 — Evaluation Results\nDataset: `csv/dataset_attack_ue_juni.csv` · 4 attack classes · interval 1s/sample\nModel aktif: **GRU-UE v4** (BiGRU [64,32], seq_len=30, 19 fitur, Weighted MSE Scheme A, threshold P97=0.025969)\nKonfigurasi terbaik: **gru_hybrid** — Recall 96.1%, F1 95.4%, FPR 5.14%, Det.Lat 4.04s" }
    },
    {
      "id": 10, "type": "stat", "title": "rule_only",
      "gridPos": { "x": 0, "y": 3, "w": 4, "h": 5 },
      "fieldConfig": { "defaults": { "decimals": 3 } },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "orientation": "vertical" },
      "targets": [
        { "expr": "xapp_ue_eval_recall_v4{config='rule_only',attack='all'}",  "instant": true, "legendFormat": "Recall" },
        { "expr": "xapp_ue_eval_f1_v4{config='rule_only',attack='all'}",      "instant": true, "legendFormat": "F1" },
        { "expr": "xapp_ue_eval_fpr_v4{config='rule_only'}",                  "instant": true, "legendFormat": "FPR" }
      ]
    },
    {
      "id": 11, "type": "stat", "title": "lstm_only",
      "gridPos": { "x": 4, "y": 3, "w": 4, "h": 5 },
      "fieldConfig": { "defaults": { "decimals": 3 } },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "orientation": "vertical" },
      "targets": [
        { "expr": "xapp_ue_eval_recall_v4{config='lstm_only',attack='all'}",  "instant": true, "legendFormat": "Recall" },
        { "expr": "xapp_ue_eval_f1_v4{config='lstm_only',attack='all'}",      "instant": true, "legendFormat": "F1" },
        { "expr": "xapp_ue_eval_fpr_v4{config='lstm_only'}",                  "instant": true, "legendFormat": "FPR" }
      ]
    },
    {
      "id": 12, "type": "stat", "title": "gru_only",
      "gridPos": { "x": 8, "y": 3, "w": 4, "h": 5 },
      "fieldConfig": { "defaults": { "decimals": 3 } },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "orientation": "vertical" },
      "targets": [
        { "expr": "xapp_ue_eval_recall_v4{config='gru_only',attack='all'}",   "instant": true, "legendFormat": "Recall" },
        { "expr": "xapp_ue_eval_f1_v4{config='gru_only',attack='all'}",       "instant": true, "legendFormat": "F1" },
        { "expr": "xapp_ue_eval_fpr_v4{config='gru_only'}",                   "instant": true, "legendFormat": "FPR" }
      ]
    },
    {
      "id": 13, "type": "stat", "title": "lstm_hybrid",
      "gridPos": { "x": 12, "y": 3, "w": 4, "h": 5 },
      "fieldConfig": { "defaults": { "decimals": 3 } },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "orientation": "vertical" },
      "targets": [
        { "expr": "xapp_ue_eval_recall_v4{config='lstm_hybrid',attack='all'}", "instant": true, "legendFormat": "Recall" },
        { "expr": "xapp_ue_eval_f1_v4{config='lstm_hybrid',attack='all'}",     "instant": true, "legendFormat": "F1" },
        { "expr": "xapp_ue_eval_fpr_v4{config='lstm_hybrid'}",                 "instant": true, "legendFormat": "FPR" }
      ]
    },
    {
      "id": 14, "type": "stat", "title": "gru_hybrid ✅ (best)",
      "gridPos": { "x": 16, "y": 3, "w": 8, "h": 5 },
      "fieldConfig": {
        "defaults": { "decimals": 3, "color": { "mode": "fixed", "fixedColor": "green" } }
      },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "orientation": "vertical" },
      "targets": [
        { "expr": "xapp_ue_eval_recall_v4{config='gru_hybrid',attack='all'}",  "instant": true, "legendFormat": "Recall" },
        { "expr": "xapp_ue_eval_f1_v4{config='gru_hybrid',attack='all'}",      "instant": true, "legendFormat": "F1" },
        { "expr": "xapp_ue_eval_fpr_v4{config='gru_hybrid'}",                  "instant": true, "legendFormat": "FPR" }
      ]
    },
    {
      "id": 20, "type": "barchart", "title": "Per-Attack Recall — GRU Hybrid",
      "gridPos": { "x": 0, "y": 8, "w": 12, "h": 7 },
      "fieldConfig": { "defaults": { "unit": "percentunit", "min": 0, "max": 1 } },
      "options": { "xField": "attack", "stacking": "none", "legend": { "displayMode": "list", "placement": "bottom" } },
      "targets": [
        { "expr": "xapp_ue_eval_recall_v4{config='gru_hybrid',attack!='all'}", "instant": true, "legendFormat": "{{attack}}" }
      ]
    },
    {
      "id": 21, "type": "barchart", "title": "Overall Recall — All 5 Configs",
      "gridPos": { "x": 12, "y": 8, "w": 12, "h": 7 },
      "fieldConfig": { "defaults": { "unit": "percentunit", "min": 0, "max": 1 } },
      "options": { "xField": "config", "stacking": "none", "legend": { "displayMode": "list", "placement": "bottom" } },
      "targets": [
        { "expr": "xapp_ue_eval_recall_v4{attack='all'}", "instant": true, "legendFormat": "{{config}}" }
      ]
    },
    {
      "id": 30, "type": "barchart", "title": "Detection Latency per Config",
      "gridPos": { "x": 0, "y": 15, "w": 12, "h": 5 },
      "fieldConfig": { "defaults": { "unit": "s", "min": 0 } },
      "options": { "xField": "config", "stacking": "none", "legend": { "displayMode": "list", "placement": "bottom" } },
      "targets": [
        { "expr": "xapp_ue_eval_det_lat_v4", "instant": true, "legendFormat": "{{config}}" }
      ]
    },
    {
      "id": 31, "type": "barchart", "title": "Mitigation Latency per Config",
      "gridPos": { "x": 12, "y": 15, "w": 12, "h": 5 },
      "fieldConfig": { "defaults": { "unit": "s", "min": 0 } },
      "options": { "xField": "config", "stacking": "none", "legend": { "displayMode": "list", "placement": "bottom" } },
      "targets": [
        { "expr": "xapp_ue_eval_mit_lat_v4", "instant": true, "legendFormat": "{{config}}" }
      ]
    }
  ]
}
```

- [ ] **Step 2: Validate JSON**

```bash
python3 -c "import json; json.load(open('grafana/provisioning/dashboards/per_ue_eval.json')); print('JSON valid')"
```

Expected: `JSON valid`

- [ ] **Step 3: Run full test suite one final time**

```bash
cd /home/telmat/sec-xapp/exporter
../venv/bin/python -m pytest test_csv_exporter.py -v
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add grafana/provisioning/dashboards/per_ue_eval.json
git commit -m "feat: add per-UE evaluation Grafana dashboard (per_ue_eval.json)"
```

---

## Verification After All Tasks

After completing all 7 tasks, verify the full stack:

```bash
# 1. Confirm 4 dashboard files exist
ls grafana/provisioning/dashboards/*.json

# 2. Confirm test suite passes
cd exporter && ../venv/bin/python -m pytest test_csv_exporter.py -v

# 3. Confirm exporter starts clean and exposes per-UE eval metrics
CSV_DIR=/tmp ../venv/bin/python csv_exporter.py &
sleep 2
curl -s http://localhost:8000/metrics | grep "xapp_ue_" | sort | head -20
kill %1
```

Expected `/metrics` output includes:
```
xapp_ue_eval_det_lat_v4{config="gru_hybrid"} 4.04
xapp_ue_eval_mit_lat_v4{config="gru_hybrid"} 4.16
xapp_ue_eval_recall_v4{attack="all",config="gru_hybrid"} 0.961
xapp_ue_eval_recall_v4{attack="roq",config="gru_hybrid"} 0.922
xapp_ue_eval_fpr_v4{config="rule_only"} 0.0293
```
