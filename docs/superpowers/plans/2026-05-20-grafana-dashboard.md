# Grafana Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Docker-based Grafana dashboard system with two pages — real-time KPM metrics (main page) and offline evaluation results (testing page) — for the sec-xapp hybrid IDS.

**Architecture:** A Python exporter container tails the live CSV written by `xapp_sec_moni`, runs ONNX inference and rule-based detection inline, and exposes Prometheus metrics. Grafana scrapes Prometheus and renders two pre-provisioned dashboards. Testing metrics are updated by running `evaluate_detection.py --output results/eval_results.json` manually.

**Tech Stack:** Python 3.11, prometheus-client, onnxruntime, watchdog, Docker, Grafana 10.x, Prometheus 2.x, docker-compose v2

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `docker-compose.yml` | Create | Orchestrates grafana, prometheus, csv-exporter |
| `prometheus/prometheus.yml` | Create | Scrape config: exporter at port 8000, 2s interval |
| `grafana/provisioning/datasources/prometheus.yml` | Create | Auto-provision Prometheus datasource |
| `grafana/provisioning/dashboards/dashboards.yml` | Create | Tell Grafana where to find dashboard JSON |
| `grafana/provisioning/dashboards/main.json` | Create | Main Page dashboard (KPM + detection live) |
| `grafana/provisioning/dashboards/testing.json` | Create | Testing Page dashboard (eval results) |
| `exporter/Dockerfile` | Create | Python 3.11 slim image with dependencies |
| `exporter/requirements.txt` | Create | prometheus-client, onnxruntime, watchdog |
| `exporter/csv_exporter.py` | Create | Core exporter: CSV tail + ONNX + rule check + eval JSON |
| `exporter/test_csv_exporter.py` | Create | Unit tests for exporter logic |
| `results/.gitkeep` | Create | Placeholder so results/ dir exists in repo |
| `evaluate_detection.py` | Modify | Add `--output` flag to write eval_results.json |

---

## Task 1: Add `--output` to evaluate_detection.py

**Files:**
- Modify: `evaluate_detection.py` (lines 307–459)
- Test: manual run

The `run_evaluation` function needs to optionally write a JSON file with all metrics structured for the exporter to consume.

- [ ] **Step 1: Read the current file structure**

  Open `evaluate_detection.py`. The key constants are at top:
  ```python
  LABEL_NAMES = {0: "Normal", 1: "UL Flood", 2: "DL Flood",
                 3: "Burst ON/OFF", 4: "RRC Storm", 5: "RF Jammer"}
  ```
  The attack label→key mapping we will use in JSON:
  ```
  1 → "ul_flood", 2 → "dl_flood", 3 → "burst", 4 → "rrc_storm", 5 → "rf_jammer"
  ```

- [ ] **Step 2: Add `--output` argument to argparse**

  In `evaluate_detection.py`, find the `if __name__ == "__main__":` block (line ~454) and add the argument:

  ```python
  if __name__ == "__main__":
      parser = argparse.ArgumentParser()
      parser.add_argument("--csv",    default=DEFAULT_CSV,  help="Path ke dataset CSV")
      parser.add_argument("--model",  default=ONNX_MODEL,   help="Path ke ONNX model")
      parser.add_argument("--output", default=None,          help="Tulis hasil evaluasi ke JSON (opsional)")
      args = parser.parse_args()
      run_evaluation(args.csv, args.model, output_path=args.output)
  ```

- [ ] **Step 3: Add missing imports to top of `evaluate_detection.py`**

  Find the existing import block (lines 9–15). Add these three imports right after `import argparse`:

  ```python
  import json
  import os
  import datetime
  ```

- [ ] **Step 4: Add `output_path` parameter to `run_evaluation` and JSON writer**

  Change the function signature from `def run_evaluation(csv_path, onnx_path):` to `def run_evaluation(csv_path, onnx_path, output_path=None):`.

  Add a helper function ABOVE `run_evaluation`:

  ```python
  ATTACK_KEY = {1: "ul_flood", 2: "dl_flood", 3: "burst", 4: "rrc_storm", 5: "rf_jammer"}

  def _build_eval_json(labels, rule_sev, lstm_sev, final_sev, y_true, csv_path):
      """Return dict matching eval_results.json schema."""
      from sklearn.metrics import (accuracy_score, precision_score,
                                   recall_score, f1_score, confusion_matrix)

      def stage_metrics(pred_sev):
          y_pred = (pred_sev >= 1).astype(int)
          normal_mask = labels == 0
          n_normal = normal_mask.sum()
          fp = (pred_sev[normal_mask] >= 1).sum()
          # avg latency: mean timestep index of first detection per attack sequence
          # simplified: use 300ms per rule window as proxy
          return {
              "accuracy":   float(accuracy_score(y_true, y_pred)),
              "recall":     float(recall_score(y_true, y_pred, zero_division=0)),
              "precision":  float(precision_score(y_true, y_pred, zero_division=0)),
              "f1":         float(f1_score(y_true, y_pred, zero_division=0)),
              "fpr":        float(fp / n_normal) if n_normal > 0 else 0.0,
          }

      def attack_metrics(lbl, pred_sev):
          mask = labels == lbl
          if mask.sum() == 0:
              return None
          y_t = (labels[mask] != 0).astype(int)
          y_p = (pred_sev[mask] >= 1).astype(int)
          return {
              "recall":     float(recall_score(y_t, y_p, zero_division=0)),
              "precision":  float(precision_score(y_t, y_p, zero_division=0)),
              "f1":         float(f1_score(y_t, y_p, zero_division=0)),
              "count":      int(mask.sum()),
          }

      per_stage = {
          "stage1":  stage_metrics(rule_sev),
          "stage2":  stage_metrics(lstm_sev),
          "hybrid":  stage_metrics(final_sev),
      }

      per_attack = {}
      for lbl, key in ATTACK_KEY.items():
          entry = {}
          for stage_name, sev in [("stage1", rule_sev), ("stage2", lstm_sev), ("hybrid", final_sev)]:
              m = attack_metrics(lbl, sev)
              if m:
                  entry[stage_name] = m
          if entry:
              per_attack[key] = entry

      return {
          "timestamp": datetime.datetime.now().isoformat(),
          "dataset": str(csv_path),
          "per_stage": per_stage,
          "per_attack": per_attack,
      }
  ```

