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
]

def _make_row(**kwargs):
    defaults = {h: "0.0" for h in HEADER}
    defaults["timestamp_ms"] = "1000"
    defaults["datetime"] = "2026-01-01 00:00:00"
    defaults["label"] = "0"
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


# ── SimpleRuleEngine tests ───────────────────────────────────────────────────

def _make_parsed_row(**kwargs):
    """Return a float dict suitable for SimpleRuleEngine.update()"""
    defaults = {
        "prb_usage_dl_ratio": 0.0, "prb_usage_ul_ratio": 0.0,
        "cqi": 15.0, "rach_preamble": 0.0, "air_delay_ul": 0.0,
        "prb_direction": 0.0, "prb_total": 0.0,
        "prb_dl_delta": 0.0, "prb_ul_delta": 0.0, "prb_burst_index": 0.0,
        "empty_ind_rate": 0.0,
        "prb_dl_roll_mean": 0.0, "prb_dl_roll_std": 0.0,
        "prb_ul_roll_std": 0.0, "prb_ul_roll_max": 0.0,
        "prb_ul_roll_max_100": 0.0,
    }
    defaults.update(kwargs)
    return defaults


def test_rule_engine_ul_flood_triggers_warning():
    """3 consecutive rows with PRB_UL > 0.80 → stage >= 1."""
    from csv_exporter import SimpleRuleEngine
    engine = SimpleRuleEngine()
    row = _make_parsed_row(prb_usage_ul_ratio=0.85)
    stage = 0
    for _ in range(3):
        stage = engine.update(row)
    assert stage >= 1


def test_rule_engine_normal_stays_zero():
    """Normal PRB levels keep stage at 0."""
    from csv_exporter import SimpleRuleEngine
    engine = SimpleRuleEngine()
    row = _make_parsed_row(prb_usage_ul_ratio=0.10, prb_usage_dl_ratio=0.10)
    for _ in range(10):
        stage = engine.update(row)
    assert stage == 0


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
