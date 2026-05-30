"""
Evaluasi deteksi xApp security terhadap dataset_testing_v2.csv.
Mensimulasikan rule-based IDS (sec_ids.c) + LSTM ONNX secara sequential.

Usage:
    python3 evaluate_detection.py [--csv path/to/dataset.csv]
"""

import argparse
import json
import os
import datetime
import numpy as np
import onnxruntime as ort
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, confusion_matrix,
)

# ── Config ──────────────────────────────────────────────────────────────────
ONNX_MODEL   = "/home/telmat/sec-xapp/security_model.onnx"
DEFAULT_CSV  = "/home/telmat/sec-xapp/csv/dataset_testing_v3.csv"
LSTM_THRESH  = 0.5        # ONNX output = 0.5*(mse/raw_threshold); >0.5 = anomali
WINDOW_SIZE  = 10         # timesteps per LSTM window

LABEL_NAMES = {
    0: "Normal",
    1: "UL Flood",
    2: "DL Flood",
    3: "Burst ON/OFF",
    4: "RRC Storm",
    5: "RF Jammer",
}

# ── Rule-based IDS simulator (port dari sec_ids.c) ───────────────────────────

class RuleBasedIDS:
    EPS = 1e-6

    def __init__(self):
        self.reset()

    def reset(self):
        # Stage 1 counters
        self.ul_sat_cnt      = 0
        self.dl_sat_cnt      = 0
        self.rf_susp_cnt     = 0
        self.burst_consec    = 0
        self.empty_storm_cnt = 0

        # UL variance rolling (10 windows)
        self.ul_var_buf   = [0.0] * 10
        self.ul_var_head  = 0
        self.ul_var_count = 0

        # DL variance rolling (10 windows) — for R2b fast-path Stage 2
        self.dl_var_buf   = [0.0] * 10
        self.dl_var_head  = 0
        self.dl_var_count = 0

        # Stage 2 saturation persistence
        self.s2_sat_start_ms = 0
        self.s2_sat_dur_ms   = 0
        self.s2_rec_start_ms = 0

        # Stage 2 RF suspicion
        self.s2_rf_cnt = 0

        # Stage 2 RRC storm
        self.s2_rrc_cnt = 0

        # Rule 3c: sustained RACH counter
        self.rach_sustained_cnt = 0

        # Rule 3d: CQI rolling buffer untuk oscillation detection
        self.cqi_buf   = [0.0] * 10
        self.cqi_head  = 0
        self.cqi_count = 0

        # Rule 3e: RRC storm persistence window
        self.rrc_recent_triggers = []   # timestamps of recent 3b/3c triggers
        self.rrc_storm_until     = 0    # ms — storm window active until

        # Rule 7: previous PRB total
        self.prev_prb_total = 0.0

        # Rule 8: Periodic burst — OFF→ON transition counter
        self.burst_was_on        = False
        self.burst_on_times      = []   # ms timestamps of each OFF→ON transition
        self.burst_active_until  = 0    # ms — alert stays live this long after 2+ cycles

    def detect(self, row, now_ms):
        """
        row: dict with keys matching feature_schema.py
        Returns severity: 0=normal, 1=Stage1 WARNING, 2=Stage2 CRITICAL
        """
        EPS = self.EPS
        severity   = 0
        stage1_hit = False
        alert_type = "none"

        prb_dl = row["prb_usage_dl_ratio"]  # 0–1
        prb_ul = row["prb_usage_ul_ratio"]  # 0–1
        # Convert to % for rules (sec_ids uses % internally for threshold >80)
        prb_dl_pct = prb_dl * 100.0
        prb_ul_pct = prb_ul * 100.0

        empty_ind = row.get("empty_ind_rate", 0.0)
        air_delay = row["air_delay_ul"]

        # ── UL variance rolling ──────────────────────────────────────────────
        self.ul_var_buf[self.ul_var_head] = prb_ul_pct
        self.ul_var_head = (self.ul_var_head + 1) % 10
        if self.ul_var_count < 10:
            self.ul_var_count += 1
        s_ul = sum(self.ul_var_buf[k] for k in range(self.ul_var_count))
        ul_mean = s_ul / self.ul_var_count
        sq_ul = sum((self.ul_var_buf[k] - ul_mean) ** 2 for k in range(self.ul_var_count))
        prb_ul_variance = sq_ul / self.ul_var_count

        # ── DL variance rolling ──────────────────────────────────────────────
        self.dl_var_buf[self.dl_var_head] = prb_dl_pct
        self.dl_var_head = (self.dl_var_head + 1) % 10
        if self.dl_var_count < 10:
            self.dl_var_count += 1
        s_dl = sum(self.dl_var_buf[k] for k in range(self.dl_var_count))
        dl_mean = s_dl / self.dl_var_count
        sq_dl = sum((self.dl_var_buf[k] - dl_mean) ** 2 for k in range(self.dl_var_count))
        prb_dl_variance = sq_dl / self.dl_var_count

        # ── Rule 1: UL Saturation (DL guard: flood selalu DL≈0%) ────────────
        if prb_ul_pct > 80.0 and prb_dl_pct < 15.0:
            self.ul_sat_cnt += 1
        else:
            self.ul_sat_cnt = 0
        if self.ul_sat_cnt >= 5 and not stage1_hit:
            stage1_hit = True
            alert_type = "ul_saturation"
            severity = max(severity, 1)

        # ── Rule 2: DL Saturation ────────────────────────────────────────────
        if prb_dl_pct > 80.0 and prb_ul_pct < 30.0:
            self.dl_sat_cnt += 1
        else:
            self.dl_sat_cnt = 0
        if self.dl_sat_cnt >= 3 and not stage1_hit:
            stage1_hit = True
            alert_type = "dl_saturation"
            severity = max(severity, 1)

        # ── Rule 3b: RRC Storm via empty indications ─────────────────────────
        cond_3b = (empty_ind >= 2.0 and prb_ul_pct < 30.0 and prb_dl_pct < 30.0)
        if cond_3b:
            self.empty_storm_cnt += 1
        else:
            self.empty_storm_cnt = 0
        if self.empty_storm_cnt >= 3:
            if not stage1_hit:
                stage1_hit = True
                alert_type = "rrc_storm"
            severity = max(severity, 1)

        # ── Rule 3c: RRC Storm via sustained RACH activity (low-level reconnect)
        # Airplane toggle / RRC flood menghasilkan RACH=1 berulang tanpa saturasi PRB.
        # Threshold RACH>=1 (bukan spike) + low PRB membedakan dari UL/DL Flood.
        cond_3c = (row["rach_preamble"] >= 1.0 and prb_ul_pct < 30.0 and prb_dl_pct < 30.0)
        if cond_3c:
            self.rach_sustained_cnt += 1
        else:
            self.rach_sustained_cnt = 0
        if self.rach_sustained_cnt >= 2:
            if not stage1_hit:
                stage1_hit = True
                alert_type = "rrc_storm"
            severity = max(severity, 1)

        # ── Rule 3d: CQI Oscillation (UE repeatedly disconnect/reconnect) ───────
        # Selama RRC Storm, CQI bolak-balik antara 0 (disconnected) dan tinggi.
        # Rolling std CQI > 3.0 dengan PRB rendah = sinyal kuat storm.
        cqi_val = row["cqi"]
        self.cqi_buf[self.cqi_head] = cqi_val
        self.cqi_head = (self.cqi_head + 1) % 10
        if self.cqi_count < 10:
            self.cqi_count += 1
        cqi_mean = sum(self.cqi_buf[k] for k in range(self.cqi_count)) / self.cqi_count
        cqi_std = (sum((self.cqi_buf[k] - cqi_mean)**2 for k in range(self.cqi_count)) / self.cqi_count) ** 0.5
        prb_total = (row["prb_usage_dl_ratio"] + row["prb_usage_ul_ratio"])
        cond_3d = (cqi_std > 3.0 and prb_total < 0.05)
        if cond_3d and not stage1_hit:
            stage1_hit = True
            alert_type = "rrc_storm"
            severity = max(severity, 1)

        # ── Rule 3e: RRC Storm Persistence Window ──────────────────────────────
        # Setelah >= 2 trigger (3b/3c/3d) dalam 3s, pertahankan alert 5s ke depan.
        # Menangkap periode "diam" antar burst RACH/empty_ind.
        if cond_3b or cond_3c or cond_3d:
            self.rrc_recent_triggers = [t for t in self.rrc_recent_triggers if now_ms - t <= 3000]
            self.rrc_recent_triggers.append(now_ms)
            if len(self.rrc_recent_triggers) >= 2:
                self.rrc_storm_until = max(self.rrc_storm_until, now_ms + 5000)
        if self.rrc_storm_until > 0 and now_ms <= self.rrc_storm_until:
            if not stage1_hit:
                stage1_hit = True
                alert_type = "rrc_storm"
            severity = max(severity, 1)

        # Stage 2 RRC: increment jika salah satu kondisi 3b/3c/3d/3e aktif
        if cond_3b or cond_3c or cond_3d or (self.rrc_storm_until > 0 and now_ms <= self.rrc_storm_until):
            self.s2_rrc_cnt += 1
        else:
            self.s2_rrc_cnt = 0
        if self.s2_rrc_cnt >= 4:
            severity = max(severity, 2)

        # ── Rule 7: Radio-layer degradation (PRB collapse) ───────────────────
        prb_total_now = prb_dl + prb_ul  # ratio 0–2
        prev_total    = self.prev_prb_total
        rf_collapse = (prev_total > 0.4 and prb_total_now < 0.05 and air_delay < 1.0)
        if rf_collapse:
            self.rf_susp_cnt  += 1
            self.s2_rf_cnt    += 1
        else:
            self.rf_susp_cnt  = 0
            self.s2_rf_cnt    = 0
        self.prev_prb_total = prb_total_now
        if self.rf_susp_cnt >= 2 and not stage1_hit:
            stage1_hit = True
            alert_type = "radio_degradation_suspicion"
            severity = max(severity, 1)
        if self.s2_rf_cnt >= 5:
            severity = max(severity, 2)

        # ── Rule 8: Periodic Burst Anomaly (OFF→ON transition counter) ──────
        # Detect alternating high-UL / low-UL cycles characteristic of burst attacks.
        # burst_index approach suppressed during sustained ON phase; transition
        # counting directly measures cycle periodicity and persists the alert
        # across the OFF phase so all burst rows are covered.
        burst_is_on = (prb_ul_pct > 70.0 and prb_dl_pct < 20.0)
        if burst_is_on and not self.burst_was_on:
            self.burst_on_times.append(now_ms)
        self.burst_was_on = burst_is_on

        # Evict transitions older than 90s
        self.burst_on_times = [t for t in self.burst_on_times if now_ms - t <= 90000]
        burst_on_count = len(self.burst_on_times)

        # Extend active window 15s past the most recent ON transition once 2+ seen.
        # 15s > max OFF phase (5.8s) so every OFF gap is bridged; shorter than 30s
        # to limit post-attack lingering in normal traffic.
        if burst_on_count >= 2:
            self.burst_active_until = max(self.burst_active_until,
                                          max(self.burst_on_times) + 15000)

        burst_alert = (self.burst_active_until > 0 and now_ms <= self.burst_active_until)

        if burst_alert and not stage1_hit:
            stage1_hit = True
            alert_type = "periodic_burst_anomaly"
            severity = max(severity, 1)
        if burst_alert and burst_on_count >= 4:
            severity = max(severity, 2)

        # ── Stage 2: Saturation Persistence ─────────────────────────────────
        sat_active = alert_type in ("ul_saturation", "dl_saturation")
        if sat_active:
            if self.s2_sat_start_ms == 0:
                self.s2_sat_start_ms = now_ms
            self.s2_sat_dur_ms   = now_ms - self.s2_sat_start_ms
            self.s2_rec_start_ms = 0

            if self.s2_sat_dur_ms >= 30000:
                severity = max(severity, 2)

            # Fast-path R1b: UL flatline variance
            if (alert_type == "ul_saturation"
                    and self.s2_sat_dur_ms >= 3000
                    and prb_ul_variance < 0.0001
                    and prb_ul_pct > 80.0):
                severity = max(severity, 2)

            # Fast-path R2b: DL flatline variance — mirrors R1b for DL Flood
            # DL Flood sends constant traffic → DL PRB flatlines near 100%
            if (alert_type == "dl_saturation"
                    and self.s2_sat_dur_ms >= 3000
                    and prb_dl_variance < 0.0001
                    and prb_dl_pct > 80.0):
                severity = max(severity, 2)
        else:
            if self.s2_sat_start_ms != 0:
                if self.s2_rec_start_ms == 0:
                    self.s2_rec_start_ms = now_ms
                if now_ms - self.s2_rec_start_ms >= 5000:
                    self.s2_sat_start_ms = 0
                    self.s2_sat_dur_ms   = 0
                    self.s2_rec_start_ms = 0

        return severity, alert_type


