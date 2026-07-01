# src/detection/feature_schema_ue.py
# 19 per-UE features: 15 base (from CSV) + 4 derived burst indices.
# Base names match exactly the CSV columns written by csv_per_ue_write() in xapp_sec_moni.c.
# Burst index features are computed on-the-fly via add_burst_features_rows().

import numpy as np

FEATURE_NAMES = [
    "prb_usage_dl_ratio",   # RRU.PrbUsedDl / 100 (from KPM or MAC fallback), clipped [0,1]
    "prb_usage_ul_ratio",   # RRU.PrbUsedUl / 100 (from KPM or MAC fallback), clipped [0,1]
    "thp_dl_kbps",          # DRB.UEThpDl (kbps)
    "thp_ul_kbps",          # DRB.UEThpUl (kbps)
    "prb_direction",        # (prb_ul - prb_dl) / (prb_total + eps), bounded [-1, +1]
    "prb_total",            # prb_dl + prb_ul, clipped [0, 1]
    "prb_ul_delta",         # prb_ul[t] - prb_ul[t-1]
    "ul_efficiency",        # thp_ul / prb_ul, clipped [0, 50000]
    "prb_ul_roll_mean",     # rolling mean prb_ul_ratio over 10 timesteps
    "prb_ul_roll_std",      # rolling std  prb_ul_ratio over 10 timesteps
    "ul_persistence",       # fraction of last 10 ts with prb_ul > 0, in [0, 1]
    "thp_total_kbps",       # thp_dl + thp_ul (kbps)
    "thp_ul_delta",         # thp_ul[t] - thp_ul[t-1] (kbps)
    "thp_dl_delta",         # thp_dl[t] - thp_dl[t-1] (kbps)
    "traffic_direction",    # (thp_ul - thp_dl) / (thp_total + eps), bounded [-1, +1]
    # --- Derived burst index features (computed by add_burst_features_rows) ---
    "prb_ul_burst_index",   # log(1+prb_ul) / (prb_ul_roll_mean + eps), clipped [0, 50]
    "prb_dl_burst_index",   # log(1+prb_dl) / (prb_dl_roll_mean + eps), clipped [0, 50]
    "thp_ul_burst_index",   # log(1+thp_ul_kbps) / (thp_ul_roll_mean + eps), clipped [0, 50]
    "thp_dl_burst_index",   # log(1+thp_dl_kbps) / (thp_dl_roll_mean + eps), clipped [0, 50]
]

NUM_FEATURES = len(FEATURE_NAMES)   # 19

# Base features that exist as columns in CSV files (written by C xApp).
# The 4 burst index features are derived on-the-fly and not in any CSV.
CSV_FEATURE_NAMES = FEATURE_NAMES[:15]

# Scheme A weights: log(max attack/benign reconstruction error ratio) per feature.
# Derived empirically from GRU-UE v3 per-feature error analysis.
# High weight = feature strongly discriminates attack from benign.
FEATURE_WEIGHTS: dict = {
    "prb_usage_dl_ratio":  1.6451,
    "prb_usage_ul_ratio":  4.7411,
    "thp_dl_kbps":         3.5213,
    "thp_ul_kbps":         4.6430,
    "prb_direction":       1.0144,
    "prb_total":           3.5385,
    "prb_ul_delta":        4.0295,
    "ul_efficiency":       1.9381,
    "prb_ul_roll_mean":    3.9845,
    "prb_ul_roll_std":     3.7217,
    "ul_persistence":      1.6172,
    "thp_total_kbps":      3.3500,
    "thp_ul_delta":        3.7416,
    "thp_dl_delta":        1.9035,
    "traffic_direction":   0.7612,
    "prb_ul_burst_index":  0.4261,
    "prb_dl_burst_index":  0.4056,
    "thp_ul_burst_index":  1.2140,
    "thp_dl_burst_index":  1.1474,
}

_BURST_WINDOW = 10
_BURST_CLIP = 50.0
_EPS = 1e-6


def add_burst_features_rows(rows: list) -> None:
    """Compute 4 burst index features in-place on a per-RNTI row list.

    Must be called per RNTI (after split_by_rnti) so rolling means stay within
    each UE's own timeline. Modifies rows in-place; returns None.
    """
    n = len(rows)
    if n == 0:
        return

    prb_ul   = np.array([float(r.get("prb_usage_ul_ratio", 0.0)) for r in rows])
    prb_dl   = np.array([float(r.get("prb_usage_dl_ratio", 0.0)) for r in rows])
    thp_ul   = np.array([float(r.get("thp_ul_kbps",        0.0)) for r in rows])
    thp_dl   = np.array([float(r.get("thp_dl_kbps",        0.0)) for r in rows])
    # Use existing prb_ul_roll_mean from CSV (computed by C xApp per UE)
    prb_ul_mean = np.array([float(r.get("prb_ul_roll_mean", 0.0)) for r in rows])

    def _roll_mean(arr: np.ndarray) -> np.ndarray:
        out = np.empty(n, dtype=np.float64)
        w = _BURST_WINDOW
        for i in range(n):
            start = max(0, i - w + 1)
            out[i] = arr[start : i + 1].mean()
        return out

    prb_dl_mean = _roll_mean(prb_dl)
    thp_ul_mean = _roll_mean(thp_ul)
    thp_dl_mean = _roll_mean(thp_dl)

    # PRB features are in [0,1]: log(1+x)/(mean+eps) works well at this scale.
    # Throughput features are in kbps (0–50000): use direct ratio x/(mean+1) instead
    # so that idle→flood gives ~30000/5000 = 6, not log(30001)/5000 ≈ 0.002.
    prb_ul_bi = np.clip(np.log1p(prb_ul) / (prb_ul_mean + _EPS), 0.0, _BURST_CLIP)
    prb_dl_bi = np.clip(np.log1p(prb_dl) / (prb_dl_mean + _EPS), 0.0, _BURST_CLIP)
    thp_ul_bi = np.clip(thp_ul / (thp_ul_mean + 1.0), 0.0, _BURST_CLIP)
    thp_dl_bi = np.clip(thp_dl / (thp_dl_mean + 1.0), 0.0, _BURST_CLIP)

    for i, r in enumerate(rows):
        r["prb_ul_burst_index"] = float(prb_ul_bi[i])
        r["prb_dl_burst_index"] = float(prb_dl_bi[i])
        r["thp_ul_burst_index"] = float(thp_ul_bi[i])
        r["thp_dl_burst_index"] = float(thp_dl_bi[i])
