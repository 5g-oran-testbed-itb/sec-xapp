"""Unit tests for csv_exporter.py"""
import csv
import os
import tempfile
import time
import pytest


# ── CsvFinder tests ─────────────────────────────────────────────────────────

def test_find_newest_csv_returns_latest(tmp_path):
    """find_newest_csv returns the most recently modified CSV in the dir."""
    from csv_exporter import find_newest_csv
    (tmp_path / "old.csv").write_text("a")
    time.sleep(0.05)
    newest = tmp_path / "new.csv"
    newest.write_text("b")
    assert find_newest_csv(str(tmp_path)) == str(newest)


def test_find_newest_csv_returns_none_when_empty(tmp_path):
    from csv_exporter import find_newest_csv
    assert find_newest_csv(str(tmp_path)) is None


# ── parse_csv_row tests ──────────────────────────────────────────────────────

HEADER = [
    "timestamp_ms", "datetime",
    "prb_usage_dl_ratio", "prb_usage_ul_ratio",
    "cqi", "rach_preamble", "air_delay_ul",
    "prb_direction", "prb_total",
    "prb_dl_delta", "prb_ul_delta", "prb_burst_index",
    "label", "empty_ind_rate",
    "prb_dl_roll_mean", "prb_dl_roll_std",
    "prb_ul_roll_std", "prb_ul_roll_max", "prb_ul_roll_max_100",
    "alert_type",
]

def _make_row(**kwargs):
    defaults = {h: "0.0" for h in HEADER}
    defaults["timestamp_ms"] = "1000"
    defaults["datetime"] = "2026-01-01 00:00:00"
    defaults["label"] = "0"
    defaults["alert_type"] = "none"
    defaults.update({k: str(v) for k, v in kwargs.items()})
    return defaults


def test_parse_csv_row_converts_floats():
    from csv_exporter import parse_csv_row
    row = _make_row(prb_usage_dl_ratio="0.42", prb_usage_ul_ratio="0.87", cqi="15")
    result = parse_csv_row(row)
    assert abs(result["prb_usage_dl_ratio"] - 0.42) < 1e-6
    assert abs(result["prb_usage_ul_ratio"] - 0.87) < 1e-6
    assert abs(result["cqi"] - 15.0) < 1e-6


def test_parse_csv_row_handles_empty_string():
    from csv_exporter import parse_csv_row
    row = _make_row(air_delay_ul="")
    result = parse_csv_row(row)
    assert result["air_delay_ul"] == 0.0


# ── EvalResultsLoader tests ──────────────────────────────────────────────────

def test_eval_loader_reads_json(tmp_path):
    import json
    from csv_exporter import load_eval_results
    data = {
        "timestamp": "2026-05-20T10:00:00",
        "dataset": "test.csv",
        "per_stage": {
            "hybrid": {"accuracy": 0.941, "recall": 0.962,
                       "precision": 0.958, "f1": 0.960, "fpr": 0.005}
        },
        "per_attack": {
            "ul_flood": {
                "hybrid": {"recall": 0.991, "precision": 0.985,
                           "f1": 0.988, "count": 1000}
            }
        }
    }
    p = tmp_path / "eval_results.json"
    p.write_text(json.dumps(data))
    result = load_eval_results(str(p))
    assert result["per_stage"]["hybrid"]["accuracy"] == pytest.approx(0.941)
    assert result["per_attack"]["ul_flood"]["hybrid"]["f1"] == pytest.approx(0.988)


def test_eval_loader_returns_none_when_missing():
    from csv_exporter import load_eval_results
    assert load_eval_results("/nonexistent/path.json") is None


# ── Latency tracking tests ───────────────────────────────────────────────────