# ── LSTM simulator ────────────────────────────────────────────────────────────

class LSTMDetector:
    # Must match FEATURE_NAMES in feature_schema.py exactly
    FEATURES = [
        "prb_usage_dl_ratio", "prb_usage_ul_ratio",
        "cqi", "rach_preamble", "air_delay_ul",
        "prb_direction", "prb_total",
        "prb_dl_delta", "prb_ul_delta", "prb_burst_index",
        "empty_ind_rate",
        "prb_dl_roll_mean", "prb_dl_roll_std",
        "prb_ul_roll_std", "prb_ul_roll_max",
        "prb_ul_roll_max_100",
        "cqi_roll_std", "rach_roll_mean",
        # previously-unused CSV features
        "prb_total_variance", "prb_dl_ul_asym",
        "prb_ul_near_zero_rate", "prb_peak_drop", "rach_cqi_joint",
        # computed rolling features (maintained below)
        "rach_roll_max_30", "empty_ind_roll_sum_30",
        # CV features
        "prb_dl_roll_cv", "prb_ul_roll_cv",
    ]
    _ROLL30 = 30
    _ROLL10 = 10

    def __init__(self, model_path, threshold=LSTM_THRESH, seq_len=WINDOW_SIZE, num_features=None):
        self.sess      = ort.InferenceSession(model_path)
        self.threshold = threshold
        self.seq_len   = seq_len
        self.FEATURES  = list(self.FEATURES)
        if num_features is not None and num_features < len(self.FEATURES):
            self.FEATURES = self.FEATURES[:num_features]
        self.window    = np.zeros((seq_len, len(self.FEATURES)), dtype=np.float32)
        self.filled    = 0
        self.anomaly_cnt      = 0
        self.stage2_start_ms  = 0
        self.stage2_dur_ms    = 0
        self.severity         = 0
        self.last_score       = 0.0
        self._rach_buf    = np.zeros(self._ROLL30, dtype=np.float32)
        self._empty_buf   = np.zeros(self._ROLL30, dtype=np.float32)
        self._ul_buf      = np.zeros(self._ROLL10, dtype=np.float32)
        self._roll_filled = 0
        self._ul_filled   = 0

    def update(self, row, now_ms):
        # update rolling buffers
        self._rach_buf  = np.roll(self._rach_buf,  -1); self._rach_buf[-1]  = row["rach_preamble"]
        self._empty_buf = np.roll(self._empty_buf, -1); self._empty_buf[-1] = row["empty_ind_rate"]
        self._ul_buf    = np.roll(self._ul_buf,    -1); self._ul_buf[-1]    = row["prb_usage_ul_ratio"]
        if self._roll_filled < self._ROLL30: self._roll_filled += 1
        if self._ul_filled   < self._ROLL10: self._ul_filled   += 1
        n30 = self._roll_filled; n10 = self._ul_filled

        ul_mean = float(self._ul_buf[-n10:].mean()) if n10 > 0 else 0.0

        augmented = dict(row)
        augmented["rach_roll_max_30"]      = float(self._rach_buf[-n30:].max())
        augmented["empty_ind_roll_sum_30"] = float(self._empty_buf[-n30:].sum())
        augmented["prb_dl_roll_cv"]        = row["prb_dl_roll_std"] / (row["prb_dl_roll_mean"] + 1e-6)
        augmented["prb_ul_roll_cv"]        = row["prb_ul_roll_std"] / (ul_mean + 1e-6)

        feat = np.array([augmented[f] for f in self.FEATURES], dtype=np.float32)
        self.window = np.roll(self.window, -1, axis=0)
        self.window[-1] = feat
        if self.filled < self.seq_len:
            self.filled += 1
            return 0, 0.0

        inp = self.window[np.newaxis].astype(np.float32)
        score = float(self.sess.run(["score"], {"input": inp})[0][0])
        self.last_score = score

        # Context-aware threshold: jika cell sedang "quiet" (PRB total rendah,
        # bukan karena burst), apapun yang anomalous lebih patut dicurigai.
        # Ini berbasis cell state — bukan identitas attack — sehingga konsisten
        # dengan tujuan unknown attack detection.
        prb_total = row["prb_usage_dl_ratio"] + row["prb_usage_ul_ratio"]
        quiet_cell = (prb_total < 0.05 and row["prb_burst_index"] < 1.0)
        effective_threshold = self.threshold * 0.65 if quiet_cell else self.threshold

        if score > effective_threshold:
            self.anomaly_cnt += 1
            if self.stage2_start_ms == 0:
                self.stage2_start_ms = now_ms
            self.stage2_dur_ms = now_ms - self.stage2_start_ms
        else:
            self.anomaly_cnt     = 0
            self.stage2_start_ms = 0
            self.stage2_dur_ms   = 0

        sev = 0
        if self.anomaly_cnt >= 3:
            sev = 1
        if self.stage2_dur_ms >= 30000:
            sev = 2
        self.severity = sev
        return sev, score


