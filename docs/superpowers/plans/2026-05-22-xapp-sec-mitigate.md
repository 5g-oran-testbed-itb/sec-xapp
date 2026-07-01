# xapp_sec_mitigate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `xapp_sec_mitigate`, a standalone C xApp that receives THROTTLE/RESTORE commands from `xapp_sec_moni` via persistent UNIX Domain Socket (JSON/cJSON) and executes E2SM-RC PRB quota control against a srsRAN gNB via Near-RT RIC, fixing the crash caused by the previous inline RC approach.

**Architecture:** `xapp_sec_moni` (detection xApp) acts as IPC client; `xapp_sec_mitigate` (mitigation xApp) acts as IPC server that parses JSON commands, executes E2SM-RC Style 2 / Action 6 PRB control using a dynamically-obtained RC RF ID, and sends JSON ACKs back. The IPC socket path is `/tmp/sec_xapp_mitigate.sock`.

**Tech Stack:** C11, FlexRIC e42_xapp_api, E2SM-RC (numeric IDs 1–13), cJSON (MIT, single-file), UNIX Domain Sockets, POSIX signals.

**Spec:** `docs/superpowers/specs/2026-05-22-xapp-sec-mitigate-design.md`

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Create | `~/flexric/examples/xApp/c/monitor/vendor/cJSON.c` | JSON library |
| Create | `~/flexric/examples/xApp/c/monitor/vendor/cJSON.h` | JSON library header |
| Create | `~/flexric/examples/xApp/c/monitor/mitigate_ipc.h` | Shared IPC constants |
| Create | `~/flexric/examples/xApp/c/monitor/xapp_sec_mitigate.c` | Mitigation xApp (new binary) |
| Modify | `~/flexric/examples/xApp/c/monitor/CMakeLists.txt` | Add new target + cJSON to moni |
| Modify | `~/flexric/examples/xApp/c/monitor/xapp_sec_moni.c` | Add IPC client code |
| Modify | `~/sec-xapp/start_xapp_c_mitigate.sh` | Add mitigator pane, remove iptables watcher |
| Sync | `~/sec-xapp/copy-xapp/` | Git-tracked copy of source files |

**Important:** `~/flexric/examples/xApp/c/monitor/` is the canonical build source.  
`~/sec-xapp/copy-xapp/` is the git-tracked copy — sync after each task that touches source.  
Build command: `cd ~/flexric/build && make -j$(nproc) xapp_sec_mitigate xapp_sec_moni`

---

## Task 1: Vendor cJSON

**Files:**
- Create: `~/flexric/examples/xApp/c/monitor/vendor/cJSON.c`
- Create: `~/flexric/examples/xApp/c/monitor/vendor/cJSON.h`

- [x] **Step 1: Create vendor directory and download cJSON**

```bash
mkdir -p ~/flexric/examples/xApp/c/monitor/vendor
cd ~/flexric/examples/xApp/c/monitor/vendor
curl -fsSL https://raw.githubusercontent.com/DaveGamble/cJSON/v1.7.18/cJSON.c -o cJSON.c
curl -fsSL https://raw.githubusercontent.com/DaveGamble/cJSON/v1.7.18/cJSON.h -o cJSON.h
```

- [x] **Step 2: Verify files downloaded correctly**

```bash
head -3 ~/flexric/examples/xApp/c/monitor/vendor/cJSON.h
```

Expected output contains: `#ifndef cJSON__h` and version info.

- [x] **Step 3: Sync to copy-xapp**

```bash
mkdir -p ~/sec-xapp/copy-xapp/vendor
cp ~/flexric/examples/xApp/c/monitor/vendor/cJSON.c ~/sec-xapp/copy-xapp/vendor/
cp ~/flexric/examples/xApp/c/monitor/vendor/cJSON.h ~/sec-xapp/copy-xapp/vendor/
```

- [x] **Step 4: Commit**

```bash
cd ~/sec-xapp
git add copy-xapp/vendor/
git commit -m "feat: add cJSON v1.7.18 vendor dependency"
```

---

## Task 2: Create mitigate_ipc.h

**Files:**
- Create: `~/flexric/examples/xApp/c/monitor/mitigate_ipc.h`

- [x] **Step 1: Write the header file**

Create `~/flexric/examples/xApp/c/monitor/mitigate_ipc.h`:

```c
#ifndef MITIGATE_IPC_H
#define MITIGATE_IPC_H

/* Path of the UNIX domain socket the mitigation xApp listens on. */
#define MITIGATE_SOCK_PATH      "/tmp/sec_xapp_mitigate.sock"

/* How long moni waits for an ACK before giving up (milliseconds). */
#define MITIGATE_ACK_TIMEOUT_MS 500

/* Maximum JSON message length (bytes) including newline. */
#define MITIGATE_MAX_MSG_LEN    1024

/* moni waits this long at severity==0 before sending RESTORE (milliseconds). */
#define RESTORE_GRACE_MS        10000

#endif /* MITIGATE_IPC_H */
```

- [x] **Step 2: Sync to copy-xapp and commit**

```bash
cp ~/flexric/examples/xApp/c/monitor/mitigate_ipc.h ~/sec-xapp/copy-xapp/
cd ~/sec-xapp
git add copy-xapp/mitigate_ipc.h
git commit -m "feat: add mitigate_ipc.h IPC constants header"
```

---

## Task 3: Create xapp_sec_mitigate.c

**Files:**
- Create: `~/flexric/examples/xApp/c/monitor/xapp_sec_mitigate.c`

This is the full mitigation xApp. All E2SM-RC helper code is copied from the working
`xapp_prb_ctrl.c` (examples/xApp/c/ctrl/) to avoid the crash from the old inline approach.

- [x] **Step 1: Write xapp_sec_mitigate.c**

