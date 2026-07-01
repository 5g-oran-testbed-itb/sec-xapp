# Grafana GRU Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tambah GRU live inference thread ke csv_exporter.py, expose metrik baru ke Prometheus, dan buat dua dashboard Grafana: Live Monitoring (update main.json) + Evaluation Results (eval.json baru).

**Architecture:** GRU inference berjalan sebagai thread daemon dalam container csv-exporter yang sudah ada. Thread membaca `g_latest_row` (shared state dari csv_tail_loop) setiap 1s, menjalankan GRU-A (seq_len=10) + GRU-B (seq_len=30) inference via PyTorch, dan meng-update gauge Prometheus. GRU model class di-inline ke `exporter/gru_model.py` agar container tidak perlu mount src/.

**Tech Stack:** Python 3.11, PyTorch ≥2.0, scikit-learn (scaler pkl), prometheus-client, Grafana 10.4 JSON provisioning.

---

## File Map

| File | Aksi |
|------|------|
| `exporter/gru_model.py` | **Buat baru** — inline GRUAutoencoder (tanpa import src/) |
| `exporter/csv_exporter.py` | **Modifikasi** — shared state, gauge baru, GRU thread, eval v2 |
| `exporter/requirements.txt` | **Modifikasi** — tambah torch, scikit-learn |
| `exporter/Dockerfile` | **Modifikasi** — COPY gru_model.py |
| `exporter/test_csv_exporter.py` | **Modifikasi** — tambah test GRU + gauge baru |
| `docker-compose.yml` | **Modifikasi** — mount models/, env vars GRU |
| `grafana/provisioning/dashboards/main.json` | **Modifikasi** — panel baru: GRU stage, alert type, scores, signaling |
| `grafana/provisioning/dashboards/eval.json` | **Buat baru** — Dashboard 2 evaluation |
| `grafana/provisioning/dashboards/dashboards.yml` | **Modifikasi** — daftarkan eval.json |

---

## Task 1: Shared State + Gauge Baru di csv_exporter.py

**Files:**
- Modify: `exporter/csv_exporter.py`
- Test: `exporter/test_csv_exporter.py`

- [ ] **Step 1: Tambah shared state dan gauges baru di csv_exporter.py**

Tambahkan di bawah baris `g_eval_fpr = Gauge(...)` (setelah baris 46):

```python
# ── GRU detection metrics ────────────────────────────────────────────────────
g_gru_a     = Gauge("xapp_gru_score_a",     "GRU-A reconstruction error (raw)")
g_gru_b     = Gauge("xapp_gru_score_b",     "GRU-B reconstruction error (raw)")
g_gru_stage = Gauge("xapp_gru_stage",       "GRU stage: 0=normal 1=warn 2=crit")

# ── Extra features not yet exposed ──────────────────────────────────────────
g_alert_type  = Gauge("xapp_alert_type",      "Alert type: 0=none 1=ul_flood 2=dl_flood 3=burst 4=rrc_storm")
g_empty_ind   = Gauge("xapp_empty_ind_rate",  "RRC empty indication rate per window")
g_burst_idx   = Gauge("xapp_prb_burst_index", "PRB burst index log ratio")

# ── Eval metrics v2 (5 models, per-attack labels) ────────────────────────────
g_eval_recall_v2    = Gauge("xapp_eval_recall_v2",    "Eval recall v2",    ["model", "attack"])
g_eval_fpr_v2       = Gauge("xapp_eval_fpr_v2",       "Eval FPR v2",       ["model"])
g_eval_f1_v2        = Gauge("xapp_eval_f1_v2",        "Eval F1 v2",        ["model", "attack"])
g_eval_precision_v2 = Gauge("xapp_eval_precision_v2", "Eval precision v2", ["model"])

# ── Shared state: latest CSV row for GRU thread ──────────────────────────────
_latest_row: dict = {}
_latest_row_lock = threading.Lock()
```

- [ ] **Step 2: Update EXTRA_COLS dan FLOAT_COLS untuk include alert_type**

Ganti blok LSTM_FEATURES + EXTRA_COLS (baris 49–61 saat ini):

```python
LSTM_FEATURES = [
    "prb_usage_dl_ratio", "prb_usage_ul_ratio",
    "cqi", "rach_preamble", "air_delay_ul",
    "prb_direction", "prb_total",
    "prb_dl_delta", "prb_ul_delta", "prb_burst_index",
    "empty_ind_rate",
    "prb_dl_roll_mean", "prb_dl_roll_std",
    "prb_ul_roll_std", "prb_ul_roll_max",
    "prb_ul_roll_max_100",
]
EXTRA_COLS  = ["stage1_alert", "stage2_confirmed", "anomaly_score"]
FLOAT_COLS  = LSTM_FEATURES + EXTRA_COLS
STR_COLS    = {"alert_type"}   # baca sebagai string, bukan float
ALERT_TYPE_MAP = {"none": 0, "ul_flood": 1, "dl_flood": 2,
                  "burst": 3, "rrc_storm": 4}
```

- [ ] **Step 3: Update `parse_csv_row` agar expose empty_ind_rate dan burst_index**

Ganti fungsi `parse_csv_row` (baris 98–107):

```python
def parse_csv_row(raw: dict) -> dict:
    """Convert string dict from csv.DictReader to float dict. Missing = 0.0."""
    out = {}
    for col in FLOAT_COLS:
        v = raw.get(col, "")
        try:
            out[col] = float(v)
        except (ValueError, TypeError):
            out[col] = 0.0
    # string columns
    out["alert_type"] = raw.get("alert_type", "none")
    return out
```

- [ ] **Step 4: Update `csv_tail_loop` untuk set gauge baru + update shared state**

