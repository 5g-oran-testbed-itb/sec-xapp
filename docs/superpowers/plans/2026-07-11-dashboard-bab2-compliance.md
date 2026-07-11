# Dashboard BAB2 Compliance Remediation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `per_ue_live.json` fully satisfy BAB2 Subobjektif 2 by adding a real E2SM-RC mitigation-history timeline, a dynamic MSE threshold line that follows the runtime-selected model, a decision-latency panel, and panel descriptions.

**Architecture:** Three layers. (1) The C monitor logs an honest mitigation event to a new CSV after the mitigate binary ACKs a successful E2SM-RC Control Request, and writes the active threshold to a sidecar file. (2) The Python exporter tails that CSV into per-RNTI Prometheus gauges and publishes the active threshold. (3) The Grafana dashboard renders a state-timeline, a latency panel, and a dynamic threshold line.

**Tech Stack:** C (FlexRIC xApp), Python 3 (`prometheus_client`, `pytest`), Grafana JSON (schemaVersion 38), Prometheus.

---

## Key facts established from the codebase

- The monitor's CSV directory is `/home/telmat/sec-xapp/csv/`; files are named with `strftime("..._%Y%m%d_%H%M%S.csv")`.
- `ipc_send_mitigate(action, prb_limit, attack, confidence, reason, ue_id)` in `copy-xapp/xapp_sec_moni.c` already receives the RNTI: at the call sites `ue_id == g_throttle_target_ue_id`, and `g_throttle_target_ue_id = rnti` (line 1926). So **no new parameter is needed** — `ue_id` is the RNTI.
- `ipc_send_mitigate` returns early on ACK timeout; the point right after `printf("[IPC] ACK received: ...")` is reached only when the E2SM-RC control was actually applied.
- The active threshold is loaded in `init_onnx_ue()` into `g_ue_threshold`, right after the model/threshold paths are chosen by `g_ids_mode`.
- The exporter tails CSVs with `find_newest_csv(CSV_DIR, "<pattern>")` inside `while True` loops started as daemon threads in `main()`. `CSV_DIR` comes from the `CSV_DIR` env var. Tests use `pytest` with `tmp_path` and import functions directly from `csv_exporter`.
- Prometheus gauge current value is readable in tests via `gauge.labels(...)._value.get()` (unlabeled: `gauge._value.get()`).

## File Structure

- **Modify** `exporter/csv_exporter.py` — add mitigation + threshold metrics, a pure parser, a metric-update helper, two tail loops, thread registration.
- **Modify** `exporter/test_csv_exporter.py` — unit tests for the parser, the update helper, and the threshold sidecar reader.
- **Modify** `copy-xapp/xapp_sec_moni.c` — open a mitigation-events CSV, append a row after ACK success, write the threshold sidecar.
- **Modify** `grafana/provisioning/dashboards/per_ue_live.json` — new state-timeline + latency panels, dynamic threshold line, panel descriptions.

Order: exporter first (fully unit-testable), then C (produces the real CSV/sidecar), then the dashboard (consumes the metrics). Run exporter tests from the `exporter/` directory.

---

## Task 1: Exporter — mitigation event parser

**Files:**
- Modify: `exporter/csv_exporter.py`
- Test: `exporter/test_csv_exporter.py`

- [ ] **Step 1: Write the failing test**

Append to `exporter/test_csv_exporter.py`:

