# AE Loss-Weighting Ablation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Retrain the per-UE AEs with a non-attack training loss (benign-scale, plus uniform baseline and Scheme-A control), then evaluate all variants under the leakage-free benign-calibrated scoring to see if the training-loss weighting matters.

**Architecture:** Add a `--loss-weights {schemea,uniform,benign}` switch to the training scripts (weights come from a shared, tested helper). A two-pass procedure produces the benign-scale loss weights (pass-1 uniform model → per-feature benign residual weights → pass-2). All 6 models (3 variants × GRU/LSTM) train with identical config; only the loss weighting differs. Evaluation reuses `evaluate_scoring_comparison.py`.

**Tech Stack:** Python 3.12 (`venv/bin/python3`), torch, numpy, scikit-learn, pytest.

**Reference spec:** `docs/superpowers/specs/2026-07-29-loss-ablation-design.md`

---

## Key facts (verified)

- `train_gru_ue.py` / `train_lstm_ue.py`: `train(...)` uses module global `fw = _FEATURE_WEIGHTS` (Scheme A) in `weighted_mse`. GRU val-loss is plain MSE; LSTM val-loss also uses `fw`. Both import `FEATURE_WEIGHTS as _FW_DICT` and `FEATURE_NAMES`.
- `main()` builds model from an inline `config`, fits `MinMaxScaler` on `df_to_raw(train)`, saves `<out>_scaler.pkl`, `<out>_threshold.json`, `<out>_losses.json`. `df_to_raw` filters `label==0` (via `load_csv`) and computes burst features on the whole df.
- `GRUAutoencoder.load(path, cfg)` / `LSTMAutoencoder.load(path, cfg)` classmethods exist; `GRU_CFG` / `LSTM_CFG` are exported from `evaluate_per_ue_v2.py` (seq_len 30). `model(x)` returns reconstruction `(B, seq, F)`.
- `src/detection/scoring.py` already has `benign_calibrated_weights(residuals)` and `per_feature_residuals_from_windows(model, wins)` (both tested / used).
- `evaluate_scoring_comparison.py` exports `pooled_data(model, scaler, by_rnti)` → `(res, lbl, rule)` and `evaluate_model(model, scaler, val_data, atk_data, target_fpr)` → `{threshold, rule_only, ml_only, hybrid, ...}`.
- Tests: `venv/bin/python3 -m pytest tests/ -v`. Data: `csv/dataset_training_ue_juni.csv` (benign), `csv/dataset_validation_ue_juni.csv` (benign), `csv/dataset_attack_ue_juni.csv`.

## File structure

- Modify: `src/detection/scoring.py` (+ `load_loss_weights`), `tests/test_scoring.py` (+ tests)
- Modify: `train_gru_ue.py`, `train_lstm_ue.py` (add `--loss-weights` / `--loss-weights-json`)
- Create: `derive_loss_weights.py` (pass-1 model → benign-scale loss-weight JSON)
- Create: `eval_loss_ablation.py` (evaluate 6 models → JSON + `docs/loss_ablation_results.md`)
- Create: `plot_loss_ablation.py` (figures → `eval_figures/loss_ablation/`)
- Output: `models/ablation_loss/` (6 models + 2 weight JSONs)

---

## Task 1: Shared loss-weight selector

**Files:** Modify `src/detection/scoring.py`; Test `tests/test_scoring.py`

- [ ] **Step 1: Write failing tests** — append to `tests/test_scoring.py`:

```python
import json as _json
from src.detection.scoring import load_loss_weights


def test_load_loss_weights_uniform():
    assert list(load_loss_weights("uniform", ["a", "b", "c"], {"a": 5.0})) == [1.0, 1.0, 1.0]


def test_load_loss_weights_schemea():
    w = load_loss_weights("schemea", ["a", "b"], {"a": 4.7, "b": 0.4})
    assert w[0] == pytest.approx(4.7) and w[1] == pytest.approx(0.4)


def test_load_loss_weights_benign_from_json(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(_json.dumps({"a": 2.0, "b": 3.0}))
    w = load_loss_weights("benign", ["a", "b"], {}, str(p))
    assert w[0] == pytest.approx(2.0) and w[1] == pytest.approx(3.0)


def test_load_loss_weights_benign_needs_json():
    with pytest.raises(ValueError):
        load_loss_weights("benign", ["a"], {})


def test_load_loss_weights_rejects_unknown():
    with pytest.raises(ValueError):
        load_loss_weights("bogus", ["a"], {})
```

