"""
Per-UE Attack Detection Evaluation Dashboard
Evaluasi offline hybrid IDS per-UE (Rule-Based + GRU/LSTM-UE v4).
"""
import csv
import glob
import os
import time
from collections import OrderedDict

import numpy as np
import onnxruntime as ort
import dash
from dash import dcc, html, Input, Output, State, ctx, no_update
import plotly.graph_objs as go
from datetime import datetime
from sklearn.metrics import roc_curve, auc

# ── Config ────────────────────────────────────────────────────────────────────
CSV_DIR            = os.getenv("CSV_DIR",            "/data/csv")
UE_WINDOW_SIZE     = 30
GRU_UE_THRESH      = 0.025969
LSTM_UE_THRESH     = 0.025266
XAPP_CYCLE_MS      = 120.0  # one xApp KPM/E2SM-RC cycle (mitigation control latency)
_LAT_SAMPLE        = 300    # max windows/rows sampled for live latency profiling
UE_ONNX_MODEL      = os.getenv("UE_ONNX_MODEL",      "/data/models/gru_ue_v4.onnx")
LSTM_UE_ONNX_MODEL = os.getenv("LSTM_UE_ONNX_MODEL", "/data/models/lstm_ue_v4.onnx")
LABEL_NAMES        = {0: "Normal", 1: "UL Flood", 2: "DL Flood", 3: "Burst ON/OFF", 4: "RoQ"}

ATTACK_BUTTONS = [
    ("btn-ul",    "1",   "UL Flood",    "#FF6B35"),
    ("btn-dl",    "2",   "DL Flood",    "#FF4757"),
    ("btn-burst", "3",   "Burst ON/OFF","#FFA502"),
    ("btn-roq",   "4",   "RoQ",         "#FF6348"),
    ("btn-all",   "all", "All Attacks", "#8957E5"),
]

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

# ── Per-UE detectors (Rule-Based + GRU/LSTM UE v4, 15+4 features) ────────────

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

    def __init__(self, sess):
        self.sess   = sess
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
        sev = 1 if score > GRU_UE_THRESH else 0
        return sev, score


class LSTMUEDetector(GRUUEDetector):
    """Same architecture as GRUUEDetector, different model + threshold."""

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
        sev = 1 if score > LSTM_UE_THRESH else 0
        return sev, score


# Pre-load ONNX sessions once at startup — reused across all run_eval calls
_GRU_SESS  = ort.InferenceSession(UE_ONNX_MODEL)
_LSTM_SESS = ort.InferenceSession(LSTM_UE_ONNX_MODEL)

# ── CSV source helpers ────────────────────────────────────────────────────────

def _csv_has_ue_cols(path):
    try:
        with open(path, newline="") as f:
            header = f.readline()
        return "thp_ul_kbps" in header
    except OSError:
        return False


def find_live_csv():
    """Return the most recently modified per_ue_training_*.csv."""
    files = [f for f in glob.glob(os.path.join(CSV_DIR, "per_ue_training_*.csv"))
             if _csv_has_ue_cols(f)]
    return max(files, key=os.path.getmtime) if files else None


def get_csv_options():
    """Return dropdown options for per-UE CSVs (per_ue_training_* + dataset_*ue*)."""
    patterns = ["per_ue_training_*.csv", "dataset_*ue*.csv", "dataset_attack_ue*.csv",
                "dataset_training_ue*.csv"]
    seen = set(); items = []
    for pat in patterns:
        for path in glob.glob(os.path.join(CSV_DIR, pat)):
            if path in seen or not _csv_has_ue_cols(path):
                continue
            seen.add(path)
            mtime = os.path.getmtime(path)
            dt    = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            items.append((mtime, path, os.path.basename(path), dt))
    items.sort(key=lambda x: x[0], reverse=True)
    return [{"label": f"{n}  ({dt})", "value": p} for _, p, n, dt in items]


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


# ── Evaluation engine — window-level, mirrors evaluate_per_ue_v2.py ───────────

_VAL_CSV   = "dataset_validation_ue_juni.csv"
_VAL_CACHE = {}  # cached validation-set detection (mode-independent raw arrays)


def _rmean(arr, w=_UE_BURST_WIN):
    out = np.empty(len(arr), dtype=np.float64)
    for i in range(len(arr)):
        out[i] = arr[max(0, i - w + 1):i + 1].mean()
    return out