- [ ] **Step 5: Call the JSON writer at end of `run_evaluation`**

  At the very end of `run_evaluation`, just before `print()` (last line), add:

  ```python
      if output_path:
          os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
          result = _build_eval_json(labels, rule_sev, lstm_sev, final_sev, y_true, csv_path)
          with open(output_path, "w") as f:
              json.dump(result, f, indent=2)
          print(f"\n[OK] Hasil evaluasi ditulis ke: {output_path}")
  ```

- [ ] **Step 6: Test the output**

  ```bash
  cd /home/telmat/sec-xapp
  python3 evaluate_detection.py \
    --csv csv/dataset_testing_v2_with_empty.csv \
    --output results/eval_results.json
  ```

  Expected: script runs normally, then prints `[OK] Hasil evaluasi ditulis ke: results/eval_results.json`.

  Verify JSON structure:
  ```bash
  python3 -c "
  import json
  d = json.load(open('results/eval_results.json'))
  print('Keys:', list(d.keys()))
  print('Stages:', list(d['per_stage'].keys()))
  print('Attacks:', list(d['per_attack'].keys()))
  print('Hybrid accuracy:', d['per_stage']['hybrid']['accuracy'])
  "
  ```
  Expected output:
  ```
  Keys: ['timestamp', 'dataset', 'per_stage', 'per_attack']
  Stages: ['stage1', 'stage2', 'hybrid']
  Attacks: ['ul_flood', 'dl_flood', 'burst', 'rrc_storm', 'rf_jammer']
  Hybrid accuracy: 0.94...
  ```

- [ ] **Step 7: Create results placeholder**

  ```bash
  touch /home/telmat/sec-xapp/results/.gitkeep
  ```

---

## Task 2: Docker Infrastructure

**Files:**
- Create: `docker-compose.yml`
- Create: `prometheus/prometheus.yml`
- Create: `grafana/provisioning/datasources/prometheus.yml`
- Create: `grafana/provisioning/dashboards/dashboards.yml`

- [ ] **Step 1: Create `prometheus/prometheus.yml`**

  ```bash
  mkdir -p /home/telmat/sec-xapp/prometheus
  ```

  Create `/home/telmat/sec-xapp/prometheus/prometheus.yml`:
  ```yaml
  global:
    scrape_interval: 2s
    evaluation_interval: 2s

  scrape_configs:
    - job_name: 'xapp-exporter'
      static_configs:
        - targets: ['csv-exporter:8000']
  ```

- [ ] **Step 2: Create Grafana provisioning dirs and datasource**

  ```bash
  mkdir -p /home/telmat/sec-xapp/grafana/provisioning/datasources
  mkdir -p /home/telmat/sec-xapp/grafana/provisioning/dashboards
  ```

  Create `/home/telmat/sec-xapp/grafana/provisioning/datasources/prometheus.yml`:
  ```yaml
  apiVersion: 1

  datasources:
    - name: Prometheus
      type: prometheus
      access: proxy
      url: http://prometheus:9090
      isDefault: true
      editable: false
  ```

- [ ] **Step 3: Create dashboard provisioner config**

  Create `/home/telmat/sec-xapp/grafana/provisioning/dashboards/dashboards.yml`:
  ```yaml
  apiVersion: 1

  providers:
    - name: 'xapp-dashboards'
      orgId: 1
      type: file
      disableDeletion: false
      updateIntervalSeconds: 10
      options:
        path: /etc/grafana/provisioning/dashboards
        foldersFromFilesStructure: false
  ```

- [ ] **Step 4: Create `docker-compose.yml`**

  Create `/home/telmat/sec-xapp/docker-compose.yml`:
  ```yaml
  version: '3.8'

  services:
    csv-exporter:
      build: ./exporter
      container_name: xapp-exporter
      ports:
        - "8000:8000"
      volumes:
        - ./csv:/data/csv:ro
        - ./results:/data/results:ro
        - ./security_model.onnx:/data/security_model.onnx:ro
        - ./security_model.onnx.data:/data/security_model.onnx.data:ro
      environment:
        - CSV_DIR=/data/csv
        - EVAL_JSON=/data/results/eval_results.json
        - ONNX_MODEL=/data/security_model.onnx
        - GRAFANA_URL=http://grafana:3000
        - GRAFANA_TOKEN=admin:admin
      restart: unless-stopped
      depends_on:
        - grafana

    prometheus:
      image: prom/prometheus:latest
      container_name: xapp-prometheus
      ports:
        - "9090:9090"
      volumes:
        - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
        - prometheus_data:/prometheus
      command:
        - '--config.file=/etc/prometheus/prometheus.yml'
        - '--storage.tsdb.retention.time=7d'
      restart: unless-stopped

    grafana:
      image: grafana/grafana:10.4.0
      container_name: xapp-grafana
      ports:
        - "3000:3000"
      volumes:
        - ./grafana/provisioning:/etc/grafana/provisioning:ro
        - grafana_data:/var/lib/grafana
      environment:
        - GF_SECURITY_ADMIN_USER=admin
        - GF_SECURITY_ADMIN_PASSWORD=admin
        - GF_AUTH_ANONYMOUS_ENABLED=false
        - GF_USERS_ALLOW_SIGN_UP=false
      restart: unless-stopped

  volumes:
    prometheus_data:
    grafana_data:
  ```

- [ ] **Step 5: Verify infrastructure starts (without exporter image yet)**

  ```bash
  cd /home/telmat/sec-xapp
  docker compose up -d prometheus grafana
  sleep 5
  curl -s http://localhost:3000/api/health | python3 -m json.tool
  ```
  Expected: `{"commit": "...", "database": "ok", "version": "10.4.0"}`

  ```bash
  curl -s http://localhost:9090/-/healthy
  ```
  Expected: `Prometheus Server is Healthy.`

  ```bash
  docker compose down
  ```

---

## Task 3: Python Exporter — Core CSV Tailing

**Files:**
- Create: `exporter/requirements.txt`
- Create: `exporter/Dockerfile`
- Create: `exporter/test_csv_exporter.py` (tests first)
- Create: `exporter/csv_exporter.py`

- [ ] **Step 1: Create `exporter/requirements.txt`**

  ```bash
  mkdir -p /home/telmat/sec-xapp/exporter
  ```

  Create `/home/telmat/sec-xapp/exporter/requirements.txt`:
  ```
  prometheus-client==0.20.0
  onnxruntime==1.18.1
  watchdog==4.0.1
  numpy==1.26.4
  requests==2.32.3
  ```

