# Design: Dashboard BAB2 Compliance Remediation

**Date:** 2026-07-11
**Status:** Approved
**Scope target:** BAB2 Subobjektif 2 / Subsistem 3 (Dashboard Monitoring Visualisasi)

## Problem

Verification of the live dashboard (`grafana/provisioning/dashboards/per_ue_live.json`)
against BAB2.md found it satisfies most of Subobjektif 2, with three defects:

1. **"Riwayat mitigasi E2SM-RC" is only partially met.** The requirement
   (BAB2.md:146) asks for a *history* of mitigation. The dashboard only shows the
   cumulative counter `xapp_attacks_blocked_total` ("Total Blocked Attacks"), which
   is a single number, not a timeline. Worse, that counter is a *proxy* — the
   exporter increments it on any per-UE stage 0/1 → 2 transition, not on a confirmed
   E2SM-RC Control Request actually being sent.

2. **Stale / structurally-wrong MSE threshold line.** Panels 6 and 12 hardcode
   `0.025969` labeled "GRU-UE v4". The model is chosen **at runtime** via the
   `--ids-mode` prompt in `start_xapp_c.sh` / `start_xapp_c_mitigate.sh`:
   - `gru-hybrid` / `gru-only`  → `models/gru_ue_v5_threshold.json` = **0.026026**
   - `lstm-hybrid` / `lstm-only` → `models/lstm_ue_v6_threshold.json` = **0.027047**
   A single hardcoded constant is therefore wrong for at least one mode, and the
   current value/label matches neither.

3. **Decision latency is not visualized** although the exporter already publishes
   `xapp_latency_detect_ms`, `xapp_latency_confirm_ms`, `xapp_latency_total_ms`.

4. **No panel descriptions** (Grafana best practice).

## Goals

- Add a genuine **mitigation history** panel driven by an honest signal (a real
  E2SM-RC Control Request that was actually applied).
- Make the MSE threshold line **dynamic** so it always matches whichever model the
  xApp loaded at runtime — no hardcoded constant, no stale label.
- Surface **decision latency** (visual proof of the < 1 s constraint) using the
  existing `xapp_latency_*` metrics.
- Add descriptions to key panels.

## Non-Goals (YAGNI)

- Per-UE latency breakdown (the `xapp_latency_*` gauges are global "last event"
  values — acceptable, keep global).
- Refactoring or removing the existing stage-2 proxy counter
  `xapp_attacks_blocked_total` (kept for backward compatibility).
- Touching the mitigate binary's control path — instrumentation lives in the
  monitor, which already owns CSV-writing infra the exporter reads.

## Architecture — three layers

### Layer 1: C instrumentation (honest mitigation event)

**File:** `copy-xapp/xapp_sec_moni.c`, function `ipc_send_mitigate()`
(~line 900–960), immediately **after a successful ACK** is received.

Rationale: the mitigate binary (`xapp_sec_mitigate.c` → `execute_rc_control()`)
only replies `"PRB quota applied"` when the E2SM-RC Control Request was actually
sent. The monitor receives that ACK. Logging at the post-ACK point means the log
reflects *controls that were actually applied*, not mere detections.

