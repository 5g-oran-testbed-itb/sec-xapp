/*
 * xApp: PRB Control via E2SM-RC Style 2, Action 6
 * ================================================
 * Kompatibel dengan: FlexRIC dev branch (commit 41df8703)
 *                    srsRAN Project (commit 3ed363da, main branch)
 *
 * Build:
 *   cp xapp_prb_ctrl.c ~/flexric/examples/xApp/c/ctrl/
 *   Edit ~/flexric/examples/xApp/c/ctrl/CMakeLists.txt:
 *
 *     add_executable(xapp_prb_ctrl
 *       xapp_prb_ctrl.c
 *       ../../../../src/util/alg_ds/alg/defer.c
 *     )
 *     target_link_libraries(xapp_prb_ctrl
 *       PUBLIC e42_xapp -pthread -lsctp -ldl
 *     )
 *
 *   cd ~/flexric/build && make -j$(nproc)
 *
 * Jalankan:
 *   ./build/examples/xApp/c/ctrl/xapp_prb_ctrl \
 *       -c /usr/local/etc/flexric/flexric.conf \
 *       --min_prb 10 --max_prb 50 --ue_f1ap 1
 *
 * Cara dapat gnb_cu_ue_f1ap_id:
 *   grep -i "f1ap\|ue_id" /tmp/gnb.log | tail -20
 *   Atau lihat output xapp_oran_moni — field UE ID di indication
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

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdint.h>
#include <assert.h>
#include <signal.h>

/* ─── Konfigurasi default ────────────────────────────────────────── */
static uint32_t g_min_prb    = 10;
static uint32_t g_max_prb    = 80;
static uint32_t g_ded_prb    = 100;
static uint32_t g_ue_f1ap    = 1;
static uint32_t g_interval_s = 5;
static uint32_t g_repeat     = 3;
static uint8_t  g_sst        = 1;
static uint8_t  g_plmn[3]    = {0x00, 0xf0, 0x10}; /* 001-01 */

static volatile int g_running = 1;

static void handle_signal(int sig) { (void)sig; g_running = 0; }

/* ─── PLMN encoder ───────────────────────────────────────────────── */
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

/* ─── Helper builders ────────────────────────────────────────────── */
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

/* ─── Build Control Message ──────────────────────────────────────── */
static e2sm_rc_ctrl_msg_frmt_1_t build_ctrl_msg(void)
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

  /* ID 11, 12, 13: PRB ratios */
  seq_ran_param_t p11 = make_int_param(11, (int64_t)g_min_prb);
  seq_ran_param_t p12 = make_int_param(12, (int64_t)g_max_prb);
  seq_ran_param_t p13 = make_int_param(13, (int64_t)g_ded_prb);

  /* ID 3: RRM Policy {ID5, ID11, ID12, ID13} */
  seq_ran_param_t p3_ch[4] = {p5, p11, p12, p13};
  seq_ran_param_t p3 = make_struct_param(3, p3_ch, 4);

  /* ID 2: RRM Policy Ratio Group {ID3} */
  seq_ran_param_t p2 = make_struct_param(2, &p3, 1);

  /* ID 1: RRM Policy Ratio List [ID2] */
  seq_ran_param_t p1 = make_list_param_single(1, &p2);

  /* Wrap ke format 1 */
  e2sm_rc_ctrl_msg_frmt_1_t msg = {0};
  msg.sz_ran_param = 1;
  msg.ran_param    = calloc(1, sizeof(seq_ran_param_t));
  assert(msg.ran_param);
  msg.ran_param[0] = p1;

  return msg;
}

/* ─── Build Control Header ───────────────────────────────────────── */
static e2sm_rc_ctrl_hdr_frmt_1_t build_ctrl_hdr(void)
{
  e2sm_rc_ctrl_hdr_frmt_1_t hdr = {0};
  hdr.ric_style_type         = 2;          /* Style 2: PRB Quota */
  hdr.ctrl_act_id            = 6;   	   /* Action 6: Slice-level PRB Quota */
  hdr.ue_id.type             = GNB_DU_UE_ID_E2SM;
  hdr.ue_id.gnb_du.gnb_cu_ue_f1ap = g_ue_f1ap;
  return hdr;
}

