# Dual-LSTM Ensemble Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `DualLSTMDetector` to `evaluate_detection.py` combining v16 (thresh=0.21) and v22 (thresh=0.5) with 2-of-3 window voting to hit UL Flood ≥80%, RRC Storm ≥80%, FPR <5%.

**Architecture:** A `DualLSTMDetector` wraps two `LSTMDetector` instances. Each model produces a raw ONNX score per window. A 3-element rolling vote buffer per model triggers on 2-of-3 windows exceeding that model's threshold. The dual detector fires if either model votes true. `run_evaluation` gains a `--dual` flag that swaps in `DualLSTMDetector` in place of the single `LSTMDetector`.

**Tech Stack:** Python 3.12, onnxruntime, numpy, existing `evaluate_detection.py` patterns.

---

## File Map

| File | Change |
|------|--------|
| `evaluate_detection.py` | Add `DualLSTMDetector` class after `LSTMDetector`; extend `run_evaluation` with dual mode; add `--dual`, `--model-a`, `--thresh-a`, `--model-b`, `--thresh-b` CLI args |
| `tests/test_dual_lstm.py` | New — unit tests for voting logic |

---

### Task 1: Unit tests for 2-of-3 voting logic

**Files:**
- Create: `tests/test_dual_lstm.py`

- [ ] **Step 1: Create tests directory and test file**

```bash
mkdir -p tests
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_dual_lstm.py`:

```python
"""Unit tests for DualLSTMDetector 2-of-3 voting."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import after path setup — DualLSTMDetector does not exist yet
from evaluate_detection import _vote_2of3


def test_vote_false_when_buffer_empty():
    buf = [False, False, False]
    assert _vote_2of3(buf) is False


def test_vote_false_when_only_one_true():
    buf = [True, False, False]
    assert _vote_2of3(buf) is False


def test_vote_true_when_two_true():
    buf = [True, True, False]
    assert _vote_2of3(buf) is True


def test_vote_true_when_all_true():
    buf = [True, True, True]
    assert _vote_2of3(buf) is True


def test_vote_true_non_consecutive():
    buf = [True, False, True]
    assert _vote_2of3(buf) is True


if __name__ == "__main__":
    test_vote_false_when_buffer_empty()
    test_vote_false_when_only_one_true()
    test_vote_true_when_two_true()
    test_vote_true_when_all_true()
    test_vote_true_non_consecutive()
    print("All tests passed.")
```

- [ ] **Step 3: Run tests — expect ImportError (`_vote_2of3` not defined yet)**

```bash
/home/telmat/xapp/security-xapp/venv/bin/python3 tests/test_dual_lstm.py
```

Expected output contains: `ImportError` or `cannot import name '_vote_2of3'`

- [ ] **Step 4: Commit skeleton**

```bash
git add tests/test_dual_lstm.py
git commit -m "test: add failing unit tests for DualLSTMDetector 2-of-3 voting"
```

---

### Task 2: Add `_vote_2of3` helper and `DualLSTMDetector` class

**Files:**
- Modify: `evaluate_detection.py` — insert after `LSTMDetector` class (after line ~398)

- [ ] **Step 1: Add `_vote_2of3` helper and `DualLSTMDetector` immediately after the closing of `LSTMDetector`**

Insert this block after the `LSTMDetector` class (after the line `return sev, score`):

