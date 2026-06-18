"""
Attack Detection Evaluation Dashboard
Evaluasi offline hybrid IDS (Rule-Based + LSTM) per skenario serangan.
"""
import csv
import glob
import os

import numpy as np
import onnxruntime as ort
import dash
from dash import dcc, html, Input, Output, State, ctx
import plotly.graph_objs as go
from plotly.subplots import make_subplots
from datetime import datetime
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_curve, auc,
)

# ── Config ────────────────────────────────────────────────────────────────────
CSV_DIR    = os.getenv("CSV_DIR",   "/data/csv")
ONNX_MODEL = os.getenv("ONNX_MODEL", "/data/security_model.onnx")
WINDOW_SIZE = 10
LSTM_THRESH = 0.5

UE_WINDOW_SIZE = 30
UE_THRESH      = 0.025969
UE_ONNX_MODEL  = os.getenv("UE_ONNX_MODEL", "/data/models/gru_ue_v4.onnx")
UE_LABEL_NAMES = {0: "Normal", 1: "UL Flood", 2: "DL Flood", 3: "Burst ON/OFF", 4: "RoQ"}

ATTACK_BUTTONS = [
    ("btn-ul",    "1",   "UL Flood",    "#FF6B35"),
    ("btn-dl",    "2",   "DL Flood",    "#FF4757"),
    ("btn-burst", "3",   "Burst ON/OFF","#FFA502"),
    ("btn-rrc",   "4",   "RRC Storm",   "#FF6348"),
    ("btn-rf",    "5",   "RF Jammer",   "#D2A8FF"),
    ("btn-all",   "all", "All Attacks", "#8957E5"),
]

LABEL_NAMES = {0: "Normal", 1: "UL Flood", 2: "DL Flood",
               3: "Burst ON/OFF", 4: "RRC Storm", 5: "RF Jammer"}

BG    = "#0D1117"
CARD  = "#161B22"
BORD  = "#30363D"
ACCENT= "#58A6FF"
RED   = "#FF6B35"
GREEN = "#3FB950"
GOLD  = "#FFA502"
TEXT  = "#C9D1D9"
DIM   = "#8B949E"

STAGE_COLORS = {"Rule-Based": ACCENT, "LSTM": GOLD, "Hybrid": GREEN}


# ── Detection pipeline (mirrors evaluate_detection.py) ───────────────────────

class RuleBasedIDS:
    EPS = 1e-6

    def __init__(self):
        self.ul_sat_cnt = self.dl_sat_cnt = self.rf_susp_cnt = 0
        self.empty_storm_cnt = 0
        self.ul_var_buf = [0.0]*10; self.ul_var_head = self.ul_var_count = 0
        self.dl_var_buf = [0.0]*10; self.dl_var_head = self.dl_var_count = 0
        self.s2_sat_start_ms = self.s2_sat_dur_ms = self.s2_rec_start_ms = 0
        self.s2_rf_cnt = self.s2_rrc_cnt = 0
        self.prev_prb_total = 0.0
        self.burst_was_on = False
        self.burst_on_times = []
        self.burst_active_until = 0

    def detect(self, row, now_ms):
        severity = 0; stage1_hit = False; alert_type = "none"
        prb_dl = float(row.get("prb_usage_dl_ratio", 0))
        prb_ul = float(row.get("prb_usage_ul_ratio", 0))
        prb_dl_pct = prb_dl * 100.0
        prb_ul_pct = prb_ul * 100.0
        empty_ind  = float(row.get("empty_ind_rate", 0))
        air_delay  = float(row.get("air_delay_ul", 0))

        # UL variance
        self.ul_var_buf[self.ul_var_head] = prb_ul_pct
        self.ul_var_head = (self.ul_var_head + 1) % 10
        if self.ul_var_count < 10: self.ul_var_count += 1
        ul_mean = sum(self.ul_var_buf[:self.ul_var_count]) / self.ul_var_count
        prb_ul_variance = sum((v - ul_mean)**2 for v in self.ul_var_buf[:self.ul_var_count]) / self.ul_var_count

        # DL variance
        self.dl_var_buf[self.dl_var_head] = prb_dl_pct
        self.dl_var_head = (self.dl_var_head + 1) % 10
        if self.dl_var_count < 10: self.dl_var_count += 1
        dl_mean = sum(self.dl_var_buf[:self.dl_var_count]) / self.dl_var_count
        prb_dl_variance = sum((v - dl_mean)**2 for v in self.dl_var_buf[:self.dl_var_count]) / self.dl_var_count

        # R1: UL saturation
        if prb_ul_pct > 80.0 and prb_dl_pct < 15.0: self.ul_sat_cnt += 1
        else: self.ul_sat_cnt = 0
        if self.ul_sat_cnt >= 5 and not stage1_hit:
            stage1_hit = True; alert_type = "ul_saturation"; severity = max(severity, 1)

        # R2: DL saturation
        if prb_dl_pct > 80.0 and prb_ul_pct < 30.0: self.dl_sat_cnt += 1
        else: self.dl_sat_cnt = 0
        if self.dl_sat_cnt >= 3 and not stage1_hit:
            stage1_hit = True; alert_type = "dl_saturation"; severity = max(severity, 1)

        # R3b: RRC storm via empty indications
        if empty_ind >= 2.0 and prb_ul_pct < 30.0 and prb_dl_pct < 30.0:
            self.empty_storm_cnt += 1; self.s2_rrc_cnt += 1
        else:
            self.empty_storm_cnt = 0; self.s2_rrc_cnt = 0
        if self.empty_storm_cnt >= 3:
            if not stage1_hit: stage1_hit = True; alert_type = "rrc_storm"
            severity = max(severity, 1)
        if self.s2_rrc_cnt >= 4: severity = max(severity, 2)

        # R7: Radio collapse
        prb_total_now = prb_dl + prb_ul
        rf_collapse = (self.prev_prb_total > 0.4 and prb_total_now < 0.05 and air_delay < 1.0)
        if rf_collapse: self.rf_susp_cnt += 1; self.s2_rf_cnt += 1
        else: self.rf_susp_cnt = 0; self.s2_rf_cnt = 0
        self.prev_prb_total = prb_total_now
        if self.rf_susp_cnt >= 2 and not stage1_hit:
            stage1_hit = True; alert_type = "radio_degradation"; severity = max(severity, 1)
        if self.s2_rf_cnt >= 5: severity = max(severity, 2)

        # R8: Periodic burst
        burst_is_on = (prb_ul_pct > 70.0 and prb_dl_pct < 20.0)
        if burst_is_on and not self.burst_was_on: self.burst_on_times.append(now_ms)
        self.burst_was_on = burst_is_on
        self.burst_on_times = [t for t in self.burst_on_times if now_ms - t <= 90000]
        burst_on_count = len(self.burst_on_times)
        if burst_on_count >= 2:
            self.burst_active_until = max(self.burst_active_until,
                                          max(self.burst_on_times) + 15000)
        burst_alert = (self.burst_active_until > 0 and now_ms <= self.burst_active_until)
        if burst_alert and not stage1_hit:
            stage1_hit = True; alert_type = "periodic_burst"; severity = max(severity, 1)
        if burst_alert and burst_on_count >= 4: severity = max(severity, 2)

        # Stage 2: saturation persistence + variance flatline
        sat_active = alert_type in ("ul_saturation", "dl_saturation")
        if sat_active:
            if self.s2_sat_start_ms == 0: self.s2_sat_start_ms = now_ms
            self.s2_sat_dur_ms = now_ms - self.s2_sat_start_ms
            self.s2_rec_start_ms = 0
            if self.s2_sat_dur_ms >= 30000: severity = max(severity, 2)
            if (alert_type == "ul_saturation" and self.s2_sat_dur_ms >= 3000
                    and prb_ul_variance < 0.0001 and prb_ul_pct > 80.0):
                severity = max(severity, 2)
            if (alert_type == "dl_saturation" and self.s2_sat_dur_ms >= 3000
                    and prb_dl_variance < 0.0001 and prb_dl_pct > 80.0):
                severity = max(severity, 2)
        else:
            if self.s2_sat_start_ms != 0:
                if self.s2_rec_start_ms == 0: self.s2_rec_start_ms = now_ms
                if now_ms - self.s2_rec_start_ms >= 5000:
                    self.s2_sat_start_ms = self.s2_sat_dur_ms = self.s2_rec_start_ms = 0

        return severity, alert_type