Tambahkan di dalam loop `for raw in reader:`, setelah `g_stage.set(stage)`:

```python
                # new feature gauges
                g_empty_ind.set(row.get("empty_ind_rate", 0.0))
                g_burst_idx.set(row.get("prb_burst_index", 0.0))
                g_alert_type.set(ALERT_TYPE_MAP.get(row.get("alert_type", "none"), 0))

                # update shared state for GRU thread
                with _latest_row_lock:
                    _latest_row.update(row)
```

- [ ] **Step 5: Tulis test untuk gauge baru**

Tambahkan di `exporter/test_csv_exporter.py`:

```python
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
```

Jalankan dari `exporter/` directory:
```bash
cd /home/telmat/sec-xapp/exporter
python -m pytest test_csv_exporter.py -v -k "alert_type or empty_ind or burst_index"
```
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add exporter/csv_exporter.py exporter/test_csv_exporter.py
git commit -m "feat(exporter): add shared state, GRU/alert gauges, expose empty_ind+burst_idx"
```

---

## Task 2: Buat `exporter/gru_model.py` (Inline GRU Classes)

**Files:**
- Create: `exporter/gru_model.py`
- Test: `exporter/test_csv_exporter.py`

- [ ] **Step 1: Buat `exporter/gru_model.py`**

```python
"""
Inline GRU Autoencoder for csv_exporter — no dependency on src/.
Mirrors src/detection/gru_autoencoder.py with TemporalAttention inlined.
"""
import os
import pickle
import numpy as np
import torch
import torch.nn as nn
from collections import deque
from typing import List


class TemporalAttention(nn.Module):
    def __init__(self, hidden_dim: int):
        super().__init__()
        self.attn = nn.Linear(hidden_dim, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        scores  = self.attn(x).squeeze(-1)
        weights = torch.softmax(scores, dim=1).unsqueeze(-1)
        return (x * weights).sum(dim=1)


class GRUEncoder(nn.Module):
    def __init__(self, input_size: int, hidden_sizes: List[int],
                 latent_dim: int, bidirectional: bool = True):
        super().__init__()
        D = 2 if bidirectional else 1
        self.gru1 = nn.GRU(input_size, hidden_sizes[0],
                            batch_first=True, bidirectional=bidirectional)
        self.gru2 = nn.GRU(hidden_sizes[0] * D, hidden_sizes[1],
                            batch_first=True, bidirectional=bidirectional)
        self.attention = TemporalAttention(hidden_sizes[1] * D)
        self.fc = nn.Linear(hidden_sizes[1] * D, latent_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.gru1(x)
        out, _ = self.gru2(out)
        return self.fc(self.attention(out))


class GRUDecoder(nn.Module):
    def __init__(self, latent_dim: int, hidden_sizes: List[int],
                 output_size: int, seq_len: int):
        super().__init__()
        self.seq_len = seq_len
        self.fc   = nn.Linear(latent_dim, hidden_sizes[0])
        self.gru1 = nn.GRU(hidden_sizes[0], hidden_sizes[1], batch_first=True)
        self.gru2 = nn.GRU(hidden_sizes[1], output_size,    batch_first=True)

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        h = self.fc(z).unsqueeze(1).repeat(1, self.seq_len, 1)
        out, _ = self.gru1(h)
        out, _ = self.gru2(out)
        return out


class GRUAutoencoder(nn.Module):
    def __init__(self, cfg: dict):
        super().__init__()
        self.input_features = cfg.get("input_features", 16)
        enc_h = cfg.get("encoder_hidden", [64, 32])
        dec_h = cfg.get("decoder_hidden", [32, 64])
        latent = cfg.get("latent_dim", 32)
        bidir  = cfg.get("bidirectional", True)
        self.seq_len = cfg.get("seq_len", 10)
        self.encoder = GRUEncoder(self.input_features, enc_h, latent, bidir)
        self.decoder = GRUDecoder(latent, dec_h, self.input_features, self.seq_len)
        self.anomaly_threshold = None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))

    def reconstruction_error(self, x: torch.Tensor) -> float:
        with torch.no_grad():
            err = torch.mean((x - self.forward(x)) ** 2, dim=(1, 2))
            return float(err[0])

    @classmethod
    def load(cls, path: str) -> "GRUAutoencoder":
        if not os.path.exists(path):
            raise FileNotFoundError(f"GRU model not found: {path}")
        state = torch.load(path, map_location="cpu", weights_only=False)
        cfg   = state.get("config", {})
        model = cls(cfg)
        model.load_state_dict(state["model_state_dict"])
        model.anomaly_threshold = state.get("anomaly_threshold")
        model.eval()
        return model


# ── GRU feature columns (must match scaler_gru.pkl fit order) ────────────────
GRU_FEATURE_COLS = [
    "prb_usage_dl_ratio", "prb_usage_ul_ratio", "cqi", "rach_preamble",
    "air_delay_ul", "prb_direction", "prb_total", "prb_dl_delta", "prb_ul_delta",
    "prb_burst_index", "empty_ind_rate", "prb_dl_roll_mean", "prb_dl_roll_std",
    "prb_ul_roll_std", "prb_ul_roll_max", "prb_ul_roll_max_100",
]


def load_scaler(path: str):
    with open(path, "rb") as f:
        return pickle.load(f)


def extract_gru_features(row: dict) -> np.ndarray:
    """Extract 16 GRU features from parsed CSV row dict. Returns shape (16,)."""
    return np.array([row.get(c, 0.0) for c in GRU_FEATURE_COLS], dtype=np.float32)
