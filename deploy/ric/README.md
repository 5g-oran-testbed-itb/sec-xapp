# RIC Node Deployment

Near-RT RIC node (`10.91.2.2`): FlexRIC core + the security xApp
(`xapp_sec_moni` / `xapp_sec_mitigate`), built from the `vendor/flexric`
submodule.

## Build

```bash
cd vendor/flexric
mkdir -p build && cd build
cmake ..
make -j$(nproc) xapp_sec_moni xapp_sec_mitigate
```

Binaries land at `vendor/flexric/build/examples/xApp/c/monitor/`.

## Run

### Startup (tmux, orchestrates RIC + gNB SSH + xApp)

```bash
./deploy/ric/start_xapp_c.sh
# Attach: tmux attach -t xapp_c
```

Wait for `E2AP listening on :36421` in the RIC pane, then E2 Setup success in
the gNB pane, connect the UE, then press ENTER in the left-bottom pane to
start `xapp_sec_moni`.

### Manual (no script)

```bash
# RIC
vendor/flexric/build/examples/ric/nearRT-RIC

# xApp -- detection + recording (default, no mitigation)
vendor/flexric/build/examples/xApp/c/monitor/xapp_sec_moni \
    -c deploy/ric/my_xapp_kpm.conf

# xApp -- with E2SM-RC PRB throttle mitigation (opt-in)
vendor/flexric/build/examples/xApp/c/monitor/xapp_sec_moni \
    -c deploy/ric/my_xapp_kpm.conf --mitigate
```

| Mode | Command | When |
|---|---|---|
| Detection-only | without `--mitigate` | Dataset collection, testing |
| With mitigation | `--mitigate` | Live demo |

## Hot-label switching

The xApp reads `/tmp/xapp_label` on the RIC node roughly every 120ms via a
`stat()` mtime cache -- the label can change **without restarting the xApp**;
the E2 session and LSTM/GRU sliding window are not reset.

**Format:** `<label>,<scenario>,<attacker_ue>,<epoch_ms>`

```
0,baseline,none,1746861115000
1,ul_flood,UE1,1746861235123
0,recovery,none,1746861480000
```

Label changes are switched from the controller (attack-orchestration scripts,
a separate repo -- see the root `README.md`), and are logged to
`logs/scenario_events.log`:

```
epoch_ms,event,label,scenario,attacker_ue,details
1746861235123,START,1,ul_flood,UE1,
```

## Mitigation modes

**Primary: E2SM-RC PRB throttle** (O-RAN compliant, RC Style 2 / Action 6)

| Parameter | Value |
|---|---|
| Throttle | max=5%, dedicated=5%, min=0% |
| Restore | max=100%, dedicated=100% |
| Cooldown | 30s between throttle events |
| Auto-restore | 10s after severity returns to 0 |
| PLMN / S-NSSAI | `00101` / SST=1 |

**Fallback: SSH AMF subscriber barring** (for signaling-storm attacks, where
PRB throttle is a control-plane no-op) -- see `deploy/core/README.md`.

**Effectiveness by attack type:**

| Attack | Plane | PRB Throttle | SSH AMF |
|---|---|---|---|
| UL / DL Flood | Data | Effective | Overkill |
| Burst ON/OFF | Data | Effective | Overkill |
| Signaling Storm | Control | Not effective | Only effective option |
| RF Jamming | Physical | Not effective | Not effective |

## Known issues

- `DRB.UEThpDl/UL` and `DRB.RlcSduVolumeDL/UL` are always 0 in srsRAN's KPM DU
  report -- the feature schema is PRB-only as a result.
- CQI does not reset to 0 on UE detach in srsRAN (keep-last policy); the
  `empty_ind_rate` proxy signal is used instead of a CQI-based rule.
- srsRAN sends KPM Indications with 0 measurement records on UE detach,
  violating the E2SM-KPM `SIZE(1..65535)` constraint -- FlexRIC's decoder
  rejects these messages, and the resulting decode failures are used as a
  proxy signal for RRC/signaling-storm detection.
