import copy

import pytest

from eval_loss_ablation import build_markdown, validate_results


def _metric(*, recall=0.9, fpr_attack=0.02, auc=None):
    return {
        "recall": recall,
        "precision": 0.8,
        "f1": 0.85,
        "fpr_attack": fpr_attack,
        "fpr_val": 0.04,
        "auc": auc,
        "per_class_recall": {
            "ul_flood": 0.91,
            "dl_flood": 0.92,
            "burst": 0.93,
            "roq": 0.94,
        },
        "confusion": {"tp": 90, "fp": 2, "tn": 98, "fn": 10},
    }


def _result(threshold):
    return {
        "threshold": threshold,
        "threshold_pct_val": 95.0,
        "threshold_pct_attack_benign": 97.0,
        "rule_only": _metric(),
        "ml_only": _metric(auc=0.97),
        "hybrid": _metric(recall=0.95),
    }


def _all_results():
    return {
        "gru": {
            "uniform": _result(0.012345),
            "benign": _result(0.006789),
        },
        "lstm": {
            "uniform": _result(0.013579),
            "benign": _result(0.008642),
        },
    }


def test_build_markdown_reports_only_uniform_and_benign_matched_pairs():
    report = build_markdown(_all_results(), target_fpr=0.03)

    assert "GRU | uniform | 0.012345" in report
    assert "GRU | benign | 0.006789" in report
    assert "LSTM | uniform | Hybrid | 95.00%" in report
    assert "LSTM | benign | 91.00% | 92.00% | 93.00% | 94.00%" in report
    assert "Scheme A" not in report
    assert "schemea" not in report
    assert "single-seed" in report


def test_validate_results_rejects_hybrid_above_target_fpr():
    results = _all_results()
    validate_results(results, target_fpr=0.03)
    bad = copy.deepcopy(results)
    bad["gru"]["benign"]["hybrid"]["fpr_attack"] = 0.031

    with pytest.raises(RuntimeError, match="exceeds target FPR"):
        validate_results(bad, target_fpr=0.03)


def test_validate_results_rejects_invalid_ml_auc():
    results = _all_results()