class LSTMDetector:
    FEATURES = [
        "prb_usage_dl_ratio", "prb_usage_ul_ratio",
        "cqi", "rach_preamble", "air_delay_ul",
        "prb_direction", "prb_total",
        "prb_dl_delta", "prb_ul_delta", "prb_burst_index",
        "empty_ind_rate",
        "prb_dl_roll_mean", "prb_dl_roll_std",
        "prb_ul_roll_std", "prb_ul_roll_max", "prb_ul_roll_max_100",
    ]

    def __init__(self, model_path):
        self.sess = ort.InferenceSession(model_path)
        self.window = np.zeros((WINDOW_SIZE, len(self.FEATURES)), dtype=np.float32)
        self.filled = 0
        self.anomaly_cnt = 0
        self.stage2_start_ms = self.stage2_dur_ms = 0

    def update(self, row, now_ms):
        feat = np.array([float(row.get(f, 0)) for f in self.FEATURES], dtype=np.float32)
        self.window = np.roll(self.window, -1, axis=0)
        self.window[-1] = feat
        if self.filled < WINDOW_SIZE:
            self.filled += 1
            return 0, 0.0
        inp = self.window[np.newaxis].astype(np.float32)
        score = float(self.sess.run(["score"], {"input": inp})[0][0])
        if score > LSTM_THRESH:
            self.anomaly_cnt += 1
            if self.stage2_start_ms == 0: self.stage2_start_ms = now_ms
            self.stage2_dur_ms = now_ms - self.stage2_start_ms
        else:
            self.anomaly_cnt = 0
            self.stage2_start_ms = self.stage2_dur_ms = 0
        sev = 0
        if self.anomaly_cnt >= 3: sev = 1
        if self.stage2_dur_ms >= 30000: sev = 2
        return sev, score


# ── Per-UE detectors (UE CSV: 15 base + 4 burst features, GRU v4) ─────────────

_UE_CSV_FEATURES = [
    "prb_usage_dl_ratio", "prb_usage_ul_ratio", "thp_dl_kbps", "thp_ul_kbps",
    "prb_direction", "prb_total", "prb_ul_delta", "ul_efficiency",
    "prb_ul_roll_mean", "prb_ul_roll_std", "ul_persistence", "thp_total_kbps",
    "thp_ul_delta", "thp_dl_delta", "traffic_direction",
]
_UE_BURST_CLIP = 50.0
_UE_BURST_WIN  = 10
_UE_EPS        = 1e-6

# Rule indices match _UE_CSV_FEATURES order:
# 0=prb_dl 1=prb_ul 2=thp_dl 3=thp_ul 7=ul_eff 8=prb_ul_roll_mean 9=prb_ul_roll_std 10=ul_persistence
_UE_RULE_DEFS = [
    (lambda f: (f[3] > 15000.0) or  (f[1] > 0.70),  5),   # R1 UL Flood
    (lambda f: (f[2] > 15000.0) or  (f[0] > 0.85),  5),   # R2 DL Flood
    (lambda f: (f[9] > 0.12)    and (f[8] > 0.05),  5),   # R3 Burst
    (lambda f: (f[10] >= 0.90)  and (f[8] > 0.50),  10),  # R4 RoQ
    (lambda f: (f[1] > 0.30)    and (f[7] < 5000.0), 3),  # R5 Efficiency
]