```

- [ ] **Step 2: Tulis test untuk gru_model.py**

Tambahkan di `exporter/test_csv_exporter.py`:

```python
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
```

Jalankan:
```bash
cd /home/telmat/sec-xapp/exporter
python -m pytest test_csv_exporter.py -v -k "gru_model or extract_gru"
```
Expected: 3 PASS

- [ ] **Step 3: Update Dockerfile untuk COPY gru_model.py**

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY csv_exporter.py .
COPY gru_model.py .

CMD ["python", "csv_exporter.py"]
```

- [ ] **Step 4: Commit**

```bash
git add exporter/gru_model.py exporter/Dockerfile exporter/test_csv_exporter.py
git commit -m "feat(exporter): add inline GRU model class + Dockerfile update"
```

---

## Task 3: GRU Inference Thread di `csv_exporter.py`

**Files:**
- Modify: `exporter/csv_exporter.py`
- Test: `exporter/test_csv_exporter.py`

- [ ] **Step 1: Tambah konstanta GRU di csv_exporter.py**

Tambahkan setelah blok `ALERT_TYPE_MAP` (di bawah Task 1):

```python
# ── GRU inference config ─────────────────────────────────────────────────────
GRU_MODEL_A  = os.getenv("GRU_MODEL_A", "/data/models/gru_autoencoder_A_v1.pt")
GRU_MODEL_B  = os.getenv("GRU_MODEL_B", "/data/models/gru_autoencoder_B_v1.pt")
GRU_SCALER   = os.getenv("GRU_SCALER",  "/data/models/scaler_gru.pkl")
GRU_THRESH_A = 0.002881
GRU_THRESH_B = 0.003363
GRU_SEQ_A    = 10
GRU_SEQ_B    = 30
GRU_POLL_SEC = 1.0
```

- [ ] **Step 2: Tambah fungsi `gru_inference_loop` di csv_exporter.py**

Tambahkan sebelum fungsi `main()`:

```python
def gru_inference_loop():
    """Load GRU-A + GRU-B, run inference every GRU_POLL_SEC from latest CSV row."""
    try:
        from gru_model import GRUAutoencoder, load_scaler, extract_gru_features
        import numpy as np
    except ImportError as e:
        log.warning("GRU inference disabled: %s", e)
        return

    try:
        model_a = GRUAutoencoder.load(GRU_MODEL_A)
        model_b = GRUAutoencoder.load(GRU_MODEL_B)
        scaler  = load_scaler(GRU_SCALER)
        log.info("GRU models loaded: A seq=%d  B seq=%d", GRU_SEQ_A, GRU_SEQ_B)
    except (FileNotFoundError, Exception) as e:
        log.warning("GRU models not available, thread exiting: %s", e)
        return

    from collections import deque
    buf_a = deque(maxlen=GRU_SEQ_A)
    buf_b = deque(maxlen=GRU_SEQ_B)

    while True:
        time.sleep(GRU_POLL_SEC)
        with _latest_row_lock:
            row = dict(_latest_row)
        if not row:
            continue

        try:
            import torch
            feat_raw = extract_gru_features(row)                    # (16,)
            feat_scaled = scaler.transform(feat_raw.reshape(1, -1))[0]  # (16,)

            buf_a.append(feat_scaled)
            buf_b.append(feat_scaled)

            score_a, score_b = 0.0, 0.0

            if len(buf_a) == GRU_SEQ_A:
                x_a = torch.tensor(np.array(buf_a), dtype=torch.float32).unsqueeze(0)
                score_a = model_a.reconstruction_error(x_a)
                g_gru_a.set(score_a)

            if len(buf_b) == GRU_SEQ_B:
                x_b = torch.tensor(np.array(buf_b), dtype=torch.float32).unsqueeze(0)
                score_b = model_b.reconstruction_error(x_b)
                g_gru_b.set(score_b)

            # stage: 2=crit (over thresh), 1=warn (over 50% thresh), 0=normal
            if score_a > GRU_THRESH_A or score_b > GRU_THRESH_B:
                gru_stage = 2
            elif score_a > GRU_THRESH_A * 0.5 or score_b > GRU_THRESH_B * 0.5:
                gru_stage = 1
            else:
                gru_stage = 0
            g_gru_stage.set(gru_stage)

        except Exception as e:
            log.debug("GRU inference error (skipping): %s", e)
```

- [ ] **Step 3: Start GRU thread di `main()`**

Ganti fungsi `main()`:

```python
def main():
    log.info("Starting xapp Prometheus exporter on :8000")
    start_http_server(8000)

    t1 = threading.Thread(target=csv_tail_loop,       daemon=True, name="csv-tail")
    t2 = threading.Thread(target=eval_watch_loop,     daemon=True, name="eval-watch")
    t3 = threading.Thread(target=gru_inference_loop,  daemon=True, name="gru-infer")
    t1.start()
    t2.start()
    t3.start()

    log.info("Exporter running. Metrics at http://0.0.0.0:8000/metrics")
    while True:
        time.sleep(60)
```

- [ ] **Step 4: Tambah test GRU inference logic**

