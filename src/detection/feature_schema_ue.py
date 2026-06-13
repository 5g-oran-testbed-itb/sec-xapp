# src/detection/feature_schema_ue.py
# 15 per-UE features from KPM FORMAT_3 + MAC PRB fallback.
# Names match exactly the CSV columns written by csv_per_ue_write() in xapp_sec_moni.c.
# Cell-level feature_schema.py is untouched — these two schemas are independent.

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
]

NUM_FEATURES = len(FEATURE_NAMES)   # 15

# Uniform weighting — no per-feature weighting for per-UE models yet.
FEATURE_WEIGHTS: dict = {}