- [ ] **Step 2: Run — expect FAIL** — `venv/bin/python3 -m pytest tests/test_scoring.py -k load_loss_weights -v` → ImportError.

- [ ] **Step 3: Implement** — append to `src/detection/scoring.py`:

```python
def load_loss_weights(mode: str, feature_names: list,
                      attack_weight_dict: dict, json_path: str = None) -> np.ndarray:
    """Training-loss weight vector for the ablation.

    mode: "uniform" (ones) | "schemea" (attack_weight_dict) | "benign" (from json_path).
    Returns (F,) float32 aligned to feature_names.
    """
    n = len(feature_names)
    if mode == "uniform":
        return np.ones(n, dtype=np.float32)
    if mode == "schemea":
        return np.array([attack_weight_dict.get(f, 1.0) for f in feature_names],
                        dtype=np.float32)
    if mode == "benign":
        if not json_path:
            raise ValueError("benign loss-weights requires json_path")
        import json
        with open(json_path) as f:
            d = json.load(f)
        return np.array([float(d[f]) for f in feature_names], dtype=np.float32)
    raise ValueError(f"unknown loss-weights mode: {mode!r}")
```

- [ ] **Step 4: Run — expect PASS** — `venv/bin/python3 -m pytest tests/test_scoring.py -v` (13 passed).

- [ ] **Step 5: Commit**

```bash
git add src/detection/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): add load_loss_weights selector for loss ablation"
```

---

## Task 2: Wire `--loss-weights` into both training scripts

**Files:** Modify `train_gru_ue.py`, `train_lstm_ue.py`

- [ ] **Step 1: `train_gru_ue.py` — make `train()` accept weights.** Change the signature and the `fw` line:

Replace `def train(model: GRUAutoencoder, train_norm: np.ndarray, val_seqs: np.ndarray,\n          epochs: int, batch_size: int, lr: float, checkpoint_path: str):`
with the same plus `, loss_weights=None):`, and replace `fw = _FEATURE_WEIGHTS` with:

```python
    fw = _FEATURE_WEIGHTS if loss_weights is None else loss_weights
```

- [ ] **Step 2: `train_gru_ue.py` — add CLI args + import.** After the existing `from src.detection.feature_schema_ue import (...)` block add:

```python
from src.detection.scoring import load_loss_weights
```

In `main()` argparse, after `--threshold-percentile`, add:

```python
    parser.add_argument("--loss-weights", choices=["schemea", "uniform", "benign"],
                        default="schemea")
    parser.add_argument("--loss-weights-json", type=str, default=None)
```

- [ ] **Step 3: `train_gru_ue.py` — build + pass the tensor.** Immediately before the `train(` call in `main()`, insert:

```python
    loss_w = torch.tensor(
        load_loss_weights(args.loss_weights, FEATURE_NAMES, _FW_DICT, args.loss_weights_json),
        dtype=torch.float32)
    print(f"[*] Loss weighting: {args.loss_weights}")
```

and add `loss_weights=loss_w,` to the `train(...)` call arguments.

- [ ] **Step 4: Repeat Steps 1–3 for `train_lstm_ue.py`** (identical edits: `train()` gets `loss_weights=None` and `fw = _FEATURE_WEIGHTS if loss_weights is None else loss_weights`; same import; same two argparse lines; same `loss_w` construction and `loss_weights=loss_w` in the `train(...)` call). LSTM's val-loss uses `fw` too, so it follows automatically.

- [ ] **Step 5: Smoke-check both import + parse** —

Run: `venv/bin/python3 -c "import train_gru_ue, train_lstm_ue; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add train_gru_ue.py train_lstm_ue.py
git commit -m "feat(train): add --loss-weights {schemea,uniform,benign} to per-UE trainers"
```

---

## Task 3: Benign-scale loss-weight deriver

**Files:** Create `derive_loss_weights.py`

- [ ] **Step 1: Create `derive_loss_weights.py`:**