def _vote_2of3(buf):
    """Return True if at least 2 of the 3 booleans in buf are True."""
    return sum(buf) >= 2


class DualLSTMDetector:
    """
    Combines two LSTMDetector models with independent 2-of-3 window voting.

    Model A (v16, thresh=0.21): tuned for UL/DL Flood.
    Model B (v22, thresh=0.50): tuned for RRC Storm.

    Alert fires if either model's vote buffer has ≥2 of last 3 windows above
    its threshold. Uses raw ONNX scores — ignores LSTMDetector's internal
    severity/anomaly_cnt.
    """

    def __init__(self, model_a, thresh_a, model_b, thresh_b,
                 seq_len=WINDOW_SIZE, num_features=None):
        self.det_a   = LSTMDetector(model_a, threshold=thresh_a,
                                    seq_len=seq_len, num_features=num_features)
        self.det_b   = LSTMDetector(model_b, threshold=thresh_b,
                                    seq_len=seq_len, num_features=num_features)
        self.thresh_a = thresh_a
        self.thresh_b = thresh_b
        self._buf_a   = [False, False, False]
        self._buf_b   = [False, False, False]

    def update(self, row, now_ms):
        """
        Returns (severity, combined_score, score_a, score_b).
        severity = 1 if either model votes anomaly, else 0.
        combined_score = max(score_a, score_b).
        """
        _, score_a = self.det_a.update(row, now_ms)
        _, score_b = self.det_b.update(row, now_ms)

        self._buf_a = self._buf_a[1:] + [score_a > self.thresh_a]
        self._buf_b = self._buf_b[1:] + [score_b > self.thresh_b]

        vote = _vote_2of3(self._buf_a) or _vote_2of3(self._buf_b)
        sev  = 1 if vote else 0
        return sev, max(score_a, score_b), score_a, score_b


