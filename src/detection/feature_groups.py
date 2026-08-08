"""Feature-group definitions for the grouped feature ablation.

Groups partition the 19 per-UE features exactly once (verified by
`assert_partition()`), but the ablation *configurations* built from them
deliberately overlap: `ul_efficiency` is a ratio of a PRB quantity and a
throughput quantity, so it leaves with either family. Results are therefore
read as the contribution of an information family, not as additive shares.

The rule engine R1-R5 is NOT ablated. It indexes features positionally
(f[0]..f[3], f[7]..f[10]) and is not a learned component, so every
configuration evaluates the rule branch on the full 19-feature vector.
Only the autoencoder input/output dimensionality changes.
"""
from src.detection.feature_schema_ue import FEATURE_NAMES

GROUPS = {
    "prb_raw":     ["prb_usage_dl_ratio", "prb_usage_ul_ratio"],
    "prb_derived": ["prb_direction", "prb_total", "prb_ul_delta",
                    "prb_ul_roll_mean", "prb_ul_roll_std", "ul_persistence"],
    "prb_burst":   ["prb_ul_burst_index", "prb_dl_burst_index"],
    "thp_raw":     ["thp_dl_kbps", "thp_ul_kbps"],
    "thp_derived": ["thp_total_kbps", "thp_ul_delta", "thp_dl_delta",
                    "traffic_direction"],
    "thp_burst":   ["thp_ul_burst_index", "thp_dl_burst_index"],
    "shared":      ["ul_efficiency"],
}

# Raw KPM measurements, before any feature engineering — the ablation floor.
BASE_4 = ["prb_usage_dl_ratio", "prb_usage_ul_ratio", "thp_dl_kbps", "thp_ul_kbps"]

# Temporal = anything derived from more than one timestep.
TEMPORAL = (["prb_ul_delta", "prb_ul_roll_mean", "prb_ul_roll_std", "ul_persistence",
             "thp_ul_delta", "thp_dl_delta"]
            + GROUPS["prb_burst"] + GROUPS["thp_burst"])

ALL_BURST = GROUPS["prb_burst"] + GROUPS["thp_burst"]

DROPPED = {
    "full_19":              [],
    "no_prb_family":        (GROUPS["prb_raw"] + GROUPS["prb_derived"]
                             + GROUPS["prb_burst"] + GROUPS["shared"]),
    "no_throughput_family": (GROUPS["thp_raw"] + GROUPS["thp_derived"]
                             + GROUPS["thp_burst"] + GROUPS["shared"]),
    "no_temporal_family":   TEMPORAL,
    "no_burst":             ALL_BURST,
    "base_only_4":          [f for f in FEATURE_NAMES if f not in BASE_4],
}

CONFIGS = tuple(DROPPED)


def assert_partition():
    """The seven groups must cover all 19 features exactly once."""
    flat = [f for names in GROUPS.values() for f in names]
    assert len(flat) == len(set(flat)), "a feature appears in two groups"
    assert set(flat) == set(FEATURE_NAMES), (
        f"group/schema mismatch: {set(flat) ^ set(FEATURE_NAMES)}")


def kept_features(config):
    """Feature names the autoencoder sees, in canonical schema order."""
    if config not in DROPPED:
        raise ValueError(f"unknown config {config!r}; expected one of {CONFIGS}")
    dropped = set(DROPPED[config])
    return [f for f in FEATURE_NAMES if f not in dropped]


def kept_indices(config):
    """Column indices into the full 19-feature array, in schema order."""
    keep = set(kept_features(config))
    return [i for i, f in enumerate(FEATURE_NAMES) if f in keep]


assert_partition()