```python
# ── mitigation event parser tests ────────────────────────────────────────────

MIT_HEADER = ["epoch_ms", "action", "rnti", "ue_id", "prb_limit", "attack", "confidence"]

def _make_mit_row(**kwargs):
    defaults = {
        "epoch_ms": "1000", "action": "THROTTLE", "rnti": "17921",
        "ue_id": "17921", "prb_limit": "0", "attack": "ul_flood",
        "confidence": "0.98",
    }
    defaults.update({k: str(v) for k, v in kwargs.items()})
    return defaults

def test_parse_mitigation_row_throttle():
    from csv_exporter import parse_mitigation_row
    row = parse_mitigation_row(_make_mit_row(action="THROTTLE", rnti="17921", prb_limit="0"))
    assert row["action"] == "THROTTLE"
    assert row["rnti"] == "17921"
    assert row["prb_limit"] == 0
    assert row["attack"] == "ul_flood"

def test_parse_mitigation_row_restore_defaults_prb_100():
    from csv_exporter import parse_mitigation_row
    row = parse_mitigation_row(_make_mit_row(action="RESTORE", prb_limit="100"))
    assert row["action"] == "RESTORE"
    assert row["prb_limit"] == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd exporter && python -m pytest test_csv_exporter.py::test_parse_mitigation_row_throttle -v`
Expected: FAIL with `ImportError: cannot import name 'parse_mitigation_row'`.

- [ ] **Step 3: Write minimal implementation**

In `exporter/csv_exporter.py`, add near the other `parse_*` functions (after `parse_ue_alert_row`):

```python
def parse_mitigation_row(raw: dict) -> dict:
    """Parse one row from mitigation_events_*.csv. Returns typed dict."""
    try:
        prb = int(float(raw.get("prb_limit", 100)))
    except (TypeError, ValueError):
        prb = 100
    return {
        "epoch_ms": raw.get("epoch_ms", ""),
        "action":   (raw.get("action", "") or "").strip().upper(),
        "rnti":     (raw.get("rnti", "") or "").strip(),
        "prb_limit": prb,
        "attack":   (raw.get("attack", "") or "").strip(),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd exporter && python -m pytest test_csv_exporter.py -k mitigation_row -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add exporter/csv_exporter.py exporter/test_csv_exporter.py
git commit -m "feat(exporter): parse mitigation_events CSV rows"
```

---

## Task 2: Exporter — mitigation metrics + update helper

**Files:**
- Modify: `exporter/csv_exporter.py`
- Test: `exporter/test_csv_exporter.py`

- [ ] **Step 1: Write the failing test**

Append to `exporter/test_csv_exporter.py`:

```python
def test_update_mitigation_metrics_throttle_then_restore():
    from csv_exporter import (
        parse_mitigation_row, update_mitigation_metrics,
        g_ue_mitigation_active, g_ue_mitigation_prb_limit, c_mitigations_applied,
    )
    # THROTTLE → active=1, prb_limit=0, counter +1
    before = c_mitigations_applied.labels(rnti="17921", attack="ul_flood")._value.get()
    update_mitigation_metrics(parse_mitigation_row(
        _make_mit_row(action="THROTTLE", rnti="17921", prb_limit="0", attack="ul_flood")))
    assert g_ue_mitigation_active.labels(rnti="17921")._value.get() == 1
    assert g_ue_mitigation_prb_limit.labels(rnti="17921")._value.get() == 0
    assert c_mitigations_applied.labels(rnti="17921", attack="ul_flood")._value.get() == before + 1

    # RESTORE → active=0, prb_limit=100, counter unchanged
    update_mitigation_metrics(parse_mitigation_row(
        _make_mit_row(action="RESTORE", rnti="17921", prb_limit="100", attack="ul_flood")))
    assert g_ue_mitigation_active.labels(rnti="17921")._value.get() == 0
    assert g_ue_mitigation_prb_limit.labels(rnti="17921")._value.get() == 100
    assert c_mitigations_applied.labels(rnti="17921", attack="ul_flood")._value.get() == before + 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd exporter && python -m pytest test_csv_exporter.py::test_update_mitigation_metrics_throttle_then_restore -v`
Expected: FAIL with `ImportError: cannot import name 'g_ue_mitigation_active'`.

- [ ] **Step 3: Write minimal implementation**

In `exporter/csv_exporter.py`, add these gauges next to the other per-UE gauges (near `c_attacks_blocked`, ~line 70):