class RuleBasedUEIDS:
    def __init__(self):
        self.counters = [0] * len(_UE_RULE_DEFS)

    def detect(self, row):
        f = [float(row.get(c, 0)) for c in _UE_CSV_FEATURES]
        mask = 0
        for i, (cond, needed) in enumerate(_UE_RULE_DEFS):
            if cond(f):
                self.counters[i] += 1
            else:
                self.counters[i] = 0
            if self.counters[i] >= needed:
                mask |= (1 << i)
        return (1 if mask > 0 else 0)


class GRUUEDetector:
    """Online GRU UE detector. Maintains rolling seq window + burst feature buffers."""

    def __init__(self, model_path):
        self.sess   = ort.InferenceSession(model_path)
        self.window = np.zeros((UE_WINDOW_SIZE, 19), dtype=np.float32)
        self.filled = 0
        # Rolling buffers for burst index computation (last 10 values)
        self._prb_ul_buf = [0.0] * _UE_BURST_WIN
        self._prb_dl_buf = [0.0] * _UE_BURST_WIN
        self._thp_ul_buf = [0.0] * _UE_BURST_WIN
        self._thp_dl_buf = [0.0] * _UE_BURST_WIN

    def _push(self, buf, val):
        buf.pop(0); buf.append(val)
        return sum(buf) / len(buf)

    def update(self, row):
        prb_ul = float(row.get("prb_usage_ul_ratio", 0))
        prb_dl = float(row.get("prb_usage_dl_ratio", 0))
        thp_ul = float(row.get("thp_ul_kbps", 0))
        thp_dl = float(row.get("thp_dl_kbps", 0))

        prb_ul_mean = self._push(self._prb_ul_buf, prb_ul)
        prb_dl_mean = self._push(self._prb_dl_buf, prb_dl)
        thp_ul_mean = self._push(self._thp_ul_buf, thp_ul)
        thp_dl_mean = self._push(self._thp_dl_buf, thp_dl)

        base = [float(row.get(c, 0)) for c in _UE_CSV_FEATURES]
        burst = [
            min(np.log1p(prb_ul) / (prb_ul_mean + _UE_EPS), _UE_BURST_CLIP),
            min(np.log1p(prb_dl) / (prb_dl_mean + _UE_EPS), _UE_BURST_CLIP),
            min(thp_ul / (thp_ul_mean + 1.0), _UE_BURST_CLIP),
            min(thp_dl / (thp_dl_mean + 1.0), _UE_BURST_CLIP),
        ]
        feat = np.array(base + burst, dtype=np.float32)

        self.window = np.roll(self.window, -1, axis=0)
        self.window[-1] = feat
        if self.filled < UE_WINDOW_SIZE:
            self.filled += 1
            return 0, 0.0

        inp = self.window[np.newaxis].astype(np.float32)
        score = float(self.sess.run(["mse"], {"input": inp})[0][0])
        sev = 1 if score > UE_THRESH else 0
        return sev, score


# ── CSV source helpers ────────────────────────────────────────────────────────

REQUIRED_COL    = "prb_usage_dl_ratio"  # column that identifies compatible CSVs
UE_MARKER_COL   = "thp_ul_kbps"         # present in UE CSV, absent in cell-level CSV

def _csv_has_required_cols(path):
    try:
        with open(path, newline="") as f:
            header = f.readline()
        return REQUIRED_COL in header
    except OSError:
        return False


def is_ue_csv(rows):
    """True if this CSV uses the per-UE feature schema (has thp_ul_kbps column)."""
    return bool(rows) and UE_MARKER_COL in rows[0]


def find_live_csv():
    """Return the most recently modified training_*.csv with required columns."""
    files = [f for f in glob.glob(os.path.join(CSV_DIR, "training_*.csv"))
             if _csv_has_required_cols(f)]
    return max(files, key=os.path.getmtime) if files else None


def get_csv_options():
    """Return list of dcc.Dropdown options for all compatible CSVs, newest first."""
    patterns = ["training_*.csv", "dataset_*.csv"]
    all_files = []
    for pat in patterns:
        all_files.extend(glob.glob(os.path.join(CSV_DIR, pat)))

    seen = set()
    items = []
    for path in all_files:
        if path in seen or not _csv_has_required_cols(path):
            continue
        seen.add(path)
        mtime = os.path.getmtime(path)
        name  = os.path.basename(path)
        dt    = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        items.append((mtime, path, name, dt))

    items.sort(key=lambda x: x[0], reverse=True)

    options = []
    for _, path, name, dt in items:
        options.append({"label": f"{name}  ({dt})", "value": path})
    return options


def csv_label_summary(path):
    """Return label distribution string for a CSV, e.g. '0:12k 1:1k 2:1k'."""
    try:
        counts = {}
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                lbl = row.get("label", "?")
                counts[lbl] = counts.get(lbl, 0) + 1
        total = sum(counts.values())
        parts = []
        for k in sorted(counts.keys(), key=lambda x: int(x) if x.isdigit() else 999):
            n = counts[k]
            parts.append(f"L{k}:{n:,}")
        return f"{total:,} baris  |  " + "  ".join(parts)
    except OSError:
        return "?"


# ── Evaluation engine ─────────────────────────────────────────────────────────