On ACK success, append one row to a new file
`mitigation_events_<startup_ts>.csv` in the **same directory as the existing
`ue_alerts_*.csv`** (reuse the monitor's CSV directory logic):

```
epoch_ms,action,rnti,ue_id,prb_limit,attack,confidence
```

- Written for both `THROTTLE` and `RESTORE` actions.
- Nothing written on ACK timeout (honest: log = actually-applied controls only).
- **rnti vs ue_id:** the RC target passed over IPC is `ue_id`
  (`g_throttle_target_ue_id`), but the dashboard joins by `rnti`. The monitor knows
  the offending RNTI at detection time, so the row logs **both**; downstream panels
  key on `rnti`. The implementation plan must resolve where the attacker RNTI is
  available at the `ipc_send_mitigate()` call site (pass it in as a new argument if
  needed).

Also, the monitor already loads the active threshold into `g_ue_threshold`.
Write a small sidecar so the exporter can publish it (see Layer 2):
`/tmp/xapp_active_threshold` containing `"<threshold_value> <model_name>"`
(e.g. `0.027047 lstm_ue_v6`). Written once at startup after the threshold JSON is
loaded.

### Layer 2: Exporter (`exporter/csv_exporter.py`)

New Prometheus series:

- `xapp_ue_mitigation_active{rnti}` — Gauge, 0/1 (1 = throttle currently active).
- `xapp_ue_mitigation_prb_limit{rnti}` — Gauge, current PRB cap % (e.g. 5 when
  throttled, 100 after restore).
- `xapp_mitigations_applied_total` — Counter, incremented per real `THROTTLE`
  event (honest complement to `xapp_attacks_blocked_total`, which is retained).
- `xapp_ue_threshold` — Gauge, the actually-loaded per-UE threshold value.
- `xapp_ue_model_info{model="gru_ue_v5"|"lstm_ue_v6"}` — Gauge (value 1) for
  legend/labeling.

Behavior:

- Tail the newest `mitigation_events_*.csv`. Track latest state per rnti:
  `THROTTLE` → active=1, prb_limit=N; `RESTORE` → active=0, prb_limit=100.
- Read `/tmp/xapp_active_threshold` to populate `xapp_ue_threshold` and
  `xapp_ue_model_info` (re-read periodically so a mid-session restart is picked up).

### Layer 3: Dashboard (`grafana/provisioning/dashboards/per_ue_live.json`)

- **New panel "Riwayat Mitigasi E2SM-RC"** — `state-timeline`, expr
  `xapp_ue_mitigation_active` per rnti. Value mappings: 0 → "Normal" (green),
  1 → "Throttled" (red). This is the actual history the requirement demands.
- **New panel "Latensi Keputusan Mitigasi"** — `timeseries`, exprs
  `xapp_latency_detect_ms`, `xapp_latency_confirm_ms`, `xapp_latency_total_ms`,
  with a 1000 ms reference line for the < 1 s constraint. Description notes these
  are global last-event values, not per-UE.
- **Fix MSE threshold (panels 6 & 12):**
  - Panel 6 (MSE timeseries): replace the constant-expression threshold target
    `0.025969 + 0 * count(...)` with `xapp_ue_threshold` so the dashed line always
    matches the loaded model; legend derived from `xapp_ue_model_info`.
  - Panel 12 (Avg MSE stat): stat color thresholds can't be dynamic — remove the
    misleading hardcoded `0.025969` orange step (keep a neutral scheme) and add a
    description pointing to the dynamic threshold shown in panel 6.
- **Descriptions** added to key panels (stat row, mitigation timeline, latency,
  MSE, throughput/PRB).

## Data flow

```
moni detects attack (per-UE stage → 2)
  → ipc_send_mitigate("THROTTLE", rnti, ue_id, ...)
  → mitigate binary: execute_rc_control() → E2SM-RC Control Request → gNB
  → ACK "PRB quota applied"
  → moni appends row to mitigation_events_<ts>.csv   [honest event]
csv_exporter tails mitigation_events_*.csv
  → xapp_ue_mitigation_active/prb_limit + xapp_mitigations_applied_total
Grafana per_ue_live.json state-timeline
  → renders per-RNTI throttle history
```

## Testing

- **Exporter unit tests** (`exporter/test_csv_exporter.py`): add a
  mitigation-events fixture; assert `xapp_ue_mitigation_active{rnti}` toggles 1 on
  THROTTLE and 0 on RESTORE, `xapp_ue_mitigation_prb_limit` follows, and
  `xapp_mitigations_applied_total` increments on THROTTLE only. Add a fixture for
  the `/tmp/xapp_active_threshold` sidecar → assert `xapp_ue_threshold` and
  `xapp_ue_model_info`.
- **C build:** `cd ~/flexric/build && make -j$(nproc) xapp_sec_moni` compiles
  clean.
- **End-to-end (manual):** run `start_xapp_c_mitigate.sh` (gru-hybrid), trigger a
  scripted UL flood, confirm a `mitigation_events_*.csv` THROTTLE row appears on
  ACK, the state-timeline lights red, and the MSE threshold line reads 0.026026;
  repeat with lstm mode → line reads 0.027047.
- **Dashboard:** JSON validity (`python -m json.tool`) + Grafana provisioning
  reload without error.

## Risks / open implementation details

- **RNTI availability at `ipc_send_mitigate()`**: must confirm the attacker RNTI
  is in scope at the call site; if not, thread it through as a parameter.
- **CSV directory**: reuse the exact directory the monitor already writes
  `ue_alerts_*.csv` into; do not hardcode a new path.
- **Exporter file rotation**: mitigation-events file is per-startup; exporter must
  pick the newest by glob, consistent with how it already selects `ue_alerts_*`.