def test_track_latency_detect_on_stage0_to_1(monkeypatch):
    """Stage 0→1 after 3.1s → g_latency_detect = 3100 ms."""
    import csv_exporter
    csv_exporter._stage_ts.update({"t0": None, "t1": None, "t2": None})
    t = [0.0]
    monkeypatch.setattr(csv_exporter.time, "monotonic", lambda: t[0])

    csv_exporter._track_stage_latency(0, -1)   # record t0
    t[0] = 3.1
    csv_exporter._track_stage_latency(1, 0)    # detect: 3100ms

    assert csv_exporter.g_latency_detect._value.get() == pytest.approx(3100.0, rel=1e-3)


def test_track_latency_confirm_and_total_on_stage1_to_2(monkeypatch):
    """Stage 0→1→2: confirm = 5000ms, total = 8000ms."""
    import csv_exporter
    csv_exporter._stage_ts.update({"t0": None, "t1": None, "t2": None})
    t = [0.0]
    monkeypatch.setattr(csv_exporter.time, "monotonic", lambda: t[0])

    csv_exporter._track_stage_latency(0, -1)   # t0=0
    t[0] = 3.0
    csv_exporter._track_stage_latency(1, 0)    # t1=3.0
    t[0] = 8.0
    csv_exporter._track_stage_latency(2, 1)    # t2=8.0

    assert csv_exporter.g_latency_confirm._value.get() == pytest.approx(5000.0, rel=1e-3)
    assert csv_exporter.g_latency_total._value.get() == pytest.approx(8000.0, rel=1e-3)


def test_track_latency_noop_when_stage_unchanged(monkeypatch):
    """Calling with same stage → gauge unchanged."""
    import csv_exporter
    csv_exporter._stage_ts.update({"t0": None, "t1": None, "t2": None})
    t = [0.0]
    monkeypatch.setattr(csv_exporter.time, "monotonic", lambda: t[0])

    before = csv_exporter.g_latency_detect._value.get()
    csv_exporter._track_stage_latency(0, 0)    # no-op
    assert csv_exporter.g_latency_detect._value.get() == before


# ── New gauge / parse tests ──────────────────────────────────────────────────

def test_parse_csv_row_includes_alert_type_string():
    from csv_exporter import parse_csv_row
    row = _make_row()
    row["alert_type"] = "ul_flood"
    result = parse_csv_row(row)
    assert result["alert_type"] == "ul_flood"


def test_parse_csv_row_includes_empty_ind_rate():
    from csv_exporter import parse_csv_row
    row = _make_row(empty_ind_rate="3.0")
    result = parse_csv_row(row)
    assert result["empty_ind_rate"] == pytest.approx(3.0)


def test_parse_csv_row_includes_prb_burst_index():
    from csv_exporter import parse_csv_row
    row = _make_row(prb_burst_index="2.5")
    result = parse_csv_row(row)
    assert result["prb_burst_index"] == pytest.approx(2.5)


# ── gru_model tests ──────────────────────────────────────────────────────────

def test_gru_model_load_raises_on_missing_file():
    from gru_model import GRUAutoencoder
    with pytest.raises(FileNotFoundError):
        GRUAutoencoder.load("/nonexistent/model.pt")


def test_extract_gru_features_correct_order():
    from gru_model import extract_gru_features, GRU_FEATURE_COLS
    row = {col: float(i) for i, col in enumerate(GRU_FEATURE_COLS)}
    features = extract_gru_features(row)
    assert features.shape == (16,)
    assert features[0] == pytest.approx(0.0)   # prb_usage_dl_ratio
    assert features[10] == pytest.approx(10.0)  # empty_ind_rate


def test_extract_gru_features_missing_col_defaults_to_zero():
    from gru_model import extract_gru_features
    features = extract_gru_features({})
    assert features.shape == (16,)
    assert all(v == 0.0 for v in features)


# ── GRU stage logic tests ────────────────────────────────────────────────────

