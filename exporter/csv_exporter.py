"""
Prometheus exporter for sec-xapp.
Tails live CSV from xapp_sec_moni, reads pre-computed detection columns
(anomaly_score, stage1_alert, stage2_confirmed) written by the C binary,
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
from prometheus_client import Gauge, start_http_server

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config from environment ──────────────────────────────────────────────────
CSV_DIR      = os.getenv("CSV_DIR",      "/data/csv")
EVAL_JSON    = os.getenv("EVAL_JSON",    "/data/results/eval_results.json")
GRAFANA_URL  = os.getenv("GRAFANA_URL",  "http://grafana:3000")
GRAFANA_TOKEN = os.getenv("GRAFANA_TOKEN", "admin:admin")
POLL_INTERVAL = 0.1   # 100ms: sub-second resolution for latency measurement accuracy
EVAL_POLL     = 10.0  # seconds between eval JSON checks

# ── Prometheus metrics ───────────────────────────────────────────────────────
g_prb_dl      = Gauge("xapp_prb_dl_ratio",     "PRB DL utilization (0-1)")
g_prb_ul      = Gauge("xapp_prb_ul_ratio",     "PRB UL utilization (0-1)")
g_cqi         = Gauge("xapp_cqi",              "Channel Quality Indicator")
g_rach        = Gauge("xapp_rach_preamble",    "RACH preamble count")
g_air_delay   = Gauge("xapp_air_delay_ul_ms",  "UL air delay (ms)")
g_anomaly     = Gauge("xapp_anomaly_score",    "LSTM anomaly score")
g_stage       = Gauge("xapp_detection_stage",  "Detection stage: 0=normal 1=warn 2=crit")

g_latency_detect  = Gauge("xapp_latency_detect_ms",  "Stage 0→1 detection latency ms (last event)")
g_latency_confirm = Gauge("xapp_latency_confirm_ms", "Stage 1→2 confirmation latency ms (last event)")
g_latency_total   = Gauge("xapp_latency_total_ms",   "Total Stage 0→2 mitigation latency ms (last event)")

g_eval_acc    = Gauge("xapp_eval_accuracy",    "Eval accuracy",   ["stage"])
g_eval_rec    = Gauge("xapp_eval_recall",      "Eval recall",     ["stage", "attack"])
g_eval_prec   = Gauge("xapp_eval_precision",   "Eval precision",  ["stage", "attack"])
g_eval_f1     = Gauge("xapp_eval_f1",          "Eval F1",         ["stage", "attack"])
g_eval_fpr    = Gauge("xapp_eval_fpr",         "Eval FPR",        ["stage"])

# ── GRU detection metrics ────────────────────────────────────────────────────
g_gru_a     = Gauge("xapp_gru_score_a",     "GRU-A reconstruction error (raw)")
g_gru_b     = Gauge("xapp_gru_score_b",     "GRU-B reconstruction error (raw)")
g_gru_stage = Gauge("xapp_gru_stage",       "GRU stage: 0=normal 1=warn 2=crit")

# ── Extra features not yet exposed ──────────────────────────────────────────
g_alert_type  = Gauge("xapp_alert_type",      "Alert type: 0=none 1=ul_flood 2=dl_flood 3=burst 4=rrc_storm")
g_empty_ind   = Gauge("xapp_empty_ind_rate",  "RRC empty indication rate per window")
g_burst_idx   = Gauge("xapp_prb_burst_index", "PRB burst index log ratio")

# ── Eval metrics v2 (5 models, per-attack labels) ────────────────────────────
g_eval_recall_v2    = Gauge("xapp_eval_recall_v2",    "Eval recall v2",    ["model", "attack"])
g_eval_fpr_v2       = Gauge("xapp_eval_fpr_v2",       "Eval FPR v2",       ["model"])
g_eval_f1_v2        = Gauge("xapp_eval_f1_v2",        "Eval F1 v2",        ["model", "attack"])
g_eval_precision_v2 = Gauge("xapp_eval_precision_v2", "Eval precision v2", ["model"])

# ── Shared state: latest CSV row for GRU thread ──────────────────────────────
_latest_row: dict = {}
_latest_row_lock = threading.Lock()

# ── CSV columns we care about ────────────────────────────────────────────────
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
EXTRA_COLS  = ["stage1_alert", "stage2_confirmed", "anomaly_score"]
FLOAT_COLS  = LSTM_FEATURES + EXTRA_COLS
STR_COLS    = {"alert_type"}
ALERT_TYPE_MAP = {"none": 0, "ul_flood": 1, "dl_flood": 2,
                  "burst": 3, "rrc_storm": 4}

# ── GRU inference config ─────────────────────────────────────────────────────
GRU_MODEL_A  = os.getenv("GRU_MODEL_A", "/data/models/gru_autoencoder_A_v1.pt")
GRU_MODEL_B  = os.getenv("GRU_MODEL_B", "/data/models/gru_autoencoder_B_v1.pt")
GRU_SCALER   = os.getenv("GRU_SCALER",  "/data/models/scaler_gru.pkl")
GRU_THRESH_A = 0.002881
GRU_THRESH_B = 0.003363
GRU_SEQ_A    = 10
GRU_SEQ_B    = 30
GRU_POLL_SEC = 1.0

# ── Known evaluation results (from dataset_attack_mei.csv offline eval) ──────
KNOWN_EVAL = {
    "rule": {
        "overall":   {"recall": 0.9765, "precision": 0.9862, "f1": 0.9813, "fpr": 0.0140},
        "ul_flood":  {"recall": 0.987,  "f1": 0.963},
        "dl_flood":  {"recall": 0.993,  "f1": 0.967},
        "burst":     {"recall": 0.982,  "f1": 0.971},
        "rrc_storm": {"recall": 0.940,  "f1": 0.938},
    },
    "lstm": {
        "overall":   {"recall": 0.7918, "precision": 0.9284, "f1": 0.8547, "fpr": 0.0627},
        "ul_flood":  {"recall": 0.814,  "f1": 0.893},
        "dl_flood":  {"recall": 0.997,  "f1": 0.995},
        "burst":     {"recall": 0.614,  "f1": 0.736},
        "rrc_storm": {"recall": 0.838,  "f1": 0.882},
    },
    "lstm_tuned": {
        "overall":   {"recall": 0.9661, "precision": 0.9220, "f1": 0.9435, "fpr": 0.0839},
        "ul_flood":  {"recall": 0.992,  "f1": 0.944},
        "dl_flood":  {"recall": 0.997,  "f1": 0.975},
        "burst":     {"recall": 0.915,  "f1": 0.944},
        "rrc_storm": {"recall": 0.988,  "f1": 0.967},
    },
    "gru_tuned": {
        "overall":   {"recall": 0.932,  "precision": 0.930, "f1": 0.930, "fpr": 0.053},
        "ul_flood":  {"recall": 0.996,  "f1": 0.970},
        "dl_flood":  {"recall": 0.994,  "f1": 0.980},
        "burst":     {"recall": 0.987,  "f1": 0.987},
        "rrc_storm": {"recall": 0.715,  "f1": 0.790},
    },
    "hybrid_lstm": {
        "overall":   {"recall": 0.9831, "precision": 0.9410, "f1": 0.9616, "fpr": 0.0137},
        "ul_flood":  {"recall": 0.992,  "f1": 0.964},
        "dl_flood":  {"recall": 0.997,  "f1": 0.967},
        "burst":     {"recall": 0.995,  "f1": 0.971},
        "rrc_storm": {"recall": 0.940,  "f1": 0.938},
    },
    "hybrid_gru": {
        "overall":   {"recall": 0.9824, "precision": 0.9724, "f1": 0.9773, "fpr": 0.0287},
        "ul_flood":  {"recall": 0.992,  "f1": 0.937},
        "dl_flood":  {"recall": 0.993,  "f1": 0.937},
        "burst":     {"recall": 0.987,  "f1": 0.971},
        "rrc_storm": {"recall": 0.940,  "f1": 0.938},
    },
}

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


# ── Public helpers (unit-testable) ───────────────────────────────────────────

def find_newest_csv(csv_dir: str, pattern: str = "*.csv"):
    """Return path to the most recently modified file matching pattern in csv_dir, or None."""
    files = glob.glob(os.path.join(csv_dir, pattern))
    if not files:
        return None
    try:
        return max(files, key=os.path.getmtime)
    except FileNotFoundError:
        return None


def parse_csv_row(raw: dict) -> dict:
    """Convert string dict from csv.DictReader to float dict. Missing = 0.0."""
    out = {}
    for col in FLOAT_COLS:
        v = raw.get(col, "")
        try:
            out[col] = float(v)
        except (ValueError, TypeError):
            out[col] = 0.0
    out["alert_type"] = raw.get("alert_type", "none")
    return out


def load_eval_results(path: str):
    """Load eval_results.json, return dict or None if missing/invalid."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ── Grafana annotation helper ────────────────────────────────────────────────