- [ ] **Step 2: Create `exporter/Dockerfile`**

  Create `/home/telmat/sec-xapp/exporter/Dockerfile`:
  ```dockerfile
  FROM python:3.11-slim

  WORKDIR /app
  COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt

  COPY csv_exporter.py .

  EXPOSE 8000
  CMD ["python3", "-u", "csv_exporter.py"]
  ```

- [ ] **Step 3: Write failing tests for CSV tail logic**

  Create `/home/telmat/sec-xapp/exporter/test_csv_exporter.py`:
  ```python
  """Unit tests for csv_exporter.py"""
  import csv
  import os
  import tempfile
  import time
  import pytest


  # ── CsvFinder tests ─────────────────────────────────────────────────────────

  def test_find_newest_csv_returns_latest(tmp_path):
      """find_newest_csv returns the most recently modified CSV in the dir."""
      from csv_exporter import find_newest_csv
      (tmp_path / "old.csv").write_text("a")
      time.sleep(0.05)
      newest = tmp_path / "new.csv"
      newest.write_text("b")
      assert find_newest_csv(str(tmp_path)) == str(newest)


  def test_find_newest_csv_returns_none_when_empty(tmp_path):
      from csv_exporter import find_newest_csv
      assert find_newest_csv(str(tmp_path)) is None


  # ── parse_csv_row tests ──────────────────────────────────────────────────────

  HEADER = [
      "timestamp_ms", "datetime",
      "prb_usage_dl_ratio", "prb_usage_ul_ratio",
      "cqi", "rach_preamble", "air_delay_ul",
      "prb_direction", "prb_total",
      "prb_dl_delta", "prb_ul_delta", "prb_burst_index",
      "label", "empty_ind_rate",
      "prb_dl_roll_mean", "prb_dl_roll_std",
      "prb_ul_roll_std", "prb_ul_roll_max", "prb_ul_roll_max_100",
  ]

  def _make_row(**kwargs):
      defaults = {h: "0.0" for h in HEADER}
      defaults["timestamp_ms"] = "1000"
      defaults["datetime"] = "2026-01-01 00:00:00"
      defaults["label"] = "0"
      defaults.update({k: str(v) for k, v in kwargs.items()})
      return defaults


  def test_parse_csv_row_converts_floats():
      from csv_exporter import parse_csv_row
      row = _make_row(prb_usage_dl_ratio="0.42", prb_usage_ul_ratio="0.87", cqi="15")
      result = parse_csv_row(row)
      assert abs(result["prb_usage_dl_ratio"] - 0.42) < 1e-6
      assert abs(result["prb_usage_ul_ratio"] - 0.87) < 1e-6
      assert abs(result["cqi"] - 15.0) < 1e-6


  def test_parse_csv_row_handles_empty_string():
      from csv_exporter import parse_csv_row
      row = _make_row(air_delay_ul="")
      result = parse_csv_row(row)
      assert result["air_delay_ul"] == 0.0


  # ── SimpleRuleEngine tests ───────────────────────────────────────────────────

  def test_rule_engine_ul_flood_triggers_warning():
      """3 consecutive rows with PRB_UL > 0.80 → stage = 1."""
      from csv_exporter import SimpleRuleEngine
      engine = SimpleRuleEngine()
      row = _make_row(prb_usage_ul_ratio="0.85")
      parsed = {k: float(v) for k, v in row.items()
                if k not in ("datetime",) and _is_float(v)}
      stage = 0
      for _ in range(3):
          stage = engine.update(parsed)
      assert stage >= 1


  def test_rule_engine_normal_stays_zero():
      """Normal PRB levels keep stage at 0."""
      from csv_exporter import SimpleRuleEngine
      engine = SimpleRuleEngine()
      row = _make_row(prb_usage_ul_ratio="0.10", prb_usage_dl_ratio="0.10")
      parsed = {k: float(v) for k, v in row.items()
                if k not in ("datetime",) and _is_float(v)}
      for _ in range(10):
          stage = engine.update(parsed)
      assert stage == 0


  def _is_float(v):
      try:
          float(v)
          return True
      except (ValueError, TypeError):
          return False


  # ── EvalResultsLoader tests ──────────────────────────────────────────────────

  def test_eval_loader_reads_json(tmp_path):
      import json
      from csv_exporter import load_eval_results
      data = {
          "timestamp": "2026-05-20T10:00:00",
          "dataset": "test.csv",
          "per_stage": {
              "hybrid": {"accuracy": 0.941, "recall": 0.962,
                         "precision": 0.958, "f1": 0.960, "fpr": 0.005}
          },
          "per_attack": {
              "ul_flood": {
                  "hybrid": {"recall": 0.991, "precision": 0.985,
                             "f1": 0.988, "count": 1000}
              }
          }
      }
      p = tmp_path / "eval_results.json"
      p.write_text(json.dumps(data))
      result = load_eval_results(str(p))
      assert result["per_stage"]["hybrid"]["accuracy"] == pytest.approx(0.941)
      assert result["per_attack"]["ul_flood"]["hybrid"]["f1"] == pytest.approx(0.988)


  def test_eval_loader_returns_none_when_missing():
      from csv_exporter import load_eval_results
      assert load_eval_results("/nonexistent/path.json") is None
  ```

- [ ] **Step 4: Run tests to verify they all fail**

  ```bash
  cd /home/telmat/sec-xapp/exporter
  pip install prometheus-client onnxruntime watchdog numpy requests pytest 2>/dev/null | tail -1
  python3 -m pytest test_csv_exporter.py -v 2>&1 | head -30
  ```
  Expected: all tests FAIL with `ModuleNotFoundError: No module named 'csv_exporter'`

