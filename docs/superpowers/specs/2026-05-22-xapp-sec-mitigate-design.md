# Design: xapp_sec_mitigate — E2SM-RC Mitigation xApp

**Date:** 2026-05-22  
**Status:** Approved  
**Author:** telmat

---

## Background & Motivation

The existing `xapp_sec_moni` xApp performs two-stage anomaly detection (rule-based IDS + LSTM).
When Stage 2 (CRITICAL) is confirmed, mitigation via E2SM-RC PRB throttle was previously attempted
inline inside `xapp_sec_moni`. This caused the gNB E2 agent to crash (srsRAN RC Bug #468),
traced to two root causes:

1. Using `SM_RC_ID` (static constant) instead of a dynamically-obtained RC RAN Function ID from the DU.
2. Using `GNB_UE_ID_E2SM` (CU-level) instead of `GNB_DU_UE_ID_E2SM` (DU-level).

A teammate's standalone `xapp_prb_ctrl.c` successfully sent E2SM-RC PRB control by obtaining
the RC RF ID dynamically and using `GNB_DU_UE_ID_E2SM`. This design adopts the same approach
in a new dedicated mitigation xApp that communicates with the detection xApp via UNIX Domain Socket.

---

## Architecture

```
        +-------------------+
        |  xapp_sec_moni    |
        |  (Detection xApp) |
        |  IPC client       |
        +-------------------+
                |
                | Persistent UNIX Domain Socket
                | /tmp/sec_xapp_mitigate.sock
                | JSON + newline delimiter
                v
        +-------------------+
        |  xapp_sec_mitigate|
        |  (Mitigation xApp)|
        |  IPC server       |
        +-------------------+
                |
                | E2SM-RC Control (Style 2, Action 6)
                | Dynamic RC RF ID from DU
                v
           Near-RT RIC
                |
                v
              gNB (srsRAN)
```

**Principle:** Separation of concerns.
- Detection xApp determines: Normal / Warning / Critical.
- Mitigation xApp determines: Throttle / Restore / Error.

---

## File Structure

```
copy-xapp/
├── xapp_sec_moni.c          # existing — add IPC client code
├── xapp_sec_mitigate.c      # NEW: standalone mitigation xApp
├── mitigate_ipc.h           # NEW: shared IPC constants
├── sec_ids.c / sec_ids.h    # existing, unchanged
├── ue_tracker.c / ue_tracker.h  # existing, unchanged
└── vendor/
    ├── cJSON.c              # NEW: cJSON (MIT license, single-file)
    └── cJSON.h
```

Files in `copy-xapp/` are copied to the appropriate FlexRIC source directory
(`~/flexric/examples/xApp/c/monitor/`) and built via FlexRIC's CMake system.
The compiled binary lands at:
`~/flexric/build/examples/xApp/c/monitor/xapp_sec_mitigate`

### `mitigate_ipc.h` — constants only, no complex structs

```c
#define MITIGATE_SOCK_PATH      "/tmp/sec_xapp_mitigate.sock"
#define MITIGATE_ACK_TIMEOUT_MS 500
#define MITIGATE_MAX_MSG_LEN    1024
#define RESTORE_GRACE_MS        10000   /* calm period before RESTORE is sent */
```

Schema is JSON — not duplicated into C structs to preserve extensibility.

### CMakeLists addition

```cmake
add_executable(xapp_sec_mitigate
  xapp_sec_mitigate.c
  vendor/cJSON.c
  ../../../../src/util/alg_ds/alg/defer.c
)
target_link_libraries(xapp_sec_mitigate
  PUBLIC e42_xapp -pthread -lsctp -ldl -lm
)
```

`xapp_sec_moni` CMake target also gains `vendor/cJSON.c`.

---

## IPC Protocol

### Transport

- UNIX Domain Socket (stream), path: `/tmp/sec_xapp_mitigate.sock`
- One persistent client (`xapp_sec_moni`) at a time
- Each message delimited by `\n` (newline) — receiver reads until `\n`
- JSON serialized with `cJSON_PrintUnformatted()` (compact, no whitespace)

### Connection lifecycle

```
[mitigate] bind → listen → init RIC → scan DU → accept (blocks until moni connects)
[moni]     startup → connect loop (retry every 2s until mitigator ready)
           → persistent connection maintained
           → on disconnect: reconnect loop (retry every 2s, log warning)
```

`xapp_sec_moni` detection continues regardless of socket state — IPC failure is non-fatal.

### Request schema (moni → mitigate)

```json
{
  "version": 1,
  "timestamp": 1748000000,
  "attack": "UL_FLOOD",
  "severity": "CRITICAL",
  "confidence": 0.94,
  "action": "THROTTLE",
  "prb_limit": 5,
  "reason": "stage2_persistence_confirmed"
}
```

| Field | Type | Values |
|---|---|---|
| `version` | int | `1` |
| `timestamp` | int | Unix epoch seconds |
| `attack` | string | `"UL_FLOOD"`, `"DL_FLOOD"`, `"RRC_STORM"`, `"RADIO_DEGRADATION"`, `"BURST_ANOMALY"`, `"UNKNOWN"` |
| `severity` | string | `"WARNING"`, `"CRITICAL"` |
| `confidence` | float | LSTM anomaly score (0.0–1.0), `0.0` if rule-only |
| `action` | string | `"THROTTLE"` or `"RESTORE"` |
| `prb_limit` | int | 1–100 |
| `reason` | string | human-readable trigger string |

For `RESTORE`: `action="RESTORE"`, `prb_limit=100`.

### Response schema (mitigate → moni)

```json
{
  "status": "OK",
  "action": "THROTTLE",
  "applied": true,
  "message": "PRB quota applied"
}
```

| Field | Type | Values |
|---|---|---|
| `status` | string | `"OK"` or `"ERROR"` |
| `action` | string | echoes request `action` |
| `applied` | bool | `true` if E2SM-RC was sent successfully |
| `message` | string | description or error reason |

Error example:
```json
{
  "status": "ERROR",
  "action": "THROTTLE",
  "applied": false,
  "message": "RIC_CONTROL_FAILURE"
}
```

No silent failures — mitigator always sends ACK.

### When moni sends messages

| Condition | Action | prb_limit |
|---|---|---|
| `rule_based_detect()` returns 2 AND throttle not active AND cooldown elapsed | `THROTTLE` | 5 |
| Severity == 0 for `RESTORE_GRACE_MS` (10s) AND throttle is active | `RESTORE` | 100 |

Cooldown: `THROTTLE_COOLDOWN_MS = 30000` — no two THROTTLEs within 30s.

### ACK timeout behavior in moni

```
send JSON\n → setsockopt(SO_RCVTIMEO = 500ms) → recv ACK
  ├── OK     → set g_throttle_active=1, log success
  └── timeout/error → log "[IPC] WARNING: ACK timeout"
                     → g_throttle_active stays 0
                     → detection continues normally
```

---

## E2SM-RC Execution (Mitigator)

### Root cause fix

| Old (crashed) | New (working) |
|---|---|
| `SM_RC_ID` static constant | `rc_rf_id` obtained dynamically from DU RAN function list |
| `GNB_UE_ID_E2SM` (CU-level) | `GNB_DU_UE_ID_E2SM` with `gnb_cu_ue_f1ap` |
| Named constants (`RRM_Policy_Ratio_List_8_4_3_6`) | Numeric IDs (1–13) |
| Inline allocation with raw pointers | Helper builder functions |

### Startup sequence

```
1. Parse CLI args
2. bind(UNIX socket) → listen()          ← socket ready before RIC
3. init_xapp_api()                        ← connect to Near-RT RIC
4. e2_nodes_xapp_api() → find DU → get rc_rf_id dynamically
5. accept() client connection
6. Loop: recv JSON\n → validate → execute E2SM-RC → send ACK\n
```

### Helper builders (same as xapp_prb_ctrl.c)

```c
make_int_param(id, val)
make_octet_param(id, data, len)
make_struct_param(id, children, n)
make_list_param_single(id, child)
```

### PRB parameter mapping

| JSON action | min_prb | max_prb | ded_prb |
|---|---|---|---|
| `THROTTLE` | `0` | `prb_limit` | `prb_limit` |
| `RESTORE` | `0` | `100` | `100` |

`prb_limit` from JSON is clamped before use:
```c
if (prb_limit < 1)   prb_limit = 1;
if (prb_limit > 100) prb_limit = 100;
if (action == RESTORE) prb_limit = 100;  /* override regardless of JSON */
```

### CLI args

```
./xapp_sec_mitigate \
    -c /usr/local/etc/flexric/flexric.conf \
    --ue_f1ap 1     \   # gnb_cu_ue_f1ap_id (default: 1)
    --mcc     001   \   # PLMN MCC (default: 001)
    --mnc     01    \   # PLMN MNC (default: 01)
    --sst     1         # S-NSSAI SST (default: 1)
```

PRB limits come from IPC JSON — not CLI.

### Signal handling & cleanup

```c
signal(SIGINT,  handle_signal);
signal(SIGTERM, handle_signal);

// on exit:
unlink(MITIGATE_SOCK_PATH);
close(client_fd);
close(server_fd);
try_stop_xapp_api();
```

`unlink()` on exit prevents "address already in use" on next run.

---

## Deployment

Both xApps run as separate processes, typically in separate tmux panes:

```bash
# Pane A: mitigation xApp (start first — it's the server)
./xapp_sec_mitigate -c flexric.conf --ue_f1ap 1

# Pane B: detection xApp (connects to mitigator on startup)
./xapp_sec_moni -c flexric.conf --label 0 --mode hybrid
```

The start script (`start_xapp_c_mitigate.sh`) will be updated:
- Remove the iptables watcher (Layer 2 fallback) — replaced by RC throttle
- Add a pane for `xapp_sec_mitigate`
- Start mitigator before `xapp_sec_moni`

---

## Buku TA Wording

> Komunikasi antar xApp dilakukan menggunakan persistent UNIX domain socket dengan mekanisme
> auto-reconnect. Detection xApp bertindak sebagai client yang mengirim pesan mitigasi berbasis
> JSON ketika Stage 2 menghasilkan status CRITICAL, sedangkan mitigation xApp bertindak sebagai
> server yang menerjemahkan pesan tersebut menjadi E2SM-RC Control. Format JSON diimplementasikan
> menggunakan library cJSON sebagai dependency eksternal ringan (satu file `.c`/`.h`, lisensi MIT)
> untuk menjaga interoperabilitas, kemudahan pengembangan, dan extensibility terhadap penambahan
> metadata keamanan di masa depan. Desain ini menjaga modularitas, mendukung acknowledgement,
> dan tetap ringan untuk lingkungan Near-RT RIC.
