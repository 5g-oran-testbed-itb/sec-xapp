# Security xApp — O-RAN Near-RT RIC Anomaly Detection

Near-RT anomaly detection for O-RAN networks, combining a rule-based IDS with
per-UE LSTM/GRU-Autoencoder models, implemented as a C-native xApp on a
physical 3-node 5G SA testbed (band n78, USRP B200 RF, FlexRIC Near-RT RIC,
Open5GS core). Detects data-plane, control-plane, and physical-layer attacks
and can trigger E2SM-RC PRB-throttle mitigation.

This repository is the reproducibility artifact for the accompanying thesis:
detection/training code, evaluation results, and 3-node deployment
configuration. It does **not** contain the vendor RAN/RIC source trees
(those are pinned git submodules) or the raw datasets (released separately,
see [Reproducing results](#reproducing-results)).

---

## Testbed topology

```
CONTROLLER (laptop, separate repo)
  │  ADB/USB → UE-1, UE-2 (COTS 5G phones)
  │  SSH → RIC
  ▼
┌──────────────────────────────────────────────┐
│  O-RAN TESTBED (LAN 10.91.2.0/24)             │
│  gNB (RAN)      10.91.2.1   srsRAN + E2 Agent │
│  Near-RT RIC    10.91.2.2   FlexRIC + xApp    │
│  5G Core        10.91.2.4   Open5GS           │
└──────────────────────────────────────────────┘
        ↕ 5G NR band n78, USRP B200
```

| Node | IP | Software | Path in this repo |
|---|---|---|---|
| RAN (gNB) | `10.91.2.1` | srsRAN Project + E2 Agent | `vendor/srsran/` (build), `deploy/ran/` (config, deploy) |
| RIC | `10.91.2.2` | FlexRIC + security xApp | `vendor/flexric/` (build), `deploy/ric/` (config, deploy) |
| Core | `10.91.2.4` | Open5GS (via `docker_open5gs`) | `deploy/core/` (config, deploy) |

Interfaces: E2AP SCTP `:36421` (RAN→RIC), E42 TCP `:36422` (xApp→RIC), N2
NGAP `:38412` (RAN→AMF), N3 GTP-U `:2152` (RAN→UPF). Full table in
`deploy/ran/README.md`.

Attack-orchestration scripts (traffic generation, label switching) live in a
separate controller repository, not included here.

---

## Repository structure

| Path | Contents |
|---|---|
| `src/detection/` | Detection library: feature schemas, GRU/LSTM autoencoder models, scoring, shared training utilities |
| `scripts/train/` | Model training entry points (`train_gru_ue.py`, `train_lstm_ue.py`, `train_gru.py`, `train_lstm.py`) |
| `scripts/eval/` | Evaluation, threshold calibration, and ablation-study entry points |
| `scripts/plot/` | Figure-generation entry points |
| `scripts/export/` | PyTorch → ONNX export entry points |
| `scripts/data/` | Dataset utilities (`fetch_dataset.sh`, `patch_rolling_stats.py`) |
| `models/` | Deployed model checkpoints + scalers + thresholds (final versions only — see `docs/MODEL_EVALUATION.md`) |
| `deploy/ric/`, `deploy/ran/`, `deploy/core/` | Per-node scripts, configs, and deployment READMEs |
| `observability/` | Monitoring stack: `docker-compose.yml`, Grafana provisioning, Prometheus config, metrics exporter, live testing dashboard |
| `vendor/flexric/`, `vendor/srsran/` | **Git submodules** — forked FlexRIC and srsRAN Project, each carrying the local patches this project needed on top of upstream (see below) |
| `docs/` | Methodology, results, and architecture documentation |
| `copy-xapp/` | Snapshot of the C xApp source as built into `vendor/flexric` (for reference without cloning the submodule) |

### Why `vendor/` is a submodule, not copied-in code

FlexRIC (EURECOM, MPL-2.0) and srsRAN Project (SRS, dual AGPLv3/commercial)
both required local patches: FlexRIC needed the security xApp itself plus
core changes for per-UE KPM style 4/5 reporting; srsRAN needed a full-UE-list
KPM fix and a bounds-check that prevents a segfault. Both sets of changes are
committed with full history on top of their respective upstreams, in
publicly forked repos (`5g-oran-testbed-itb/flexric-sec-xapp`,
`5g-oran-testbed-itb/srsran-sec-xapp`), and pinned here as submodules —
keeping the vendor code's license and history separate from this repo's own.

```bash
git clone --recursive <this-repo-url>
# or, if already cloned:
git submodule update --init --recursive
```

---

## Detection code

| Module | Purpose |
|---|---|
| `feature_schema_ue.py` | 19 per-UE features (15 from CSV + 4 derived burst-index features) |
| `feature_schema.py` | 11-feature legacy schema (PRB metrics + `empty_ind_rate`) used by the earlier single-model pipeline |
| `feature_groups.py` | Feature-group partitioning used for the grouped feature-ablation study |
| `gru_autoencoder.py`, `lstm_autoencoder.py` | Autoencoder architectures |
| `scoring.py` | Anomaly-score math (benign-calibrated feature weighting), isolated from evaluation code so it's unit-testable without a trained model |
| `detector.py` | Rule-based + threshold-based detection combination |
| `training_utils.py` | Shared helpers for reproducible training (seeding, scaler fitting) |

**19 per-UE features** (`feature_schema_ue.py`):

| # | Feature | Description |
|---|---|---|
| 1 | `prb_usage_dl_ratio` | RRU.PrbUsedDl / available, clipped [0,1] |
| 2 | `prb_usage_ul_ratio` | RRU.PrbUsedUl / available, clipped [0,1] |
| 3 | `thp_dl_kbps` | DRB.UEThpDl (kbps) |
| 4 | `thp_ul_kbps` | DRB.UEThpUl (kbps) |
| 5 | `prb_direction` | (prb_ul − prb_dl) / (prb_total + ε), bounded [−1,+1] |
| 6 | `prb_total` | prb_dl + prb_ul, clipped [0,1] |
| 7 | `prb_ul_delta` | prb_ul[t] − prb_ul[t−1] |
| 8 | `ul_efficiency` | thp_ul / prb_ul, clipped [0, 50000] |
| 9 | `prb_ul_roll_mean` | Rolling mean of prb_ul_ratio, 10 timesteps |
| 10 | `prb_ul_roll_std` | Rolling std of prb_ul_ratio, 10 timesteps |
| 11 | `ul_persistence` | Fraction of last 10 timesteps with prb_ul > 0 |
| 12 | `thp_total_kbps` | thp_dl + thp_ul |
| 13 | `thp_ul_delta` | thp_ul[t] − thp_ul[t−1] |
| 14 | `thp_dl_delta` | thp_dl[t] − thp_dl[t−1] |
| 15 | `traffic_direction` | (thp_ul − thp_dl) / (thp_total + ε), bounded [−1,+1] |
| 16–19 | `{prb,thp}_{ul,dl}_burst_index` | log(1+x) / (rolling_mean + ε), clipped [0,50] — derived, computed by `add_burst_features_rows()` |

---

## Reproducing results

1. **Fetch the dataset** (not included in this repo):
   ```bash
   DATASET_URL=<release-url> ./scripts/data/fetch_dataset.sh
   ```
   See `docs/DATASET_MANIFEST.md` for the file list, row counts, and SHA256
   checksums it verifies against.

2. **Train** a model:
   ```bash
   ./venv/bin/python3 scripts/train/train_gru_ue.py \
       --train csv/dataset_training_ue_juni.csv \
       --val   csv/dataset_validation_ue_juni.csv \
       --model-out models/gru_ue_v5.pt
   ```
   (`scripts/train/train_lstm_ue.py` takes the same interface for the LSTM
   architecture.)

3. **Evaluate**:
   ```bash
   ./venv/bin/python3 scripts/eval/evaluate_per_ue_v2.py \
       --val csv/dataset_validation_ue_juni.csv \
       --attack csv/dataset_attack_ue_juni.csv \
       --save-figures
   ```

4. **Plot**:
   ```bash
   ./venv/bin/python3 scripts/plot/plot_learning_curves_v5.py
   ```

Run any script with `--help` for its full flag set — training and
evaluation scripts are self-contained CLI entry points; none of them read
implicit config beyond the paths passed on the command line.

---

## Deploying the testbed

```bash
git clone --recursive <this-repo-url>
```

Build and run order follows the E2AP handshake dependency: RIC first (it
must be listening before the gNB attempts E2 Setup), then RAN, then Core
services must already be reachable for UE attach.

| Node | Guide |
|---|---|
| RIC | [`deploy/ric/README.md`](deploy/ric/README.md) — build FlexRIC + xApp, run, hot-label switching, mitigation modes |
| RAN | [`deploy/ran/README.md`](deploy/ran/README.md) — build srsRAN, gNB config (`cots_n78_copied.yml`), sync script |
| Core | [`deploy/core/README.md`](deploy/core/README.md) — Open5GS setup, slice management, AMF barring fallback |

Monitoring stack (Grafana + Prometheus + metrics exporter):
```bash
cd observability && docker compose up -d
```

---

## Attack scenarios & mitigation

| Label | Scenario | Expected signature |
|---|---|---|
| 0 | Baseline / recovery | PRB ≈ 0, CQI = 15 |
| 1 | UL Flood | PRB_UL ≈ 90%, `prb_direction` ≈ +1.0 |
| 2 | DL Flood | PRB_DL ≈ 90%, `prb_direction` ≈ −1.0 |
| 3 | Burst ON/OFF | Repeated `prb_burst_index` spikes |
| 4 | RRC / Signaling Storm | CQI keep-last artifact, RACH spike, `empty_ind_rate` proxy |
| 5 | RF Burst Jammer | CQI drop, `air_delay_ul` increase |

Mitigation: E2SM-RC PRB throttle is primary (effective for data-plane
floods and bursts); SSH-based AMF subscriber barring is the fallback for
signaling storms, where PRB throttle is a control-plane no-op. Full detail
in [`deploy/ric/README.md`](deploy/ric/README.md) and
[`deploy/core/README.md`](deploy/core/README.md).

Attack-orchestration scripts themselves are not part of this repository —
they live in a separate controller-side repo.

---

## Known issues / limitations

- **DRB throughput metrics unreliable on srsRAN's KPM DU report** for some
  configurations — see `docs/FEATURE_LIMITATIONS_AND_FUTURE_WORK.md` for the
  full, current list of measurement limitations and their impact on feature
  design.
- **CQI keep-last on UE detach** (srsRAN behavior) — CQI does not reset on
  detach, so CQI-based rules are unreliable; `empty_ind_rate` is used as a
  proxy for RRC/signaling-storm detection instead.
- **srsRAN SIZE(0) MeasurementData on detach** — violates the E2SM-KPM
  `SIZE(1..65535)` constraint; FlexRIC rejects these messages, and the
  resulting decode failures are repurposed as a detection signal rather than
  worked around.
- **Attack-dataset benign portion is a softer evaluation condition** than
  training (no sustained high-throughput episodes) — see
  `docs/DATASET_MANIFEST.md`.

See `docs/MODEL_EVALUATION.md` and `docs/FEATURE_LIMITATIONS_AND_FUTURE_WORK.md`
for full quantitative results and their caveats.

---

## License & attribution

This repository's own code (`src/`, `scripts/`, `copy-xapp/`, `deploy/`,
`observability/`) has no separate vendor license attached. The two
submodules carry their upstream licenses independently:

- `vendor/flexric` — fork of [EURECOM Mosaic5G FlexRIC](https://gitlab.eurecom.fr/mosaic5g/flexric), MPL-2.0.
- `vendor/srsran` — fork of [srsRAN Project](https://github.com/srsran/srsRAN_Project), dual AGPLv3 / commercial (SRS).

Neither submodule's license is altered or superseded by inclusion here.