- [ ] **Step 5: Create `exporter/csv_exporter.py` — foundation**

  Create `/home/telmat/sec-xapp/exporter/csv_exporter.py`:
  ```python
  """
  Prometheus exporter for sec-xapp.
  Tails live CSV from xapp_sec_moni, runs rule check + ONNX inference,
  exposes /metrics. Also watches eval_results.json for testing page metrics.
  """
  import csv
  import json
  import os
  import threading
  import time
  import glob
  import logging
  import requests
  import numpy as np
  import onnxruntime as ort
  from prometheus_client import Gauge, start_http_server

  logging.basicConfig(level=logging.INFO,
                      format="%(asctime)s %(levelname)s %(message)s")
  log = logging.getLogger(__name__)

  # ── Config from environment ──────────────────────────────────────────────────
  CSV_DIR      = os.getenv("CSV_DIR",      "/data/csv")
  EVAL_JSON    = os.getenv("EVAL_JSON",    "/data/results/eval_results.json")
  ONNX_MODEL   = os.getenv("ONNX_MODEL",  "/data/security_model.onnx")
  GRAFANA_URL  = os.getenv("GRAFANA_URL",  "http://grafana:3000")
  GRAFANA_TOKEN = os.getenv("GRAFANA_TOKEN", "admin:admin")
  POLL_INTERVAL = 1.0   # seconds between CSV tail polls
  EVAL_POLL     = 10.0  # seconds between eval JSON checks

  # ── Prometheus metrics ───────────────────────────────────────────────────────
  g_prb_dl      = Gauge("xapp_prb_dl_ratio",     "PRB DL utilization (0-1)")
  g_prb_ul      = Gauge("xapp_prb_ul_ratio",     "PRB UL utilization (0-1)")
  g_cqi         = Gauge("xapp_cqi",              "Channel Quality Indicator")
  g_rach        = Gauge("xapp_rach_preamble",    "RACH preamble count")
  g_air_delay   = Gauge("xapp_air_delay_ul_ms",  "UL air delay (ms)")
  g_anomaly     = Gauge("xapp_anomaly_score",    "LSTM anomaly score")
  g_stage       = Gauge("xapp_detection_stage",  "Detection stage: 0=normal 1=warn 2=crit")

  g_eval_acc    = Gauge("xapp_eval_accuracy",    "Eval accuracy",   ["stage"])
  g_eval_rec    = Gauge("xapp_eval_recall",      "Eval recall",     ["stage", "attack"])
  g_eval_prec   = Gauge("xapp_eval_precision",   "Eval precision",  ["stage", "attack"])
  g_eval_f1     = Gauge("xapp_eval_f1",          "Eval F1",         ["stage", "attack"])
  g_eval_fpr    = Gauge("xapp_eval_fpr",         "Eval FPR",        ["stage"])

  # ── CSV columns we care about ────────────────────────────────────────────────
  FLOAT_COLS = [
      "prb_usage_dl_ratio", "prb_usage_ul_ratio",
      "cqi", "rach_preamble", "air_delay_ul",
      "prb_direction", "prb_total",
      "prb_dl_delta", "prb_ul_delta", "prb_burst_index",
      "empty_ind_rate",
      "prb_dl_roll_mean", "prb_dl_roll_std",
      "prb_ul_roll_std", "prb_ul_roll_max", "prb_ul_roll_max_100",
  ]

  LSTM_FEATURES = [
      "prb_usage_dl_ratio", "prb_usage_ul_ratio",
      "cqi", "rach_preamble", "air_delay_ul",
      "prb_direction", "prb_total",
      "prb_dl_delta", "prb_ul_delta", "prb_burst_index",
      "empty_ind_rate",
      "prb_dl_roll_mean", "prb_dl_roll_std",
      "prb_ul_roll_std", "prb_ul_roll_max",
      "prb_ul_roll_max_100",
  ]
  WINDOW_SIZE = 10


  # ── Public helpers (unit-testable) ───────────────────────────────────────────

  def find_newest_csv(csv_dir: str):
      """Return path to the most recently modified .csv in csv_dir, or None."""
      files = glob.glob(os.path.join(csv_dir, "*.csv"))
      if not files:
          return None
      return max(files, key=os.path.getmtime)


  def parse_csv_row(raw: dict) -> dict:
      """Convert string dict from csv.DictReader to float dict. Missing = 0.0."""
      out = {}
      for col in FLOAT_COLS:
          v = raw.get(col, "")
          try:
              out[col] = float(v)
          except (ValueError, TypeError):
              out[col] = 0.0
      return out


  def load_eval_results(path: str):
      """Load eval_results.json, return dict or None if missing/invalid."""
      try:
          with open(path) as f:
              return json.load(f)
      except (FileNotFoundError, json.JSONDecodeError):
          return None


  # ── Rule-based stage engine ──────────────────────────────────────────────────

  class SimpleRuleEngine:
      """Lightweight port of sec_ids.c Stage-1 rules for live monitoring."""

      def __init__(self):
          self._ul_cnt = 0
          self._dl_cnt = 0
          self._rf_cnt = 0

      def update(self, row: dict) -> int:
          """Return current detection stage: 0=normal, 1=warning, 2=critical."""
          prb_ul = row.get("prb_usage_ul_ratio", 0.0)
          prb_dl = row.get("prb_usage_dl_ratio", 0.0)
          air_dl = row.get("air_delay_ul", 0.0)

          # R1: UL saturation
          if prb_ul > 0.80:
              self._ul_cnt += 1
          else:
              self._ul_cnt = max(0, self._ul_cnt - 1)

          # R2: DL saturation (guard: UL must be low)
          if prb_dl > 0.80 and prb_ul < 0.30:
              self._dl_cnt += 1
          else:
              self._dl_cnt = max(0, self._dl_cnt - 1)

          # R6: RF delay proxy
          if air_dl > 100.0:
              self._rf_cnt += 1
          else:
              self._rf_cnt = max(0, self._rf_cnt - 1)

          if self._ul_cnt >= 3 or self._dl_cnt >= 3 or self._rf_cnt >= 3:
              return 1
          return 0


  # ── ONNX inference wrapper ───────────────────────────────────────────────────

  class OnnxInferencer:
      THRESHOLD = 0.5  # ONNX output already normalized: >0.5 = anomaly

      def __init__(self, model_path: str):
          self._sess    = ort.InferenceSession(model_path)
          self._window  = np.zeros((WINDOW_SIZE, len(LSTM_FEATURES)), dtype=np.float32)
          self._filled  = 0
          self._in_name = self._sess.get_inputs()[0].name

      def update(self, row: dict) -> float:
          """Feed one row, return normalized anomaly score (0–1+)."""
          feat = np.array([row.get(f, 0.0) for f in LSTM_FEATURES], dtype=np.float32)
          self._window = np.roll(self._window, -1, axis=0)
          self._window[-1] = feat
          if self._filled < WINDOW_SIZE:
              self._filled += 1
              return 0.0
          inp = self._window[np.newaxis, ...]  # shape (1, 10, 16)
          out = self._sess.run(None, {self._in_name: inp})
          return float(out[0])  # ONNX output: normalized score


  # ── Grafana annotation helper ────────────────────────────────────────────────

  def push_grafana_annotation(stage: int, prev_stage: int):
      """POST a stage-change annotation to Grafana."""
      tags = {0: ["normal"], 1: ["warning"], 2: ["critical"]}
      text = {0: "Returned to NORMAL", 1: "STAGE1 WARNING detected",
              2: "STAGE2 CRITICAL confirmed"}
      try:
          user, pwd = GRAFANA_TOKEN.split(":", 1)
          requests.post(
              f"{GRAFANA_URL}/api/annotations",
              auth=(user, pwd),
              json={
                  "text": text.get(stage, "Unknown"),
                  "tags": tags.get(stage, []),
                  "time": int(time.time() * 1000),
              },
              timeout=2,
          )
      except Exception as e:
          log.debug("Grafana annotation skipped: %s", e)


  # ── Background threads ───────────────────────────────────────────────────────

  def csv_tail_loop(onnx: OnnxInferencer, rule: SimpleRuleEngine):
      """Continuously tail the newest CSV and update KPM + detection metrics."""
      current_file = None
      file_handle  = None
      reader       = None
      prev_stage   = 0

      while True:
          newest = find_newest_csv(CSV_DIR)

          if newest != current_file:
              if file_handle:
                  file_handle.close()
              if newest:
                  log.info("Tailing new CSV: %s", newest)
                  file_handle = open(newest, newline="")
                  reader = csv.DictReader(file_handle)
                  # skip to end so we only read new rows
                  for _ in reader:
                      pass
              current_file = newest

          if reader:
              for raw in reader:
                  row = parse_csv_row(raw)

                  g_prb_dl.set(row["prb_usage_dl_ratio"])
                  g_prb_ul.set(row["prb_usage_ul_ratio"])
                  g_cqi.set(row["cqi"])
                  g_rach.set(row["rach_preamble"])
                  g_air_delay.set(row["air_delay_ul"])

                  score = onnx.update(row)
                  g_anomaly.set(score)

                  stage = rule.update(row)
                  g_stage.set(stage)

                  if stage != prev_stage:
                      push_grafana_annotation(stage, prev_stage)
                      prev_stage = stage

          time.sleep(POLL_INTERVAL)


  def eval_watch_loop():
      """Poll eval_results.json and update testing metrics when it changes."""
      last_mtime = 0.0
      while True:
          try:
              mtime = os.path.getmtime(EVAL_JSON)
          except FileNotFoundError:
              time.sleep(EVAL_POLL)
              continue

          if mtime != last_mtime:
              data = load_eval_results(EVAL_JSON)
              if data:
                  _update_eval_metrics(data)
                  log.info("Eval metrics updated from %s", EVAL_JSON)
              last_mtime = mtime

          time.sleep(EVAL_POLL)


  def _update_eval_metrics(data: dict):
      """Push eval JSON data into Prometheus gauges."""
      for stage, metrics in data.get("per_stage", {}).items():
          g_eval_acc.labels(stage=stage).set(metrics.get("accuracy", 0))
          g_eval_fpr.labels(stage=stage).set(metrics.get("fpr", 0))
          # per-stage overall (no attack label) use attack="all"
          g_eval_rec.labels(stage=stage,  attack="all").set(metrics.get("recall", 0))
          g_eval_prec.labels(stage=stage, attack="all").set(metrics.get("precision", 0))
          g_eval_f1.labels(stage=stage,   attack="all").set(metrics.get("f1", 0))

      for attack, stages in data.get("per_attack", {}).items():
          for stage, metrics in stages.items():
              g_eval_rec.labels(stage=stage,  attack=attack).set(metrics.get("recall", 0))
              g_eval_prec.labels(stage=stage, attack=attack).set(metrics.get("precision", 0))
              g_eval_f1.labels(stage=stage,   attack=attack).set(metrics.get("f1", 0))


  # ── Main ─────────────────────────────────────────────────────────────────────

  def main():
      log.info("Starting xapp Prometheus exporter on :8000")
      start_http_server(8000)

      onnx = OnnxInferencer(ONNX_MODEL)
      rule = SimpleRuleEngine()

      t1 = threading.Thread(target=csv_tail_loop, args=(onnx, rule), daemon=True)
      t2 = threading.Thread(target=eval_watch_loop, daemon=True)
      t1.start()
      t2.start()

      log.info("Exporter running. Metrics at http://0.0.0.0:8000/metrics")
      while True:
          time.sleep(60)


  if __name__ == "__main__":
      main()
  ```