def _build_feat_group(grp):
    """19-feature array for one RNTI group, matching add_burst_features_rows():
    prb_ul_burst uses the CSV's prb_ul_roll_mean (feat idx 8); the other three
    burst indices use fresh rolling means."""
    fb = np.array([[float(r.get(c, 0.0)) for c in _UE_CSV_FEATURES] for r in grp],
                  dtype=np.float32)  # (g, 15)
    prb_dl_m = _rmean(fb[:, 0]); thp_ul_m = _rmean(fb[:, 3]); thp_dl_m = _rmean(fb[:, 2])
    burst = np.stack([
        np.clip(np.log1p(fb[:, 1]) / (fb[:, 8]  + _UE_EPS), 0, _UE_BURST_CLIP),  # CSV roll mean
        np.clip(np.log1p(fb[:, 0]) / (prb_dl_m  + _UE_EPS), 0, _UE_BURST_CLIP),
        np.clip(fb[:, 3] / (thp_ul_m + 1.0),                0, _UE_BURST_CLIP),
        np.clip(fb[:, 2] / (thp_dl_m + 1.0),                0, _UE_BURST_CLIP),
    ], axis=1).astype(np.float32)
    return np.concatenate([fb, burst], axis=1)  # (g, 19)


def _batch_infer(sess, X, thresh):
    m = len(X)
    if m < UE_WINDOW_SIZE:
        return np.zeros(m, np.float32), np.zeros(m, int)
    view = np.lib.stride_tricks.sliding_window_view(X, UE_WINDOW_SIZE, axis=0)
    wins = np.ascontiguousarray(view.transpose(0, 2, 1)).astype(np.float32)
    sc = sess.run(["mse"], {"input": wins})[0]
    scores = np.zeros(m, np.float32)
    scores[UE_WINDOW_SIZE - 1:] = sc
    return scores, (scores > thresh).astype(int)


def _ml_for_mode(mode):
    """Which ML model a mode uses: 'lstm', 'gru', or None (rule only)."""
    if mode in ("lstm", "lstm_hybrid"):       return "lstm"
    if mode in ("gru", "hybrid", "gru_hybrid"): return "gru"
    return None


def _detect_rows(rows, ml):
    """Per-RNTI detection running rule + the one ML model `ml` needs (None = rule
    only). A row that is the LAST timestep of a complete seq_len window has
    is_window=True (matches aligned[SEQ_LEN-1:] in evaluate_per_ue_v2.py)."""
    rnti_groups = OrderedDict()
    for idx, r in enumerate(rows):
        rnti_groups.setdefault(r.get("rnti", "0"), []).append(idx)

    N = len(rows)
    rule_sev  = np.zeros(N, dtype=int)
    ml_sev    = np.zeros(N, dtype=int)
    ml_scores = np.zeros(N, dtype=np.float32)
    feat_full = np.zeros((N, 19), dtype=np.float32)
    is_window = np.zeros(N, dtype=bool)

    sess, thresh = ((_LSTM_SESS, LSTM_UE_THRESH) if ml == "lstm"
                    else (_GRU_SESS, GRU_UE_THRESH) if ml == "gru" else (None, None))

    for rnti, idxs in rnti_groups.items():
        grp = [rows[i] for i in idxs]
        ff  = _build_feat_group(grp)
        idx_arr = np.array(idxs)
        feat_full[idx_arr] = ff
        if len(idxs) >= UE_WINDOW_SIZE:
            is_window[idx_arr[UE_WINDOW_SIZE - 1:]] = True
        ids = RuleBasedUEIDS()
        for k, r in enumerate(grp):
            rule_sev[idxs[k]] = ids.detect(r)
        if sess is not None:
            sc, sv = _batch_infer(sess, ff, thresh)
            ml_scores[idx_arr] = sc; ml_sev[idx_arr] = sv

    return {"rnti_groups": rnti_groups, "is_window": is_window, "feat_full": feat_full,
            "rule_sev": rule_sev, "ml_sev": ml_sev, "ml_scores": ml_scores}


def _final_by_mode(rule_sev, ml_sev, mode):
    if mode == "rule":                  return rule_sev
    if mode in ("lstm", "gru"):         return ml_sev
    return np.maximum(rule_sev, ml_sev)  # lstm_hybrid / hybrid / gru_hybrid


def _validation_detection(ml):
    """Cached window-level detection on the separate benign validation set."""
    if ml in _VAL_CACHE:
        return _VAL_CACHE[ml]
    path = os.path.join(CSV_DIR, _VAL_CSV)
    d = _detect_rows(load_rows(path), ml) if os.path.exists(path) else None
    _VAL_CACHE[ml] = d
    return d