```python
# ── E2SM-RC mitigation state (honest: logged after a confirmed Control Request) ──
g_ue_mitigation_active    = Gauge("xapp_ue_mitigation_active",
                                  "Per-UE E2SM-RC throttle active (1) or restored (0)", ["rnti"])
g_ue_mitigation_prb_limit = Gauge("xapp_ue_mitigation_prb_limit",
                                  "Per-UE current PRB cap % under throttle (100=unrestricted)", ["rnti"])
c_mitigations_applied     = Counter("xapp_mitigations_applied_total",
                                    "Cumulative E2SM-RC THROTTLE control requests actually applied",
                                    ["rnti", "attack"])
```

Then add the update helper next to `parse_mitigation_row`:

```python
def update_mitigation_metrics(row: dict) -> None:
    """Apply one parsed mitigation event to the per-RNTI gauges/counter."""
    rnti = row["rnti"]
    if not rnti:
        return
    if row["action"] == "THROTTLE":
        g_ue_mitigation_active.labels(rnti=rnti).set(1)
        g_ue_mitigation_prb_limit.labels(rnti=rnti).set(row["prb_limit"])
        c_mitigations_applied.labels(rnti=rnti, attack=row["attack"] or "unknown").inc()
    elif row["action"] == "RESTORE":
        g_ue_mitigation_active.labels(rnti=rnti).set(0)
        g_ue_mitigation_prb_limit.labels(rnti=rnti).set(row["prb_limit"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd exporter && python -m pytest test_csv_exporter.py::test_update_mitigation_metrics_throttle_then_restore -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add exporter/csv_exporter.py exporter/test_csv_exporter.py
git commit -m "feat(exporter): per-UE mitigation state gauges + applied counter"
```

---

## Task 3: Exporter — mitigation tail loop + thread

**Files:**
- Modify: `exporter/csv_exporter.py`

- [ ] **Step 1: Add the tail loop**

In `exporter/csv_exporter.py`, add after `ue_feature_tail_loop` (mirrors the existing tail-loop pattern exactly):

```python
def mitigation_tail_loop():
    """Tail newest mitigation_events_*.csv and update per-RNTI mitigation metrics."""
    current_file = None
    file_handle  = None
    reader       = None

    while True:
        newest = find_newest_csv(CSV_DIR, "mitigation_events_*.csv")

        if newest != current_file:
            if file_handle:
                file_handle.close()
                file_handle = None
                reader = None
            if newest:
                log.info("Tailing new mitigation CSV: %s", newest)
                file_handle = open(newest, newline="")
                reader = csv.DictReader(file_handle)
                for _ in reader:
                    pass  # skip existing rows on first open
            current_file = newest

        if reader:
            for raw in reader:
                update_mitigation_metrics(parse_mitigation_row(raw))

        time.sleep(POLL_INTERVAL)
```

- [ ] **Step 2: Register the thread**

In `main()` (~line 577), add the thread alongside the others:

```python
    t6 = threading.Thread(target=mitigation_tail_loop, daemon=True, name="mitigation-tail")
```

and add `t6.start()` next to `t5.start()`.

- [ ] **Step 3: Verify the module still imports and all tests pass**

Run: `cd exporter && python -c "import csv_exporter" && python -m pytest test_csv_exporter.py -v`
Expected: import succeeds, all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add exporter/csv_exporter.py
git commit -m "feat(exporter): tail mitigation_events CSV into Prometheus"
```

---

## Task 4: Exporter — active-threshold sidecar metric

**Files:**
- Modify: `exporter/csv_exporter.py`
- Test: `exporter/test_csv_exporter.py`

- [ ] **Step 1: Write the failing test**

Append to `exporter/test_csv_exporter.py`:

```python
def test_read_active_threshold_sidecar(tmp_path):
    from csv_exporter import read_active_threshold, g_ue_threshold, g_ue_model_info
    p = tmp_path / "xapp_active_threshold"
    p.write_text("0.027047 lstm_ue_v6\n")
    read_active_threshold(str(p))
    assert abs(g_ue_threshold._value.get() - 0.027047) < 1e-9
    assert g_ue_model_info.labels(model="lstm_ue_v6")._value.get() == 1