Create `~/flexric/examples/xApp/c/monitor/xapp_sec_mitigate.c`:

```c
/*
 * xapp_sec_mitigate — E2SM-RC PRB Throttle Mitigation xApp
 * =========================================================
 * Listens on UNIX Domain Socket /tmp/sec_xapp_mitigate.sock.
 * Receives JSON commands from xapp_sec_moni:
 *   {"action":"THROTTLE","prb_limit":5, ...}
 *   {"action":"RESTORE", "prb_limit":100, ...}
 * Executes E2SM-RC Style 2 / Action 6 PRB quota control.
 * Replies with JSON ACK.
 *
 * Build:
 *   cd ~/flexric/build && make -j$(nproc) xapp_sec_mitigate
 *
 * Run (start before xapp_sec_moni):
 *   ./build/examples/xApp/c/monitor/xapp_sec_mitigate \
 *       -c /usr/local/etc/flexric/flexric.conf \
 *       --ue_f1ap 1 --mcc 001 --mnc 01 --sst 1
 */

#include "../../../../src/xApp/e42_xapp_api.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_struct.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_list.h"
#include "../../../../src/sm/rc_sm/ie/ir/lst_ran_param.h"
#include "../../../../src/sm/rc_sm/ie/ir/e2sm_rc_ctrl_hdr_frmt_1.h"
#include "../../../../src/sm/rc_sm/ie/ir/e2sm_rc_ctrl_msg_frmt_1.h"
#include "../../../../src/sm/rc_sm/rc_sm_ric.h"
#include "../../../../src/util/time_now_us.h"
#include "../../../../src/util/ngran_types.h"

#include "mitigate_ipc.h"
#include "vendor/cJSON.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdint.h>
#include <assert.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <errno.h>

/* ─── CLI config ────────────────────────────────────────────────────── */
static uint32_t g_ue_f1ap = 1;
static uint8_t  g_sst     = 1;
static uint8_t  g_plmn[3] = {0x00, 0xf0, 0x10}; /* 001-01 */

/* ─── E2 state ──────────────────────────────────────────────────────── */
static global_e2_node_id_t g_du_node_id;
static int      g_du_node_valid = 0;
static uint32_t g_rc_rf_id      = 0;

/* ─── Socket state ──────────────────────────────────────────────────── */
static int g_server_fd = -1;
static int g_client_fd = -1;

/* ─── Signal ────────────────────────────────────────────────────────── */
static volatile int g_running = 1;

static void handle_signal(int sig)
{
    (void)sig;
    g_running = 0;
}

static void cleanup(void)
{
    if (g_client_fd >= 0) { close(g_client_fd); g_client_fd = -1; }
    if (g_server_fd >= 0) { close(g_server_fd); g_server_fd = -1; }
    unlink(MITIGATE_SOCK_PATH);
    if (g_du_node_valid)
        try_stop_xapp_api();
}

/* ─── PLMN encoder ──────────────────────────────────────────────────── */
static void encode_plmn(const char* mcc, const char* mnc, uint8_t out[3])
{
    uint8_t m[3] = {0}, n[3] = {0xf, 0xf, 0xf};
    for (int i = 0; i < 3 && mcc[i]; i++) m[i] = mcc[i] - '0';
    int nl = (int)strlen(mnc);
    for (int i = 0; i < nl && mnc[i]; i++) n[i] = mnc[i] - '0';
    out[0] = (m[1] << 4) | m[0];
    out[1] = (nl == 2 ? 0xf0 : (n[2] << 4)) | m[2];
    out[2] = (n[1] << 4) | n[0];
}

/* ─── E2SM-RC helper builders (from xapp_prb_ctrl.c — proven working) ─ */
static seq_ran_param_t make_int_param(uint64_t id, int64_t val)
{
    seq_ran_param_t p = {0};
    p.ran_param_id = id;
    p.ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
    p.ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
    assert(p.ran_param_val.flag_false);
    p.ran_param_val.flag_false->type    = INTEGER_RAN_PARAMETER_VALUE;
    p.ran_param_val.flag_false->int_ran = val;
    return p;
}

static seq_ran_param_t make_octet_param(uint64_t id,
                                         const uint8_t* data, size_t len)
{
    seq_ran_param_t p = {0};
    p.ran_param_id = id;
    p.ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
    p.ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
    assert(p.ran_param_val.flag_false);
    p.ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
    p.ran_param_val.flag_false->octet_str_ran.buf = calloc(len, 1);
    assert(p.ran_param_val.flag_false->octet_str_ran.buf);
    memcpy(p.ran_param_val.flag_false->octet_str_ran.buf, data, len);
    p.ran_param_val.flag_false->octet_str_ran.len = len;
    return p;
}

static seq_ran_param_t make_struct_param(uint64_t id,
                                          seq_ran_param_t* children,
                                          size_t n)
{
    seq_ran_param_t p = {0};
    p.ran_param_id = id;
    p.ran_param_val.type  = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
    p.ran_param_val.strct = calloc(1, sizeof(ran_param_struct_t));
    assert(p.ran_param_val.strct);
    p.ran_param_val.strct->sz_ran_param_struct = n;
    p.ran_param_val.strct->ran_param_struct    = calloc(n, sizeof(seq_ran_param_t));
    assert(p.ran_param_val.strct->ran_param_struct);
    memcpy(p.ran_param_val.strct->ran_param_struct, children,
           n * sizeof(seq_ran_param_t));
    return p;
}

static seq_ran_param_t make_list_param_single(uint64_t id,
                                               seq_ran_param_t* child)
{
    seq_ran_param_t p = {0};
    p.ran_param_id = id;
    p.ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
    p.ran_param_val.lst  = calloc(1, sizeof(ran_param_list_t));
    assert(p.ran_param_val.lst);
    p.ran_param_val.lst->sz_lst_ran_param = 1;
    p.ran_param_val.lst->lst_ran_param    = calloc(1, sizeof(lst_ran_param_t));
    assert(p.ran_param_val.lst->lst_ran_param);
    lst_ran_param_t* li = &p.ran_param_val.lst->lst_ran_param[0];
    li->ran_param_struct.sz_ran_param_struct = 1;
    li->ran_param_struct.ran_param_struct    = calloc(1, sizeof(seq_ran_param_t));
    assert(li->ran_param_struct.ran_param_struct);
    li->ran_param_struct.ran_param_struct[0] = *child;
    return p;
}

/* ─── Build Control Message ─────────────────────────────────────────── */
static e2sm_rc_ctrl_msg_frmt_1_t build_ctrl_msg(int max_prb)
{
    /* ID 9: SST */
    uint8_t sst_byte = g_sst;
    seq_ran_param_t p9 = make_octet_param(9, &sst_byte, 1);

    /* ID 8: S-NSSAI {ID9} */
    seq_ran_param_t p8 = make_struct_param(8, &p9, 1);

    /* ID 7: PLMN */
    seq_ran_param_t p7 = make_octet_param(7, g_plmn, 3);

    /* ID 6: RRM Policy Member {ID7, ID8} */
    seq_ran_param_t p6_ch[2] = {p7, p8};
    seq_ran_param_t p6 = make_struct_param(6, p6_ch, 2);

    /* ID 5: RRM Policy Member List [ID6] */
    seq_ran_param_t p5 = make_list_param_single(5, &p6);

    /* ID 11: min=0, ID 12: max=max_prb, ID 13: ded=max_prb */
    seq_ran_param_t p11 = make_int_param(11, 0);
    seq_ran_param_t p12 = make_int_param(12, (int64_t)max_prb);
    seq_ran_param_t p13 = make_int_param(13, (int64_t)max_prb);

    /* ID 3: RRM Policy {ID5, ID11, ID12, ID13} */
    seq_ran_param_t p3_ch[4] = {p5, p11, p12, p13};
    seq_ran_param_t p3 = make_struct_param(3, p3_ch, 4);

    /* ID 2: RRM Policy Ratio Group {ID3} */
    seq_ran_param_t p2 = make_struct_param(2, &p3, 1);

    /* ID 1: RRM Policy Ratio List [ID2] */
    seq_ran_param_t p1 = make_list_param_single(1, &p2);

    e2sm_rc_ctrl_msg_frmt_1_t msg = {0};
    msg.sz_ran_param = 1;
    msg.ran_param    = calloc(1, sizeof(seq_ran_param_t));
    assert(msg.ran_param);
    msg.ran_param[0] = p1;
    return msg;
}

/* ─── Build Control Header ──────────────────────────────────────────── */
static e2sm_rc_ctrl_hdr_frmt_1_t build_ctrl_hdr(void)
{
    e2sm_rc_ctrl_hdr_frmt_1_t hdr = {0};
    hdr.ric_style_type           = 2;
    hdr.ctrl_act_id              = 6;
    hdr.ue_id.type               = GNB_DU_UE_ID_E2SM;
    hdr.ue_id.gnb_du.gnb_cu_ue_f1ap = g_ue_f1ap;
    return hdr;
}

/* ─── Execute E2SM-RC PRB control ───────────────────────────────────── */
static int execute_rc_control(int max_prb)
{
    if (!g_du_node_valid || g_rc_rf_id == 0) {
        printf("[MITIGATE] ERROR: DU node or RC RF ID not available\n");
        fflush(stdout);
        return 0;
    }

    rc_ctrl_req_data_t rc_ctrl = {0};
    rc_ctrl.hdr.format = FORMAT_1_E2SM_RC_CTRL_HDR;
    rc_ctrl.hdr.frmt_1 = build_ctrl_hdr();
    rc_ctrl.msg.format = FORMAT_1_E2SM_RC_CTRL_MSG;
    rc_ctrl.msg.frmt_1 = build_ctrl_msg(max_prb);

    control_sm_xapp_api(&g_du_node_id, g_rc_rf_id, &rc_ctrl);
    free_rc_ctrl_req_data(&rc_ctrl);
    printf("[MITIGATE] E2SM-RC sent: max_prb=%d%% (check gNB log for ACK)\n", max_prb);
    fflush(stdout);
    return 1;
}

/* ─── IPC receive loop ──────────────────────────────────────────────── */
static void ipc_recv_loop(int client_fd)
{
    char buf[MITIGATE_MAX_MSG_LEN];

    while (g_running) {
        int pos = 0;
        while (pos < (int)sizeof(buf) - 1 && g_running) {
            ssize_t n = recv(client_fd, buf + pos, 1, 0);
            if (n <= 0) {
                printf("[IPC] Client disconnected (recv=%zd errno=%d)\n", n, errno);
                fflush(stdout);
                return;
            }
            if (buf[pos] == '\n') { buf[pos] = '\0'; break; }
            pos++;
        }
        if (!g_running) break;
        if (pos == 0) continue;

        /* Parse JSON */
        cJSON* root = cJSON_Parse(buf);
        if (!root) {
            const char* err = "{\"status\":\"ERROR\",\"action\":\"UNKNOWN\","
                              "\"applied\":false,\"message\":\"JSON_PARSE_ERROR\"}\n";
            send(client_fd, err, strlen(err), MSG_NOSIGNAL);
            printf("[IPC] JSON parse error: %s\n", buf);
            fflush(stdout);
            continue;
        }

        cJSON* j_action = cJSON_GetObjectItemCaseSensitive(root, "action");
        cJSON* j_prb    = cJSON_GetObjectItemCaseSensitive(root, "prb_limit");
        cJSON* j_attack = cJSON_GetObjectItemCaseSensitive(root, "attack");

        if (!cJSON_IsString(j_action) || !cJSON_IsNumber(j_prb)) {
            const char* err = "{\"status\":\"ERROR\",\"action\":\"UNKNOWN\","
                              "\"applied\":false,\"message\":\"MISSING_FIELDS\"}\n";
            send(client_fd, err, strlen(err), MSG_NOSIGNAL);
            cJSON_Delete(root);
            printf("[IPC] Missing required fields in: %s\n", buf);
            fflush(stdout);
            continue;
        }

        const char* action    = j_action->valuestring;
        const char* attack    = cJSON_IsString(j_attack) ? j_attack->valuestring : "UNKNOWN";
        int         prb_limit = (int)j_prb->valuedouble;

        /* Clamp and override */
        if (prb_limit < 1)   prb_limit = 1;
        if (prb_limit > 100) prb_limit = 100;
        if (strcmp(action, "RESTORE") == 0) prb_limit = 100;

        printf("[IPC] action=%s attack=%s prb_limit=%d\n", action, attack, prb_limit);
        fflush(stdout);

        int ok = execute_rc_control(prb_limit);

        /* Build and send ACK */
        cJSON* ack = cJSON_CreateObject();
        cJSON_AddStringToObject(ack, "status",  ok ? "OK" : "ERROR");
        cJSON_AddStringToObject(ack, "action",  action);
        cJSON_AddBoolToObject  (ack, "applied", ok ? 1 : 0);
        cJSON_AddStringToObject(ack, "message", ok ? "PRB quota applied"
                                                    : "RIC_CONTROL_FAILURE");
        char* ack_str = cJSON_PrintUnformatted(ack);
        cJSON_Delete(ack);
        cJSON_Delete(root);

        size_t alen  = strlen(ack_str);
        char*  ack_nl = malloc(alen + 2);
        assert(ack_nl);
        memcpy(ack_nl, ack_str, alen);
        ack_nl[alen]   = '\n';
        ack_nl[alen+1] = '\0';
        free(ack_str);

        send(client_fd, ack_nl, alen + 1, MSG_NOSIGNAL);
        free(ack_nl);
    }
}

/* ─── Main ──────────────────────────────────────────────────────────── */
int main(int argc, char* argv[])
{
    char mcc[8] = "001", mnc[8] = "01";

    signal(SIGINT,  handle_signal);
    signal(SIGTERM, handle_signal);
    atexit(cleanup);

    /* Parse custom args; strip them before passing to init_fr_args */
    char* fr_argv[32];
    int   fr_argc = 0;
    fr_argv[fr_argc++] = argv[0];

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--ue_f1ap") && i + 1 < argc)
            { g_ue_f1ap = (uint32_t)atoi(argv[++i]); continue; }
        if (!strcmp(argv[i], "--sst") && i + 1 < argc)
            { g_sst = (uint8_t)atoi(argv[++i]); continue; }
        if (!strcmp(argv[i], "--mcc") && i + 1 < argc)
            { strncpy(mcc, argv[++i], 7); continue; }
        if (!strcmp(argv[i], "--mnc") && i + 1 < argc)
            { strncpy(mnc, argv[++i], 7); continue; }
        if (!strcmp(argv[i], "--help")) {
            printf("Usage: xapp_sec_mitigate -c <flexric.conf>\n");
            printf("  --ue_f1ap N   gnb_cu_ue_f1ap_id (default 1)\n");
            printf("  --mcc     S   PLMN MCC (default 001)\n");
            printf("  --mnc     S   PLMN MNC (default 01)\n");
            printf("  --sst     N   S-NSSAI SST (default 1)\n");
            return 0;
        }
        /* FlexRIC flags: -c -p -a -d -n */
        if ((!strcmp(argv[i], "-c") || !strcmp(argv[i], "-p") ||
             !strcmp(argv[i], "-a") || !strcmp(argv[i], "-d") ||
             !strcmp(argv[i], "-n")) && i + 1 < argc) {
            fr_argv[fr_argc++] = argv[i];
            fr_argv[fr_argc++] = argv[++i];
        }
    }

    encode_plmn(mcc, mnc, g_plmn);

    printf("========================================\n");
    printf("  xApp: E2SM-RC Mitigation (IPC Server)\n");
    printf("  Socket : %s\n", MITIGATE_SOCK_PATH);
    printf("  UE F1AP: %u  MCC=%s MNC=%s SST=%u\n",
           g_ue_f1ap, mcc, mnc, g_sst);
    printf("========================================\n\n");

    /* Step 1: Bind UNIX socket BEFORE connecting to RIC */
    unlink(MITIGATE_SOCK_PATH); /* remove stale socket from previous run */

    g_server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (g_server_fd < 0) { perror("socket"); return 1; }

    struct sockaddr_un addr = {0};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, MITIGATE_SOCK_PATH, sizeof(addr.sun_path) - 1);

    if (bind(g_server_fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("bind"); return 1;
    }
    if (listen(g_server_fd, 1) < 0) {
        perror("listen"); return 1;
    }
    printf("[IPC] Listening on %s\n\n", MITIGATE_SOCK_PATH);
    fflush(stdout);

    /* Step 2: Connect to Near-RT RIC and get E2 nodes */
    fr_args_t args = init_fr_args(fr_argc, fr_argv);
    init_xapp_api(&args);
    free_fr_args(&args);
    sleep(1);

    e2_node_arr_xapp_t nodes = e2_nodes_xapp_api();
    if (nodes.len == 0) {
        fprintf(stderr, "[ERROR] No E2 nodes connected.\n");
        return 1;
    }

    /* Find DU node and RC RF ID */
    int du_idx = -1;
    for (int i = 0; i < (int)nodes.len; i++) {
        printf("  Node[%d] type=%d\n", i, nodes.n[i].id.type);
        if (NODE_IS_DU(nodes.n[i].id.type) && du_idx < 0)
            du_idx = i;
    }
    if (du_idx < 0) {
        fprintf(stderr, "[ERROR] No DU node found.\n");
        free_e2_node_arr_xapp(&nodes);
        return 1;
    }

    for (size_t i = 0; i < nodes.n[du_idx].len_rf; i++) {
        if (nodes.n[du_idx].rf[i].defn.type == RC_RAN_FUNC_DEF_E) {
            g_rc_rf_id = nodes.n[du_idx].rf[i].id;
            printf("[xApp] RC RF ID = %u (DU index %d)\n", g_rc_rf_id, du_idx);
            break;
        }
    }
    if (g_rc_rf_id == 0) {
        fprintf(stderr, "[ERROR] RC RAN Function not found — check e2sm_rc_enabled in gnb.yml\n");
        free_e2_node_arr_xapp(&nodes);
        return 1;
    }

    g_du_node_id    = cp_global_e2_node_id(&nodes.n[du_idx].id);
    g_du_node_valid = 1;
    free_e2_node_arr_xapp(&nodes);

    /* Step 3: Accept client and loop */
    printf("[IPC] Waiting for xapp_sec_moni to connect...\n");
    fflush(stdout);

    while (g_running) {
        struct timeval tv = { .tv_sec = 1, .tv_usec = 0 };
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(g_server_fd, &fds);
        int sel = select(g_server_fd + 1, &fds, NULL, NULL, &tv);
        if (sel <= 0) continue; /* timeout or signal */

        g_client_fd = accept(g_server_fd, NULL, NULL);
        if (g_client_fd < 0) {
            if (g_running) perror("accept");
            continue;
        }
        printf("[IPC] xapp_sec_moni connected.\n");
        fflush(stdout);

        ipc_recv_loop(g_client_fd);

        close(g_client_fd);
        g_client_fd = -1;
        if (g_running)
            printf("[IPC] Client disconnected. Waiting for reconnect...\n");
        fflush(stdout);
    }

    printf("\n[xApp] Shutting down.\n");
    return 0;
}
```