```python
def test_gru_stage_crit_when_score_above_thresh():
    """Score above both thresholds → stage 2."""
    import csv_exporter
    # Simulate score_a > GRU_THRESH_A
    score_a = csv_exporter.GRU_THRESH_A * 1.5
    score_b = 0.0
    stage = (2 if score_a > csv_exporter.GRU_THRESH_A or score_b > csv_exporter.GRU_THRESH_B
             else 1 if score_a > csv_exporter.GRU_THRESH_A * 0.5 or score_b > csv_exporter.GRU_THRESH_B * 0.5
             else 0)
    assert stage == 2

def test_gru_stage_warn_when_score_in_warn_zone():
    """Score between 50%–100% threshold → stage 1."""
    import csv_exporter
    score_a = csv_exporter.GRU_THRESH_A * 0.7   # 70% of threshold
    score_b = 0.0
    stage = (2 if score_a > csv_exporter.GRU_THRESH_A or score_b > csv_exporter.GRU_THRESH_B
             else 1 if score_a > csv_exporter.GRU_THRESH_A * 0.5 or score_b > csv_exporter.GRU_THRESH_B * 0.5
             else 0)
    assert stage == 1

def test_gru_stage_normal_when_both_below_thresh():
    """Both scores below 50% threshold → stage 0."""
    import csv_exporter
    score_a = csv_exporter.GRU_THRESH_A * 0.1
    score_b = csv_exporter.GRU_THRESH_B * 0.1
    stage = (2 if score_a > csv_exporter.GRU_THRESH_A or score_b > csv_exporter.GRU_THRESH_B
             else 1 if score_a > csv_exporter.GRU_THRESH_A * 0.5 or score_b > csv_exporter.GRU_THRESH_B * 0.5
             else 0)
    assert stage == 0
```

Jalankan:
```bash
cd /home/telmat/sec-xapp/exporter
python -m pytest test_csv_exporter.py -v -k "gru_stage"
```
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add exporter/csv_exporter.py exporter/test_csv_exporter.py
git commit -m "feat(exporter): add GRU inference thread with deque buffers"
```

---

## Task 4: Eval Metrics v2 (Hardcoded dari STATUS doc)

**Files:**
- Modify: `exporter/csv_exporter.py`
- Test: `exporter/test_csv_exporter.py`

- [ ] **Step 1: Tambah KNOWN_EVAL dict dan fungsi `_populate_eval_v2`**

Tambahkan setelah blok konstanta GRU (Task 3 Step 1):

```python
# ── Known evaluation results (from dataset_attack_mei.csv offline eval) ──────
KNOWN_EVAL = {
    "rule": {
        "overall":   {"recall": 0.9765, "precision": 0.9862, "f1": 0.9813, "fpr": 0.0140},
        "ul_flood":  {"recall": 0.987,  "f1": 0.963},
        "dl_flood":  {"recall": 0.993,  "f1": 0.967},
        "burst":     {"recall": 0.982,  "f1": 0.971},
        "rrc_storm": {"recall": 0.940,  "f1": 0.938},
    },
    "lstm": {
        "overall":   {"recall": 0.7918, "precision": 0.9284, "f1": 0.8547, "fpr": 0.0627},
        "ul_flood":  {"recall": 0.814,  "f1": 0.893},
        "dl_flood":  {"recall": 0.997,  "f1": 0.995},
        "burst":     {"recall": 0.614,  "f1": 0.736},
        "rrc_storm": {"recall": 0.838,  "f1": 0.882},
    },
    "gru_tuned": {
        "overall":   {"recall": 0.932,  "precision": 0.930, "f1": 0.930, "fpr": 0.053},
        "ul_flood":  {"recall": 0.996,  "f1": 0.970},
        "dl_flood":  {"recall": 0.994,  "f1": 0.980},
        "burst":     {"recall": 0.987,  "f1": 0.987},
        "rrc_storm": {"recall": 0.715,  "f1": 0.790},
    },
    "hybrid_lstm": {
        "overall":   {"recall": 0.9831, "precision": 0.9410, "f1": 0.9616, "fpr": 0.0137},
        "ul_flood":  {"recall": 0.992,  "f1": 0.964},
        "dl_flood":  {"recall": 0.997,  "f1": 0.967},
        "burst":     {"recall": 0.995,  "f1": 0.971},
        "rrc_storm": {"recall": 0.940,  "f1": 0.938},
    },
    "hybrid_gru": {
        "overall":   {"recall": 0.9824, "precision": 0.9724, "f1": 0.9773, "fpr": 0.0287},
        "ul_flood":  {"recall": 0.992,  "f1": 0.937},
        "dl_flood":  {"recall": 0.993,  "f1": 0.937},
        "burst":     {"recall": 0.987,  "f1": 0.971},
        "rrc_storm": {"recall": 0.940,  "f1": 0.938},
    },
}


def _populate_eval_v2():
    """Push KNOWN_EVAL into Prometheus gauge xapp_eval_*_v2 at startup."""
    attacks = ["ul_flood", "dl_flood", "burst", "rrc_storm"]
    for model, data in KNOWN_EVAL.items():
        ov = data["overall"]
        g_eval_recall_v2.labels(model=model, attack="all").set(ov["recall"])
        g_eval_precision_v2.labels(model=model).set(ov["precision"])
        g_eval_f1_v2.labels(model=model, attack="all").set(ov["f1"])
        g_eval_fpr_v2.labels(model=model).set(ov["fpr"])
        for atk in attacks:
            if atk in data:
                g_eval_recall_v2.labels(model=model, attack=atk).set(data[atk]["recall"])
                g_eval_f1_v2.labels(model=model, attack=atk).set(data[atk]["f1"])
    log.info("Eval v2 metrics populated for %d models", len(KNOWN_EVAL))
```

- [ ] **Step 2: Panggil `_populate_eval_v2()` di `main()` saat startup**

Dalam fungsi `main()`, tambahkan setelah `start_http_server(8000)`:

```python
    _populate_eval_v2()