- [ ] **Step 6: Run tests to verify they pass**

  ```bash
  cd /home/telmat/sec-xapp/exporter
  python3 -m pytest test_csv_exporter.py -v
  ```
  Expected:
  ```
  test_csv_exporter.py::test_find_newest_csv_returns_latest PASSED
  test_csv_exporter.py::test_find_newest_csv_returns_none_when_empty PASSED
  test_csv_exporter.py::test_parse_csv_row_converts_floats PASSED
  test_csv_exporter.py::test_parse_csv_row_handles_empty_string PASSED
  test_csv_exporter.py::test_rule_engine_ul_flood_triggers_warning PASSED
  test_csv_exporter.py::test_rule_engine_normal_stays_zero PASSED
  test_csv_exporter.py::test_eval_loader_reads_json PASSED
  test_csv_exporter.py::test_eval_loader_returns_none_when_missing PASSED
  8 passed
  ```

- [ ] **Step 7: Commit**

  ```bash
  cd /home/telmat/sec-xapp
  git init  # if not already a repo
  git add exporter/ prometheus/ grafana/ docker-compose.yml results/.gitkeep evaluate_detection.py
  git commit -m "feat: add Grafana dashboard infrastructure and CSV exporter"
  ```

---

## Task 4: Build and Smoke-Test the Exporter Container

**Files:** no new files — verifies Docker build works

- [ ] **Step 1: Build the exporter image**

  ```bash
  cd /home/telmat/sec-xapp
  docker compose build csv-exporter
  ```
  Expected: `Successfully built ...` or `=> exporting to image DONE`