- [x] **Step 2: Sync to copy-xapp and commit**

```bash
cp ~/flexric/examples/xApp/c/monitor/xapp_sec_mitigate.c ~/sec-xapp/copy-xapp/
cd ~/sec-xapp
git add copy-xapp/xapp_sec_mitigate.c
git commit -m "feat: add xapp_sec_mitigate IPC server + E2SM-RC executor"
```

---

## Task 4: Update CMakeLists.txt

**Files:**
- Modify: `~/flexric/examples/xApp/c/monitor/CMakeLists.txt`

- [x] **Step 1: Add xapp_sec_mitigate target and vendor/cJSON.c to xapp_sec_moni**

At the end of `~/flexric/examples/xApp/c/monitor/CMakeLists.txt`, append:

```cmake
# xapp_sec_mitigate
add_executable(xapp_sec_mitigate
    xapp_sec_mitigate.c
    vendor/cJSON.c
    ${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c
)

target_link_libraries(xapp_sec_mitigate
    PUBLIC
    e42_xapp
    $<TARGET_OBJECTS:e2_time_obj>
    -pthread
    -lsctp
    -ldl
    -lm
)
```

Also, find the `add_executable(xapp_sec_moni ...)` block and add `vendor/cJSON.c` to its source list:

```cmake
add_executable(xapp_sec_moni
    xapp_sec_moni.c
    sec_ids.c
    ue_tracker.c
    vendor/cJSON.c
    ${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c
    ${CMAKE_SOURCE_DIR}/src/util/e2ap_ngran_type.c
)
```