/* ─── Main ───────────────────────────────────────────────────────── */
int main(int argc, char* argv[])
{
  char mcc[8] = "001", mnc[8] = "01";

  signal(SIGINT, handle_signal);
  signal(SIGTERM, handle_signal);

  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--help")) {
      printf("Usage: xapp_prb_ctrl -c <flexric.conf> [options]\n");
      printf("  --min_prb  N   Min PRB%% (default 10)\n");
      printf("  --max_prb  N   Max PRB%% (default 80)\n");
      printf("  --ded_prb  N   Dedicated PRB%% (default 100)\n");
      printf("  --ue_f1ap  N   gnb_cu_ue_f1ap_id (default 1)\n");
      printf("  --interval N   Seconds between sends (default 5)\n");
      printf("  --repeat   N   Total sends, 0=infinite (default 3)\n");
      printf("  --sst      N   S-NSSAI SST (default 1)\n");
      printf("  --mcc      S   MCC e.g. 001\n");
      printf("  --mnc      S   MNC e.g. 01\n");
      return 0;
    }
    if (i + 1 >= argc) continue;
    if      (!strcmp(argv[i], "--min_prb"))  g_min_prb    = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--max_prb"))  g_max_prb    = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--ded_prb"))  g_ded_prb    = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--ue_f1ap"))  g_ue_f1ap    = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--interval")) g_interval_s = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--repeat"))   g_repeat     = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--sst"))      g_sst        = (uint8_t)atoi(argv[++i]);
    else if (!strcmp(argv[i], "--mcc"))      strncpy(mcc, argv[++i], 7);
    else if (!strcmp(argv[i], "--mnc"))      strncpy(mnc, argv[++i], 7);
  }

  encode_plmn(mcc, mnc, g_plmn);

  printf("========================================\n");
  printf("  xApp: E2SM-RC PRB Control\n");
  printf("  UE F1AP ID : %u\n", g_ue_f1ap);
  printf("  Min PRB%%   : %u\n", g_min_prb);
  printf("  Max PRB%%   : %u\n", g_max_prb);
  printf("  Repeat     : %u | Interval: %us\n", g_repeat, g_interval_s);
  printf("========================================\n\n");

  /* Pisahkan args FlexRIC (-c, -p, -a, -d, -n) dari args custom kita */
  /* Buat argv baru yang hanya berisi args FlexRIC */
  char* fr_argv[32];
  int   fr_argc = 0;
  fr_argv[fr_argc++] = argv[0]; /* nama program */

  for (int i = 1; i < argc; i++) {
    /* FlexRIC args: -c, -p, -a, -d, -n, -h */
    if (!strcmp(argv[i], "-c") || !strcmp(argv[i], "-p") ||
        !strcmp(argv[i], "-a") || !strcmp(argv[i], "-d") ||
        !strcmp(argv[i], "-n")) {
      fr_argv[fr_argc++] = argv[i];
      if (i + 1 < argc) fr_argv[fr_argc++] = argv[++i];
    }
    /* Skip semua --custom args kita */
  }

  /* Init — pakai fr_args_t sesuai API dev branch */
  fr_args_t args = init_fr_args(fr_argc, fr_argv);
  init_xapp_api(&args);
  sleep(1);

  /* Get E2 nodes — nama struct: e2_node_arr_xapp_t */
  e2_node_arr_xapp_t nodes = e2_nodes_xapp_api();
  if (nodes.len == 0) {
    fprintf(stderr, "[ERROR] Tidak ada E2 node terhubung.\n");
    try_stop_xapp_api();
    return 1;
  }

  printf("[xApp] %d E2 node(s) connected\n", nodes.len);
  global_e2_node_id_t* target = NULL;
  for (int i = 0; i < (int)nodes.len; i++) {
    printf("  Node[%d] type=%d\n", i, nodes.n[i].id.type);
    if (NODE_IS_DU(nodes.n[i].id.type)) {
      target = &nodes.n[i].id;
      printf("  → Target DU ditemukan di index %d\n", i);
      break;
    }
  }
  if (target == NULL) {
    fprintf(stderr, "[ERROR] Tidak menemukan node DU!\n");
    free_e2_node_arr_xapp(&nodes);
    try_stop_xapp_api();
    return 1;
  }

  /* Cek RF ID yang benar untuk RC */
  uint32_t rc_rf_id = 0;
    /* Cari RF ID di node DU, bukan node 0 */
  int du_idx = -1;
  for (int i = 0; i < (int)nodes.len; i++) {
    if (NODE_IS_DU(nodes.n[i].id.type)) {
      du_idx = i;
      break;
    }
  }

  for (size_t i = 0; i < nodes.n[du_idx].len_rf; i++) {
    if (nodes.n[du_idx].rf[i].defn.type == RC_RAN_FUNC_DEF_E) {
      rc_rf_id = nodes.n[du_idx].rf[i].id;
      printf("[xApp] RC RF ID = %u (dari DU index %d)\n", rc_rf_id, du_idx);
      break;
    }
  }
  if (rc_rf_id == 0) {
    fprintf(stderr, "[ERROR] RC RAN Function tidak ditemukan di E2 node.\n");
    fprintf(stderr, "        Pastikan e2sm_rc_enabled: true di gnb.yml\n");
    free_e2_node_arr_xapp(&nodes);
    try_stop_xapp_api();
    return 1;
  }

  /* Control loop */
  uint32_t sent = 0;
  while (g_running && (g_repeat == 0 || sent < g_repeat)) {

    printf("[xApp] Send #%u → UE=%u min=%u%% max=%u%%\n",
           sent + 1, g_ue_f1ap, g_min_prb, g_max_prb);

    rc_ctrl_req_data_t rc_ctrl       = {0};
    rc_ctrl.hdr.format               = FORMAT_1_E2SM_RC_CTRL_HDR;
    rc_ctrl.hdr.frmt_1               = build_ctrl_hdr();
    rc_ctrl.msg.format               = FORMAT_1_E2SM_RC_CTRL_MSG;
    rc_ctrl.msg.frmt_1               = build_ctrl_msg();

    /* Kirim — pakai rc_rf_id yang didapat dari E2 setup */
    control_sm_xapp_api(target, rc_rf_id, &rc_ctrl);
    printf("  → Control sent (cek log gNB untuk ACK/FAIL)\n");

    free_rc_ctrl_req_data(&rc_ctrl);
    sent++;

    if (g_running && (g_repeat == 0 || sent < g_repeat))
      sleep(g_interval_s);
  }

  printf("\n[xApp] Done. %u request(s) sent.\n", sent);
  free_e2_node_arr_xapp(&nodes);
  try_stop_xapp_api();
  return 0;
}