```python

def _vote_2of3(buf):
    """Return True if at least 2 of the 3 booleans in buf are True."""
    return sum(buf) >= 2


class DualLSTMDetector:
    """
    Combines two LSTMDetector models with independent 2-of-3 window voting.

    Model A (v16, thresh=0.21): tuned for UL/DL Flood.
    Model B (v22, thresh=0.50): tuned for RRC Storm.

    Alert fires if either model's vote buffer has ≥2 of last 3 windows above
    its threshold. Uses raw ONNX scores — ignores LSTMDetector's internal
    severity/anomaly_cnt.
    """

    def __init__(self, model_a, thresh_a, model_b, thresh_b,
                 seq_len=WINDOW_SIZE, num_features=None):
        self.det_a   = LSTMDetector(model_a, threshold=thresh_a,
                                    seq_len=seq_len, num_features=num_features)
        self.det_b   = LSTMDetector(model_b, threshold=thresh_b,
                                    seq_len=seq_len, num_features=num_features)
        self.thresh_a = thresh_a
        self.thresh_b = thresh_b
        self._buf_a   = [False, False, False]
        self._buf_b   = [False, False, False]

    def update(self, row, now_ms):
        """
        Returns (severity, combined_score, score_a, score_b).
        severity = 1 if either model votes anomaly, else 0.
        combined_score = max(score_a, score_b).
        """
        _, score_a = self.det_a.update(row, now_ms)
        _, score_b = self.det_b.update(row, now_ms)

        self._buf_a = self._buf_a[1:] + [score_a > self.thresh_a]
        self._buf_b = self._buf_b[1:] + [score_b > self.thresh_b]

        vote = _vote_2of3(self._buf_a) or _vote_2of3(self._buf_b)
        sev  = 1 if vote else 0
        return sev, max(score_a, score_b), score_a, score_b
```

- [ ] **Step 2: Run unit tests — expect all pass**

```bash
/home/telmat/xapp/security-xapp/venv/bin/python3 tests/test_dual_lstm.py
```

Expected: `All tests passed.`

- [ ] **Step 3: Commit**

```bash
git add evaluate_detection.py tests/test_dual_lstm.py
git commit -m "feat: add DualLSTMDetector with 2-of-3 window voting"
```

---

### Task 3: Wire `DualLSTMDetector` into `run_evaluation`

**Files:**
- Modify: `evaluate_detection.py:471` — `run_evaluation` signature and loop

- [ ] **Step 1: Update `run_evaluation` signature to accept dual-mode params**

Replace:
```python
def run_evaluation(csv_path, onnx_path, output_path=None, seq_len=WINDOW_SIZE, num_features=None):
    print(f"Loading dataset: {csv_path}")
    rows = load_csv(csv_path)
    print(f"  {len(rows)} rows, labels: { {int(l): sum(1 for r in rows if int(r['label'])==l) for l in sorted(set(int(r['label']) for r in rows))} }")

    print(f"\nLoading ONNX model: {onnx_path}  (seq_len={seq_len})")
    ids  = RuleBasedIDS()
    lstm = LSTMDetector(onnx_path, seq_len=seq_len, num_features=num_features)
```

With:
```python
def run_evaluation(csv_path, onnx_path, output_path=None, seq_len=WINDOW_SIZE, num_features=None,
                   dual=False, model_a=None, thresh_a=0.21, model_b=None, thresh_b=0.5):
    print(f"Loading dataset: {csv_path}")
    rows = load_csv(csv_path)
    print(f"  {len(rows)} rows, labels: { {int(l): sum(1 for r in rows if int(r['label'])==l) for l in sorted(set(int(r['label']) for r in rows))} }")

    ids = RuleBasedIDS()
    if dual:
        print(f"\nDual-LSTM Ensemble  (seq_len={seq_len})")
        print(f"  Model A: {model_a}  thresh={thresh_a}")
        print(f"  Model B: {model_b}  thresh={thresh_b}")
        lstm = DualLSTMDetector(model_a, thresh_a, model_b, thresh_b,
                                seq_len=seq_len, num_features=num_features)
    else:
        print(f"\nLoading ONNX model: {onnx_path}  (seq_len={seq_len})")
        lstm = LSTMDetector(onnx_path, seq_len=seq_len, num_features=num_features)
```

- [ ] **Step 2: Update the per-row loop to handle both detector return shapes**

Replace the loop body:
```python
    for r in rows:
        now_ms  = int(r["timestamp_ms"])
        label   = int(r["label"])
        rsev, _ = ids.detect(r, now_ms)
        lsev, score = lstm.update(r, now_ms)
        fsev = max(rsev, lsev)

        labels.append(label)
        rule_sev.append(rsev)
        lstm_sev.append(lsev)
        final_sev.append(fsev)
        lstm_scores.append(score)
```