- [x] **Step 2: Test build**

```bash
cd ~/flexric/build
cmake .. -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -5
make -j$(nproc) xapp_sec_mitigate 2>&1 | tail -20
```

Expected: binary at `~/flexric/build/examples/xApp/c/monitor/xapp_sec_mitigate`

```bash
ls -lh ~/flexric/build/examples/xApp/c/monitor/xapp_sec_mitigate
```

- [x] **Step 3: Sync CMakeLists to copy-xapp and commit**

```bash
cp ~/flexric/examples/xApp/c/monitor/CMakeLists.txt ~/sec-xapp/copy-xapp/
cd ~/sec-xapp
git add copy-xapp/CMakeLists.txt
git commit -m "build: add xapp_sec_mitigate target and vendor/cJSON to CMakeLists"
```

---

## Task 5: Smoke-test xapp_sec_mitigate IPC (without RIC)

Before touching `xapp_sec_moni`, verify the socket + JSON path works in isolation.

- [x] **Step 1: Start mitigator in socket-only mode for testing**

In terminal A, temporarily run the binary with a dummy config path to test socket (it will fail RIC init but socket will bind):

Actually the mitigator needs RIC. Use `socat` to test the socket after starting the mitigator
with the actual RIC running in your testbed. Skip this step if testbed is not available and
proceed directly to Task 6.

