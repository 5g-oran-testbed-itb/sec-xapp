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
#include "../../../../src/util/e2ap_ngran_types.h"

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
#include <sys/wait.h>
#include <errno.h>
#include <pthread.h>

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

    /* ID 3 (RRM Policy) = ONLY ID 5 (Member List); ID 11/12/13 are SIBLINGS under
     * ID 2 (Ratio Group). EMPIRICAL: this is the layout srsRAN's Style2/Action6 decoder
     * ACCEPTS on THIS deployed build — it returns CONTROL ACKNOWLEDGE (session 20:05).
     * The alternative nesting (ID11/12/13 INSIDE ID3, as in xapp_prb_ctrl.c, which
     * targets a DIFFERENT srsRAN+flexric build — note octet_str vs octet_str_ran)
     * caused "Communication with E2 Node lost" here (20:37 test). Do NOT change without
     * re-verifying against the deployed srsRAN. Whether the quota is actually ENFORCED
     * (brate drop) is a SEPARATE open issue. */
    seq_ran_param_t p3 = make_struct_param(3, &p5, 1);
    seq_ran_param_t p2_ch[4] = {p3, p11, p12, p13};
    seq_ran_param_t p2 = make_struct_param(2, p2_ch, 4);

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

/* ─── Execute E2SM-RC PRB control (in detached thread) ─────────────── */
typedef struct { int max_prb; } rc_ctrl_arg_t;

static void* rc_control_thread(void* arg)
{
    rc_ctrl_arg_t* a = (rc_ctrl_arg_t*)arg;
    int max_prb = a->max_prb;
    free(a);

    if (!g_du_node_valid || g_rc_rf_id == 0) {
        printf("[MITIGATE] ERROR: DU node or RC RF ID not available\n");
        fflush(stdout);
        return NULL;
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
    return NULL;
}

static int execute_rc_control(int max_prb)
{
    rc_ctrl_arg_t* arg = malloc(sizeof(rc_ctrl_arg_t));
    assert(arg);
    arg->max_prb = max_prb;
    pthread_t tid;
    if (pthread_create(&tid, NULL, rc_control_thread, arg) != 0) {
        printf("[MITIGATE] ERROR: failed to create RC control thread\n");
        fflush(stdout);
        free(arg);
        return 0;
    }
    pthread_detach(tid);
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
        cJSON* j_ue_id  = cJSON_GetObjectItemCaseSensitive(root, "ue_id");

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

        if (cJSON_IsNumber(j_ue_id)) {
            g_ue_f1ap = (uint32_t)j_ue_id->valuedouble;
        }

        /* Clamp and override */
        if (prb_limit < 0)   prb_limit = 0;
        if (prb_limit > 100) prb_limit = 100;
        if (strcmp(action, "THROTTLE") == 0 && prb_limit < 5) prb_limit = 5;
        if (strcmp(action, "RESTORE") == 0) prb_limit = 100;

        printf("[IPC] action=%s attack=%s prb_limit=%d target_ue_id=%u\n", action, attack, prb_limit, g_ue_f1ap);
        fflush(stdout);

        /* Build and send ACK immediately to prevent client timeout */
        cJSON* ack = cJSON_CreateObject();
        cJSON_AddStringToObject(ack, "status",  "OK");
        cJSON_AddStringToObject(ack, "action",  action);
        cJSON_AddBoolToObject  (ack, "applied", 1);
        cJSON_AddStringToObject(ack, "message", "Mitigation received");
        char* ack_str = cJSON_PrintUnformatted(ack);
        cJSON_Delete(ack);

        size_t alen  = strlen(ack_str);
        char*  ack_nl = malloc(alen + 2);
        assert(ack_nl);
        memcpy(ack_nl, ack_str, alen);
        ack_nl[alen]   = '\n';
        ack_nl[alen+1] = '\0';
        free(ack_str);

        send(client_fd, ack_nl, alen + 1, MSG_NOSIGNAL);
        free(ack_nl);

        /* Send E2SM-RC control request directly to RIC (O-RAN compliant) */
        printf("[MITIGATE] Executing E2SM-RC PRB control: action=%s, limit=%d%%\n", action, prb_limit);
        fflush(stdout);
        execute_rc_control(prb_limit);

        cJSON_Delete(root);
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

    e2_node_arr_xapp_t nodes = {0};
    int du_idx = -1;
    int retry_cnt = 0;
    while (g_running) {
        nodes = e2_nodes_xapp_api();
        if (nodes.len > 0) {
            for (int i = 0; i < (int)nodes.len; i++) {
                printf("  Node[%d] type=%d\n", i, nodes.n[i].id.type);
                if (E2AP_NODE_IS_DU(nodes.n[i].id.type) && du_idx < 0) {
                    du_idx = i;
                }
            }
            if (du_idx >= 0) {
                break;
            }
            free_e2_node_arr_xapp(&nodes);
        }
        printf("[xApp] Waiting for E2 DU node connection... (retry %d/30)\n", retry_cnt + 1);
        fflush(stdout);
        sleep(1);
        retry_cnt++;
        if (retry_cnt >= 30) {
            fprintf(stderr, "[ERROR] No E2 DU node found after 30s. Exiting.\n");
            return 1;
        }
    }

    if (!g_running || du_idx < 0) {
        if (nodes.len > 0) free_e2_node_arr_xapp(&nodes);
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