def test_gru_stage_crit_when_score_above_thresh():
    """Score above threshold → stage 2."""
    import csv_exporter
    score_a = csv_exporter.GRU_THRESH_A * 1.5
    score_b = 0.0
    stage = (2 if score_a > csv_exporter.GRU_THRESH_A or score_b > csv_exporter.GRU_THRESH_B
             else 1 if score_a > csv_exporter.GRU_THRESH_A * 0.5 or score_b > csv_exporter.GRU_THRESH_B * 0.5
             else 0)
    assert stage == 2


def test_gru_stage_warn_when_score_in_warn_zone():
    """Score between 50%–100% of threshold → stage 1."""
    import csv_exporter
    score_a = csv_exporter.GRU_THRESH_A * 0.7
    score_b = 0.0
    stage = (2 if score_a > csv_exporter.GRU_THRESH_A or score_b > csv_exporter.GRU_THRESH_B
             else 1 if score_a > csv_exporter.GRU_THRESH_A * 0.5 or score_b > csv_exporter.GRU_THRESH_B * 0.5
             else 0)
    assert stage == 1


def test_gru_stage_normal_when_both_below_thresh():
    """Both scores below 50% of threshold → stage 0."""
    import csv_exporter
    score_a = csv_exporter.GRU_THRESH_A * 0.1
    score_b = csv_exporter.GRU_THRESH_B * 0.1
    stage = (2 if score_a > csv_exporter.GRU_THRESH_A or score_b > csv_exporter.GRU_THRESH_B
             else 1 if score_a > csv_exporter.GRU_THRESH_A * 0.5 or score_b > csv_exporter.GRU_THRESH_B * 0.5
             else 0)
    assert stage == 0


# ── Eval v2 tests ────────────────────────────────────────────────────────────

def test_populate_eval_v2_sets_rule_recall():
    import csv_exporter
    csv_exporter._populate_eval_v2()
    val = csv_exporter.g_eval_recall_v2.labels(model="rule", attack="all")._value.get()
    assert abs(val - 0.9765) < 0.001


def test_populate_eval_v2_sets_gru_tuned_rrc_recall():
    import csv_exporter
    csv_exporter._populate_eval_v2()
    val = csv_exporter.g_eval_recall_v2.labels(model="gru_tuned", attack="rrc_storm")._value.get()
    assert abs(val - 0.715) < 0.001


def test_populate_eval_v2_sets_hybrid_gru_fpr():
    import csv_exporter
    csv_exporter._populate_eval_v2()
    val = csv_exporter.g_eval_fpr_v2.labels(model="hybrid_gru")._value.get()
    assert abs(val - 0.0287) < 0.0001


def test_find_newest_csv_filters_by_pattern(tmp_path):
    """find_newest_csv with pattern 'ue_alerts_*.csv' ignores other CSV files."""
    from csv_exporter import find_newest_csv
    (tmp_path / "training_20260617.csv").write_text("cell-level")
    time.sleep(0.05)
    alert = tmp_path / "ue_alerts_20260617.csv"
    alert.write_text("per-ue")
    assert find_newest_csv(str(tmp_path), "ue_alerts_*.csv") == str(alert)


def test_find_newest_csv_default_pattern_unchanged(tmp_path):
    """Default pattern '*.csv' still returns newest csv (backward-compatible)."""
    from csv_exporter import find_newest_csv
    (tmp_path / "a.csv").write_text("a")
    time.sleep(0.05)
    b = tmp_path / "b.csv"
    b.write_text("b")
    assert find_newest_csv(str(tmp_path)) == str(b)


# ── Per-UE v4 eval tests ─────────────────────────────────────────────────────

def test_populate_eval_ue_v4_sets_gru_hybrid_overall_recall():
    import csv_exporter
    csv_exporter._populate_eval_ue_v4()
    val = csv_exporter.g_ue_eval_recall_v4.labels(config="gru_hybrid", attack="all")._value.get()
    assert abs(val - 0.961) < 0.001


