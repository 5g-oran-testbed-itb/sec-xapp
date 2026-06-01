# Feature names — PRB-based KPM metrics + discriminative engineered features.
# Names match CSV column names written by xapp_sec_moni.c (unless marked *computed*).
#
# NOTE: srsRAN KPM DU does not reliably report DRB volume metrics (always 0).
# Only RRU.PrbUsedDl/UL and RRU.PrbAvailDl/UL are accurate.
FEATURE_NAMES = [
    # ── Base PRB / signal features ────────────────────────────────────────────
    "prb_usage_dl_ratio",    # RRU.PrbUsedDl / (PrbUsedDl + PrbAvailDl)
    "prb_usage_ul_ratio",    # RRU.PrbUsedUl / (PrbUsedUl + PrbAvailUl)
    "cqi",                   # CQI (often 15 in srsRAN keep-last)
    "rach_preamble",         # RACH.PreambleDedCell
    "air_delay_ul",          # DRB.AirIfDelayUl (ms)
    "prb_direction",         # (prb_ul - prb_dl) / (prb_total + eps), bounded [-1,+1]
    "prb_total",             # prb_dl + prb_ul — total load
    "prb_dl_delta",          # prb_dl[t] - prb_dl[t-1]
    "prb_ul_delta",          # prb_ul[t] - prb_ul[t-1]
    "prb_burst_index",       # log(1 + prb_total) / (rolling_mean + eps)
    "empty_ind_rate",        # #empty KPM indications per window — RRC storm proxy
    # ── Rolling window features (window=10ts) ────────────────────────────────
    "prb_dl_roll_mean",      # rolling mean prb_dl_ratio — sustained DL flood
    "prb_dl_roll_std",       # rolling std prb_dl_ratio  — flood=low, burst=high
    "prb_ul_roll_std",       # rolling std prb_ul_ratio  — burst ON/OFF = high variance
    "prb_ul_roll_max",       # rolling max prb_ul_ratio (10ts) — burst peak short-term
    "prb_ul_roll_max_100",   # rolling max prb_ul_ratio (100ts) — peak persists through OFF phase
    "cqi_roll_std",          # rolling std CQI (10ts) — high oscillation = RRC storm
    "rach_roll_mean",        # rolling mean RACH (10ts) — sustained vs isolated spike
    # ── Discriminative features (already in CSV, previously unused) ───────────
    "prb_total_variance",    # rolling variance prb_total — Burst 29× vs Normal
    "prb_dl_ul_asym",        # |prb_dl² - prb_ul²| norm — DL Flood 74× vs Normal
    "prb_ul_near_zero_rate", # rate prb_ul ≈ 0 in window — UL Flood signature
    "prb_peak_drop",         # (recent_peak - current) / (recent_peak + eps) — Burst OFF phase
    "rach_cqi_joint",        # RACH × (1 - CQI/15) — RRC Storm 240× vs Normal
    # ── Long-window computed features (*computed* during preprocessing) ───────
    "rach_roll_max_30",      # rolling max rach_preamble (30ts=30s) — RRC storm quiet phase
    "empty_ind_roll_sum_30", # rolling sum empty_ind_rate (30ts) — cumulative RRC storm signal
    # ── Coefficient of Variation — flood (UDP) vs legitimate transfer (TCP) ──
    "prb_dl_roll_cv",        # prb_dl_roll_std / (prb_dl_roll_mean + eps) — DL Flood≈0, TCP download>0
    "prb_ul_roll_cv",        # prb_ul_roll_std / (prb_ul_roll_mean + eps) — UL Flood≈0, TCP upload>0
]

NUM_FEATURES = len(FEATURE_NAMES)

# Per-feature weights — dipakai di training loss DAN inference scoring (ONNX).
# Bobot > 1 = fitur diskriminatif penting; 1.0 = default.
FEATURE_WEIGHTS = {name: 1.0 for name in FEATURE_NAMES}
FEATURE_WEIGHTS.update({
    # UL Flood
    "prb_ul_near_zero_rate":  5.0,  # 0.01 UL Flood vs 0.87 Normal — sinyal terkuat
    "prb_usage_ul_ratio":     3.0,  # PRB UL tinggi saat UL Flood
    "prb_ul_roll_std":        3.0,  # UL variance — Flood + Burst
    "prb_ul_roll_max":        2.0,  # burst peak short-term
    # DL Flood
    "prb_dl_ul_asym":         4.0,  # 74× DL Flood vs Normal
    "prb_dl_roll_mean":       2.0,  # sustained DL load
    # Burst ON/OFF
    "prb_total_variance":     5.0,  # 29× Burst vs Normal
    "prb_peak_drop":          5.0,  # OFF phase: recent peak vs current
    "prb_burst_index":        2.0,  # burst intensity
    # RRC Storm
    "rach_cqi_joint":         4.0,  # 240× RRC Storm vs Normal
    "rach_roll_max_30":       5.0,  # quiet phase: RACH dalam 30s terakhir
    "empty_ind_roll_sum_30":  5.0,  # cumulative storm signal
    "cqi_roll_std":           2.0,  # CQI oscillation
    "rach_roll_mean":         2.0,  # sustained reconnect
    "rach_preamble":          2.0,  # reconnect spike
    "empty_ind_rate":         2.0,  # APER failure proxy
    # CV features — membedakan UDP flood (CV≈0) dari TCP transfer (CV>0)
    "prb_dl_roll_cv":         3.0,  # key DL Flood vs download discriminator
    "prb_ul_roll_cv":         3.0,  # key UL Flood vs upload discriminator
})
