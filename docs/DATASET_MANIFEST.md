# Dataset Manifest

CSV datasets are **not tracked in this repo** (`.gitignore: *.csv`) — they total
several hundred MB across raw per-session telemetry captures plus the curated
datasets actually used for training/evaluation. This manifest covers the
**curated datasets** that feed `scripts/train/`, `scripts/eval/`, and the
results in `docs/`. Everything else in a full `csv/` export (per-session
`mac_per_ue_*.csv`, `per_ue_training_*.csv`, `training_*.csv`, `ue_alerts_*.csv`,
`mitigation_events_*.csv` — ~590 files, mostly single-row or short raw capture
logs from individual live-testbed sessions) is raw provenance data, not part
of the reproducibility pipeline, and is excluded from the release bundle.

## Current (per-UE, June collection — "juni")

These are the datasets referenced by `docs/per_ue_v5_results.md`,
`docs/per_ue_v6_results.md`, and the current `scripts/train/train_*_ue.py`
pipeline.

| File | Rows | Description | SHA256 |
|---|---|---|---|
| `dataset_training_ue_juni.csv` | 4,201 | Benign, ~70 min, 2 UE. Includes controlled speedtest (upload phase) + iperf3 UL/DL traffic per the documented collection protocol. | `eec48ae7687c04e7f54ac576f59ea88416f6f862fb12b812b6f419ba0a265641` |
| `dataset_validation_ue_juni.csv` | 1,801 | Benign, ~30 min. Includes one speedtest window; detection rules R1–R4 fire exclusively in this window (expected, not a defect — see `docs/FEATURE_LIMITATIONS_AND_FUTURE_WORK.md`). | `f99702c10ba4276c1e5fb407b851d0035bf686fb3af23a44fd6adf3b01b11497` |
| `dataset_attack_ue_juni.csv` | 8,133 | Attack + benign mix, ~96.8 min. Benign portion has no sustained high-throughput episodes (no speedtest/iperf3 in this file — a softer evaluation condition than training, noted as a limitation). | `2792c3c133a94f4c169d8a424ec5804c41b3b2b184bc7fea589712d50ffa0f92` |

## Superseded (earlier collection — "mei" and pre-per-UE)

Kept for provenance / historical result reproduction (`docs/opsi_b_*.md`,
`docs/scoring_comparison_results.md` reference these), not the current
pipeline's input.

| File | Rows | SHA256 |
|---|---|---|
| `dataset_training.csv` | 60,306 | `90cd00ccaafbc0c04321a65b2bfd4bca863c21533d27a2e9cf2300f09db6953` |
| `dataset_validation.csv` | 15,765 | `a8187426998f5f449e3ec4d0656fe3406412d1cf8bad262f1fdd0165f552d0f` |
| `dataset_attack_mei.csv` | 17,941 | `d2db2f8bf56f094d7116fd048b2cd4e20750ef75f4b64fdc6180fce542c545e` |

## Fetching

```bash
DATASET_URL=<release-archive-url> ./scripts/data/fetch_dataset.sh
```

See `scripts/data/fetch_dataset.sh` for the download + checksum-verification
logic. `DATASET_URL` must point at an archive containing at minimum the three
"current" files above at `csv/<filename>`. The release destination (Drive,
Zenodo, institutional storage, etc.) is chosen at publish time — this repo
does not host the data itself.