def test_populate_eval_ue_v4_sets_gru_hybrid_roq_recall():
    import csv_exporter
    csv_exporter._populate_eval_ue_v4()
    val = csv_exporter.g_ue_eval_recall_v4.labels(config="gru_hybrid", attack="roq")._value.get()
    assert abs(val - 0.922) < 0.001


def test_populate_eval_ue_v4_sets_gru_hybrid_det_latency():
    import csv_exporter
    csv_exporter._populate_eval_ue_v4()
    val = csv_exporter.g_ue_eval_det_lat_v4.labels(config="gru_hybrid")._value.get()
    assert abs(val - 4.04) < 0.01


def test_populate_eval_ue_v4_sets_rule_only_fpr():
    import csv_exporter
    csv_exporter._populate_eval_ue_v4()
    val = csv_exporter.g_ue_eval_fpr_v4.labels(config="rule_only")._value.get()
    assert abs(val - 0.0293) < 0.0001


# ── parse_ue_alert_row tests ────────────────────────────────────────────────

def test_parse_ue_alert_row_extracts_fields():
    from csv_exporter import parse_ue_alert_row
    raw = {"timestamp_ms": "1750000000000", "rnti": "0x1a2b",
           "rule_mask": "0x01", "rule_stage": "2",
           "mse": "0.031500", "threshold": "0.025969", "alert_type": "ul_flood"}
    result = parse_ue_alert_row(raw)
    assert result["rnti"] == "6699"
    assert result["rule_stage"] == 2
    assert abs(result["mse"] - 0.0315) < 1e-6
    assert result["alert_type"] == 1  # ul_flood → 1


def test_parse_ue_alert_row_roq_maps_to_4():
    from csv_exporter import parse_ue_alert_row
    raw = {"rnti": "0x0001", "rule_stage": "1", "mse": "0.028", "alert_type": "roq", "threshold": "0.025969"}
    result = parse_ue_alert_row(raw)
    assert result["alert_type"] == 4


def test_parse_ue_alert_row_unknown_alert_type_defaults_zero():
    from csv_exporter import parse_ue_alert_row
    raw = {"rnti": "0x0002", "rule_stage": "0", "mse": "0.0", "alert_type": "unknown_type"}
    result = parse_ue_alert_row(raw)
    assert result["alert_type"] == 0


def test_parse_ue_feature_row_extracts_fields():
    from csv_exporter import parse_ue_feature_row
    raw = {
        "timestamp_ms": "1750000000000", "datetime": "2026-06-17 14:00:00",
        "rnti": "0x2345",
        "prb_usage_ul_ratio": "0.75", "prb_usage_dl_ratio": "0.30",
        "thp_ul_kbps": "5000.0", "thp_dl_kbps": "1200.0",
        "prb_direction": "0.43", "ul_efficiency": "6666.7", "label": "1",
    }
    result = parse_ue_feature_row(raw)
    assert result["rnti"] == "9029"
    assert abs(result["prb_usage_ul_ratio"] - 0.75) < 1e-6
    assert abs(result["thp_ul_kbps"] - 5000.0) < 1e-6
    assert abs(result["prb_direction"] - 0.43) < 1e-6


def test_parse_ue_feature_row_missing_col_defaults_zero():
    from csv_exporter import parse_ue_feature_row
    result = parse_ue_feature_row({"rnti": "0x0001"})
    assert result["thp_ul_kbps"] == 0.0
    assert result["prb_usage_dl_ratio"] == 0.0
    assert result["ul_efficiency"] == 0.0


# ── mitigation event parser tests ────────────────────────────────────────────

MIT_HEADER = ["epoch_ms", "action", "rnti", "ue_id", "prb_limit", "attack", "confidence"]

def _make_mit_row(**kwargs):
    defaults = {
        "epoch_ms": "1000", "action": "THROTTLE", "rnti": "17921",
        "ue_id": "17921", "prb_limit": "0", "attack": "ul_flood",
        "confidence": "0.98",
    }
    defaults.update({k: str(v) for k, v in kwargs.items()})
    return defaults

