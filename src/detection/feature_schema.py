# 11 features — PRB-based metrics from DU KPM + empty_ind_rate for RRC storm detection.
# Names match exactly the CSV column names written by xapp_sec_moni.c.
#
# NOTE: srsRAN KPM DU does not reliably report any DRB volume metrics:
#   - DRB.UEThpDl, DRB.UEThpUl: always 0
#   - DRB.RlcSduTransmittedVolumeDL/UL: always 0
# Only RRU.PrbUsedDl/UL and RRU.PrbAvailDl/UL are accurate.
# Feature set uses only what srsRAN actually reports.
FEATURE_NAMES = [
    "prb_usage_dl_ratio",   # RRU.PrbUsedDl / (PrbUsedDl + PrbAvailDl)
    "prb_usage_ul_ratio",   # RRU.PrbUsedUl / (PrbUsedUl + PrbAvailUl)
    "cqi",                  # CQI (often 15 in srsRAN, low variance)
    "rach_preamble",        # RACH.PreambleDedCell
    "air_delay_ul",         # DRB.AirIfDelayUl (ms)
    "prb_direction",        # (prb_ul - prb_dl) / (prb_total + eps), bounded [-1,+1]
    "prb_total",            # prb_dl + prb_ul — total load
    "prb_dl_delta",         # prb_dl[t] - prb_dl[t-1] — DL PRB change rate
    "prb_ul_delta",         # prb_ul[t] - prb_ul[t-1] — UL PRB change rate
    "prb_burst_index",      # log(1 + prb_total) / (rolling_mean + eps)
    "empty_ind_rate",       # #empty APER-failed KPM indications per window — RRC storm proxy
    "prb_dl_roll_mean",     # rolling mean prb_dl_ratio (10 timestep) — sustained DL flood
    "prb_dl_roll_std",      # rolling std prb_dl_ratio  — flood=low std, burst=high std
    "prb_ul_roll_std",      # rolling std prb_ul_ratio  — burst ON/OFF = high variance
    "prb_ul_roll_max",      # rolling max prb_ul_ratio (10ts) — burst peak short-term
    "prb_ul_roll_max_100",  # rolling max prb_ul_ratio (100ts=10s) — peak persists through OFF phase (max=58ts)
]

NUM_FEATURES = len(FEATURE_NAMES)

# Per-feature MSE loss weights. Missing keys default to 1.0 in training scripts.
# Empty = uniform weighting across all features.
FEATURE_WEIGHTS: dict = {}