If testbed available:
```bash
# Terminal A: start mitigator
~/flexric/build/examples/xApp/c/monitor/xapp_sec_mitigate \
    -c ~/sec-xapp/my_xapp_kpm.conf --ue_f1ap 1
```

```bash
# Terminal B: send test THROTTLE message
echo '{"version":1,"timestamp":1748000000,"attack":"UL_FLOOD","severity":"CRITICAL","confidence":0.94,"action":"THROTTLE","prb_limit":5,"reason":"test"}' \
    | socat - UNIX-CONNECT:/tmp/sec_xapp_mitigate.sock
```

Expected response:
```json
{"status":"OK","action":"THROTTLE","applied":true,"message":"PRB quota applied"}
```

```bash
# Terminal B: send test RESTORE
echo '{"version":1,"timestamp":1748000000,"attack":"NONE","severity":"CRITICAL","confidence":0.0,"action":"RESTORE","prb_limit":100,"reason":"recovery"}' \
    | socat - UNIX-CONNECT:/tmp/sec_xapp_mitigate.sock
```

Expected:
```json
{"status":"OK","action":"RESTORE","applied":true,"message":"PRB quota applied"}
```

---

## Task 6: Add IPC client to xapp_sec_moni.c

**Files:**
- Modify: `~/flexric/examples/xApp/c/monitor/xapp_sec_moni.c`