def test_parse_mitigation_row_throttle():
    from csv_exporter import parse_mitigation_row
    row = parse_mitigation_row(_make_mit_row(action="THROTTLE", rnti="17921", prb_limit="0"))
    assert row["action"] == "THROTTLE"
    assert row["rnti"] == "17921"
    assert row["prb_limit"] == 0
    assert row["attack"] == "ul_flood"

def test_parse_mitigation_row_restore_defaults_prb_100():
    from csv_exporter import parse_mitigation_row
    row = parse_mitigation_row(_make_mit_row(action="RESTORE", prb_limit="100"))
    assert row["action"] == "RESTORE"
    assert row["prb_limit"] == 100


def test_update_mitigation_metrics_throttle_then_restore():
    from csv_exporter import (
        parse_mitigation_row, update_mitigation_metrics,
        g_ue_mitigation_active, g_ue_mitigation_prb_limit, c_mitigations_applied,
    )
    # THROTTLE → active=1, prb_limit=0, counter +1
    before = c_mitigations_applied.labels(rnti="17921", attack="ul_flood")._value.get()
    update_mitigation_metrics(parse_mitigation_row(
        _make_mit_row(action="THROTTLE", rnti="17921", prb_limit="0", attack="ul_flood")))
    assert g_ue_mitigation_active.labels(rnti="17921")._value.get() == 1
    assert g_ue_mitigation_prb_limit.labels(rnti="17921")._value.get() == 0
    assert c_mitigations_applied.labels(rnti="17921", attack="ul_flood")._value.get() == before + 1

    # RESTORE → active=0, prb_limit=100, counter unchanged
    update_mitigation_metrics(parse_mitigation_row(
        _make_mit_row(action="RESTORE", rnti="17921", prb_limit="100", attack="ul_flood")))
    assert g_ue_mitigation_active.labels(rnti="17921")._value.get() == 0
    assert g_ue_mitigation_prb_limit.labels(rnti="17921")._value.get() == 100
    assert c_mitigations_applied.labels(rnti="17921", attack="ul_flood")._value.get() == before + 1


def test_read_active_threshold_sidecar(tmp_path):
    from csv_exporter import read_active_threshold, g_ue_threshold, g_ue_model_info
    p = tmp_path / "xapp_active_threshold"
    p.write_text("0.027047 lstm_ue_v6\n")
    read_active_threshold(str(p))
    assert abs(g_ue_threshold._value.get() - 0.027047) < 1e-9
    assert g_ue_model_info.labels(model="lstm_ue_v6")._value.get() == 1


def test_read_active_threshold_missing_file_noop(tmp_path):
    from csv_exporter import read_active_threshold
    # Should not raise when the sidecar does not exist yet.
    read_active_threshold(str(tmp_path / "does_not_exist"))


def test_read_active_threshold_sidecar_switch(tmp_path):
    from csv_exporter import read_active_threshold, g_ue_threshold, g_ue_model_info
    p = tmp_path / "xapp_active_threshold"

    # Start with GRU
    p.write_text("0.026026 gru_ue_v5\n")
    read_active_threshold(str(p))
    assert abs(g_ue_threshold._value.get() - 0.026026) < 1e-9
    assert g_ue_model_info.labels(model="gru_ue_v5")._value.get() == 1
    assert g_ue_model_info.labels(model="lstm_ue_v6")._value.get() == 0

    # Switch to LSTM
    p.write_text("0.027047 lstm_ue_v6\n")
    read_active_threshold(str(p))
    assert abs(g_ue_threshold._value.get() - 0.027047) < 1e-9
    assert g_ue_model_info.labels(model="gru_ue_v5")._value.get() == 0
    assert g_ue_model_info.labels(model="lstm_ue_v6")._value.get() == 1




