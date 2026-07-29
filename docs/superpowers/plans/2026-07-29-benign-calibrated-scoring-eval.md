# Benign-Calibrated Scoring & Leakage-Free Re-Evaluation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare three anomaly-scoring schemes (Uniform MSE, Benign-calibrated MSE, Attack-informed Scheme A) on the existing per-UE attack dataset, so the two attack-free schemes yield leakage-free numbers.

**Architecture:** Add a small pure-Python scoring module (`src/detection/scoring.py`) holding the weight math and weighted-score, unit-tested without any model. A new offline driver (`evaluate_scoring_comparison.py`) reuses the existing data helpers from `evaluate_per_ue_v2.py`, computes per-feature reconstruction residuals once per model, then evaluates all three scoring schemes for both deployed models (GRU v5, LSTM v6). The big `evaluate_per_ue_v2.py` is left untouched (imported, not edited).

**Tech Stack:** Python 3.12 (`venv/bin/python3`), numpy, torch, scikit-learn, pytest.

**Reference spec:** `docs/superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md`

---

## Key facts (verified against the codebase)

- `evaluate_per_ue_v2.py` exposes reusable helpers: `load_csv`, `preprocess_rows`, `split_by_rnti`, `add_burst_features_rows` (from `feature_schema_ue`), `extract_features` (→ 15 base cols), `get_labels`, `build_windows(X, seq_len)` (→ `(N-seq+1, seq, F)`), `compute_roc_auc(neg, pos)`, `load_models(...)`, and constant `SEQ_LEN = 30`. Importing the module is safe (its `main()` is guarded by `if __name__ == "__main__"`).
- Current scoring (`score_ml`, lines 207-239): `fe = ((recon - chunk)**2).mean(dim=1)` → `(B, F)`; `score = (fe * w).sum(dim=1) / w.sum()`. `w = _WEIGHT_VEC` is the Scheme A vector built from `FEATURE_WEIGHTS`.
- Window→label alignment (line 762-763): the label of window `i` is `get_labels(rows)[SEQ_LEN-1:][i]`.
- `NUM_FEATURES = 19`, `FEATURE_NAMES` and `FEATURE_WEIGHTS` live in `src/detection/feature_schema_ue.py`. `add_burst_features_rows` must be called per-RNTI before `extract_features`.
- Model files exist: `models/gru_ue_v5.pt` (+ `_scaler.pkl`, `_threshold.json`) and `models/lstm_ue_v6.pt` (+ `_scaler.pkl`, `_threshold.json`).
- Attack file `csv/dataset_attack_ue_juni.csv` label counts: `0`=5810 (held-out benign), `1`=484 ul_flood, `2`=368 dl_flood, `3`=725 burst, `4`=746 roq.
- Tests run with: `venv/bin/python3 -m pytest tests/ -v`. Convention: `tests/test_*.py`, `sys.path.insert` to repo root at top (see `tests/test_eval_per_ue_v2.py`).

## File structure

- Create: `src/detection/scoring.py` — pure scoring math + residual extraction. One responsibility: turn per-feature residuals into a weighted anomaly score, and derive weight vectors. No dependency on `evaluate_per_ue_v2`.
- Create: `tests/test_scoring.py` — unit tests for the pure functions (no model needed).
- Create: `evaluate_scoring_comparison.py` — driver; reuses `evaluate_per_ue_v2` helpers + `scoring.py`; opens the attack file once; prints/saves the 3-scheme × 2-model comparison.
- Create: `results/scoring_comparison/` (output dir, auto-created at runtime).

---

## Task 1: Pure scoring math (`weighted_score` + `benign_calibrated_weights`)

**Files:**
- Create: `src/detection/scoring.py`
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scoring.py`:

```python
# tests/test_scoring.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
from src.detection.scoring import weighted_score, benign_calibrated_weights


def test_weighted_score_uniform_equals_feature_mean():
    # residuals: 2 windows, 3 features
    res = np.array([[1.0, 2.0, 3.0], [4.0, 4.0, 4.0]], dtype=np.float32)
    w = np.ones(3, dtype=np.float32)
    out = weighted_score(res, w)
    assert out == pytest.approx([2.0, 4.0])  # plain mean over features


def test_weighted_score_respects_weights():
    res = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    w = np.array([3.0, 1.0, 1.0], dtype=np.float32)
    # (1*3 + 0 + 0) / (3+1+1) = 3/5
    assert weighted_score(res, w)[0] == pytest.approx(0.6)