def run_eval(attack_filter, det_mode="hybrid", csv_path=None):
    """
    Run detection on the testing dataset.
    attack_filter: "1"-"5" or "all"
    det_mode: "hybrid" | "rule" | "lstm"
    Returns dict with metrics, arrays for plotting.

    Methodology mirrors evaluate_per_ue_v2.py: window-level evaluation (label =
    last timestep of each seq_len window, per RNTI), recall/precision/F1 on the
    attack-dataset windows, and FPR on the SEPARATE benign validation set.
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

    # ── Detection (per RNTI, active model only) ───────────────────────────────────
    ml = _ml_for_mode(det_mode)
    d  = _detect_rows(rows, ml)
    rnti_groups = d["rnti_groups"]; is_window = d["is_window"]; feat_full = d["feat_full"]
    rule_sev = d["rule_sev"]; ml_sev = d["ml_sev"]; active_scores = d["ml_scores"]
    final_sev = _final_by_mode(rule_sev, ml_sev, det_mode)

    labels     = np.array([int(r["label"])        for r in rows])
    timestamps = np.array([int(r["timestamp_ms"]) for r in rows])
    prb_dl_arr = [float(r.get("prb_usage_dl_ratio", 0)) for r in rows]
    prb_ul_arr = [float(r.get("prb_usage_ul_ratio", 0)) for r in rows]

    # ── Window-level evaluation mask (drop first seq_len-1 rows per RNTI) ─────────
    wmask    = is_window
    w_labels = labels[wmask]
    w_attack = (w_labels > 0).astype(int)

    # FPR from the SEPARATE benign validation set (STATUS methodology)
    val_d = _validation_detection(ml)
    if val_d is not None:
        vmask   = val_d["is_window"]
        v_final = _final_by_mode(val_d["rule_sev"], val_d["ml_sev"], det_mode)
        n_val   = int(vmask.sum())
        fpr_val = float((v_final[vmask] >= 1).sum()) / n_val if n_val > 0 else 0.0
        val_scores = val_d["ml_scores"][vmask]
    else:
        fpr_val = None; val_scores = None

    def metrics(pred):
        p  = (pred[wmask] >= 1).astype(int)
        tp = int(((p == 1) & (w_attack == 1)).sum())
        fp = int(((p == 1) & (w_attack == 0)).sum())
        tn = int(((p == 0) & (w_attack == 0)).sum())
        fn = int(((p == 0) & (w_attack == 1)).sum())
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        acc  = (tp + tn) / max(1, tp + fp + tn + fn)
        in_fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        return {"accuracy": acc, "recall": rec, "precision": prec, "f1": f1,
                "fpr": fpr_val if fpr_val is not None else in_fpr}

    # ROC-AUC: validation-benign (y=0) vs attack-positive windows (y=1).
    # Reported only for pure-ML modes (matches STATUS: Hybrid/Rule use binary OR).
    roc_auc = None
    fpr_arr = tpr_arr = None
    if det_mode in ("lstm", "gru") and val_scores is not None:
        attack_pos = active_scores[wmask][w_attack == 1]
        if len(attack_pos) > 0 and len(val_scores) > 0:
            y_roc = np.concatenate([np.zeros(len(val_scores)), np.ones(len(attack_pos))])
            s_roc = np.concatenate([val_scores, attack_pos])
            fpr_arr, tpr_arr, _ = roc_curve(y_roc, s_roc)
            roc_auc = float(auc(fpr_arr, tpr_arr))

    # Detection latency — per RNTI, window-level segments (matches
    # compute_detection_latency(): aligned arrays start at SEQ_LEN-1).
    det_latencies = []
    for rnti, idxs in rnti_groups.items():
        widxs = idxs[UE_WINDOW_SIZE - 1:]          # window rows only
        g_lab = [labels[i]     for i in widxs]
        g_ts  = [timestamps[i] for i in widxs]
        g_sev = [final_sev[i]  for i in widxs]
        m = len(widxs); j = 0
        while j < m:
            if g_lab[j] != 0:
                s = j; seg_lbl = g_lab[j]
                while j < m and g_lab[j] == seg_lbl:
                    j += 1
                for k in range(s, j):              # first detection within segment
                    if g_sev[k] >= 1:
                        det_latencies.append(max(0.0, (g_ts[k] - g_ts[s]) / 1000.0))
                        break
            else:
                j += 1

    det_latency_avg = float(np.mean(det_latencies)) if det_latencies else None

    # ── Latency profiling (live, on actual data) ─────────────────────────────────
    # Four distinct latencies (kept separate, not conflated):
    #   inference  = window -> ONNX -> score          (ML only)
    #   decision   = features -> alert decision        (all modes; rule eval / inference+compare)
    #   mitigation = alert -> E2SM-RC -> control       (constant XAPP_CYCLE_MS)
    #   e2e detect = attack start -> alert             (det_latency_avg, dominated by 1 Hz KPM)
    def _p95(times_ms):
        return float(np.percentile(times_ms, 95)) if times_ms else None

    uses_ml   = det_mode in ("lstm", "gru", "lstm_hybrid", "hybrid")
    uses_rule = det_mode in ("rule", "lstm_hybrid", "hybrid")
    ml_sess   = _LSTM_SESS if det_mode in ("lstm", "lstm_hybrid") else _GRU_SESS

    inference_p95 = None
    if uses_ml and len(feat_full) >= UE_WINDOW_SIZE:
        n_win = len(feat_full) - UE_WINDOW_SIZE + 1
        idxs  = range(min(_LAT_SAMPLE, n_win))
        t_inf = []
        for k in idxs:
            w = feat_full[k:k + UE_WINDOW_SIZE][np.newaxis].astype(np.float32)
            t0 = time.perf_counter()
            ml_sess.run(["mse"], {"input": w})
            t_inf.append((time.perf_counter() - t0) * 1000.0)
        inference_p95 = _p95(t_inf)

    rule_p95 = None
    if uses_rule and rows:
        ids2  = RuleBasedUEIDS()
        t_rul = []
        for row in rows[:_LAT_SAMPLE]:
            t0 = time.perf_counter()
            ids2.detect(row)
            t_rul.append((time.perf_counter() - t0) * 1000.0)
        rule_p95 = _p95(t_rul)

    if det_mode == "rule":
        decision_p95 = rule_p95
    elif det_mode in ("lstm", "gru"):
        decision_p95 = inference_p95
    else:  # lstm_hybrid / hybrid — both rule and ML run per decision
        decision_p95 = (rule_p95 or 0.0) + (inference_p95 or 0.0)

    return {
        "csv_path":        csv_path,
        "attack_filter":   attack_filter,
        "det_mode":        det_mode,
        "n_rows":          int(wmask.sum()),     # window count (matches STATUS)
        "n_attack":        int(w_attack.sum()),  # positive windows
        "label_mismatch":  label_mismatch,
        "available_labels": available_labels,
        "label_names":     LABEL_NAMES,
        "is_ue":           True,
        "stage_metrics": {
            "Rule-Based": metrics(rule_sev),
            "LSTM":        metrics(ml_sev),
            "Hybrid":      metrics(final_sev),
        },
        "active_metrics": metrics(final_sev),
        "roc_auc":    roc_auc,
        "fpr_arr":    fpr_arr.tolist() if fpr_arr is not None else None,
        "tpr_arr":    tpr_arr.tolist() if tpr_arr is not None else None,
        "det_latency_avg": det_latency_avg,
        "inference_p95_ms": inference_p95,
        "decision_p95_ms":  decision_p95,
        "mitigation_ms":    XAPP_CYCLE_MS,
        "e2e_detection_s":  det_latency_avg,
        "timestamps":  timestamps.tolist(),
        "prb_dl":      prb_dl_arr,
        "prb_ul":      prb_ul_arr,
        "labels":      labels.tolist(),
        "final_sev":   final_sev.tolist(),
        "lstm_scores": active_scores.tolist(),
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
        html.P("Evaluasi offline Per-UE IDS (Rule-Based Stage 1 + GRU/LSTM-UE v4 Stage 2)",
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
                html.Div(id="mode-btn-row",
                         style={"display": "flex", "gap": "8px", "flexWrap": "wrap"},
                         children=[
                    html.Button("📐 Rule-Based Only", id="mode-rule",       n_clicks=0,
                                style=_btn_style(ACCENT)),
                    html.Button("🧠 LSTM Only",       id="mode-lstm",       n_clicks=0,
                                style=_btn_style(GOLD)),
                    html.Button("🤖 GRU Only",        id="mode-gru",        n_clicks=0,
                                style=_btn_style("#58A6FF")),
                    html.Button("⚡ LSTM Hybrid",     id="mode-lstm-hybrid",n_clicks=0,
                                style=_btn_style(GOLD)),
                    html.Button("★ GRU Hybrid",       id="mode-hybrid",     n_clicks=0,
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

        # Metric cards (quality)
        html.Div(id="metric-cards", style={"display": "grid",
            "gridTemplateColumns": "repeat(6, 1fr)", "gap": "10px",
            "marginBottom": "10px"}),

        # Latency breakdown (4 distinct latencies, not conflated)
        html.Div("Latency Breakdown", style={"color": DIM, "fontSize": "11px",
                 "textTransform": "uppercase", "letterSpacing": "0.5px",
                 "margin": "4px 2px 6px"}),
        html.Div(id="latency-cards", style={"display": "grid",
            "gridTemplateColumns": "repeat(4, 1fr)", "gap": "10px",
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
    dcc.Store(id="active-attack", data="all"),
    dcc.Store(id="active-mode",   data="hybrid"),
    dcc.Store(id="csv-source",    data="live"),
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
    Input("btn-live-src",   "n_clicks"),
    Input("csv-dropdown",   "value"),
    Input("live-refresh",   "n_intervals"),
    State("csv-source",     "data"),
)
def handle_csv_source(live_clicks, dropdown_val, _intervals, current_source):
    options = get_csv_options()
    triggered = ctx.triggered_id

    # Determine new source
    source_changed = True
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
        source_changed = (triggered != "live-refresh")  # interval must NOT re-trigger eval

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

    # On interval tick: refresh info bar only, keep csv-source unchanged so
    # update_dashboard (which has csv-source as Input) does NOT re-run eval.
    out_source = source if source_changed else no_update

    return out_source, btn_style, options, new_dd_val, info_el, interval_disabled


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
    Output("active-mode",      "data"),
    Output("mode-rule",        "style"),
    Output("mode-lstm",        "style"),
    Output("mode-gru",         "style"),
    Output("mode-lstm-hybrid", "style"),
    Output("mode-hybrid",      "style"),
    Input("mode-rule",         "n_clicks"),
    Input("mode-lstm",         "n_clicks"),
    Input("mode-gru",          "n_clicks"),
    Input("mode-lstm-hybrid",  "n_clicks"),
    Input("mode-hybrid",       "n_clicks"),
    prevent_initial_call=True,
)
def store_mode(*_):
    tid = ctx.triggered_id
    mode = "hybrid"
    if   tid == "mode-rule":        mode = "rule"
    elif tid == "mode-lstm":        mode = "lstm"
    elif tid == "mode-gru":         mode = "gru"
    elif tid == "mode-lstm-hybrid": mode = "lstm_hybrid"
    return (
        mode,
        _btn_style(ACCENT, selected=(mode == "rule")),
        _btn_style(GOLD,       selected=(mode == "lstm")),
        _btn_style("#58A6FF", selected=(mode == "gru")),
        _btn_style(GOLD,       selected=(mode == "lstm_hybrid")),
        _btn_style(GREEN,  selected=(mode in ("hybrid", "gru_hybrid"))),
    )


@app.callback(
    Output("status-bar",    "children"),
    Output("metric-cards",  "children"),
    Output("latency-cards", "children"),
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
            html.Span("👆 Klik salah satu tombol Skenario Serangan di atas untuk memulai evaluasi",
                      style={"color": GOLD, "fontWeight": "bold"}),
            html.Span(f"  [{src_name}]", style={"color": DIM, "fontSize": "12px"}),
        ])
        cards = [_metric_card("—", "—") for _ in range(6)]
        lat_cards = [_metric_card("—", "—") for _ in range(4)]
        empty_table = html.P("Belum ada data — pilih skenario serangan untuk memulai.", style={"color": DIM})
        return msg, cards, lat_cards, empty_fig, empty_fig, empty_fig, empty_table

    # Run evaluation
    result = run_eval(attack_filter, det_mode, csv_path=resolved_csv)

    if result is None:
        csv_name = os.path.basename(resolved_csv) if resolved_csv else "?"
        return (html.Span(f"⚠ CSV kosong atau tidak terbaca: {csv_name}", style={"color": GOLD}),
                [], empty_fig, empty_fig, empty_fig, html.P("Tidak ada data.", style={"color": DIM}))

    ml_name   = "GRU" if det_mode in ("gru", "gru_hybrid", "hybrid") else "LSTM"
    ml_thresh = GRU_UE_THRESH if det_mode in ("gru", "gru_hybrid", "hybrid") else LSTM_UE_THRESH
    mode_labels = {
        "rule":        "Rule-Based Only",
        "lstm":        "LSTM Only",
        "gru":         "GRU Only",
        "lstm_hybrid": "⚡ LSTM Hybrid",
        "hybrid":      "★ GRU Hybrid",
    }
    active_m = result["active_metrics"]
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

    # Quality metric cards — show active mode metrics prominently
    def pct(v): return f"{v*100:.1f}"
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
    ]

    # Latency breakdown cards — 4 distinct latencies
    inf_p95 = result.get("inference_p95_ms")
    dec_p95 = result.get("decision_p95_ms")
    e2e_s   = result.get("e2e_detection_s")
    def _ms(v, dec=3): return f"{v:.{dec}f}" if v is not None else "N/A"
    lat_cards = [
        _metric_card("Inference (P95)", _ms(inf_p95), "ms",
                     "#D2A8FF" if inf_p95 is not None else DIM),
        _metric_card("Decision (P95)",  _ms(dec_p95), "ms",
                     GREEN if dec_p95 is not None else DIM),
        _metric_card("Mitigation",      f"{result['mitigation_ms']:.0f}", "ms", GOLD),
        _metric_card("E2E Detection",   f"{e2e_s:.2f}" if e2e_s is not None else "N/A",
                     "s", GREEN if (e2e_s is not None and e2e_s < 5) else GOLD),
    ]

    # PRB time-series
    ts = result["timestamps"]
    labels = result["labels"]
    t_rel = [(t - ts[0]) / 1000.0 for t in ts]
    det_flags = result["final_sev"]

    fig_prb = go.Figure()

    # Shade attack regions — build shapes/annotations as lists and apply ONCE.
    # (fig.add_vrect in a loop is O(n^2): each call re-validates all shapes,
    #  taking ~65s for ~500 regions on large datasets → perpetual spinner.)
    attack_colors = {"1": "rgba(255,107,53,0.12)", "2": "rgba(255,71,87,0.12)",
                     "3": "rgba(255,165,2,0.12)",  "4": "rgba(255,99,72,0.12)"}
    shapes = []
    annotations = []
    prev_lbl = 0
    seg_start = None
    for i, lbl in enumerate(labels):
        if lbl != 0 and prev_lbl == 0:
            seg_start = t_rel[i]
        if lbl == 0 and prev_lbl != 0 and seg_start is not None:
            color = attack_colors.get(str(prev_lbl), "rgba(255,255,255,0.08)")
            shapes.append(dict(type="rect", xref="x", yref="paper",
                               x0=seg_start, x1=t_rel[i], y0=0, y1=1,
                               fillcolor=color, layer="below", line_width=0))
            annotations.append(dict(x=(seg_start + t_rel[i]) / 2, y=1, yref="paper",
                                    text=_lnames.get(prev_lbl, ""), showarrow=False,
                                    font=dict(color=DIM, size=10)))
            seg_start = None
        prev_lbl = lbl
    if seg_start is not None:
        color = attack_colors.get(str(prev_lbl), "rgba(255,255,255,0.08)")
        shapes.append(dict(type="rect", xref="x", yref="paper",
                           x0=seg_start, x1=t_rel[-1], y0=0, y1=1,
                           fillcolor=color, layer="below", line_width=0))

    # 80% threshold line as a shape (avoid add_hline re-validation too)
    if t_rel:
        shapes.append(dict(type="line", xref="x", yref="y",
                           x0=t_rel[0], x1=t_rel[-1], y0=80, y1=80,
                           line=dict(color="#555", width=1, dash="dash")))
        annotations.append(dict(x=t_rel[-1], y=80, yref="y", text="80% threshold",
                                showarrow=False, font=dict(color=DIM, size=10),
                                xanchor="right", yanchor="bottom"))

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

    fig_prb.update_layout(title=f"PRB Utilization — {attack_name}",
                           xaxis_title="Waktu (detik)", yaxis_title="PRB (%)",
                           yaxis_range=[0, 105], shapes=shapes, annotations=annotations,
                           **_layout_base())

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

    return status, cards, lat_cards, fig_prb, fig_roc, fig_lstm, table


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing Evaluation Dashboard — http://0.0.0.0:8050")
    app.run(host="0.0.0.0", port=8050, debug=False)
