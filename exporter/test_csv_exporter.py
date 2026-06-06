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