def test_benign_weights_penalize_large_residual_features():
    # feature 0 has small benign residual (stable), feature 1 large (noisy)
    res = np.array([[0.01, 1.0], [0.01, 1.0], [0.02, 1.2]], dtype=np.float32)
    w = benign_calibrated_weights(res)
    assert w[0] > w[1]  # stable feature gets more weight


def test_benign_weights_are_capped():
    # feature 2 has ~zero benign residual → raw weight explodes; cap must bound it
    res = np.array([[1.0, 1.0, 1e-12], [1.0, 1.0, 1e-12], [1.2, 0.9, 1e-12]],
                   dtype=np.float32)
    w = benign_calibrated_weights(res, cap_mult=10.0)
    raw_median = np.median(1.0 / (np.median(res, axis=0)
                                  + np.median(np.abs(res - np.median(res, axis=0)), axis=0)
                                  + 1e-6))
    assert w.max() <= 10.0 * raw_median + 1e-3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python3 -m pytest tests/test_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.detection.scoring'`

- [ ] **Step 3: Write minimal implementation**

Create `src/detection/scoring.py`:

```python
"""Pure anomaly-scoring math for per-UE autoencoder detection.

Isolated from evaluate_per_ue_v2.py so the weight/score logic is unit-testable
without loading a trained model. See
docs/superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md
"""
import numpy as np


def weighted_score(residuals: np.ndarray, weight_vec: np.ndarray) -> np.ndarray:
    """Weighted mean of per-feature residuals.

    residuals: (N, F) per-feature squared reconstruction error (mean over time).
    weight_vec: (F,) non-negative weights.
    Returns: (N,) float32 anomaly score = sum(w*e) / sum(w).
    """
    w = np.asarray(weight_vec, dtype=np.float64)
    res = np.asarray(residuals, dtype=np.float64)
    return ((res * w).sum(axis=1) / w.sum()).astype(np.float32)


def benign_calibrated_weights(residuals: np.ndarray,
                              eps: float = 1e-6,
                              cap_mult: float = 10.0) -> np.ndarray:
    """Weights from benign residual scale only (no attack labels).

    Higher weight for features whose benign residual is small AND stable, via
    inverse (median + MAD). Capped at cap_mult * median(raw weight) so a
    near-zero-residual feature cannot dominate the score.

    residuals: (N, F) per-feature squared residuals on BENIGN windows.
    Returns: (F,) float32 weight vector.
    """
    res = np.asarray(residuals, dtype=np.float64)
    med = np.median(res, axis=0)                       # (F,)
    mad = np.median(np.abs(res - med), axis=0)         # (F,)
    raw = 1.0 / (med + mad + eps)                       # (F,)
    cap = cap_mult * np.median(raw)
    return np.minimum(raw, cap).astype(np.float32)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python3 -m pytest tests/test_scoring.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/detection/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): add pure weighted-score and benign-calibrated weights"
```

---

## Task 2: Weight-vector dispatch + residual extraction