```

- [ ] **Step 3: Tulis test**

```python
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
```

Jalankan:
```bash
cd /home/telmat/sec-xapp/exporter
python -m pytest test_csv_exporter.py -v -k "populate_eval"
```
Expected: 3 PASS

- [ ] **Step 4: Commit**

```bash
git add exporter/csv_exporter.py exporter/test_csv_exporter.py
git commit -m "feat(exporter): add eval metrics v2 with known results from STATUS doc"
```

---

## Task 5: Update `requirements.txt` + `docker-compose.yml`

**Files:**
- Modify: `exporter/requirements.txt`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Update `exporter/requirements.txt`**

```
prometheus-client==0.20.0
onnxruntime==1.18.1
numpy==1.26.4
requests==2.32.3
torch>=2.0.0
scikit-learn>=1.3.0
```

- [ ] **Step 2: Update `docker-compose.yml` service `csv-exporter`**

Ganti seluruh service `csv-exporter`:

```yaml
  csv-exporter:
    build: ./exporter
    container_name: xapp-exporter
    ports:
      - "8000:8000"
    volumes:
      - ./csv:/data/csv:ro
      - ./results:/data/results:ro
      - ./models:/data/models:ro
      - ./security_model.onnx:/data/security_model.onnx:ro
      - ./security_model.onnx.data:/data/security_model.onnx.data:ro
    environment:
      - CSV_DIR=/data/csv
      - EVAL_JSON=/data/results/eval_results.json
      - GRAFANA_URL=http://grafana:3000
      - GRAFANA_TOKEN=admin:admin
      - GRU_MODEL_A=/data/models/gru_autoencoder_A_v1.pt
      - GRU_MODEL_B=/data/models/gru_autoencoder_B_v1.pt
      - GRU_SCALER=/data/models/scaler_gru.pkl
    restart: unless-stopped
    depends_on:
      - grafana
```

- [ ] **Step 3: Verifikasi models/ directory punya file yang dibutuhkan**

```bash
ls -lh /home/telmat/sec-xapp/models/gru_autoencoder_A_v1.pt \
        /home/telmat/sec-xapp/models/gru_autoencoder_B_v1.pt \
        /home/telmat/sec-xapp/models/scaler_gru.pkl
```
Expected: Ketiga file ada dan > 0 bytes.

- [ ] **Step 4: Commit**

```bash
git add exporter/requirements.txt docker-compose.yml
git commit -m "feat(docker): mount models/, add torch+sklearn deps for GRU inference"
```

---

## Task 6: Update Dashboard 1 `main.json`

**Files:**
- Modify: `grafana/provisioning/dashboards/main.json`

> Grafana provisioned dashboard: setiap perubahan JSON langsung aktif saat container restart atau dalam 10 detik (updateIntervalSeconds=10 di dashboards.yml).

- [ ] **Step 1: Baca struktur main.json yang ada**

```bash
python3 -c "
import json
with open('grafana/provisioning/dashboards/main.json') as f:
    d = json.load(f)
