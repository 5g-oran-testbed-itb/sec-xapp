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
    "prb_ul_roll_std", "prb_ul_roll_max",
    "prb_ul_roll_max_100",
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