def load_rows(csv_path):
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def run_eval(attack_filter, det_mode="hybrid", csv_path=None):
    """
    Run detection on the testing dataset.
    attack_filter: "1"-"5" or "all"
    det_mode: "hybrid" | "rule" | "lstm"
    Returns dict with metrics, arrays for plotting.
    """
    if not csv_path:
        csv_path = find_live_csv()
    if not csv_path:
        return None

    all_rows = load_rows(csv_path)
    if not all_rows:
        return None

    # Filter rows by attack label
    label_mismatch = False
    if attack_filter == "all":
        rows = all_rows
    else:
        lbl = int(attack_filter)
        rows = [r for r in all_rows if int(r["label"]) == lbl or int(r["label"]) == 0]
        if not rows:
            # Label not present in this CSV — fall back to all rows
            rows = all_rows
            label_mismatch = True

    # Collect available labels for info display
    available_labels = sorted({int(r["label"]) for r in all_rows})

    ue_mode = is_ue_csv(all_rows)
    if ue_mode:
        ids  = RuleBasedUEIDS()
        lstm = GRUUEDetector(UE_ONNX_MODEL)
        label_names = UE_LABEL_NAMES
    else:
        ids  = RuleBasedIDS()
        lstm = LSTMDetector(ONNX_MODEL)
        label_names = LABEL_NAMES

    labels     = []
    rule_sev   = []
    lstm_sev   = []
    final_sev  = []
    lstm_scores= []
    timestamps = []
    prb_dl_arr = []
    prb_ul_arr = []

    for row in rows:
        lbl    = int(row["label"])
        now_ms = int(row["timestamp_ms"])
        if ue_mode:
            rsev        = ids.detect(row)
            lsev, lsc   = lstm.update(row)
        else:
            rsev, _     = ids.detect(row, now_ms)
            lsev, lsc   = lstm.update(row, now_ms)

        if det_mode == "rule":
            fsev = rsev
        elif det_mode == "lstm":
            fsev = lsev
        else:
            fsev = max(rsev, lsev)

        labels.append(lbl)
        rule_sev.append(rsev)
        lstm_sev.append(lsev)
        final_sev.append(fsev)
        lstm_scores.append(lsc)
        timestamps.append(now_ms)
        prb_dl_arr.append(float(row.get("prb_usage_dl_ratio", 0)))
        prb_ul_arr.append(float(row.get("prb_usage_ul_ratio", 0)))

    labels     = np.array(labels)
    rule_sev   = np.array(rule_sev)
    lstm_sev   = np.array(lstm_sev)
    final_sev  = np.array(final_sev)
    lstm_scores= np.array(lstm_scores)
    timestamps = np.array(timestamps)

    y_true = (labels != 0).astype(int)

    def metrics(pred):
        y_pred = (pred >= 1).astype(int)
        normal = labels == 0
        fp = (pred[normal] >= 1).sum()
        fpr = float(fp / normal.sum()) if normal.sum() > 0 else 0.0
        return {
            "accuracy":  float(accuracy_score(y_true, y_pred)),
            "recall":    float(recall_score(y_true, y_pred, zero_division=0)),
            "precision": float(precision_score(y_true, y_pred, zero_division=0)),
            "f1":        float(f1_score(y_true, y_pred, zero_division=0)),
            "fpr":       fpr,
        }

    # ROC-AUC on LSTM scores (requires both classes present)
    roc_auc = None  # None = tidak terdefinisi (1 kelas)
    fpr_arr = tpr_arr = None
    has_both_classes = y_true.sum() > 0 and y_true.sum() < len(y_true)
    if has_both_classes:
        fpr_arr, tpr_arr, _ = roc_curve(y_true, lstm_scores)
        roc_auc = float(auc(fpr_arr, tpr_arr))

    # Detection latency per attack segment
    det_latencies = []
    i = 0
    n = len(labels)
    while i < n:
        if labels[i] != 0:
            seg_start = i
            seg_lbl = labels[i]
            while i < n and labels[i] == seg_lbl:
                i += 1
            # Find first detection after seg_start
            for j in range(seg_start, min(i + 50, n)):
                if final_sev[j] >= 1:
                    lat = (timestamps[j] - timestamps[seg_start]) / 1000.0
                    det_latencies.append(max(0.0, lat))
                    break
        else:
            i += 1

    det_latency_avg = float(np.mean(det_latencies)) if det_latencies else None

    return {
        "csv_path":        csv_path,
        "attack_filter":   attack_filter,
        "det_mode":        det_mode,
        "n_rows":          len(rows),
        "n_attack":        int(y_true.sum()),
        "label_mismatch":  label_mismatch,
        "available_labels": available_labels,
        "label_names":     label_names,
        "is_ue":           ue_mode,
        "stage_metrics": {
            "Rule-Based": metrics(rule_sev),
            "LSTM":        metrics(lstm_sev),
            "Hybrid":      metrics(final_sev),
        },
        "active_metrics": (metrics(rule_sev) if det_mode == "rule"
                          else metrics(lstm_sev) if det_mode == "lstm"
                          else metrics(final_sev)),
        "roc_auc":    roc_auc,
        "fpr_arr":    fpr_arr.tolist() if fpr_arr is not None else None,
        "tpr_arr":    tpr_arr.tolist() if tpr_arr is not None else None,
        "det_latency_avg": det_latency_avg,
        "timestamps":  timestamps.tolist(),
        "prb_dl":      prb_dl_arr,
        "prb_ul":      prb_ul_arr,
        "labels":      labels.tolist(),
        "final_sev":   final_sev.tolist(),
        "lstm_scores": lstm_scores.tolist(),
    }


# ── Dash helpers ──────────────────────────────────────────────────────────────