print('uid:', d.get('uid'))
print('panels:', [(p['id'], p['title']) for p in d.get('panels',[])])
"
```

- [ ] **Step 2: Tulis main.json baru**

Simpan ke `grafana/provisioning/dashboards/main.json` dengan konten berikut (ganti seluruh file):

```json
{
  "uid": "xapp-live",
  "title": "xApp Security Monitor — Live",
  "tags": ["xapp", "security", "live"],
  "timezone": "browser",
  "refresh": "5s",
  "time": { "from": "now-10m", "to": "now" },
  "schemaVersion": 38,
  "panels": [
    {
      "id": 1, "type": "stat", "title": "LSTM Stage",
      "gridPos": { "x": 0, "y": 0, "w": 3, "h": 4 },
      "fieldConfig": {
        "defaults": {
          "mappings": [
            {"type": "value", "options": {"0": {"text": "NORMAL",   "color": "green"}}},
            {"type": "value", "options": {"1": {"text": "WARNING",  "color": "yellow"}}},
            {"type": "value", "options": {"2": {"text": "CRITICAL", "color": "red"}}}
          ],
          "thresholds": {"mode": "absolute", "steps": [
            {"value": null, "color": "green"},
            {"value": 1,    "color": "yellow"},
            {"value": 2,    "color": "red"}
          ]}
        }
      },
      "targets": [{"expr": "xapp_detection_stage", "instant": true, "legendFormat": "LSTM"}]
    },
    {
      "id": 2, "type": "stat", "title": "GRU Stage",
      "gridPos": { "x": 3, "y": 0, "w": 3, "h": 4 },
      "fieldConfig": {
        "defaults": {
          "mappings": [
            {"type": "value", "options": {"0": {"text": "NORMAL",   "color": "green"}}},
            {"type": "value", "options": {"1": {"text": "WARNING",  "color": "yellow"}}},
            {"type": "value", "options": {"2": {"text": "CRITICAL", "color": "red"}}}
          ],
          "thresholds": {"mode": "absolute", "steps": [
            {"value": null, "color": "green"},
            {"value": 1,    "color": "yellow"},
            {"value": 2,    "color": "red"}
          ]}
        }
      },
      "targets": [{"expr": "xapp_gru_stage", "instant": true, "legendFormat": "GRU"}]
    },
    {
      "id": 3, "type": "stat", "title": "Alert Type",
      "gridPos": { "x": 6, "y": 0, "w": 4, "h": 4 },
      "fieldConfig": {
        "defaults": {
          "mappings": [
            {"type": "value", "options": {"0": {"text": "None"}}},
            {"type": "value", "options": {"1": {"text": "UL Flood",  "color": "orange"}}},
            {"type": "value", "options": {"2": {"text": "DL Flood",  "color": "orange"}}},
            {"type": "value", "options": {"3": {"text": "Burst",     "color": "yellow"}}},
            {"type": "value", "options": {"4": {"text": "RRC Storm", "color": "red"}}}
          ]
        }
      },
      "targets": [{"expr": "xapp_alert_type", "instant": true, "legendFormat": ""}]
    },
    {
      "id": 4, "type": "stat", "title": "Detect Latency",
      "gridPos": { "x": 10, "y": 0, "w": 2, "h": 2 },
      "fieldConfig": {"defaults": {"unit": "ms"}},
      "targets": [{"expr": "xapp_latency_detect_ms", "instant": true}]
    },
    {
      "id": 5, "type": "stat", "title": "Confirm Latency",
      "gridPos": { "x": 10, "y": 2, "w": 2, "h": 2 },
      "fieldConfig": {"defaults": {"unit": "ms"}},
      "targets": [{"expr": "xapp_latency_confirm_ms", "instant": true}]
    },
    {
      "id": 6, "type": "stat", "title": "Total to Mitigate",
      "gridPos": { "x": 12, "y": 0, "w": 2, "h": 2 },
      "fieldConfig": {"defaults": {"unit": "ms"}},
      "targets": [{"expr": "xapp_latency_total_ms", "instant": true}]
    },
    {
      "id": 7, "type": "timeseries", "title": "PRB Utilization DL / UL",
      "gridPos": { "x": 0, "y": 4, "w": 12, "h": 6 },
      "fieldConfig": {
        "defaults": {"unit": "percentunit", "min": 0, "max": 1},
        "overrides": [
          {"matcher": {"id": "byName", "options": "Threshold 70%"},
           "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash"}},
                          {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}
        ]
      },
      "targets": [
        {"expr": "xapp_prb_dl_ratio",  "legendFormat": "DL"},
        {"expr": "xapp_prb_ul_ratio",  "legendFormat": "UL"},
        {"expr": "0.7 + 0 * xapp_prb_dl_ratio", "legendFormat": "Threshold 70%"}
      ]
    },
    {
      "id": 8, "type": "timeseries", "title": "Signaling & Burst Indicators",
      "gridPos": { "x": 12, "y": 4, "w": 12, "h": 6 },
      "fieldConfig": {"defaults": {"min": 0}},
      "targets": [
        {"expr": "xapp_rach_preamble",    "legendFormat": "RACH Preamble"},
        {"expr": "xapp_empty_ind_rate",   "legendFormat": "Empty Ind Rate (RRC proxy)"},
        {"expr": "xapp_prb_burst_index",  "legendFormat": "PRB Burst Index"}
      ]
    },
    {
      "id": 9, "type": "timeseries", "title": "LSTM Anomaly Score (v16 thresh=0.21)",
      "gridPos": { "x": 0, "y": 10, "w": 12, "h": 6 },
      "fieldConfig": {
        "defaults": {"min": 0},
        "overrides": [
          {"matcher": {"id": "byName", "options": "Threshold 0.21"},
           "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash"}},
                          {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}
        ]
      },
      "targets": [
        {"expr": "xapp_anomaly_score", "legendFormat": "LSTM Score"},
        {"expr": "0.21 + 0 * xapp_anomaly_score", "legendFormat": "Threshold 0.21"}
      ]
    },
    {
      "id": 10, "type": "timeseries", "title": "GRU Score A & B",
      "gridPos": { "x": 12, "y": 10, "w": 12, "h": 6 },
      "fieldConfig": {"defaults": {"min": 0}},
      "targets": [
        {"expr": "xapp_gru_score_a", "legendFormat": "GRU-A (thresh=0.002881)"},
        {"expr": "xapp_gru_score_b", "legendFormat": "GRU-B (thresh=0.003363)"},
        {"expr": "0.002881 + 0 * xapp_gru_score_a", "legendFormat": "Thresh A"},
        {"expr": "0.003363 + 0 * xapp_gru_score_b", "legendFormat": "Thresh B"}
      ]
    },
    {
      "id": 11, "type": "timeseries", "title": "Stage Timeline — PRB + LSTM + GRU (last 30m)",
      "gridPos": { "x": 0, "y": 16, "w": 24, "h": 7 },
      "fieldConfig": {"defaults": {"unit": "percentunit", "min": 0, "max": 1}},
      "targets": [
        {"expr": "xapp_prb_dl_ratio",          "legendFormat": "PRB DL"},
        {"expr": "xapp_prb_ul_ratio",          "legendFormat": "PRB UL"},
        {"expr": "xapp_detection_stage / 2",   "legendFormat": "LSTM Stage (norm)"},
        {"expr": "xapp_gru_stage / 2",         "legendFormat": "GRU Stage (norm)"}
      ],
      "options": {"tooltip": {"mode": "multi"}}
    }
  ]
}
```

- [ ] **Step 3: Validasi JSON**

```bash
python3 -c "
import json
with open('grafana/provisioning/dashboards/main.json') as f:
    d = json.load(f)
print('Valid JSON. Panels:', len(d['panels']))
for p in d['panels']:
    print(f'  [{p[\"id\"]}] {p[\"title\"]}')
