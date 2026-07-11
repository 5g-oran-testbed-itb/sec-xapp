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
from prometheus_client import Gauge, Counter, start_http_server

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

# ── Per-UE IDS v4 live metrics (per-RNTI label) ──────────────────────────────
g_ue_mse        = Gauge("xapp_ue_mse",        "Per-UE MSE reconstruction error (on alert)", ["rnti"])
g_ue_alert_type = Gauge("xapp_ue_alert_type", "Per-UE alert type: 0=none 1=ul_flood 2=dl_flood 3=burst 4=roq", ["rnti"])
g_ue_stage      = Gauge("xapp_ue_stage",      "Per-UE IDS stage: 0/1/2", ["rnti"])

# Cumulative count of attacks escalated to Stage 2 (critical → mitigation triggered).
c_attacks_blocked = Counter("xapp_attacks_blocked_total",
                            "Cumulative attacks escalated to Stage 2 (mitigation triggered)",
                            ["rnti", "attack_type"])
_ue_prev_stage = {}  # rnti -> last seen stage, to detect 0/1 → 2 transitions

# ── E2SM-RC mitigation state (honest: logged after a confirmed Control Request) ──
g_ue_mitigation_active    = Gauge("xapp_ue_mitigation_active",
                                  "Per-UE E2SM-RC throttle active (1) or restored (0)", ["rnti"])
g_ue_mitigation_prb_limit = Gauge("xapp_ue_mitigation_prb_limit",
                                  "Per-UE current PRB cap % under throttle (100=unrestricted)", ["rnti"])
c_mitigations_applied     = Counter("xapp_mitigations_applied_total",
                                    "Cumulative E2SM-RC THROTTLE control requests actually applied",
                                    ["rnti", "attack"])

_ATTACK_NAME = {0: "none", 1: "ul_flood", 2: "dl_flood", 3: "burst", 4: "roq"}

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

# ── Active per-UE ML threshold (mirrors whatever model the xApp loaded) ──────
g_ue_threshold  = Gauge("xapp_ue_threshold", "Active per-UE ML decision threshold (loaded model)")
g_ue_model_info = Gauge("xapp_ue_model_info", "Active per-UE model (value=1)", ["model"])
ACTIVE_THRESHOLD_PATH = os.getenv("ACTIVE_THRESHOLD_PATH", "/tmp/xapp_active_threshold")
_model_info_last = {"model": None}

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
                stage = row["rule_stage"]
                g_ue_mse.labels(rnti=rnti).set(row["mse"])
                g_ue_alert_type.labels(rnti=rnti).set(row["alert_type"])
                g_ue_stage.labels(rnti=rnti).set(stage)

                # One "blocked attack" per escalation into Stage 2 (critical →
                # mitigation). Count only the 0/1 → 2 transition, not every row.
                if stage >= 2 and _ue_prev_stage.get(rnti, 0) < 2:
                    atype = _ATTACK_NAME.get(row["alert_type"], "unknown")
                    c_attacks_blocked.labels(rnti=rnti, attack_type=atype).inc()
                _ue_prev_stage[rnti] = stage

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


def threshold_watch_loop():
    """Periodically refresh the active-threshold gauges from the sidecar file."""
    while True:
        read_active_threshold()
        time.sleep(EVAL_POLL)


# ── Main ─────────────────────────────────────────────────────────────────────

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
    t6 = threading.Thread(target=mitigation_tail_loop, daemon=True, name="mitigation-tail")
    t7 = threading.Thread(target=threshold_watch_loop, daemon=True, name="threshold-watch")
    t1.start()
    t2.start()
    t3.start()
    t4.start()
    t5.start()
    t6.start()
    t7.start()

    log.info("Exporter running. Metrics at http://0.0.0.0:8000/metrics")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