With:
```python
    for r in rows:
        now_ms  = int(r["timestamp_ms"])
        label   = int(r["label"])
        rsev, _ = ids.detect(r, now_ms)
        result  = lstm.update(r, now_ms)
        lsev    = result[0]
        score   = result[1]
        fsev    = max(rsev, lsev)

        labels.append(label)
        rule_sev.append(rsev)
        lstm_sev.append(lsev)
        final_sev.append(fsev)
        lstm_scores.append(score)
```

- [ ] **Step 3: Smoke-test single-model mode still works**

```bash
/home/telmat/xapp/security-xapp/venv/bin/python3 evaluate_detection.py \
  --csv csv/dataset_attack_mei.csv \
  --model security_model_v16.onnx \
  --seq-len 10 --num-features 25 2>&1 | grep -E "Recall|FPR|RRC|UL Flood"
```

Expected: same numbers as before (v16 single-model results unchanged).

- [ ] **Step 4: Commit**

```bash
git add evaluate_detection.py
git commit -m "feat: wire DualLSTMDetector into run_evaluation with dual= flag"
```

---

### Task 4: Add `--dual` CLI arguments

**Files:**
- Modify: `evaluate_detection.py:625` — argparse block

- [ ] **Step 1: Replace the `__main__` argparse block**

Replace:
```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv",     default=DEFAULT_CSV,  help="Path ke dataset CSV")
    parser.add_argument("--model",   default=ONNX_MODEL,   help="Path ke ONNX model")
    parser.add_argument("--seq-len", default=WINDOW_SIZE, type=int, help="LSTM window size (default: 10)")
    parser.add_argument("--output",       default=None, help="Tulis hasil evaluasi ke JSON (opsional)")
    parser.add_argument("--num-features", default=None, type=int, help="Gunakan N fitur pertama (default: semua 27)")
    args = parser.parse_args()
    run_evaluation(args.csv, args.model, output_path=args.output, seq_len=args.seq_len, num_features=args.num_features)
```

With:
```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv",          default=DEFAULT_CSV,  help="Path ke dataset CSV")
    parser.add_argument("--model",        default=ONNX_MODEL,   help="Path ke ONNX model (single mode)")
    parser.add_argument("--seq-len",      default=WINDOW_SIZE,  type=int,   help="LSTM window size (default: 10)")
    parser.add_argument("--output",       default=None,                     help="Tulis hasil evaluasi ke JSON (opsional)")
    parser.add_argument("--num-features", default=None,          type=int,   help="Gunakan N fitur pertama (default: semua 27)")
    parser.add_argument("--dual",         action="store_true",              help="Pakai DualLSTMDetector (model-a + model-b)")
    parser.add_argument("--model-a",      default="security_model_v16.onnx", help="ONNX model A (default: v16)")
    parser.add_argument("--thresh-a",     default=0.21,          type=float, help="Threshold model A (default: 0.21)")
    parser.add_argument("--model-b",      default="security_model_v22.onnx", help="ONNX model B (default: v22)")
    parser.add_argument("--thresh-b",     default=0.5,           type=float, help="Threshold model B (default: 0.5)")
    args = parser.parse_args()
    run_evaluation(
        args.csv, args.model,
        output_path=args.output,
        seq_len=args.seq_len,
        num_features=args.num_features,
        dual=args.dual,
        model_a=args.model_a, thresh_a=args.thresh_a,
        model_b=args.model_b, thresh_b=args.thresh_b,
    )
```

- [ ] **Step 2: Run unit tests — still passing**

```bash
/home/telmat/xapp/security-xapp/venv/bin/python3 tests/test_dual_lstm.py
```

Expected: `All tests passed.`

- [ ] **Step 3: Commit**

```bash
git add evaluate_detection.py
git commit -m "feat: add --dual CLI mode with --model-a/b and --thresh-a/b args"
```

---

### Task 5: Run dual evaluation and verify targets

**Files:** none — evaluation only

- [ ] **Step 1: Run dual evaluation on attack dataset**

```bash
/home/telmat/xapp/security-xapp/venv/bin/python3 evaluate_detection.py \
  --csv csv/dataset_attack_mei.csv \
  --dual \
  --model-a security_model_v16.onnx --thresh-a 0.21 \
  --model-b security_model_v22.onnx --thresh-b 0.5 \
  --seq-len 10 --num-features 25 2>&1 | tee /tmp/eval_dual.log
```