```python
#!/usr/bin/env python3
"""Derive benign-scale loss weights from a pass-1 (uniform-loss) AE.

w_j = 1/(median(e_j)+MAD(e_j)+eps), capped — from per-feature benign TRAINING
residuals. Frozen constants (no attack info). See loss-ablation design spec.
"""
import argparse, json, pickle
import numpy as np

from evaluate_per_ue_v2 import GRU_CFG, LSTM_CFG, build_windows
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.lstm_autoencoder import LSTMAutoencoder
from src.detection.feature_schema_ue import FEATURE_NAMES
from src.detection.scoring import benign_calibrated_weights, per_feature_residuals_from_windows
from train_gru_ue import load_csv, df_to_raw   # same feature pipeline as training (label==0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", choices=["gru", "lstm"], required=True)
    ap.add_argument("--model", required=True)     # pass-1 uniform model .pt
    ap.add_argument("--scaler", required=True)
    ap.add_argument("--train", default="csv/dataset_training_ue_juni.csv")
    ap.add_argument("--seq-len", type=int, default=30)
    ap.add_argument("--out", required=True)        # weights JSON
    args = ap.parse_args()

    cfg = GRU_CFG if args.arch == "gru" else LSTM_CFG
    Model = GRUAutoencoder if args.arch == "gru" else LSTMAutoencoder
    model = Model.load(args.model, cfg)
    with open(args.scaler, "rb") as f:
        scaler = pickle.load(f)

    raw = df_to_raw(load_csv(args.train))                      # benign training only
    wins = build_windows(scaler.transform(raw).astype("float32"), args.seq_len)
    res = per_feature_residuals_from_windows(model, wins)      # (N, F)
    w = benign_calibrated_weights(res)                         # (F,)

    d = {f: float(w[i]) for i, f in enumerate(FEATURE_NAMES)}
    with open(args.out, "w") as f:
        json.dump(d, f, indent=2)
    print(f"[derive:{args.arch}] wrote {args.out}")
    print("  weights:", {k: round(v, 4) for k, v in d.items()})


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-check import** — `venv/bin/python3 -c "import derive_loss_weights"` → exit 0.

- [ ] **Step 3: Commit**

```bash
git add derive_loss_weights.py
git commit -m "feat(train): add benign-scale loss-weight deriver (two-pass)"
```

---

## Task 4: Train the 6 ablation models

**Files:** produces `models/ablation_loss/*` (no source changes)

Common args: `--train csv/dataset_training_ue_juni.csv --val csv/dataset_validation_ue_juni.csv --seq-len 30 --epochs 200`. Run from repo root with `venv/bin/python3`.

- [ ] **Step 1: Create output dir** — `mkdir -p models/ablation_loss`

- [ ] **Step 2: GRU — uniform (pass-1), then derive, then benign (pass-2), then schemea:**

```bash
venv/bin/python3 train_gru_ue.py --train csv/dataset_training_ue_juni.csv --val csv/dataset_validation_ue_juni.csv --seq-len 30 --epochs 200 --loss-weights uniform --model-out models/ablation_loss/gru_ue_lossuniform.pt
venv/bin/python3 derive_loss_weights.py --arch gru --model models/ablation_loss/gru_ue_lossuniform.pt --scaler models/ablation_loss/gru_ue_lossuniform_scaler.pkl --out models/ablation_loss/gru_ue_lossbenign_weights.json
venv/bin/python3 train_gru_ue.py --train csv/dataset_training_ue_juni.csv --val csv/dataset_validation_ue_juni.csv --seq-len 30 --epochs 200 --loss-weights benign --loss-weights-json models/ablation_loss/gru_ue_lossbenign_weights.json --model-out models/ablation_loss/gru_ue_lossbenign.pt
venv/bin/python3 train_gru_ue.py --train csv/dataset_training_ue_juni.csv --val csv/dataset_validation_ue_juni.csv --seq-len 30 --epochs 200 --loss-weights schemea --model-out models/ablation_loss/gru_ue_lossschemea.pt
```

Expected: each training prints `[*] Loss weighting: <mode>` and ends saving `.pt`, `_scaler.pkl`, `_threshold.json`.

- [ ] **Step 3: LSTM — same four commands** with `train_lstm_ue.py`, `--arch lstm`, and `models/ablation_loss/lstm_ue_loss{uniform,benign,schemea}.pt` / `lstm_ue_lossbenign_weights.json`.

- [ ] **Step 4: Verify 6 models exist** —

Run: `ls models/ablation_loss/*.pt | wc -l`
Expected: `6`

- [ ] **Step 5: Commit weights JSON + threshold/loss metadata (not the large .pt if gitignored; check `git status`)**

```bash
git add models/ablation_loss/*.json 2>/dev/null; git add -f models/ablation_loss/*.pt models/ablation_loss/*.pkl 2>/dev/null
git commit -m "chore(models): trained 6 loss-ablation AEs (uniform/benign/schemea x GRU/LSTM)"
```

---

## Task 5: Evaluate all variants (benign-calibrated scoring)

**Files:** Create `eval_loss_ablation.py`; writes `docs/loss_ablation_results.md` + `results/loss_ablation/`

- [ ] **Step 1: Create `eval_loss_ablation.py`:**

```python
#!/usr/bin/env python3
"""Evaluate the 6 loss-ablation models under leakage-free benign-calibrated scoring.

Each model: benign-calibrated weights from its own validation residuals, threshold
calibrated so Hybrid FPR(Attack) < 3%. Reports Rule/ML/Hybrid metrics per variant.
"""
import json, os, pickle
import numpy as np

from evaluate_per_ue_v2 import (load_csv, preprocess_rows, split_by_rnti,
                                GRU_CFG, LSTM_CFG)
from evaluate_scoring_comparison import pooled_data, evaluate_model, TARGET_FPR_ATTACK
from src.detection.gru_autoencoder import GRUAutoencoder
from src.detection.lstm_autoencoder import LSTMAutoencoder

VARIANTS = ["uniform", "benign", "schemea"]
OUT = "results/loss_ablation"


def load_variant(arch, variant):
    base = f"models/ablation_loss/{arch}_ue_loss{variant}"
    cfg = GRU_CFG if arch == "gru" else LSTM_CFG
    Model = GRUAutoencoder if arch == "gru" else LSTMAutoencoder
    model = Model.load(f"{base}.pt", cfg)
    with open(f"{base}_scaler.pkl", "rb") as f:
        scaler = pickle.load(f)
    return model, scaler


def main():
    os.makedirs(OUT, exist_ok=True)
    val_rows = load_csv("csv/dataset_validation_ue_juni.csv"); preprocess_rows(val_rows)
    atk_rows = load_csv("csv/dataset_attack_ue_juni.csv"); preprocess_rows(atk_rows)
    val_by, atk_by = split_by_rnti(val_rows), split_by_rnti(atk_rows)

    results = {}
    for arch in ["gru", "lstm"]:
        results[arch] = {}
        for v in VARIANTS:
            model, scaler = load_variant(arch, v)
            val_data = pooled_data(model, scaler, val_by)
            atk_data = pooled_data(model, scaler, atk_by)
            results[arch][v] = evaluate_model(model, scaler, val_data, atk_data,
                                              TARGET_FPR_ATTACK)
            print(f"[{arch}:{v}] hybrid recall={results[arch][v]['hybrid']['recall']:.4f}")

    with open(os.path.join(OUT, "loss_ablation.json"), "w") as f:
        json.dump(results, f, indent=2)

    lines = ["# Loss-weighting ablation — benign-calibrated scoring @ Hybrid FPR(Attack) < 3%\n",
             "## Hybrid metrics by training-loss variant",
             "| Model | Loss | Recall | Precision | F1 | FPR(Attack) | FPR(Val) | AUC(ML) |",
             "|---|---|---|---|---|---|---|---|"]
    for arch in ["gru", "lstm"]:
        for v in VARIANTS:
            h = results[arch][v]["hybrid"]; ml = results[arch][v]["ml_only"]
            lines.append(f"| {arch.upper()} | {v} | {h['recall']*100:.2f}% | "
                         f"{h['precision']*100:.2f}% | {h['f1']*100:.2f}% | "
                         f"{h['fpr_attack']*100:.2f}% | {h['fpr_val']*100:.2f}% | "
                         f"{ml['auc']:.4f} |")
    md = "\n".join(lines) + "\n"
    with open("docs/loss_ablation_results.md", "w") as f:
        f.write(md)
    print("\n" + md)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run** — `venv/bin/python3 eval_loss_ablation.py`
Expected: prints hybrid recall for all 6, writes `results/loss_ablation/loss_ablation.json` and `docs/loss_ablation_results.md` with a 6-row table. Sanity: all FPR(Attack) ≈ 2.9–3.0%; AUC in (0.5,1).

- [ ] **Step 3: Append interpretation to `docs/loss_ablation_results.md`** — 3–5 sentences applying spec §7 decision rule (compare `benign` vs `uniform` vs `schemea`; recommend the cleanest that meets target), plus the training-loss-leakage-closed / model-selection caveat.

- [ ] **Step 4: Commit**

```bash
git add eval_loss_ablation.py docs/loss_ablation_results.md results/loss_ablation/
git commit -m "feat(eval): loss-ablation evaluation table + interpretation"
```

---

## Task 6: Ablation figures

**Files:** Create `plot_loss_ablation.py`; writes `eval_figures/loss_ablation/`

- [ ] **Step 1: Create `plot_loss_ablation.py`** — grouped bars of Hybrid **Recall** and **F1** across the 3 loss variants, one group per model; a second figure of **per-class recall** (Hybrid) per variant. Reads `results/loss_ablation/loss_ablation.json`. Okabe-Ito palette (`uniform` #999999, `benign` #009E73, `schemea` #E69F00); bar labels; legend; `matplotlib.use("Agg")`; save at dpi 150.

```python
#!/usr/bin/env python3
"""Figures for the loss-weighting ablation → eval_figures/loss_ablation/."""
import json, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = "eval_figures/loss_ablation"; os.makedirs(OUT, exist_ok=True)
COL = {"uniform": "#999999", "benign": "#009E73", "schemea": "#E69F00"}
VARIANTS = ["uniform", "benign", "schemea"]
R = json.load(open("results/loss_ablation/loss_ablation.json"))
plt.rcParams.update({"font.size": 10.5, "axes.titlesize": 12, "figure.dpi": 150})


def grouped(metric, ylabel, fname, title):
    archs = ["gru", "lstm"]; x = np.arange(len(archs)); w = 0.26
    fig, ax = plt.subplots(figsize=(7.5, 5))
    for i, v in enumerate(VARIANTS):
        vals = [R[a][v]["hybrid"][metric] * 100 for a in archs]
        b = ax.bar(x + (i - 1) * w, vals, w, color=COL[v], label=v)
        ax.bar_label(b, fmt="%.1f", fontsize=8, padding=2)
    ax.set_xticks(x); ax.set_xticklabels(["GRU v5", "LSTM v6"])
    ax.set_ylabel(ylabel); ax.set_ylim(0, 108); ax.set_title(title, fontweight="bold")
    ax.legend(fontsize=9, loc="lower right"); ax.grid(axis="y", alpha=0.3)
    fig.savefig(os.path.join(OUT, fname), dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"[FIG] {OUT}/{fname}")


grouped("recall", "Hybrid Recall (%)", "loss_ablation_recall.png",
        "Loss-weighting ablation — Hybrid Recall @ FPR(Attack)<3%")
grouped("f1", "Hybrid F1 (%)", "loss_ablation_f1.png",
        "Loss-weighting ablation — Hybrid F1 @ FPR(Attack)<3%")

# Per-class recall (Hybrid), one panel per model
classes = ["ul_flood", "dl_flood", "burst", "roq"]
for arch in ["gru", "lstm"]:
    x = np.arange(len(classes)); w = 0.26
    fig, ax = plt.subplots(figsize=(8, 5))
    for i, v in enumerate(VARIANTS):
        pc = R[arch][v]["hybrid"]["per_class_recall"]
        vals = [(pc[c] or 0.0) * 100 for c in classes]
        b = ax.bar(x + (i - 1) * w, vals, w, color=COL[v], label=v)
        ax.bar_label(b, fmt="%.0f", fontsize=8, padding=2)
    ax.set_xticks(x); ax.set_xticklabels([c.replace("_", " ").title() for c in classes])
    ax.set_ylabel("Recall (%)"); ax.set_ylim(0, 112)
    ax.set_title(f"Per-class recall (Hybrid) — {arch.upper()} loss ablation", fontweight="bold")
    ax.legend(fontsize=9, ncol=3, loc="lower left"); ax.grid(axis="y", alpha=0.3)
    fig.savefig(os.path.join(OUT, f"loss_ablation_per_class_{arch}.png"),
                dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"[FIG] {OUT}/loss_ablation_per_class_{arch}.png")
```

- [ ] **Step 2: Run** — `venv/bin/python3 plot_loss_ablation.py` → 4 figures. Open one to eyeball.

- [ ] **Step 3: Commit**

```bash
git add plot_loss_ablation.py eval_figures/loss_ablation/
git commit -m "feat(figures): loss-weighting ablation figures"
```

---

## Self-review notes

- **Spec coverage:** two-pass (§3)→Tasks 3–4; 3-variant matched pair (§4)→Tasks 2,4; naming (§5)→Task 4; benign-cal eval + Hybrid FPR<3% (§6)→Task 5; decision rule (§7)→Task 5 Step 3; impl surface (§8)→Tasks 1–6; success criteria (§9)→Task 4 Step 4 + Task 5; limitations (§10)→Task 5 Step 3.
- **Placeholder scan:** none — every step has concrete code/commands.
- **Type consistency:** `load_loss_weights(mode, feature_names, attack_weight_dict, json_path)` used identically in both train scripts; `pooled_data`/`evaluate_model` reused with their real signatures; weights JSON keyed by `FEATURE_NAMES` and read back by same order.
- **Leakage guard:** benign loss weights derived only from benign training residuals; benign-cal scoring weights from benign validation; attack file only ever enters final evaluation.