# ── Main evaluation ───────────────────────────────────────────────────────────

ATTACK_KEY = {1: "ul_flood", 2: "dl_flood", 3: "burst", 4: "rrc_storm", 5: "rf_jammer"}


def _build_eval_json(labels, rule_sev, lstm_sev, final_sev, y_true, csv_path):
    """Return dict matching eval_results.json schema."""

    def stage_metrics(pred_sev):
        y_pred = (pred_sev >= 1).astype(int)
        normal_mask = labels == 0
        n_normal = normal_mask.sum()
        fp = (pred_sev[normal_mask] >= 1).sum()
        return {
            "accuracy":   float(accuracy_score(y_true, y_pred)),
            "recall":     float(recall_score(y_true, y_pred, zero_division=0)),
            "precision":  float(precision_score(y_true, y_pred, zero_division=0)),
            "f1":         float(f1_score(y_true, y_pred, zero_division=0)),
            "fpr":        float(fp / n_normal) if n_normal > 0 else 0.0,
        }

    def attack_metrics(lbl, pred_sev):
        # Include normal rows so FP from the detector on benign traffic is counted
        mask = (labels == lbl) | (labels == 0)
        if (labels[mask] == lbl).sum() == 0:
            return None
        y_t = (labels[mask] == lbl).astype(int)
        y_p = (pred_sev[mask] >= 1).astype(int)
        return {
            "recall":    float(recall_score(y_t, y_p, zero_division=0)),
            "precision": float(precision_score(y_t, y_p, zero_division=0)),
            "f1":        float(f1_score(y_t, y_p, zero_division=0)),
            "count":     int((labels[mask] == lbl).sum()),
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


def load_csv(path):
    import csv
    rows = []
    STR_COLS = {"datetime", "alert_type"}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({k: float(v) if k not in STR_COLS else v
                         for k, v in r.items()})
    return rows


def run_evaluation(csv_path, onnx_path, output_path=None, seq_len=WINDOW_SIZE, num_features=None):
    print(f"Loading dataset: {csv_path}")
    rows = load_csv(csv_path)
    print(f"  {len(rows)} rows, labels: { {int(l): sum(1 for r in rows if int(r['label'])==l) for l in sorted(set(int(r['label']) for r in rows))} }")

    print(f"\nLoading ONNX model: {onnx_path}  (seq_len={seq_len})")
    ids  = RuleBasedIDS()
    lstm = LSTMDetector(onnx_path, seq_len=seq_len, num_features=num_features)

    labels      = []
    rule_sev    = []
    lstm_sev    = []
    final_sev   = []
    lstm_scores = []

    for r in rows:
        now_ms  = int(r["timestamp_ms"])
        label   = int(r["label"])
        rsev, _ = ids.detect(r, now_ms)
        lsev, score = lstm.update(r, now_ms)
        fsev = max(rsev, lsev)

        labels.append(label)
        rule_sev.append(rsev)
        lstm_sev.append(lsev)
        final_sev.append(fsev)
        lstm_scores.append(score)

    labels    = np.array(labels)
    rule_sev  = np.array(rule_sev)
    lstm_sev  = np.array(lstm_sev)
    final_sev = np.array(final_sev)
    lstm_scores = np.array(lstm_scores)

    y_true = (labels != 0).astype(int)  # binary: 0=normal, 1=attack

    # ── Per-system binary metrics (Stage1+Stage2 = detected) ─────────────────
    def binary_metrics(name, pred_sev):
        y_pred = (pred_sev >= 1).astype(int)
        y_pred_s2 = (pred_sev >= 2).astype(int)
        acc  = accuracy_score(y_true, y_pred)
        prec = precision_score(y_true, y_pred, zero_division=0)
        rec  = recall_score(y_true, y_pred, zero_division=0)
        f1   = f1_score(y_true, y_pred, zero_division=0)
        try:
            auc = roc_auc_score(y_true, pred_sev)
        except Exception:
            auc = float("nan")
        print(f"\n{'='*55}")
        print(f"  {name}")
        print(f"{'='*55}")
        print(f"  Threshold  : Stage1 (any warning)")
        print(f"  Accuracy   : {acc:.4f}")
        print(f"  Precision  : {prec:.4f}")
        print(f"  Recall     : {rec:.4f}")
        print(f"  F1 Score   : {f1:.4f}")
        print(f"  ROC AUC    : {auc:.4f}")
        cm = confusion_matrix(y_true, y_pred)
        print(f"  Confusion Matrix (Stage1+):")
        print(f"    TN={cm[0,0]:5d}  FP={cm[0,1]:5d}")
        print(f"    FN={cm[1,0]:5d}  TP={cm[1,1]:5d}")
        cm2 = confusion_matrix(y_true, y_pred_s2)
        print(f"  Confusion Matrix (Stage2 only):")
        print(f"    TN={cm2[0,0]:5d}  FP={cm2[0,1]:5d}")
        print(f"    FN={cm2[1,0]:5d}  TP={cm2[1,1]:5d}")

    binary_metrics("Rule-Based IDS", rule_sev)
    binary_metrics("LSTM Autoencoder", lstm_sev)
    binary_metrics("HYBRID (Rule + LSTM, max)", final_sev)

    # ── LSTM ROC AUC dengan raw score ────────────────────────────────────────
    try:
        auc_raw = roc_auc_score(y_true, lstm_scores)
        print(f"\n  LSTM ROC AUC (raw score, no threshold): {auc_raw:.4f}")
    except Exception:
        pass

    # ── Per-label detection rate ──────────────────────────────────────────────
    print(f"\n{'='*55}")
    print("  Detection Rate Per Label (Hybrid, Stage1+)")
    print(f"{'='*55}")
    print(f"  {'Label':<18} {'Total':>6} {'S1+Det':>7} {'S2Det':>7} {'S1+Rate':>8} {'S2Rate':>8}")
    print(f"  {'-'*58}")
    for lbl in sorted(LABEL_NAMES):
        mask   = labels == lbl
        total  = mask.sum()
        if total == 0:
            continue
        s1_det = (final_sev[mask] >= 1).sum()
        s2_det = (final_sev[mask] >= 2).sum()
        print(f"  {LABEL_NAMES[lbl]:<18} {total:>6} {s1_det:>7} {s2_det:>7} "
              f"{s1_det/total:>7.1%} {s2_det/total:>7.1%}")

    # ── Per-label breakdown: rule vs lstm ─────────────────────────────────────
    print(f"\n{'='*55}")
    print("  Detection Breakdown: Rule vs LSTM (Stage1+)")
    print(f"{'='*55}")
    print(f"  {'Label':<18} {'Rule':>7} {'LSTM':>7} {'Both':>7} {'Either':>7}")
    print(f"  {'-'*54}")
    for lbl in sorted(LABEL_NAMES):
        mask  = labels == lbl
        total = mask.sum()
        if total == 0:
            continue
        r  = (rule_sev[mask] >= 1).sum()
        l  = (lstm_sev[mask] >= 1).sum()
        b  = ((rule_sev[mask] >= 1) & (lstm_sev[mask] >= 1)).sum()
        e  = ((rule_sev[mask] >= 1) | (lstm_sev[mask] >= 1)).sum()
        print(f"  {LABEL_NAMES[lbl]:<18} {r/total:>6.1%} {l/total:>7.1%} "
              f"{b/total:>7.1%} {e/total:>7.1%}")

    # ── False Positive rate (Normal traffic mis-detected) ────────────────────
    normal_mask = labels == 0
    n_normal    = normal_mask.sum()
    fp_s1 = (final_sev[normal_mask] >= 1).sum()
    fp_s2 = (final_sev[normal_mask] >= 2).sum()
    fp_rule  = (rule_sev[normal_mask] >= 1).sum()
    fp_lstm  = (lstm_sev[normal_mask] >= 1).sum()
    print(f"\n{'='*55}")
    print("  False Positive Rate (Normal traffic mis-detected)")
    print(f"{'='*55}")
    print(f"  Normal rows      : {n_normal}")
    print(f"  Rule FP (Stage1+): {fp_rule:5d} ({fp_rule/n_normal:.2%})")
    print(f"  LSTM FP (Stage1+): {fp_lstm:5d} ({fp_lstm/n_normal:.2%})")
    print(f"  Hybrid FP (S1+)  : {fp_s1:5d}  ({fp_s1/n_normal:.2%})")
    print(f"  Hybrid FP (S2)   : {fp_s2:5d}  ({fp_s2/n_normal:.2%})")

    # ── LSTM score statistics per label ──────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"  LSTM Score Statistics Per Label  (threshold={LSTM_THRESH:.6f})")
    print(f"{'='*55}")
    print(f"  {'Label':<18} {'Mean':>9} {'P50':>9} {'P95':>9} {'P99':>9} {'>Thresh':>8}")
    print(f"  {'-'*66}")
    for lbl in sorted(LABEL_NAMES):
        mask  = labels == lbl
        if mask.sum() == 0:
            continue
        sc    = lstm_scores[mask]
        above = (sc > LSTM_THRESH).sum()
        print(f"  {LABEL_NAMES[lbl]:<18} "
              f"{sc.mean():>9.6f} {np.percentile(sc,50):>9.6f} "
              f"{np.percentile(sc,95):>9.6f} {np.percentile(sc,99):>9.6f} "
              f"{above/len(sc):>7.1%}")

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        result = _build_eval_json(labels, rule_sev, lstm_sev, final_sev, y_true, csv_path)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\n[OK] Hasil evaluasi ditulis ke: {output_path}")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv",     default=DEFAULT_CSV,  help="Path ke dataset CSV")
    parser.add_argument("--model",   default=ONNX_MODEL,   help="Path ke ONNX model")
    parser.add_argument("--seq-len", default=WINDOW_SIZE, type=int, help="LSTM window size (default: 10)")
    parser.add_argument("--output",       default=None, help="Tulis hasil evaluasi ke JSON (opsional)")
    parser.add_argument("--num-features", default=None, type=int, help="Gunakan N fitur pertama (default: semua 27)")
    args = parser.parse_args()
    run_evaluation(args.csv, args.model, output_path=args.output, seq_len=args.seq_len, num_features=args.num_features)