def _btn_style(color, selected=False):
    base = {
        "backgroundColor": color if selected else "transparent",
        "color":           "white" if selected else color,
        "border":          f"2px solid {color}",
        "padding":         "10px 20px",
        "borderRadius":    "8px",
        "cursor":          "pointer",
        "fontWeight":      "bold",
        "fontSize":        "14px",
        "transition":      "all 0.2s",
    }
    return base


def _metric_card(label, value, unit="", color=ACCENT, border=BORD):
    return html.Div(style={
        "backgroundColor": CARD, "borderRadius": "10px",
        "padding": "16px", "textAlign": "center",
        "border": f"1px solid {border}",
    }, children=[
        html.Div(label, style={"color": DIM, "fontSize": "12px", "marginBottom": "6px"}),
        html.Span(value, style={"fontSize": "26px", "fontWeight": "bold", "color": color}),
        html.Span(f" {unit}", style={"fontSize": "12px", "color": DIM}) if unit else "",
    ])


def _layout_base():
    return dict(
        paper_bgcolor=BG, plot_bgcolor="#161B22",
        font=dict(color=TEXT, size=12),
        margin=dict(l=50, r=20, t=40, b=40),
        legend=dict(orientation="h", y=1.08, x=0),
        xaxis=dict(gridcolor="#21262D", zerolinecolor="#21262D"),
        yaxis=dict(gridcolor="#21262D"),
    )


# ── App layout ────────────────────────────────────────────────────────────────

app = dash.Dash(__name__, title="xApp Testing Evaluation",
                suppress_callback_exceptions=True,
                meta_tags=[{"name": "viewport",
                             "content": "width=device-width, initial-scale=1"}])

app.layout = html.Div(style={"backgroundColor": BG, "minHeight": "100vh",
                              "fontFamily": "Inter, sans-serif", "padding": "20px"}, children=[

    # Header
    html.Div(style={"marginBottom": "20px"}, children=[
        html.H1("Attack Detection Evaluation",
                style={"color": ACCENT, "margin": 0, "fontSize": "22px"}),
        html.P("Evaluasi offline Hybrid IDS (Rule-Based Stage 1 + LSTM Stage 2)",
               id="app-subtitle",
               style={"color": DIM, "margin": "4px 0 0 0", "fontSize": "13px"}),
    ]),

    # Data source selector
    html.Div(style={"backgroundColor": CARD, "borderRadius": "10px",
                    "padding": "14px 16px", "marginBottom": "12px",
                    "border": f"1px solid {BORD}"}, children=[
        html.Div(style={"display": "flex", "alignItems": "center", "gap": "12px",
                        "flexWrap": "wrap"}, children=[
            html.Div("Sumber Data:", style={"color": DIM, "fontSize": "11px",
                                            "whiteSpace": "nowrap"}),
            html.Button("⚡ Live (terbaru)", id="btn-live-src", n_clicks=0,
                        style=_btn_style(GREEN, selected=True)),
            dcc.Dropdown(
                id="csv-dropdown",
                placeholder="— atau pilih file CSV —",
                clearable=True,
                style={
                    "flex": "1", "minWidth": "280px", "maxWidth": "560px",
                    "backgroundColor": "#0D1117", "color": TEXT,
                    "border": f"1px solid {BORD}", "borderRadius": "8px",
                    "fontSize": "13px",
                },
                className="dark-dropdown",
            ),
            html.Div(id="csv-info",
                     style={"color": DIM, "fontSize": "12px", "fontFamily": "monospace",
                            "flexShrink": 0}),
        ]),
    ]),

    # Attack selector + detection mode controls
    html.Div(style={"backgroundColor": CARD, "borderRadius": "10px",
                    "padding": "16px", "marginBottom": "16px",
                    "border": f"1px solid {BORD}"}, children=[
        # Attack buttons row
        html.Div(style={"display": "flex", "alignItems": "center",
                         "gap": "16px", "flexWrap": "wrap"}, children=[
            html.Div(style={"display": "flex", "flexDirection": "column", "gap": "8px"}, children=[
                html.Div("Skenario Serangan:", style={"color": DIM, "fontSize": "11px"}),
                html.Div(style={"display": "flex", "gap": "8px", "flexWrap": "wrap"}, children=[
                    html.Button(label, id=btn_id, n_clicks=0, style=_btn_style(color))
                    for btn_id, _, label, color in ATTACK_BUTTONS
                ]),
            ]),
            # Divider
            html.Div(style={"width": "1px", "backgroundColor": BORD,
                             "alignSelf": "stretch", "margin": "0 8px"}),
            # Detection mode toggle
            html.Div(style={"display": "flex", "flexDirection": "column", "gap": "8px"}, children=[
                html.Div("Mode Deteksi:", style={"color": DIM, "fontSize": "11px"}),
                html.Div(style={"display": "flex", "gap": "8px"}, children=[
                    html.Button("📐 Rule-Based Only", id="mode-rule",  n_clicks=0,
                                style=_btn_style(ACCENT)),
                    html.Button("🧠 LSTM Only",       id="mode-lstm",  n_clicks=0,
                                style=_btn_style(GOLD)),
                    html.Button("★ Hybrid",           id="mode-hybrid",n_clicks=0,
                                style=_btn_style(GREEN, selected=True)),
                ]),
            ]),
        ]),
    ]),

    # Status bar
    html.Div(id="status-bar", style={"color": DIM, "fontSize": "13px",
                                      "marginBottom": "16px", "fontFamily": "monospace"}),

    # Loading wrapper
    dcc.Loading(type="circle", color=ACCENT, children=[

        # Metric cards
        html.Div(id="metric-cards", style={"display": "grid",
            "gridTemplateColumns": "repeat(7, 1fr)", "gap": "10px",
            "marginBottom": "16px"}),

        # Charts row
        html.Div(style={"display": "grid", "gridTemplateColumns": "3fr 2fr",
                         "gap": "14px", "marginBottom": "14px"}, children=[
            html.Div(style={"backgroundColor": CARD, "borderRadius": "10px",
                             "padding": "12px", "border": f"1px solid {BORD}"}, children=[
                dcc.Graph(id="graph-prb", style={"height": "320px"},
                          config={"displayModeBar": False}),
            ]),
            html.Div(style={"backgroundColor": CARD, "borderRadius": "10px",
                             "padding": "12px", "border": f"1px solid {BORD}"}, children=[
                dcc.Graph(id="graph-roc", style={"height": "320px"},
                          config={"displayModeBar": False}),
            ]),
        ]),

        # LSTM score time-series
        html.Div(style={"backgroundColor": CARD, "borderRadius": "10px",
                         "padding": "12px", "marginBottom": "14px",
                         "border": f"1px solid {BORD}"}, children=[
            dcc.Graph(id="graph-lstm", style={"height": "220px"},
                      config={"displayModeBar": False}),
        ]),

        # Per-stage comparison table
        html.Div(id="stage-table", style={"backgroundColor": CARD, "borderRadius": "10px",
                                           "padding": "16px", "border": f"1px solid {BORD}"}),

    ]),

    # Hidden stores
    dcc.Store(id="active-attack", data=None),
    dcc.Store(id="active-mode",   data="hybrid"),
    dcc.Store(id="csv-source",    data="live"),
    dcc.Store(id="ue-mode",       data=False),
    dcc.Interval(id="live-refresh", interval=10_000, disabled=False),
])