- [x] **Step 1: Add IPC includes and globals after existing includes**

Find the block ending with `#include "ue_tracker.h"` (around line 52) and add after it:

```c
#include "mitigate_ipc.h"
#include "vendor/cJSON.h"
#include <sys/socket.h>
#include <sys/un.h>
```

Then find `static int g_mitigate_enabled = 0;` (around line 441) and add after it:

```c
/* ─── IPC client state ────────────────────────────────────────────────
 * g_ipc_fd == -1 means disconnected. Connection is attempted at startup
 * and re-attempted each main-loop iteration if lost.                    */
static int      g_ipc_fd        = -1;
static uint64_t g_sev0_since_ms = 0;  /* epoch ms when severity first hit 0 */
```

- [x] **Step 2: Add ipc_try_connect() and ipc_send_mitigate() functions**

Add these two functions immediately before `static void rc_send_prb_quota(int max_prb_pct)` (around line 443):

```c
/* ─── IPC: try connect to mitigator (non-blocking, returns fd or -1) ── */
static int ipc_try_connect(void)
{
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    struct sockaddr_un addr = {0};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, MITIGATE_SOCK_PATH, sizeof(addr.sun_path) - 1);
    if (connect(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

/* ─── IPC: send mitigation command and wait for ACK ─────────────────── */
static void ipc_send_mitigate(const char* action, int prb_limit,
                               const char* attack, float confidence,
                               const char* reason)
{
    if (g_ipc_fd < 0) {
        printf("[IPC] Not connected — %s skipped\n", action);
        fflush(stdout);
        return;
    }

    time_t ts = time(NULL);
    cJSON* msg = cJSON_CreateObject();
    cJSON_AddNumberToObject(msg, "version",    1);
    cJSON_AddNumberToObject(msg, "timestamp",  (double)ts);
    cJSON_AddStringToObject(msg, "attack",     attack);
    cJSON_AddStringToObject(msg, "severity",   "CRITICAL");
    cJSON_AddNumberToObject(msg, "confidence", (double)confidence);
    cJSON_AddStringToObject(msg, "action",     action);
    cJSON_AddNumberToObject(msg, "prb_limit",  prb_limit);
    cJSON_AddStringToObject(msg, "reason",     reason);
    char* json = cJSON_PrintUnformatted(msg);
    cJSON_Delete(msg);

    size_t jlen = strlen(json);
    char*  buf  = malloc(jlen + 2);
    assert(buf);
    memcpy(buf, json, jlen);
    buf[jlen]   = '\n';
    buf[jlen+1] = '\0';
    free(json);

    ssize_t sent = send(g_ipc_fd, buf, jlen + 1, MSG_NOSIGNAL);
    free(buf);
    if (sent < 0) {
        printf("[IPC] send failed — mitigator disconnected\n");
        fflush(stdout);
        close(g_ipc_fd);
        g_ipc_fd = -1;
        return;
    }

    /* Wait for ACK */
    struct timeval tv = { .tv_sec = 0,
                          .tv_usec = MITIGATE_ACK_TIMEOUT_MS * 1000 };
    setsockopt(g_ipc_fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    char ack_buf[MITIGATE_MAX_MSG_LEN];
    ssize_t n = recv(g_ipc_fd, ack_buf, sizeof(ack_buf) - 1, 0);
    if (n <= 0) {
        printf("[IPC] WARNING: ACK timeout for %s — mitigation may not be applied\n",
               action);
        fflush(stdout);
        return;
    }
    ack_buf[n] = '\0';
    printf("[IPC] ACK received: %s\n", ack_buf);
    fflush(stdout);
}
```

- [x] **Step 3: Replace main-loop throttle logic with IPC calls**

Find the main loop block (around line 1793–1828):

```c
  /* Main loop: process pending RC PRB throttle actions outside callbacks.
   * sm_cb_kpm sets g_pending_throttle; we apply here to avoid blocking the
   * KPM callback thread with a network call while holding the mutex.      */
  while(keep_running) {
    sleep(1);

    struct timespec ts_main;
    clock_gettime(CLOCK_REALTIME, &ts_main);
    uint64_t now_ms = (uint64_t)ts_main.tv_sec * 1000ULL
                      + ts_main.tv_nsec / 1000000ULL;

    if (g_pending_throttle == 1 && !g_throttle_active
        && (now_ms - g_throttle_last_ms > THROTTLE_COOLDOWN_MS)) {
      if (g_mitigate_enabled) {
        printf("[MITIGATE] CRITICAL detected — applying E2SM-RC PRB throttle (max=5%%).\n");
        fflush(stdout);
        rc_send_prb_quota(5);
        g_throttle_active  = 1;
        g_throttle_last_ms = now_ms;
      } else {
        printf("[DETECT] CRITICAL — RC PRB throttle skipped (run with --mitigate to enable).\n");
        fflush(stdout);
      }
      g_pending_throttle = 0;
    } else if (g_pending_throttle == 2 && g_throttle_active
               && (now_ms - g_throttle_last_ms > THROTTLE_RESTORE_MS)) {
      if (g_mitigate_enabled) {
        printf("[MITIGATE] Attack subsided — restoring PRB quota (max=100%%).\n");
        fflush(stdout);
        rc_send_prb_quota(100);
      }
      g_throttle_active  = 0;
      g_throttle_last_ms = now_ms;
      g_pending_throttle = 0;
    }
  }
```