def push_grafana_annotation(stage: int, prev_stage: int):
    """POST a stage-change annotation to Grafana."""
    tags = {0: ["normal"], 1: ["warning"], 2: ["critical"]}
    text = {0: "Returned to NORMAL", 1: "STAGE1 WARNING detected",
            2: "STAGE2 CRITICAL confirmed"}
    try:
        if ":" in GRAFANA_TOKEN:
            user, pwd = GRAFANA_TOKEN.split(":", 1)
            auth = (user, pwd)
            headers = {}
        else:
            auth = None
            headers = {"Authorization": f"Bearer {GRAFANA_TOKEN}"}
        requests.post(
            f"{GRAFANA_URL}/api/annotations",
            auth=auth,
            headers=headers,
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

def csv_tail_loop():
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
                file_handle = None
                reader = None
            if newest:
                log.info("Tailing new CSV: %s", newest)
                file_handle = open(newest, newline="")
                reader = csv.DictReader(file_handle)
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

                # Use values computed by C binary directly from CSV
                score = row.get("anomaly_score", 0.0)
                g_anomaly.set(score)

                stage2 = int(row.get("stage2_confirmed", 0.0))
                stage1 = int(row.get("stage1_alert", 0.0))
                stage = 2 if stage2 else (1 if stage1 else 0)
                g_stage.set(stage)

                # new feature gauges
                g_empty_ind.set(row.get("empty_ind_rate", 0.0))
                g_burst_idx.set(row.get("prb_burst_index", 0.0))
                g_alert_type.set(ALERT_TYPE_MAP.get(row.get("alert_type", "none"), 0))

                # update shared state for GRU thread
                with _latest_row_lock:
                    _latest_row.update(row)

                if stage != prev_stage:
                    push_grafana_annotation(stage, prev_stage)
                    _track_stage_latency(stage, prev_stage)
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


def _populate_eval_v2():
    """Push KNOWN_EVAL into Prometheus gauge xapp_eval_*_v2 at startup."""
    attacks = ["ul_flood", "dl_flood", "burst", "rrc_storm"]
    for model, data in KNOWN_EVAL.items():
        ov = data["overall"]
        g_eval_recall_v2.labels(model=model, attack="all").set(ov["recall"])
        g_eval_precision_v2.labels(model=model).set(ov["precision"])
        g_eval_f1_v2.labels(model=model, attack="all").set(ov["f1"])
        g_eval_fpr_v2.labels(model=model).set(ov["fpr"])
        for atk in attacks:
            if atk in data:
                g_eval_recall_v2.labels(model=model, attack=atk).set(data[atk]["recall"])
                g_eval_f1_v2.labels(model=model, attack=atk).set(data[atk]["f1"])
    log.info("Eval v2 metrics populated for %d models", len(KNOWN_EVAL))


def gru_inference_loop():
    """Load GRU-A + GRU-B, run inference every GRU_POLL_SEC from latest CSV row."""
    try:
        from gru_model import GRUAutoencoder, load_scaler, extract_gru_features
        import numpy as np
    except ImportError as e:
        log.warning("GRU inference disabled: %s", e)
        return

    try:
        model_a = GRUAutoencoder.load(GRU_MODEL_A)
        model_b = GRUAutoencoder.load(GRU_MODEL_B)
        scaler  = load_scaler(GRU_SCALER)
        log.info("GRU models loaded: A seq=%d  B seq=%d", GRU_SEQ_A, GRU_SEQ_B)
    except (FileNotFoundError, Exception) as e:
        log.warning("GRU models not available, thread exiting: %s", e)
        return

    from collections import deque
    import torch
    buf_a = deque(maxlen=GRU_SEQ_A)
    buf_b = deque(maxlen=GRU_SEQ_B)

    while True:
        time.sleep(GRU_POLL_SEC)
        with _latest_row_lock:
            row = dict(_latest_row)
        if not row:
            continue

        try:
            feat_raw = extract_gru_features(row)                          # (16,)
            feat_scaled = scaler.transform(feat_raw.reshape(1, -1))[0]   # (16,)

            buf_a.append(feat_scaled)
            buf_b.append(feat_scaled)

            score_a, score_b = 0.0, 0.0

            if len(buf_a) == GRU_SEQ_A:
                x_a = torch.tensor(np.array(buf_a), dtype=torch.float32).unsqueeze(0)
                score_a = model_a.reconstruction_error(x_a)
                g_gru_a.set(score_a)

            if len(buf_b) == GRU_SEQ_B:
                x_b = torch.tensor(np.array(buf_b), dtype=torch.float32).unsqueeze(0)
                score_b = model_b.reconstruction_error(x_b)
                g_gru_b.set(score_b)

            if score_a > GRU_THRESH_A or score_b > GRU_THRESH_B:
                gru_stage = 2
            elif score_a > GRU_THRESH_A * 0.5 or score_b > GRU_THRESH_B * 0.5:
                gru_stage = 1
            else:
                gru_stage = 0
            g_gru_stage.set(gru_stage)

        except Exception as e:
            log.debug("GRU inference error (skipping): %s", e)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    log.info("Starting xapp Prometheus exporter on :8000")
    start_http_server(8000)
    _populate_eval_v2()

    t1 = threading.Thread(target=csv_tail_loop,      daemon=True, name="csv-tail")
    t2 = threading.Thread(target=eval_watch_loop,    daemon=True, name="eval-watch")
    t3 = threading.Thread(target=gru_inference_loop, daemon=True, name="gru-infer")
    t1.start()
    t2.start()
    t3.start()

    log.info("Exporter running. Metrics at http://0.0.0.0:8000/metrics")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