- [ ] **Step 2: Check targets are met**

```bash
grep -E "UL Flood|DL Flood|RRC Storm|Normal.*[0-9]+\.[0-9]+%|LSTM FP|ROC AUC|Recall|F1|Precision" /tmp/eval_dual.log
```

Targets to verify:
- UL Flood LSTM recall ≥ 80%
- DL Flood LSTM recall ≥ 80%
- RRC Storm LSTM recall ≥ 80%
- LSTM FP (Stage1+) ≤ 5%
- LSTM ROC AUC (raw score) ≥ 0.90
- LSTM Precision ≥ 0.90
- LSTM F1 ≥ 0.90

If FPR > 5%: raise `--thresh-a` by 0.01 and re-run. Note: threshold 0.22+ causes UL Flood to drop — see cliff in design doc.

- [ ] **Step 3: Commit results log**

```bash
cp /tmp/eval_dual.log docs/eval_dual_v16_v22.log
git add docs/eval_dual_v16_v22.log
git commit -m "eval: dual LSTM ensemble v16+v22 evaluation results"
```

---

### Task 6: Add per-model score columns to LSTM score stats section

**Files:**
- Modify: `evaluate_detection.py:598` — score statistics print block

- [ ] **Step 1: Track score_a and score_b arrays when in dual mode**

In `run_evaluation`, add two new arrays alongside `lstm_scores`. After:
```python
    labels      = []
    rule_sev    = []
    lstm_sev    = []
    final_sev   = []
    lstm_scores = []
```

Add:
```python
    scores_a    = []
    scores_b    = []
```

In the loop, after `score = result[1]`, add:
```python
        if dual:
            scores_a.append(result[2])
            scores_b.append(result[3])
        else:
            scores_a.append(score)
            scores_b.append(score)
```

After `lstm_scores = np.array(lstm_scores)`, add:
```python
    scores_a = np.array(scores_a)
    scores_b = np.array(scores_b)
```

- [ ] **Step 2: Print per-model score table when in dual mode**

After the existing LSTM Score Statistics block (after line ~613), add:

```python
    if dual:
        for tag, sc_arr, thresh in [("Model-A (v16)", scores_a, thresh_a),
                                     ("Model-B (v22)", scores_b, thresh_b)]:
            print(f"\n{'='*55}")
            print(f"  {tag} Score Statistics  (threshold={thresh:.3f})")
            print(f"{'='*55}")
            print(f"  {'Label':<18} {'Mean':>9} {'P50':>9} {'P95':>9} {'>Thresh':>8}")
            print(f"  {'-'*54}")
            for lbl in sorted(LABEL_NAMES):
                mask = labels == lbl
                if mask.sum() == 0:
                    continue
                sc    = sc_arr[mask]
                above = (sc > thresh).sum()
                print(f"  {LABEL_NAMES[lbl]:<18} "
                      f"{sc.mean():>9.6f} {np.percentile(sc,50):>9.6f} "
                      f"{np.percentile(sc,95):>9.6f} {above/len(sc):>7.1%}")
```

- [ ] **Step 3: Run dual eval again, verify per-model score tables appear**

```bash
/home/telmat/xapp/security-xapp/venv/bin/python3 evaluate_detection.py \
  --csv csv/dataset_attack_mei.csv \
  --dual --model-a security_model_v16.onnx --thresh-a 0.21 \
  --model-b security_model_v22.onnx --thresh-b 0.5 \
  --seq-len 10 --num-features 25 2>&1 | grep -A 20 "Model-A"
```

Expected: two score tables labeled "Model-A (v16)" and "Model-B (v22)".

- [ ] **Step 4: Run unit tests one final time**

```bash
/home/telmat/xapp/security-xapp/venv/bin/python3 tests/test_dual_lstm.py
```

Expected: `All tests passed.`

- [ ] **Step 5: Final commit**

```bash
git add evaluate_detection.py
git commit -m "feat: show per-model score tables in dual LSTM evaluation output"
```