# ── Callbacks ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("csv-source",    "data"),
    Output("btn-live-src",  "style"),
    Output("csv-dropdown",  "options"),
    Output("csv-dropdown",  "value"),
    Output("csv-info",      "children"),
    Output("live-refresh",  "disabled"),
    Output("ue-mode",       "data"),
    Input("btn-live-src",   "n_clicks"),
    Input("csv-dropdown",   "value"),
    Input("live-refresh",   "n_intervals"),
    State("csv-source",     "data"),
)
def handle_csv_source(live_clicks, dropdown_val, _intervals, current_source):
    options = get_csv_options()
    triggered = ctx.triggered_id

    # Determine new source
    if triggered == "btn-live-src":
        source = "live"
        new_dd_val = None
    elif triggered == "csv-dropdown" and dropdown_val:
        source = dropdown_val
        new_dd_val = dropdown_val
    else:
        # Interval tick or initial load — keep current source
        source = current_source or "live"
        new_dd_val = None if source == "live" else source

    # Resolve actual path for info display
    if source == "live":
        path = find_live_csv()
        live_selected = True
    else:
        path = source
        live_selected = False

    # File info badge
    if path and os.path.exists(path):
        info = csv_label_summary(path)
        mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
        info_el = html.Span([
            html.Span(os.path.basename(path),
                      style={"color": TEXT, "marginRight": "8px"}),
            html.Span(info, style={"color": DIM}),
            html.Span(f"  mod: {mtime}", style={"color": "#444", "marginLeft": "8px"}),
        ])
    else:
        info_el = html.Span("Tidak ada CSV yang ditemukan.", style={"color": RED})

    btn_style = _btn_style(GREEN, selected=live_selected)
    interval_disabled = (source != "live")

    # Detect UE CSV from header
    ue = False
    if path and os.path.exists(path):
        try:
            with open(path, newline="") as f:
                ue = UE_MARKER_COL in f.readline()
        except OSError:
            pass

    return source, btn_style, options, new_dd_val, info_el, interval_disabled, ue


@app.callback(
    Output("app-subtitle",  "children"),
    Output("btn-rrc",       "children"),
    Output("btn-rf",        "style"),
    Output("mode-lstm",     "children"),
    Input("ue-mode",        "data"),
)
def update_ue_ui(ue):
    if ue:
        subtitle  = "Evaluasi offline Hybrid IDS (Rule-Based Stage 1 + GRU-UE v4 Stage 2)"
        rrc_label = "RoQ"
        rf_style  = {**_btn_style("#555"), "opacity": "0.35", "cursor": "not-allowed"}
        ml_label  = "🤖 GRU Only"
    else:
        subtitle  = "Evaluasi offline Hybrid IDS (Rule-Based Stage 1 + LSTM Stage 2)"
        rrc_label = "RRC Storm"
        rf_style  = _btn_style("#D2A8FF")
        ml_label  = "🧠 LSTM Only"
    return subtitle, rrc_label, rf_style, ml_label


@app.callback(
    Output("active-attack", "data"),
    [Input(btn_id, "n_clicks") for btn_id, _, _, _ in ATTACK_BUTTONS],
    prevent_initial_call=True,
)
def store_attack(*_):
    triggered = ctx.triggered_id
    for btn_id, lbl_key, _, _ in ATTACK_BUTTONS:
        if triggered == btn_id:
            return lbl_key
    return None


@app.callback(
    Output("active-mode",    "data"),
    Output("mode-rule",      "style"),
    Output("mode-lstm",      "style"),
    Output("mode-hybrid",    "style"),
    Input("mode-rule",       "n_clicks"),
    Input("mode-lstm",       "n_clicks"),
    Input("mode-hybrid",     "n_clicks"),
    prevent_initial_call=True,
)
def store_mode(*_):
    tid = ctx.triggered_id
    mode = "hybrid"
    if tid == "mode-rule":  mode = "rule"
    elif tid == "mode-lstm": mode = "lstm"
    return (
        mode,
        _btn_style(ACCENT,  selected=(mode == "rule")),
        _btn_style(GOLD,    selected=(mode == "lstm")),
        _btn_style(GREEN,   selected=(mode == "hybrid")),
    )