Replace the entire `while(keep_running)` loop with:

```c
  /* Attempt initial IPC connection to xapp_sec_mitigate */
  {
    int fd = ipc_try_connect();
    if (fd >= 0) {
      g_ipc_fd = fd;
      printf("[IPC] Connected to xapp_sec_mitigate at %s\n", MITIGATE_SOCK_PATH);
      fflush(stdout);
    } else {
      printf("[IPC] xapp_sec_mitigate not yet available — will retry each second\n");
      fflush(stdout);
    }
  }

  /* Main loop: IPC reconnect + THROTTLE/RESTORE dispatch */
  while(keep_running) {
    sleep(1);

    /* Reconnect if IPC connection was lost */
    if (g_ipc_fd < 0) {
      int fd = ipc_try_connect();
      if (fd >= 0) {
        g_ipc_fd = fd;
        printf("[IPC] Reconnected to xapp_sec_mitigate\n");
        fflush(stdout);
      }
    }

    struct timespec ts_main;
    clock_gettime(CLOCK_REALTIME, &ts_main);
    uint64_t now_ms = (uint64_t)ts_main.tv_sec * 1000ULL
                      + ts_main.tv_nsec / 1000000ULL;

    /* THROTTLE: severity CRITICAL, not yet throttled, cooldown elapsed */
    if (g_pending_throttle == 1 && !g_throttle_active
        && (now_ms - g_throttle_last_ms > THROTTLE_COOLDOWN_MS)) {
      ids_detection_state_t det = ids_get_detection_state();
      printf("[DETECT] CRITICAL — sending THROTTLE to mitigator (max=5%%)\n");
      fflush(stdout);
      ipc_send_mitigate("THROTTLE", 5,
                        alert_type_to_str(det.alert_type),
                        g_last_anomaly_score,
                        "stage2_persistence_confirmed");
      g_throttle_active  = 1;
      g_throttle_last_ms = now_ms;
      g_sev0_since_ms    = 0;
      g_pending_throttle = 0;
    }

    /* Track grace period for RESTORE */
    if (g_pending_throttle == 2 && g_throttle_active) {
      if (g_sev0_since_ms == 0) {
        g_sev0_since_ms = now_ms;
        printf("[DETECT] Attack subsided — RESTORE grace period started (%lums)\n",
               (unsigned long)RESTORE_GRACE_MS);
        fflush(stdout);
      } else if (now_ms - g_sev0_since_ms >= RESTORE_GRACE_MS) {
        ids_detection_state_t det = ids_get_detection_state();
        printf("[DETECT] Grace elapsed — sending RESTORE to mitigator (max=100%%)\n");
        fflush(stdout);
        ipc_send_mitigate("RESTORE", 100,
                          alert_type_to_str(det.alert_type),
                          0.0f,
                          "recovery_grace_elapsed");
        g_throttle_active  = 0;
        g_throttle_last_ms = now_ms;
        g_sev0_since_ms    = 0;
        g_pending_throttle = 0;
      }
    } else if (g_pending_throttle != 2) {
      g_sev0_since_ms = 0; /* reset grace timer if severity returned */
    }
  }
```

- [x] **Step 4: Add IPC cleanup in shutdown section**

Find `printf("Stopping xApp...\n");` (around line 1830) and add before it:

```c
  /* Close IPC connection */
  if (g_ipc_fd >= 0) {
    close(g_ipc_fd);
    g_ipc_fd = -1;
  }
```

- [x] **Step 5: Build both targets**

```bash
cd ~/flexric/build
make -j$(nproc) xapp_sec_moni xapp_sec_mitigate 2>&1 | tail -30
```

Expected: both compile without errors.

- [x] **Step 6: Sync to copy-xapp and commit**

```bash
cp ~/flexric/examples/xApp/c/monitor/xapp_sec_moni.c ~/sec-xapp/copy-xapp/
cd ~/sec-xapp
git add copy-xapp/xapp_sec_moni.c
git commit -m "feat: add IPC client to xapp_sec_moni for xapp_sec_mitigate integration"
```

---

## Task 7: Update start_xapp_c_mitigate.sh

**Files:**
- Modify: `~/sec-xapp/start_xapp_c_mitigate.sh`

- [x] **Step 1: Add MITIGATE_BIN variable**

Find the variables block (lines 28–32) and add:

```bash
MITIGATE_BIN="/home/telmat/flexric/build/examples/xApp/c/monitor/xapp_sec_mitigate"
```

- [x] **Step 2: Replace Window 2 iptables watcher with mitigator pane**

Find the `Window 2` block (line 258–266):

```bash
# =============================================================
# Window 2: Mitigate — iptables watcher otomatis
# =============================================================
tmux new-window -t "$SESSION" -n "Mitigate"
tmux send-keys -t "$SESSION:2" \
    "echo '=== [Window 2] iptables Watcher — Layer 2 Hybrid Mitigation ===' && \
     echo '  Menunggu UE attach dan xapp mulai...' && \
     until [ -f '$PHASE2_FLAG' ]; do sleep 1; done && \
     sleep 3 && \
     '$WATCHER_SCRIPT' \
       '$XAPP_LOG' '$CORE_IP' '$CORE_USER' '$CORE_PASS' \
       '$UE_SUBNET' '$MITIGATE_FLAG' '$RECOVERY_FLAG'" Enter
```

Replace with:

```bash
# =============================================================
# Window 2: xapp_sec_mitigate — E2SM-RC mitigation (IPC server)
# Start BEFORE xapp_sec_moni (it's the socket server)
# =============================================================
tmux new-window -t "$SESSION" -n "Mitigate"
tmux send-keys -t "$SESSION:2" \
    "echo '=== [Window 2] xapp_sec_mitigate — E2SM-RC Mitigation ===' && \
     echo '  Menunggu Near-RT RIC siap (Window 0 Pane 0)...' && \
     sleep 5 && \
     '$MITIGATE_BIN' \
       -c '$XAPP_CONF' --ue_f1ap 1 --mcc 001 --mnc 01 --sst 1" Enter
```