- [ ] **Step 2: Start full stack**

  ```bash
  docker compose up -d
  sleep 10
  ```

- [ ] **Step 3: Verify exporter /metrics endpoint**

  ```bash
  curl -s http://localhost:8000/metrics | grep "^xapp_"
  ```
  Expected (at minimum):
  ```
  xapp_prb_dl_ratio 0.0
  xapp_prb_ul_ratio 0.0
  xapp_detection_stage 0.0
  xapp_anomaly_score 0.0
  ```

- [ ] **Step 4: Verify Prometheus scrapes the exporter**

  ```bash
  curl -s "http://localhost:9090/api/v1/targets" | \
    python3 -c "import sys,json; t=json.load(sys.stdin)['data']['activeTargets'][0]; print(t['health'], t['labels'])"
  ```
  Expected: `up {'instance': 'csv-exporter:8000', 'job': 'xapp-exporter'}`

- [ ] **Step 5: Trigger a live metrics update**

  Copy a real CSV row to simulate the xApp writing:
  ```bash
  NEWEST=$(ls -t /home/telmat/sec-xapp/csv/training_*.csv 2>/dev/null | head -1)
  # append one row to trigger the tailer
  tail -1 "$NEWEST" >> "$NEWEST"
  sleep 3
  curl -s http://localhost:8000/metrics | grep "xapp_prb_dl_ratio"
  ```
  Expected: value changes from 0.0 to the actual PRB value from that row.

---

## Task 5: Main Page Dashboard JSON

**Files:**
- Create: `grafana/provisioning/dashboards/main.json`

- [ ] **Step 1: Create the dashboard JSON**

  Create `/home/telmat/sec-xapp/grafana/provisioning/dashboards/main.json`:

  ```json
  {
    "__inputs": [],
    "__requires": [],
    "annotations": {
      "list": [
        {
          "builtIn": 1,
          "datasource": { "type": "grafana", "uid": "-- Grafana --" },
          "enable": true,
          "hide": false,
          "iconColor": "rgba(0, 211, 255, 1)",
          "name": "Annotations & Alerts",
          "type": "dashboard"
        }
      ]
    },
    "description": "Real-time 5G KPM metrics and security detection status from xapp_sec_moni",
    "editable": true,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 1,
    "id": null,
    "links": [],
    "panels": [
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "mappings": [
              { "options": { "0": { "color": "green", "index": 0, "text": "● NORMAL" },
                             "1": { "color": "yellow", "index": 1, "text": "⚠ WARNING" },
                             "2": { "color": "red", "index": 2, "text": "🔴 CRITICAL" } },
                "type": "value" }
            ],
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "green", "value": null },
                { "color": "yellow", "value": 1 },
                { "color": "red", "value": 2 }
              ]
            }
          }
        },
        "gridPos": { "h": 3, "w": 24, "x": 0, "y": 0 },
        "id": 1,
        "options": {
          "colorMode": "background",
          "graphMode": "none",
          "justifyMode": "center",
          "orientation": "horizontal",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "value"
        },
        "title": "Detection Status",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_detection_stage",
            "legendFormat": "Stage",
            "refId": "A"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "percentunit",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "green", "value": null },
                { "color": "yellow", "value": 0.5 },
                { "color": "red", "value": 0.8 }
              ]
            },
            "custom": { "sparkline": { "show": true } }
          }
        },
        "gridPos": { "h": 4, "w": 6, "x": 0, "y": 3 },
        "id": 2,
        "options": {
          "colorMode": "background",
          "graphMode": "area",
          "justifyMode": "auto",
          "orientation": "auto",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto"
        },
        "title": "PRB DL",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_prb_dl_ratio",
            "legendFormat": "PRB DL",
            "refId": "A"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "percentunit",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "green", "value": null },
                { "color": "yellow", "value": 0.5 },
                { "color": "red", "value": 0.8 }
              ]
            },
            "custom": { "sparkline": { "show": true } }
          }
        },
        "gridPos": { "h": 4, "w": 6, "x": 6, "y": 3 },
        "id": 3,
        "options": {
          "colorMode": "background",
          "graphMode": "area",
          "justifyMode": "auto",
          "orientation": "auto",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto"
        },
        "title": "PRB UL",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_prb_ul_ratio",
            "legendFormat": "PRB UL",
            "refId": "A"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "none",
            "min": 0,
            "max": 15,
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "red", "value": null },
                { "color": "yellow", "value": 5 },
                { "color": "green", "value": 10 }
              ]
            }
          }
        },
        "gridPos": { "h": 4, "w": 6, "x": 12, "y": 3 },
        "id": 4,
        "options": {
          "colorMode": "background",
          "graphMode": "none",
          "justifyMode": "auto",
          "orientation": "auto",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto"
        },
        "title": "CQI",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_cqi",
            "legendFormat": "CQI",
            "refId": "A"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "ms",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "green", "value": null },
                { "color": "yellow", "value": 50 },
                { "color": "red", "value": 100 }
              ]
            }
          }
        },
        "gridPos": { "h": 4, "w": 6, "x": 18, "y": 3 },
        "id": 5,
        "options": {
          "colorMode": "background",
          "graphMode": "none",
          "justifyMode": "auto",
          "orientation": "auto",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto"
        },
        "title": "UL Air Delay",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_air_delay_ul_ms",
            "legendFormat": "UL Delay",
            "refId": "A"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "palette-classic" },
            "unit": "percentunit",
            "custom": {
              "lineWidth": 2,
              "fillOpacity": 10
            },
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "green", "value": null },
                { "color": "red", "value": 0.8 }
              ]
            }
          },
          "overrides": [
            {
              "matcher": { "id": "byName", "options": "PRB DL" },
              "properties": [{ "id": "color", "value": { "fixedColor": "red", "mode": "fixed" } }]
            },
            {
              "matcher": { "id": "byName", "options": "PRB UL" },
              "properties": [{ "id": "color", "value": { "fixedColor": "blue", "mode": "fixed" } }]
            }
          ]
        },
        "gridPos": { "h": 8, "w": 15, "x": 0, "y": 7 },
        "id": 6,
        "options": {
          "legend": { "calcs": ["last", "max"], "displayMode": "list", "placement": "bottom" },
          "tooltip": { "mode": "multi", "sort": "none" }
        },
        "title": "PRB Utilization DL / UL — Last 5 min",
        "type": "timeseries",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_prb_dl_ratio",
            "legendFormat": "PRB DL",
            "refId": "A"
          },
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_prb_ul_ratio",
            "legendFormat": "PRB UL",
            "refId": "B"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "fixedColor": "green", "mode": "fixed" },
            "unit": "none",
            "custom": { "lineWidth": 2, "fillOpacity": 10 },
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "green", "value": null },
                { "color": "red", "value": 0.5 }
              ]
            }
          }
        },
        "gridPos": { "h": 8, "w": 9, "x": 15, "y": 7 },
        "id": 7,
        "options": {
          "legend": { "calcs": ["last", "max"], "displayMode": "list", "placement": "bottom" },
          "tooltip": { "mode": "single", "sort": "none" }
        },
        "title": "LSTM Anomaly Score (threshold = 0.5)",
        "type": "timeseries",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_anomaly_score",
            "legendFormat": "Anomaly Score",
            "refId": "A"
          }
        ]
      }
    ],
    "refresh": "2s",
    "schemaVersion": 39,
    "tags": ["xapp", "5g", "security"],
    "time": { "from": "now-5m", "to": "now" },
    "timepicker": {},
    "timezone": "browser",
    "title": "xApp Security Monitor — Main",
    "uid": "xapp-main-v1",
    "version": 1
  }
  ```