@app.callback(
    Output("status-bar",    "children"),
    Output("metric-cards",  "children"),
    Output("graph-prb",     "figure"),
    Output("graph-roc",     "figure"),
    Output("graph-lstm",    "figure"),
    Output("stage-table",   "children"),
    Input("active-attack",  "data"),
    Input("active-mode",    "data"),
    Input("csv-source",     "data"),
)
def update_dashboard(attack_filter, det_mode, csv_source):
    empty_fig = go.Figure().update_layout(**_layout_base())

    det_mode = det_mode or "hybrid"

    # Resolve CSV path from source
    if csv_source == "live" or not csv_source:
        resolved_csv = find_live_csv()
    else:
        resolved_csv = csv_source if os.path.exists(csv_source) else find_live_csv()

    if attack_filter is None:
        src_name = os.path.basename(resolved_csv) if resolved_csv else "—"
        msg = html.Span([
            html.Span("← Pilih skenario serangan untuk memulai evaluasi.  ",
                      style={"color": DIM}),
            html.Span(f"[{src_name}]", style={"color": "#444", "fontSize": "12px"}),
        ])
        cards = [_metric_card("—", "—") for _ in range(7)]
        empty_table = html.P("Belum ada data.", style={"color": DIM})
        return msg, cards, empty_fig, empty_fig, empty_fig, empty_table

    # Run evaluation
    result = run_eval(attack_filter, det_mode, csv_path=resolved_csv)

    if result is None:
        csv_name = os.path.basename(resolved_csv) if resolved_csv else "?"
        return (html.Span(f"⚠ CSV kosong atau tidak terbaca: {csv_name}", style={"color": GOLD}),
                [], empty_fig, empty_fig, empty_fig, html.P("Tidak ada data.", style={"color": DIM}))

    is_ue = result.get("is_ue", False)
    ml_name = "GRU" if is_ue else "LSTM"
    ml_thresh = UE_THRESH if is_ue else LSTM_THRESH
    mode_labels = {"rule": "Rule-Based Only", "lstm": f"{ml_name} Only", "hybrid": "★ Hybrid"}
    active_m = result["active_metrics"]
    det_lat  = result["det_latency_avg"]
    roc_auc  = result["roc_auc"]

    attack_name = dict((k, v) for _, k, v, _ in ATTACK_BUTTONS).get(attack_filter, attack_filter)
    _lnames   = result.get("label_names", LABEL_NAMES)
    avail_str = "  ".join(f"L{l}={_lnames.get(l,'?')}" for l in result["available_labels"])

    if result["label_mismatch"]:
        status = html.Span([
            html.Span(f"⚠ Label '{attack_name}' tidak ada di CSV ini — menampilkan semua data.  ",
                      style={"color": GOLD}),
            html.Span(f"Tersedia: {avail_str}", style={"color": DIM, "fontSize": "12px"}),
        ])
    else:
        status = html.Span([
            html.Span(f"✓ [{mode_labels.get(det_mode, det_mode)}] {attack_name}  ",
                      style={"color": GREEN}),
            html.Span(f"{result['n_rows']:,} baris | {result['n_attack']:,} attack | "
                      f"{os.path.basename(result['csv_path'])}",
                      style={"color": DIM, "fontSize": "12px"}),
        ])

    # Metric cards — show active mode metrics prominently
    def pct(v): return f"{v*100:.1f}"
    lat_text = f"{det_lat*1000:.0f}" if det_lat is not None else "N/A"
    lat_color = GREEN if det_lat and det_lat < 2 else (GOLD if det_lat else DIM)
    mode_color = {"rule": ACCENT, "lstm": GOLD, "hybrid": GREEN}.get(det_mode, GREEN)

    cards = [
        _metric_card("Accuracy",       pct(active_m["accuracy"]),  "%", mode_color, mode_color),
        _metric_card("Recall",         pct(active_m["recall"]),    "%", mode_color),
        _metric_card("Precision",      pct(active_m["precision"]), "%", mode_color),
        _metric_card("F1-Score",       pct(active_m["f1"]),        "%", mode_color, mode_color),
        _metric_card("FPR",            pct(active_m["fpr"]),       "%", GOLD),
        _metric_card(f"ROC-AUC ({ml_name})",
                     f"{roc_auc:.3f}" if roc_auc is not None else "N/A",
                     "", "#D2A8FF" if roc_auc is not None else DIM),
        _metric_card("Det. Latency",   lat_text,                   "ms", lat_color),
    ]

    # PRB time-series
    ts = result["timestamps"]
    labels = result["labels"]
    t_rel = [(t - ts[0]) / 1000.0 for t in ts]
    det_flags = result["final_sev"]

    fig_prb = go.Figure()

    # Shade attack regions
    prev_lbl = 0
    seg_start = None
    attack_colors = {"1": "rgba(255,107,53,0.12)", "2": "rgba(255,71,87,0.12)",
                     "3": "rgba(255,165,2,0.12)",  "4": "rgba(255,99,72,0.12)"}
    for i, lbl in enumerate(labels):
        if lbl != 0 and prev_lbl == 0:
            seg_start = t_rel[i]
        if lbl == 0 and prev_lbl != 0 and seg_start is not None:
            color = attack_colors.get(str(prev_lbl), "rgba(255,255,255,0.08)")
            fig_prb.add_vrect(x0=seg_start, x1=t_rel[i], fillcolor=color,
                              layer="below", line_width=0,
                              annotation_text=_lnames.get(prev_lbl, ""),
                              annotation_font_color=DIM, annotation_font_size=10)
            seg_start = None
        prev_lbl = lbl
    if seg_start is not None:
        color = attack_colors.get(str(prev_lbl), "rgba(255,255,255,0.08)")
        fig_prb.add_vrect(x0=seg_start, x1=t_rel[-1], fillcolor=color,
                          layer="below", line_width=0)

    fig_prb.add_trace(go.Scatter(x=t_rel, y=[v*100 for v in result["prb_dl"]],
                                  name="PRB DL (%)", line=dict(color=ACCENT, width=1.5)))
    fig_prb.add_trace(go.Scatter(x=t_rel, y=[v*100 for v in result["prb_ul"]],
                                  name="PRB UL (%)", line=dict(color=RED, width=1.5)))

    # Detection markers
    det_times = [t_rel[i] for i, s in enumerate(det_flags) if s >= 1]
    det_prb   = [(result["prb_ul"][i] + result["prb_dl"][i]) * 50 for i, s in enumerate(det_flags) if s >= 1]
    if det_times:
        fig_prb.add_trace(go.Scatter(x=det_times, y=det_prb, mode="markers",
                                      name="Detection",
                                      marker=dict(symbol="x", size=6, color=GREEN)))

    fig_prb.add_hline(y=80, line_dash="dash", line_color="#555",
                      annotation_text="80% threshold", annotation_font_color=DIM,
                      annotation_font_size=10)
    fig_prb.update_layout(title=f"PRB Utilization — {attack_name}",
                           xaxis_title="Waktu (detik)", yaxis_title="PRB (%)",
                           yaxis_range=[0, 105], **_layout_base())

    # ROC curve
    fig_roc = go.Figure()
    if result["fpr_arr"] is not None and result["tpr_arr"] is not None:
        auc_label = f"{ml_name} AUC={roc_auc:.3f}" if roc_auc is not None else ml_name
        fig_roc.add_trace(go.Scatter(x=result["fpr_arr"], y=result["tpr_arr"],
                                      name=auc_label,
                                      line=dict(color=GOLD, width=2)))
        roc_title = f"ROC Curve — AUC={roc_auc:.3f}"
    else:
        fig_roc.add_annotation(text="ROC-AUC tidak tersedia<br>(hanya 1 kelas di CSV ini)",
                               x=0.5, y=0.5, xref="paper", yref="paper",
                               showarrow=False, font=dict(color=DIM, size=14))
        roc_title = "ROC Curve — N/A (1 kelas)"
    fig_roc.add_trace(go.Scatter(x=[0, 1], y=[0, 1], name="Random",
                                  line=dict(color="#444", dash="dash", width=1)))
    fig_roc.update_layout(title=roc_title,
                           xaxis_title="False Positive Rate",
                           yaxis_title="True Positive Rate",
                           yaxis_range=[0, 1.02], xaxis_range=[0, 1],
                           **_layout_base())

    # LSTM score time-series
    fig_lstm = go.Figure()
    fig_lstm.add_trace(go.Scatter(x=t_rel, y=result["lstm_scores"],
                                   name=f"{ml_name} Anomaly Score",
                                   line=dict(color=GOLD, width=1.5),
                                   fill="tozeroy", fillcolor="rgba(255,165,2,0.08)"))
    fig_lstm.add_hline(y=ml_thresh, line_dash="dash", line_color=RED,
                        annotation_text=f"threshold={ml_thresh}",
                        annotation_font_color=RED, annotation_font_size=10)
    fig_lstm.update_layout(title=f"{ml_name} Anomaly Score",
                            xaxis_title="Waktu (detik)", yaxis_title="Score",
                            **_layout_base())

    # Per-stage comparison table
    stages = ["Rule-Based", "LSTM", "Hybrid"]
    col_style = {"padding": "8px 14px", "textAlign": "center", "fontSize": "13px"}
    hdr_style = {**col_style, "color": DIM, "fontSize": "11px",
                 "borderBottom": f"1px solid {BORD}", "fontWeight": "normal"}

    rows_html = []
    for stage in stages:
        m = result["stage_metrics"][stage]
        color = STAGE_COLORS[stage]
        is_hybrid = stage == "Hybrid"
        row_style = {"borderBottom": f"1px solid {BORD}",
                     "backgroundColor": "rgba(63,185,80,0.06)" if is_hybrid else "transparent"}
        rows_html.append(html.Tr(style=row_style, children=[
            html.Td(("★ " if is_hybrid else "") + stage,
                    style={**col_style, "color": color, "fontWeight": "bold", "textAlign": "left"}),
            html.Td(f"{m['accuracy']*100:.1f}%",  style={**col_style, "color": TEXT}),
            html.Td(f"{m['recall']*100:.1f}%",    style={**col_style, "color": TEXT}),
            html.Td(f"{m['precision']*100:.1f}%", style={**col_style, "color": TEXT}),
            html.Td(f"{m['f1']*100:.1f}%",        style={**col_style, "color": color, "fontWeight": "bold"}),
            html.Td(f"{m['fpr']*100:.2f}%",       style={**col_style, "color": GOLD}),
        ]))

    table = html.Div([
        html.H4("Perbandingan Per-Stage", style={"color": TEXT, "margin": "0 0 12px 0",
                                                   "fontSize": "14px"}),
        html.Table(style={"width": "100%", "borderCollapse": "collapse"}, children=[
            html.Thead(html.Tr([
                html.Th("Stage",     style={**hdr_style, "textAlign": "left"}),
                html.Th("Accuracy",  style=hdr_style),
                html.Th("Recall",    style=hdr_style),
                html.Th("Precision", style=hdr_style),
                html.Th("F1-Score",  style=hdr_style),
                html.Th("FPR",       style=hdr_style),
            ])),
            html.Tbody(rows_html),
        ]),
    ])

    return status, cards, fig_prb, fig_roc, fig_lstm, table


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing Evaluation Dashboard — http://0.0.0.0:8050")
    app.run(host="0.0.0.0", port=8050, debug=False)