- [x] **Step 3: Update Pane 3 comment — remove --mitigate flag note**

Find the comment on Pane 3 (line 222):
```bash
# --mitigate (E2SM-RC PRB throttle) dinonaktifkan — Layer 1 menyebabkan E2 timeout
# Layer 2 (iptables via watcher) tetap aktif via STAGE2-CRITICAL log trigger
```

Replace with:
```bash
# IPC mitigasi aktif via xapp_sec_mitigate (Window 2)
# xapp_sec_moni menjadi IPC client — tidak perlu --mitigate flag
```

- [x] **Step 4: Remove iptables watcher script generation and cleanup**

Remove the `cat > "$WATCHER_SCRIPT" << 'WATCHER_EOF' ... WATCHER_EOF` block (lines 109–157).

Remove `WATCHER_SCRIPT="/tmp/xapp_iptables_watcher.sh"` from variables.

In the `cleanup()` function, remove the iptables SSH block:
```bash
    if [ -f "$MITIGATE_FLAG" ]; then
        ...
        rm -f "$MITIGATE_FLAG"
    fi
```
and simplify `rm -f` to not include `WATCHER_SCRIPT`, `MITIGATE_FLAG`, `RECOVERY_FLAG`.

In the cleanup section of the main tmux setup, remove:
```bash
rm -f "$PHASE2_FLAG" "$MITIGATE_FLAG" "$RECOVERY_FLAG" "$WATCHER_SCRIPT"
```
Replace with:
```bash
rm -f "$PHASE2_FLAG"
```

Also remove the sshpass iptables cleanup block at startup (lines 97–103).

- [x] **Step 5: Commit**

```bash
cd ~/sec-xapp
git add start_xapp_c_mitigate.sh
git commit -m "feat: update start script — replace iptables watcher with xapp_sec_mitigate pane"
```

---

## Task 8: Integration Test

- [ ] **Step 1: Start full stack**

```bash
cd ~/sec-xapp
./start_xapp_c_mitigate.sh
```

In the tmux session, verify:
- Pane 0: Near-RT RIC shows `E2AP listening on :36421`
- Pane 1: gNB shows E2 Setup successful
- Window 2: `xapp_sec_mitigate` shows `[IPC] Listening on /tmp/sec_xapp_mitigate.sock`

- [ ] **Step 2: Verify IPC connection after UE attach**

After pressing ENTER in Pane 2 to start `xapp_sec_moni`:
- Window 2 (`xapp_sec_mitigate`) should show: `[IPC] xapp_sec_moni connected.`
- Pane 3 (`xapp_sec_moni`) should show: `[IPC] Connected to xapp_sec_mitigate at /tmp/sec_xapp_mitigate.sock`

- [ ] **Step 3: Trigger attack and verify THROTTLE**

From the attack scripts (separate terminal):
```bash
cd ~/xapp/security-scripts
./helpers/switch_label.sh 1 ul_flood UE1
./attacks/ul_flood.sh $DEV1
```

Watch Pane 3 for:
```
>>> [STAGE2-CRITICAL] SATURATION CONFIRMED
[DETECT] CRITICAL — sending THROTTLE to mitigator (max=5%)
[IPC] ACK received: {"status":"OK","action":"THROTTLE","applied":true,...}
```

Watch Window 2 for:
```
[IPC] action=THROTTLE attack=UL_FLOOD prb_limit=5
[MITIGATE] E2SM-RC sent: max_prb=5% (check gNB log for ACK)
```

- [ ] **Step 4: Stop attack and verify RESTORE**

```bash
./helpers/switch_label.sh 0 recovery none
```

After ~10 seconds grace period, Pane 3 should show:
```
[DETECT] Grace elapsed — sending RESTORE to mitigator (max=100%)
[IPC] ACK received: {"status":"OK","action":"RESTORE","applied":true,...}
```

- [ ] **Step 5: Final sync and commit**

```bash
cd ~/sec-xapp
git add copy-xapp/
git status
git commit -m "feat: complete xapp_sec_mitigate integration — E2SM-RC IPC mitigation"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ UNIX Domain Socket `/tmp/sec_xapp_mitigate.sock` — Task 3
- ✅ cJSON JSON parsing — Tasks 1, 3, 6
- ✅ Persistent connection + auto-reconnect — Tasks 3, 6
- ✅ Dynamic RC RF ID from DU — Task 3 (`g_rc_rf_id`)
- ✅ `GNB_DU_UE_ID_E2SM` — Task 3 (`build_ctrl_hdr`)
- ✅ Numeric RAN param IDs (1–13) — Task 3 (`build_ctrl_msg`)
- ✅ THROTTLE/RESTORE with `prb_limit` from JSON — Task 3
- ✅ `prb_limit` clamping + RESTORE override — Task 3
- ✅ ACK with `status/action/applied/message` — Task 3
- ✅ ERROR ACK (no silent failure) — Task 3
- ✅ Newline delimiter — Tasks 3, 6
- ✅ `reason` field in request — Task 6
- ✅ `confidence` from `g_last_anomaly_score` — Task 6
- ✅ ACK timeout 500ms (`SO_RCVTIMEO`) — Task 6
- ✅ RESTORE grace period (`RESTORE_GRACE_MS`) — Task 6
- ✅ `unlink()` on exit — Task 3 (`cleanup()`, `atexit`)
- ✅ Signal cleanup — Task 3
- ✅ CMakeLists — Task 4
- ✅ start script update — Task 7