- [ ] **Step 2: Reload Grafana provisioning**

  ```bash
  docker compose restart grafana
  sleep 5
  ```

- [ ] **Step 3: Verify dashboard appears in Grafana**

  ```bash
  curl -s -u admin:admin http://localhost:3000/api/search?query=xapp | python3 -m json.tool
  ```
  Expected: JSON array containing `{"title": "xApp Security Monitor — Main", ...}`

  Open in browser: http://localhost:3000 → login admin/admin → Dashboards → "xApp Security Monitor — Main"

---

## Task 6: Testing Page Dashboard JSON

**Files:**
- Create: `grafana/provisioning/dashboards/testing.json`

- [ ] **Step 1: Generate a test eval_results.json so Grafana has data to show**

  ```bash
  cd /home/telmat/sec-xapp
  python3 evaluate_detection.py \
    --csv csv/dataset_testing_v2_with_empty.csv \
    --output results/eval_results.json
  sleep 15  # wait for exporter to pick it up
  curl -s http://localhost:8000/metrics | grep xapp_eval
  ```
  Expected: metrics like `xapp_eval_accuracy{stage="hybrid"} 0.94...`

- [ ] **Step 2: Create the Testing Page dashboard JSON**

  Create `/home/telmat/sec-xapp/grafana/provisioning/dashboards/testing.json`:

  ```json
  {
    "__inputs": [],
    "__requires": [],
    "description": "Offline evaluation results: accuracy, recall, F1, latency per attack and stage",
    "editable": true,
    "graphTooltip": 0,
    "id": null,
    "links": [],
    "panels": [
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "percentunit",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "red", "value": null },
                { "color": "yellow", "value": 0.8 },
                { "color": "green", "value": 0.9 }
              ]
            }
          }
        },
        "gridPos": { "h": 4, "w": 5, "x": 0, "y": 0 },
        "id": 10,
        "options": {
          "colorMode": "background",
          "graphMode": "none",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto"
        },
        "title": "Accuracy (Hybrid)",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_accuracy{stage=\"hybrid\"}",
            "legendFormat": "Accuracy",
            "refId": "A"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "percentunit",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "red", "value": null },
                { "color": "yellow", "value": 0.8 },
                { "color": "green", "value": 0.9 }
              ]
            }
          }
        },
        "gridPos": { "h": 4, "w": 5, "x": 5, "y": 0 },
        "id": 11,
        "options": {
          "colorMode": "background",
          "graphMode": "none",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto"
        },
        "title": "Recall (Hybrid)",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_recall{stage=\"hybrid\",attack=\"all\"}",
            "legendFormat": "Recall",
            "refId": "A"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "percentunit",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "red", "value": null },
                { "color": "yellow", "value": 0.8 },
                { "color": "green", "value": 0.9 }
              ]
            }
          }
        },
        "gridPos": { "h": 4, "w": 5, "x": 10, "y": 0 },
        "id": 12,
        "options": {
          "colorMode": "background",
          "graphMode": "none",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto"
        },
        "title": "Precision (Hybrid)",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_precision{stage=\"hybrid\",attack=\"all\"}",
            "legendFormat": "Precision",
            "refId": "A"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "percentunit",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "red", "value": null },
                { "color": "yellow", "value": 0.8 },
                { "color": "green", "value": 0.9 }
              ]
            }
          }
        },
        "gridPos": { "h": 4, "w": 5, "x": 15, "y": 0 },
        "id": 13,
        "options": {
          "colorMode": "background",
          "graphMode": "none",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto"
        },
        "title": "F1-Score (Hybrid)",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_f1{stage=\"hybrid\",attack=\"all\"}",
            "legendFormat": "F1",
            "refId": "A"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "percentunit",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "green", "value": null },
                { "color": "yellow", "value": 0.02 },
                { "color": "red", "value": 0.05 }
              ]
            }
          }
        },
        "gridPos": { "h": 4, "w": 4, "x": 20, "y": 0 },
        "id": 14,
        "options": {
          "colorMode": "background",
          "graphMode": "none",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto"
        },
        "title": "FPR (Hybrid)",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_fpr{stage=\"hybrid\"}",
            "legendFormat": "FPR",
            "refId": "A"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "percentunit",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "red", "value": null },
                { "color": "yellow", "value": 0.8 },
                { "color": "green", "value": 0.95 }
              ]
            },
            "custom": {
              "displayMode": "color-background",
              "align": "auto"
            }
          },
          "overrides": [
            {
              "matcher": { "id": "byName", "options": "Attack" },
              "properties": [
                { "id": "custom.width", "value": 120 },
                { "id": "unit", "value": "string" },
                { "id": "custom.displayMode", "value": "auto" }
              ]
            },
            {
              "matcher": { "id": "byName", "options": "Stage" },
              "properties": [
                { "id": "custom.width", "value": 80 },
                { "id": "unit", "value": "string" },
                { "id": "custom.displayMode", "value": "auto" }
              ]
            }
          ]
        },
        "gridPos": { "h": 9, "w": 15, "x": 0, "y": 4 },
        "id": 20,
        "options": {
          "footer": { "show": false },
          "showHeader": true,
          "sortBy": [{ "desc": true, "displayName": "F1" }]
        },
        "title": "Per-Attack Breakdown (Hybrid IDS)",
        "transformations": [
          {
            "id": "merge",
            "options": {}
          },
          {
            "id": "organize",
            "options": {
              "renameByName": {
                "Value #Recall": "Recall",
                "Value #Prec": "Precision",
                "Value #F1": "F1",
                "attack": "Attack"
              }
            }
          }
        ],
        "type": "table",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_recall{stage=\"hybrid\",attack!=\"all\"}",
            "legendFormat": "{{attack}}",
            "instant": true,
            "refId": "Recall"
          },
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_precision{stage=\"hybrid\",attack!=\"all\"}",
            "legendFormat": "{{attack}}",
            "instant": true,
            "refId": "Prec"
          },
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_f1{stage=\"hybrid\",attack!=\"all\"}",
            "legendFormat": "{{attack}}",
            "instant": true,
            "refId": "F1"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "palette-classic" },
            "unit": "percentunit",
            "custom": { "fillOpacity": 80 }
          }
        },
        "gridPos": { "h": 9, "w": 9, "x": 15, "y": 4 },
        "id": 21,
        "options": {
          "barAlignment": 0,
          "orientation": "horizontal",
          "legend": { "displayMode": "list", "placement": "bottom" },
          "tooltip": { "mode": "single" },
          "xTickLabelRotation": 0
        },
        "title": "F1-Score per Attack (Hybrid)",
        "type": "barchart",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_f1{stage=\"hybrid\",attack!=\"all\"}",
            "legendFormat": "{{attack}}",
            "instant": true,
            "refId": "A"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "percentunit",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "red", "value": null },
                { "color": "yellow", "value": 0.8 },
                { "color": "green", "value": 0.9 }
              ]
            }
          }
        },
        "gridPos": { "h": 5, "w": 8, "x": 0, "y": 13 },
        "id": 30,
        "options": {
          "colorMode": "background",
          "graphMode": "none",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto",
          "orientation": "horizontal"
        },
        "title": "Stage 1 — Rule-Based",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_accuracy{stage=\"stage1\"}",
            "legendFormat": "Accuracy",
            "instant": true,
            "refId": "A"
          },
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_recall{stage=\"stage1\",attack=\"all\"}",
            "legendFormat": "Recall",
            "instant": true,
            "refId": "B"
          },
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_f1{stage=\"stage1\",attack=\"all\"}",
            "legendFormat": "F1",
            "instant": true,
            "refId": "C"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "percentunit",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "red", "value": null },
                { "color": "yellow", "value": 0.8 },
                { "color": "green", "value": 0.9 }
              ]
            }
          }
        },
        "gridPos": { "h": 5, "w": 8, "x": 8, "y": 13 },
        "id": 31,
        "options": {
          "colorMode": "background",
          "graphMode": "none",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto",
          "orientation": "horizontal"
        },
        "title": "Stage 2 — LSTM",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_accuracy{stage=\"stage2\"}",
            "legendFormat": "Accuracy",
            "instant": true,
            "refId": "A"
          },
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_recall{stage=\"stage2\",attack=\"all\"}",
            "legendFormat": "Recall",
            "instant": true,
            "refId": "B"
          },
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_f1{stage=\"stage2\",attack=\"all\"}",
            "legendFormat": "F1",
            "instant": true,
            "refId": "C"
          }
        ]
      },
      {
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "fieldConfig": {
          "defaults": {
            "color": { "mode": "thresholds" },
            "unit": "percentunit",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "red", "value": null },
                { "color": "yellow", "value": 0.8 },
                { "color": "green", "value": 0.9 }
              ]
            }
          }
        },
        "gridPos": { "h": 5, "w": 8, "x": 16, "y": 13 },
        "id": 32,
        "options": {
          "colorMode": "background",
          "graphMode": "none",
          "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
          "textMode": "auto",
          "orientation": "horizontal"
        },
        "title": "Hybrid ★ (Rule + LSTM)",
        "type": "stat",
        "targets": [
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_accuracy{stage=\"hybrid\"}",
            "legendFormat": "Accuracy",
            "instant": true,
            "refId": "A"
          },
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_recall{stage=\"hybrid\",attack=\"all\"}",
            "legendFormat": "Recall",
            "instant": true,
            "refId": "B"
          },
          {
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "xapp_eval_f1{stage=\"hybrid\",attack=\"all\"}",
            "legendFormat": "F1",
            "instant": true,
            "refId": "C"
          }
        ]
      }
    ],
    "refresh": "30s",
    "schemaVersion": 39,
    "tags": ["xapp", "5g", "security", "evaluation"],
    "time": { "from": "now-1h", "to": "now" },
    "timepicker": {},
    "timezone": "browser",
    "title": "xApp Security Monitor — Testing",
    "uid": "xapp-testing-v1",
    "version": 1
  }
  ```