**Files:**
- Modify: `src/detection/scoring.py` (append two functions)
- Test: `tests/test_scoring.py` (append tests for `make_weight_vec`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scoring.py`:

```python
from src.detection.scoring import make_weight_vec


def test_make_weight_vec_uniform():
    names = ["a", "b", "c"]
    w = make_weight_vec("uniform", names, {"a": 5.0}, None)
    assert list(w) == [1.0, 1.0, 1.0]


def test_make_weight_vec_attack_uses_dict():
    names = ["a", "b"]
    w = make_weight_vec("attack", names, {"a": 4.7, "b": 0.4}, None)
    assert w[0] == pytest.approx(4.7) and w[1] == pytest.approx(0.4)


def test_make_weight_vec_benign_requires_residuals():
    with pytest.raises(ValueError):
        make_weight_vec("benign", ["a"], {}, None)


def test_make_weight_vec_rejects_unknown():
    with pytest.raises(ValueError):
        make_weight_vec("bogus", ["a"], {}, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python3 -m pytest tests/test_scoring.py -k make_weight_vec -v`
Expected: FAIL — `ImportError: cannot import name 'make_weight_vec'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/detection/scoring.py`:

```python
def make_weight_vec(scoring: str,
                    feature_names: list,
                    attack_weight_dict: dict,
                    benign_residuals=None) -> np.ndarray:
    """Return the (F,) weight vector for a scoring scheme.

    scoring: "uniform" | "attack" | "benign".
      - uniform: all ones.
      - attack:  Scheme A weights from attack_weight_dict (attack-informed).
      - benign:  benign_calibrated_weights(benign_residuals) (attack-free).
    """
    n = len(feature_names)
    if scoring == "uniform":
        return np.ones(n, dtype=np.float32)
    if scoring == "attack":
        return np.array([attack_weight_dict.get(f, 1.0) for f in feature_names],
                        dtype=np.float32)
    if scoring == "benign":
        if benign_residuals is None or len(benign_residuals) == 0:
            raise ValueError("benign scoring requires non-empty benign_residuals")
        return benign_calibrated_weights(benign_residuals)
    raise ValueError(f"unknown scoring mode: {scoring!r}")


def per_feature_residuals_from_windows(model, wins, batch: int = 256) -> np.ndarray:
    """Per-feature squared reconstruction error (mean over time) for each window.

    model: torch autoencoder returning (B, seq, F).
    wins: (N, seq, F) float32 scaled windows (from build_windows).
    Returns: (N, F) float32 residuals. Empty (0, F) if no windows.

    NOTE: torch is imported lazily so the pure functions above stay import-cheap.
    """
    import torch
    if len(wins) == 0:
        f = wins.shape[-1] if wins.ndim == 3 else 0
        return np.zeros((0, f), dtype=np.float32)
    model.eval()
    parts = []
    for i in range(0, len(wins), batch):
        chunk = torch.tensor(wins[i:i + batch])
        with torch.no_grad():
            recon = model(chunk)
            fe = ((recon - chunk) ** 2).mean(dim=1)   # (B, F)
        parts.append(fe.numpy())
    return np.concatenate(parts).astype(np.float32)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python3 -m pytest tests/test_scoring.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/detection/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): add weight-vector dispatch and residual extraction"
```

---

## Task 3: Comparison driver (`evaluate_scoring_comparison.py`)

**Files:**
- Create: `evaluate_scoring_comparison.py`

- [ ] **Step 1: Write the driver**

Create `evaluate_scoring_comparison.py`:

```python
#!/usr/bin/env python3
"""Leakage-aware scoring comparison: Uniform vs Benign-calibrated vs Attack-informed.

For each deployed model (GRU v5, LSTM v6) and each scoring scheme, computes
held-out metrics on csv/dataset_attack_ue_juni.csv. Uniform and Benign schemes
never touch attack data during calibration, so the attack file is a valid
held-out test for them. Attack-informed is shown as a labeled (biased) comparison.

See docs/superpowers/specs/2026-07-29-benign-calibrated-scoring-eval-design.md
"""
import argparse
import json
import os

import numpy as np

from evaluate_per_ue_v2 import (
    SEQ_LEN, load_csv, preprocess_rows, split_by_rnti, extract_features,
    get_labels, build_windows, compute_roc_auc, load_models,
)
from src.detection.feature_schema_ue import (
    FEATURE_NAMES, FEATURE_WEIGHTS, add_burst_features_rows,
)
from src.detection.scoring import (
    make_weight_vec, weighted_score, per_feature_residuals_from_windows,
)

LABEL_NAMES = {1: "ul_flood", 2: "dl_flood", 3: "burst", 4: "roq"}
SCORINGS = ["uniform", "benign", "attack"]
FPR_POINTS = [0.05, 0.03]
P_THRESHOLD = 97.0  # percentile on benign validation scores


def pooled_residuals(model, scaler, rows_by_rnti):
    """Return (residuals (M,F), labels (M,)) pooled across RNTIs, window-aligned."""
    res_parts, lbl_parts = [], []
    for rnti, rows in sorted(rows_by_rnti.items()):
        if len(rows) < SEQ_LEN:
            continue
        add_burst_features_rows(rows)
        X = extract_features(rows)
        X_scaled = scaler.transform(X).astype(np.float32)
        wins = build_windows(X_scaled, SEQ_LEN)
        if len(wins) == 0:
            continue
        res = per_feature_residuals_from_windows(model, wins)
        lbls = get_labels(rows)[SEQ_LEN - 1:]          # align: window i -> last row label
        res_parts.append(res)
        lbl_parts.append(lbls[:len(res)])
    if not res_parts:
        raise ValueError("no RNTI had >= SEQ_LEN rows")
    return np.concatenate(res_parts), np.concatenate(lbl_parts)


def recall_at_fpr(neg, pos, target_fpr):
    """TPR at a target FPR, read off the ROC curve."""
    fpr, tpr, _ = compute_roc_auc(neg, pos)
    return float(np.interp(target_fpr, fpr, tpr))


def evaluate_one(model, scaler, val_res, atk_res, atk_lbls, scoring):
    """Return a metrics dict for one (model, scoring) combination."""
    w = make_weight_vec(scoring, FEATURE_NAMES, FEATURE_WEIGHTS,
                        benign_residuals=val_res)
    val_scores = weighted_score(val_res, w)
    thr = float(np.percentile(val_scores, P_THRESHOLD))

    atk_scores = weighted_score(atk_res, w)
    neg = atk_scores[atk_lbls == 0]                    # held-out benign windows
    pos = atk_scores[atk_lbls > 0]

    held_out_fpr = float((neg > thr).mean()) if len(neg) else 0.0
    recall = float((pos > thr).mean()) if len(pos) else 0.0
    _, _, auc = compute_roc_auc(neg, pos)

    per_class = {}
    for lbl, name in LABEL_NAMES.items():
        m = atk_lbls == lbl
        per_class[name] = round(float((atk_scores[m] > thr).mean()), 4) if m.sum() else None

    return {
        "scoring": scoring,
        "leakage_free": scoring in ("uniform", "benign"),
        "threshold_p97": round(thr, 6),
        "held_out_fpr": round(held_out_fpr, 4),
        "recall_at_p97": round(recall, 4),
        "auc": auc,
        "recall_at_fpr": {f"{int(p*100)}pct": round(recall_at_fpr(neg, pos, p), 4)
                          for p in FPR_POINTS},
        "per_class_recall": per_class,
        "n_neg": int(len(neg)),
        "n_pos": int(len(pos)),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--val",    default="csv/dataset_validation_ue_juni.csv")
    ap.add_argument("--attack", default="csv/dataset_attack_ue_juni.csv")
    ap.add_argument("--output", default="results/scoring_comparison/")
    ap.add_argument("--lstm-model",  default="models/lstm_ue_v6.pt")
    ap.add_argument("--lstm-scaler", default="models/lstm_ue_v6_scaler.pkl")
    ap.add_argument("--lstm-threshold", default="models/lstm_ue_v6_threshold.json")
    ap.add_argument("--gru-model",   default="models/gru_ue_v5.pt")
    ap.add_argument("--gru-scaler",  default="models/gru_ue_v5_scaler.pkl")
    ap.add_argument("--gru-threshold", default="models/gru_ue_v5_threshold.json")
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)
    models = load_models(
        lstm_pt=args.lstm_model, lstm_pkl=args.lstm_scaler, lstm_json=args.lstm_threshold,
        gru_pt=args.gru_model, gru_pkl=args.gru_scaler, gru_json=args.gru_threshold,
    )

    print(f"[*] Loading validation: {args.val}")
    val_rows = load_csv(args.val); preprocess_rows(val_rows)
    val_by_rnti = split_by_rnti(val_rows)

    print(f"[*] Loading attack (held-out test): {args.attack}")
    atk_rows = load_csv(args.attack); preprocess_rows(atk_rows)
    atk_by_rnti = split_by_rnti(atk_rows)

    all_results = {}
    for mtype in ["gru", "lstm"]:
        model, scaler, _ = models[mtype]
        print(f"\n=== {mtype.upper()} — computing residuals ===")
        val_res, _ = pooled_residuals(model, scaler, val_by_rnti)
        atk_res, atk_lbls = pooled_residuals(model, scaler, atk_by_rnti)
        print(f"    val windows={len(val_res)}  attack windows={len(atk_res)} "
              f"(benign={int((atk_lbls==0).sum())}, attack={int((atk_lbls>0).sum())})")
        all_results[mtype] = [
            evaluate_one(model, scaler, val_res, atk_res, atk_lbls, s)
            for s in SCORINGS
        ]

    # ── Save + print ──────────────────────────────────────────────────────────
    json_path = os.path.join(args.output, "scoring_comparison.json")
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n[JSON] {json_path}")

    lines = ["# Scoring Comparison (held-out on dataset_attack_ue_juni.csv)\n",
             "| Model | Scoring | Leakage-free | Recall@P97 | Held-out FPR | AUC | Recall@5% | Recall@3% |",
             "|---|---|---|---|---|---|---|---|"]
    for mtype in ["gru", "lstm"]:
        for r in all_results[mtype]:
            lines.append(
                f"| {mtype.upper()} | {r['scoring']} | "
                f"{'yes' if r['leakage_free'] else 'NO (biased)'} | "
                f"{r['recall_at_p97']:.4f} | {r['held_out_fpr']:.4f} | {r['auc']:.4f} | "
                f"{r['recall_at_fpr']['5pct']:.4f} | {r['recall_at_fpr']['3pct']:.4f} |")
    md_path = os.path.join(args.output, "scoring_comparison.md")
    with open(md_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[MD]   {md_path}\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-check that it imports (no run yet)**

Run: `venv/bin/python3 -c "import evaluate_scoring_comparison"`
Expected: no output, exit 0 (imports resolve).

- [ ] **Step 3: Commit**

```bash
git add evaluate_scoring_comparison.py
git commit -m "feat(eval): add 3-scheme leakage-aware scoring comparison driver"
```

---

## Task 4: Run end-to-end and record results

**Files:**
- Create: `docs/scoring_comparison_results.md` (findings write-up)

- [ ] **Step 1: Run the comparison on real data**

Run: `venv/bin/python3 evaluate_scoring_comparison.py`
Expected: prints per-model residual window counts (attack benign≈5810, attack pos≈2323) and a markdown table with 6 rows (GRU/LSTM × uniform/benign/attack). Writes `results/scoring_comparison/scoring_comparison.{json,md}`.

- [ ] **Step 2: Sanity-check the output**

Verify:
- `attack` rows are flagged `NO (biased)`; `uniform`/`benign` flagged `yes`.
- Held-out FPR for all rows is a small number (roughly 0.02–0.08), not ~0 or ~1 (would signal a threshold/alignment bug).
- AUC values are in (0.5, 1.0).
If any check fails, STOP and debug (do not tune parameters against the attack file — that reintroduces leakage; fix code bugs only).

- [ ] **Step 3: Write the findings doc**

Create `docs/scoring_comparison_results.md` capturing: the generated table (paste from `results/scoring_comparison/scoring_comparison.md`), plus 3–5 sentences interpreting it against the decision rule in the spec §8:
- If `benign` reaches target (e.g. ≥80% recall @ ~5% FPR) → adopt benign-calibrated; report as leakage-free unsupervised.
- If only `attack` is far higher → note the gap is (partly) leakage; recommend Track B (fresh test set) before claiming the attack-informed number.
- Always state the model-selection-leakage limitation (spec §9).

- [ ] **Step 4: Commit**

```bash
git add docs/scoring_comparison_results.md results/scoring_comparison/
git commit -m "docs(eval): record benign-calibrated vs attack-informed scoring results"
```

---

## Self-review notes

- **Spec coverage:** C1/C2/C3 (spec §6) → `SCORINGS` in Task 3. Benign weights median+MAD+cap (§6) → Task 1. Frozen-formula-a-priori guardrail (§4.1) → params are constants, and Task 4 Step 2 forbids tuning on the attack file. Held-out FPR from `label==0` (§5) → `neg = atk_scores[atk_lbls==0]`. Both models (§6) → loop over `["gru","lstm"]` with v5/v6 defaults. Decision rule (§8) + limitations (§9) → Task 4 Step 3.
- **Out of scope (spec §3):** no AE retraining, no architecture change, no new data collection (Track B), no C runtime edits. Plan honors all.
- **Type consistency:** `weighted_score(residuals, weight_vec)`, `benign_calibrated_weights(residuals, eps, cap_mult)`, `make_weight_vec(scoring, feature_names, attack_weight_dict, benign_residuals)`, `per_feature_residuals_from_windows(model, wins, batch)` — names identical across tasks. Residual arrays are always `(N, F)`; label alignment uses `[SEQ_LEN-1:]` consistently with `evaluate_per_ue_v2._pool_per_rnti`.
- **Leakage guard:** benign weights use only `val_res` (validation, benign); attack file is read once in Task 4 and never feeds weight/threshold calibration for uniform/benign.