def test_read_active_threshold_missing_file_noop(tmp_path):
    from csv_exporter import read_active_threshold
    # Should not raise when the sidecar does not exist yet.
    read_active_threshold(str(tmp_path / "does_not_exist"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd exporter && python -m pytest test_csv_exporter.py -k active_threshold -v`
Expected: FAIL with `ImportError: cannot import name 'read_active_threshold'`.

- [ ] **Step 3: Write minimal implementation**

In `exporter/csv_exporter.py`, add the gauges near the other definitions:

```python
# ── Active per-UE ML threshold (mirrors whatever model the xApp loaded) ──────
g_ue_threshold  = Gauge("xapp_ue_threshold", "Active per-UE ML decision threshold (loaded model)")
g_ue_model_info = Gauge("xapp_ue_model_info", "Active per-UE model (value=1)", ["model"])
ACTIVE_THRESHOLD_PATH = os.getenv("ACTIVE_THRESHOLD_PATH", "/tmp/xapp_active_threshold")
_model_info_last = {"model": None}
```

Add the reader function:

```python
def read_active_threshold(path: str = None) -> None:
    """Read '<value> <model_name>' sidecar written by the xApp; set threshold gauges."""
    path = path or ACTIVE_THRESHOLD_PATH
    try:
        with open(path) as f:
            parts = f.read().split()
    except OSError:
        return
    if not parts:
        return
    try:
        g_ue_threshold.set(float(parts[0]))
    except ValueError:
        return
    if len(parts) >= 2:
        model = parts[1]
        if _model_info_last["model"] and _model_info_last["model"] != model:
            g_ue_model_info.labels(model=_model_info_last["model"]).set(0)
        g_ue_model_info.labels(model=model).set(1)
        _model_info_last["model"] = model
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd exporter && python -m pytest test_csv_exporter.py -k active_threshold -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Poll the sidecar from a background thread**

Add a small loop after `mitigation_tail_loop`:

```python
def threshold_watch_loop():
    """Periodically refresh the active-threshold gauges from the sidecar file."""
    while True:
        read_active_threshold()
        time.sleep(EVAL_POLL)
```

Register it in `main()`:

```python
    t7 = threading.Thread(target=threshold_watch_loop, daemon=True, name="threshold-watch")
```

and add `t7.start()` next to the others.

- [ ] **Step 6: Verify import + full test suite**

Run: `cd exporter && python -c "import csv_exporter" && python -m pytest test_csv_exporter.py -v`
Expected: import succeeds, all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add exporter/csv_exporter.py exporter/test_csv_exporter.py
git commit -m "feat(exporter): publish active ML threshold + model from sidecar"
```

---

## Task 5: C monitor — write mitigation events + threshold sidecar

**Files:**
- Modify: `copy-xapp/xapp_sec_moni.c`

> No unit-test harness exists for the C xApp; this task is verified by a clean build and confirmed by the end-to-end check in Task 7.

- [ ] **Step 1: Add a global FILE* for the mitigation CSV**

Near the other alert-file global (`g_ue_alert_fp`), add:

```c
static FILE* g_mit_fp = NULL;   /* mitigation_events_*.csv — honest E2SM-RC control log */
```

- [ ] **Step 2: Open the mitigation CSV in the setup block**

In the CSV-setup region of `main()` (right after the `ue_alerts_%Y%m%d_%H%M%S.csv` block, ~line 2430), add:

```c
  /* Open mitigation-events CSV — one row per confirmed E2SM-RC control */
  {
      char mit_path[256];
      time_t now_m = time(NULL);
      struct tm *tm_m = localtime(&now_m);
      strftime(mit_path, sizeof(mit_path),
               "/home/telmat/sec-xapp/csv/mitigation_events_%Y%m%d_%H%M%S.csv",
               tm_m);
      g_mit_fp = fopen(mit_path, "w");
      if (g_mit_fp) {
          fprintf(g_mit_fp,
                  "epoch_ms,action,rnti,ue_id,prb_limit,attack,confidence\n");
          fflush(g_mit_fp);
      }
  }
  defer({ if (g_mit_fp) fclose(g_mit_fp); });
```

- [ ] **Step 3: Append a row after a successful ACK**

In `ipc_send_mitigate()`, immediately after `printf("[IPC] ACK received: %s\n", ack_buf);` and its `fflush(stdout);` (the code path reached only on a real applied control), add:

```c
    if (g_mit_fp) {
        struct timespec ts_mit;
        clock_gettime(CLOCK_REALTIME, &ts_mit);
        uint64_t epoch_ms = (uint64_t)ts_mit.tv_sec * 1000ULL
                            + ts_mit.tv_nsec / 1000000ULL;
        /* ue_id == RNTI at all call sites (g_throttle_target_ue_id = rnti). */
        fprintf(g_mit_fp, "%llu,%s,%u,%u,%d,%s,%.4f\n",
                (unsigned long long)epoch_ms, action, ue_id, ue_id,
                prb_limit, attack ? attack : "unknown", (double)confidence);
        fflush(g_mit_fp);
    }
```

- [ ] **Step 4: Write the active-threshold sidecar**

In `init_onnx_ue()`, add a `model_name` to each loaded case and write the sidecar right after the existing `printf("[IDS-UE] Threshold: %.2f ...")` line. Replace the two model/threshold case bodies so each also sets a name, e.g. in the LSTM case add `const char *model_name = "lstm_ue_v6";` and in the GRU case `const char *model_name = "gru_ue_v5";` (declare `const char *model_name = "unknown";` at the top of the function beside `model_path`). Then after the threshold `printf`:

```c
    {
        FILE *tf = fopen("/tmp/xapp_active_threshold", "w");
        if (tf) {
            fprintf(tf, "%.6f %s\n", g_ue_threshold, model_name);
            fclose(tf);
        }
    }
```

- [ ] **Step 5: Build**

Run: `cd /home/telmat/flexric/build && make -j$(nproc) xapp_sec_moni`
Expected: compiles and links with no errors; `examples/xApp/c/monitor/xapp_sec_moni` is rebuilt.

- [ ] **Step 6: Commit**

```bash
git add copy-xapp/xapp_sec_moni.c
git commit -m "feat(xapp): log confirmed E2SM-RC mitigations + active-threshold sidecar"
```

---

## Task 6: Dashboard — mitigation timeline, latency, dynamic threshold, descriptions

**Files:**
- Modify: `grafana/provisioning/dashboards/per_ue_live.json`

- [ ] **Step 1: Fix the MSE threshold line (panel id 6)**

In panel id 6 (`"MSE Score per UE (on alert events)"`), replace the second target (the constant-expression threshold) so it tracks the loaded model. Change:

```json
        {
          "expr": "0.025969 + 0 * count(xapp_ue_mse)",
          "legendFormat": "Threshold GRU-UE v4 (0.0260)"
        }
```

to:

```json
        {
          "expr": "xapp_ue_threshold",
          "legendFormat": "Threshold (active model)"
        }
```

Also update the override `matcher.options` from `"Threshold GRU-UE v4"` to `"Threshold (active model)"` so the dashed red style still applies.

- [ ] **Step 2: De-stale the Avg MSE stat (panel id 12)**

In panel id 12 (`"Avg MSE (on alert)"`), remove the misleading hardcoded orange step. Replace its `thresholds.steps` with:

```json
            "steps": [
              { "value": null, "color": "green" }
            ]
```

and add a `"description"` field to the panel object:

```json
      "description": "Rata-rata MSE UE saat alert. Ambang batas keputusan aktif ditampilkan sebagai garis putus-putus di panel 'MSE Score per UE' (mengikuti model yang dimuat: GRU-UE v5 = 0.0260 atau LSTM-UE v6 = 0.0270).",
```

- [ ] **Step 3: Add the mitigation-history state-timeline panel**

Append this panel object to the `"panels"` array (new id 30, placed below the resource panels at y=38):

```json
    {
      "id": 30,
      "type": "state-timeline",
      "title": "Riwayat Mitigasi E2SM-RC (PRB Throttle per UE)",
      "description": "Riwayat aksi mitigasi E2SM-RC yang benar-benar terkirim (dicatat xApp setelah ACK Control Request). Merah = throttle aktif (PRB dibatasi), hijau = normal/restore.",
      "gridPos": { "x": 0, "y": 38, "w": 24, "h": 6 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": {
        "defaults": {
          "custom": { "fillOpacity": 80, "lineWidth": 0 },
          "mappings": [
            { "type": "value", "options": { "0": { "text": "Normal",    "color": "green" } } },
            { "type": "value", "options": { "1": { "text": "Throttled", "color": "red"   } } }
          ],
          "color": { "mode": "thresholds" },
          "thresholds": {
            "mode": "absolute",
            "steps": [
              { "value": null, "color": "green" },
              { "value": 1, "color": "red" }
            ]
          }
        }
      },
      "options": {
        "mergeValues": true,
        "showValue": "auto",
        "alignValue": "left",
        "rowHeight": 0.9,
        "legend": { "displayMode": "list", "placement": "bottom" }
      },
      "targets": [
        {
          "expr": "xapp_ue_mitigation_active",
          "legendFormat": "RNTI {{rnti}}",
          "datasource": { "type": "prometheus", "uid": "prometheus" }
        }
      ]
    }
```

- [ ] **Step 4: Add the decision-latency panel**

Append this panel object (new id 31, at y=44):

```json
    {
      "id": 31,
      "type": "timeseries",
      "title": "Latensi Keputusan Mitigasi (detect → confirm → total)",
      "description": "Latensi keputusan xApp per event terakhir (nilai global, bukan per-UE). Garis 1000 ms adalah batas atas constraint Subobjektif 4 (< 1 detik).",
      "gridPos": { "x": 0, "y": 44, "w": 24, "h": 6 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": {
        "defaults": {
          "unit": "ms",
          "min": 0,
          "color": { "mode": "palette-classic" }
        },
        "overrides": [
          {
            "matcher": { "id": "byName", "options": "Batas 1 s" },
            "properties": [
              { "id": "custom.lineStyle", "value": { "fill": "dash" } },
              { "id": "color", "value": { "mode": "fixed", "fixedColor": "red" } }
            ]
          }
        ]
      },
      "options": { "tooltip": { "mode": "multi" } },
      "targets": [
        { "expr": "xapp_latency_detect_ms",  "legendFormat": "Detect (0→1)",  "datasource": { "type": "prometheus", "uid": "prometheus" } },
        { "expr": "xapp_latency_confirm_ms", "legendFormat": "Confirm (1→2)", "datasource": { "type": "prometheus", "uid": "prometheus" } },
        { "expr": "xapp_latency_total_ms",   "legendFormat": "Total (0→2)",   "datasource": { "type": "prometheus", "uid": "prometheus" } },
        { "expr": "1000 + 0 * vector(1)",    "legendFormat": "Batas 1 s",          "datasource": { "type": "prometheus", "uid": "prometheus" } }
      ]
    }
```

- [ ] **Step 5: Add descriptions to key existing panels**

Add a `"description"` field to these panel objects (insert after each panel's `"title"`):

- Panel id 14 (`"UE Aktif — Status Real-Time"`):
  ```json
      "description": "Pemetaan UE aktif per RNTI dengan metrik trafik, tipe alert (rule-based), dan stage deteksi hibrida secara real-time.",
  ```
- Panel id 20 (`"Total Blocked Attacks"`):
  ```json
      "description": "Jumlah kumulatif eskalasi ke Stage 2 (proxy deteksi). Untuk aksi E2SM-RC yang benar-benar terkirim lihat panel 'Riwayat Mitigasi E2SM-RC'.",
  ```
- Panel id 30 already has a description (Step 3); panel id 31 already has one (Step 4).

- [ ] **Step 6: Validate the JSON**

Run: `python -m json.tool grafana/provisioning/dashboards/per_ue_live.json > /dev/null && echo VALID`
Expected: prints `VALID` (no JSON errors).

- [ ] **Step 7: Commit**

```bash
git add grafana/provisioning/dashboards/per_ue_live.json
git commit -m "feat(dashboard): mitigation timeline, latency panel, dynamic threshold, descriptions"
```

---

## Task 7: End-to-end verification

**Files:** none (runtime verification).

- [ ] **Step 1: Restart the stack with mitigation**

Run: `./start_xapp_c_mitigate.sh` and at the prompt select cell-mode Hybrid and per-UE `gru-hybrid`. Attach a UE.
Expected: `xapp_sec_moni`, `xapp_sec_mitigate`, RIC, and the exporter all come up; `/tmp/xapp_active_threshold` is created containing `0.026026 gru_ue_v5`.

- [ ] **Step 2: Trigger an attack and confirm the honest event**

Run a scripted UL flood (per `~/xapp/security-scripts/attacks/ul_flood.sh`) long enough to reach Stage 2.
Expected: monitor logs `[IPC] ACK received`, and a new `/home/telmat/sec-xapp/csv/mitigation_events_*.csv` gains a `THROTTLE` row with the attacker RNTI; after the attack subsides a `RESTORE` row appears.

- [ ] **Step 3: Confirm Prometheus metrics**

Run: `curl -s localhost:8000/metrics | grep -E "xapp_ue_mitigation_active|xapp_mitigations_applied_total|xapp_ue_threshold"`
Expected: `xapp_ue_mitigation_active{rnti="..."} 1` during throttle (→ 0 after restore), `xapp_mitigations_applied_total` ≥ 1, `xapp_ue_threshold 0.026026`.

- [ ] **Step 4: Confirm the dashboard**

Open Grafana → "xApp Security Monitor — Per-UE Live". 
Expected: the "Riwayat Mitigasi E2SM-RC" timeline shows a red band during the attack; the "Latensi Keputusan Mitigasi" panel shows values under the 1000 ms line; the MSE threshold dashed line reads ≈0.0260 (GRU v5). Optionally re-run in `lstm-hybrid` and confirm the line moves to ≈0.0270.

- [ ] **Step 5: Final commit (if any docs/notes changed)**

```bash
git add -A && git commit -m "docs: dashboard BAB2 compliance verified end-to-end" || echo "nothing to commit"
```

---

## Self-review notes

- **Spec coverage:** mitigation-history (Tasks 1–3, 5, 6-Step3), honest counter (Task 2), dynamic threshold (Tasks 4, 5-Step4, 6-Step1/2), latency panel (6-Step4), descriptions (6-Step5), tests (Tasks 1,2,4), C build (5), e2e (7). All spec sections mapped.
- **rnti vs ue_id:** resolved — `ue_id == rnti` at every `ipc_send_mitigate` call site (`g_throttle_target_ue_id = rnti`); CSV logs both columns with the same value for forward-compat.
- **Naming consistency:** `parse_mitigation_row` / `update_mitigation_metrics` / `mitigation_tail_loop` / `read_active_threshold` / `threshold_watch_loop`; gauges `xapp_ue_mitigation_active`, `xapp_ue_mitigation_prb_limit`, `xapp_mitigations_applied_total`, `xapp_ue_threshold`, `xapp_ue_model_info` — used identically across exporter tasks and dashboard queries.
- **CSV dir:** reuses the monitor's existing `/home/telmat/sec-xapp/csv/` path; exporter selects newest by glob, matching `ue_alerts_*` handling.