- [ ] **Step 3: Reload Grafana and verify**

  ```bash
  docker compose restart grafana
  sleep 5
  curl -s -u admin:admin "http://localhost:3000/api/search?query=xapp" | \
    python3 -c "import sys,json; [print(d['title']) for d in json.load(sys.stdin)]"
  ```
  Expected:
  ```
  xApp Security Monitor — Main
  xApp Security Monitor — Testing
  ```

  Open in browser: http://localhost:3000 → Dashboards → both dashboards should load without "No data" if eval_results.json was generated in Step 1.

- [ ] **Step 4: Final commit**

  ```bash
  cd /home/telmat/sec-xapp
  git add grafana/ docker-compose.yml prometheus/ exporter/ evaluate_detection.py results/
  git commit -m "feat: complete Grafana dashboard with CSV exporter and testing page"
  ```

---

## Quick-Start Reference

After implementation, normal usage:

```bash
# 1. Start the stack
cd /home/telmat/sec-xapp
docker compose up -d

# 2. Start the xApp (in a separate terminal or tmux)
./start_xapp_c.sh

# 3. Open Grafana
xdg-open http://localhost:3000  # login: admin/admin

# 4. After running attack experiments, update testing page:
python3 evaluate_detection.py \
  --csv csv/dataset_testing_v2_with_empty.csv \
  --output results/eval_results.json
# Grafana auto-refreshes within 30s

# 5. Stop stack
docker compose down
```