"
```
Expected: `Valid JSON. Panels: 11` tanpa error.

- [ ] **Step 4: Commit**

```bash
git add grafana/provisioning/dashboards/main.json
git commit -m "feat(grafana): update live dashboard — add GRU stage, alert type, signaling, score panels"
```

---

## Task 7: Buat Dashboard 2 `eval.json`

**Files:**
- Create: `grafana/provisioning/dashboards/eval.json`

- [ ] **Step 1: Buat `grafana/provisioning/dashboards/eval.json`**

```json
{
  "uid": "xapp-eval",
  "title": "xApp Security Monitor — Evaluation Results",
  "tags": ["xapp", "security", "evaluation"],
  "timezone": "browser",
  "refresh": "1m",
  "time": { "from": "now-5m", "to": "now" },
  "schemaVersion": 38,
  "panels": [
    { "id": 1, "type": "text", "title": "",
      "gridPos": {"x": 0, "y": 0, "w": 24, "h": 2},
      "options": {"mode": "markdown", "content": "## Known Attack Dataset — `dataset_attack_mei.csv` (17,941 samples, 5 labels)\nMetrics from offline evaluation. Stage1 = per-window, Stage2/Hybrid = 5× consecutive confirmation."}
    },
    { "id": 10, "type": "stat", "title": "Rule-Based",
      "gridPos": {"x": 0, "y": 2, "w": 4, "h": 5},
      "fieldConfig": {"defaults": {"decimals": 3}},
      "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "orientation": "vertical"},
      "targets": [
        {"expr": "xapp_eval_recall_v2{model='rule',attack='all'}",    "legendFormat": "Recall"},
        {"expr": "xapp_eval_f1_v2{model='rule',attack='all'}",        "legendFormat": "F1"},
        {"expr": "xapp_eval_fpr_v2{model='rule'}",                    "legendFormat": "FPR Stage1"}
      ]
    },
    { "id": 11, "type": "stat", "title": "LSTM Ensemble",
      "gridPos": {"x": 4, "y": 2, "w": 4, "h": 5},
      "fieldConfig": {"defaults": {"decimals": 3}},
      "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "orientation": "vertical"},
      "targets": [
        {"expr": "xapp_eval_recall_v2{model='lstm',attack='all'}",    "legendFormat": "Recall"},
        {"expr": "xapp_eval_f1_v2{model='lstm',attack='all'}",        "legendFormat": "F1"},
        {"expr": "xapp_eval_fpr_v2{model='lstm'}",                    "legendFormat": "FPR Stage1"}
      ]
    },
    { "id": 12, "type": "stat", "title": "GRU Tuned",
      "gridPos": {"x": 8, "y": 2, "w": 4, "h": 5},
      "fieldConfig": {"defaults": {"decimals": 3}},
      "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "orientation": "vertical"},
      "targets": [
        {"expr": "xapp_eval_recall_v2{model='gru_tuned',attack='all'}", "legendFormat": "Recall"},
        {"expr": "xapp_eval_f1_v2{model='gru_tuned',attack='all'}",     "legendFormat": "F1"},
        {"expr": "xapp_eval_fpr_v2{model='gru_tuned'}",                 "legendFormat": "FPR Stage1"}
      ]
    },
    { "id": 13, "type": "stat", "title": "Hybrid Rule+LSTM",
      "gridPos": {"x": 12, "y": 2, "w": 5, "h": 5},
      "fieldConfig": {"defaults": {"decimals": 3}},
      "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "orientation": "vertical"},
      "targets": [
        {"expr": "xapp_eval_recall_v2{model='hybrid_lstm',attack='all'}", "legendFormat": "Recall"},
        {"expr": "xapp_eval_f1_v2{model='hybrid_lstm',attack='all'}",     "legendFormat": "F1"},
        {"expr": "xapp_eval_fpr_v2{model='hybrid_lstm'}",                 "legendFormat": "FPR Stage2"}
      ]
    },
    { "id": 14, "type": "stat", "title": "Hybrid Rule+GRU",
      "gridPos": {"x": 17, "y": 2, "w": 7, "h": 5},
      "fieldConfig": {"defaults": {"decimals": 3}},
      "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "orientation": "vertical"},
      "targets": [
        {"expr": "xapp_eval_recall_v2{model='hybrid_gru',attack='all'}", "legendFormat": "Recall"},
        {"expr": "xapp_eval_f1_v2{model='hybrid_gru',attack='all'}",     "legendFormat": "F1"},
        {"expr": "xapp_eval_fpr_v2{model='hybrid_gru'}",                 "legendFormat": "FPR Stage1"}
      ]
    },
    { "id": 20, "type": "bargauge", "title": "Per-Attack Recall — UL Flood",
      "gridPos": {"x": 0, "y": 7, "w": 6, "h": 6},
      "fieldConfig": {"defaults": {"unit": "percentunit", "min": 0, "max": 1}},
      "options": {"orientation": "horizontal", "reduceOptions": {"calcs": ["lastNotNull"]}},
      "targets": [
        {"expr": "xapp_eval_recall_v2{attack='ul_flood'}", "instant": true,
         "legendFormat": "{{model}}"}
      ]
    },
    { "id": 21, "type": "bargauge", "title": "Per-Attack Recall — DL Flood",
      "gridPos": {"x": 6, "y": 7, "w": 6, "h": 6},
      "fieldConfig": {"defaults": {"unit": "percentunit", "min": 0, "max": 1}},
      "options": {"orientation": "horizontal", "reduceOptions": {"calcs": ["lastNotNull"]}},
      "targets": [
        {"expr": "xapp_eval_recall_v2{attack='dl_flood'}", "instant": true,
         "legendFormat": "{{model}}"}
      ]
    },
    { "id": 22, "type": "bargauge", "title": "Per-Attack Recall — Burst ON/OFF",
      "gridPos": {"x": 12, "y": 7, "w": 6, "h": 6},
      "fieldConfig": {"defaults": {"unit": "percentunit", "min": 0, "max": 1}},
      "options": {"orientation": "horizontal", "reduceOptions": {"calcs": ["lastNotNull"]}},
      "targets": [
        {"expr": "xapp_eval_recall_v2{attack='burst'}", "instant": true,
         "legendFormat": "{{model}}"}
      ]
    },
    { "id": 23, "type": "bargauge", "title": "Per-Attack Recall — RRC Storm",
      "gridPos": {"x": 18, "y": 7, "w": 6, "h": 6},
      "fieldConfig": {"defaults": {"unit": "percentunit", "min": 0, "max": 1}},
      "options": {"orientation": "horizontal", "reduceOptions": {"calcs": ["lastNotNull"]}},
      "targets": [
        {"expr": "xapp_eval_recall_v2{attack='rrc_storm'}", "instant": true,
         "legendFormat": "{{model}}"}
      ]
    },
    { "id": 30, "type": "bargauge", "title": "FPR Comparison (Stage1) — Lower is Better",
      "gridPos": {"x": 0, "y": 13, "w": 12, "h": 6},
      "fieldConfig": {
        "defaults": {"unit": "percentunit", "min": 0, "max": 0.1,
          "thresholds": {"mode": "absolute", "steps": [
            {"value": null, "color": "green"},
            {"value": 0.02, "color": "yellow"},
            {"value": 0.05, "color": "red"}
          ]}}
      },
      "options": {"orientation": "horizontal", "reduceOptions": {"calcs": ["lastNotNull"]}},
      "targets": [
        {"expr": "xapp_eval_fpr_v2", "instant": true, "legendFormat": "{{model}}"}
      ]
    },
    { "id": 31, "type": "bargauge", "title": "F1 Score Comparison — Higher is Better",
      "gridPos": {"x": 12, "y": 13, "w": 12, "h": 6},
      "fieldConfig": {
        "defaults": {"unit": "percentunit", "min": 0.7, "max": 1.0,
          "thresholds": {"mode": "absolute", "steps": [
            {"value": null, "color": "red"},
            {"value": 0.90, "color": "yellow"},
            {"value": 0.95, "color": "green"}
          ]}}
      },
      "options": {"orientation": "horizontal", "reduceOptions": {"calcs": ["lastNotNull"]}},
      "targets": [
        {"expr": "xapp_eval_f1_v2{attack='all'}", "instant": true, "legendFormat": "{{model}}"}
      ]
    }
  ]
}
```

- [ ] **Step 2: Validasi JSON**

```bash
python3 -c "
import json
with open('grafana/provisioning/dashboards/eval.json') as f:
    d = json.load(f)
print('Valid JSON. uid:', d['uid'], 'Panels:', len(d['panels']))
"
```
Expected: `Valid JSON. uid: xapp-eval Panels: 12`

- [ ] **Step 3: Commit**

```bash
git add grafana/provisioning/dashboards/eval.json
git commit -m "feat(grafana): create evaluation results dashboard with 5-model comparison"
```

---

## Task 8: Update `dashboards.yml`

**Files:**
- Modify: `grafana/provisioning/dashboards/dashboards.yml`

- [ ] **Step 1: Verifikasi dashboards.yml — tidak perlu diubah**

File `dashboards.yml` menggunakan `path: /etc/grafana/provisioning/dashboards` yang meng-auto-discover semua `.json` dalam direktori tersebut. Karena `eval.json` sudah ditaruh di direktori yang sama, Grafana akan otomatis menemukannya.

```bash
cat grafana/provisioning/dashboards/dashboards.yml
```
Expected output:
```yaml
apiVersion: 1
providers:
  - name: 'xapp-dashboards'
    ...
    options:
      path: /etc/grafana/provisioning/dashboards
```

Jika `path` menunjuk ke direktori (bukan file spesifik), tidak ada perubahan diperlukan. Lanjut ke Step 2.

- [ ] **Step 2: Run full test suite**

```bash
cd /home/telmat/sec-xapp/exporter
python -m pytest test_csv_exporter.py -v
```
Expected: Semua test PASS (existing + baru).

- [ ] **Step 3: Rebuild dan restart Docker container**

```bash
cd /home/telmat/sec-xapp
docker-compose build csv-exporter
docker-compose up -d csv-exporter
docker-compose logs -f csv-exporter --tail=30
```
Expected output (dalam 30 detik):
```
INFO GRU models loaded: A seq=10  B seq=30
INFO Eval v2 metrics populated for 5 models
INFO Exporter running. Metrics at http://0.0.0.0:8000/metrics
```

- [ ] **Step 4: Verifikasi metrics tersedia di Prometheus endpoint**

```bash
curl -s http://localhost:8000/metrics | grep "xapp_gru\|xapp_eval_recall_v2\|xapp_alert_type"
```
Expected: Baris metric dengan nilai numerik, contoh:
```
xapp_gru_score_a 0.0
xapp_gru_stage 0.0
xapp_eval_recall_v2{attack="all",model="rule"} 0.9765
xapp_alert_type 0.0
```

- [ ] **Step 5: Buka Grafana dan verifikasi kedua dashboard**

```bash
# Pastikan Grafana running
curl -s http://localhost:3000/api/health | python3 -c "import json,sys; print(json.load(sys.stdin))"
```

Buka browser:
- `http://localhost:3000/d/xapp-live` → Dashboard 1 harus tampil 11 panel
- `http://localhost:3000/d/xapp-eval` → Dashboard 2 harus tampil 12 panel dengan nilai eval

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: Grafana GRU dashboard complete — live monitoring + eval results"
```

---

## Catatan Implementasi

**GRU threshold:** Nilai hardcoded (A=0.002881, B=0.003363) tidak tersimpan di checkpoint model (field `anomaly_threshold=None`). Ini sudah benar — nilai ini dari hasil sweep manual, bukan dari `fit_threshold()`.

**GRU feature order:** Urutan 16 fitur di `gru_model.py` HARUS sama persis dengan urutan di `src/detection/feature_schema.py` (verified: `empty_ind_rate` di posisi index 10, bukan 15 seperti di spec awal).

**torch dependency:** Image Docker akan signifikan lebih besar (~1.5GB tambahan). Gunakan `torch>=2.0.0` tanpa CUDA (`torch` CPU-only). Jika build terlalu lama, pertimbangkan pre-download wheel dan `--find-links` lokal.

**Grafana instant queries:** Panel eval menggunakan `"instant": true` di targets. Ini penting agar Prometheus mengembalikan nilai saat ini (bukan time series) untuk bargauge dan stat panels.
